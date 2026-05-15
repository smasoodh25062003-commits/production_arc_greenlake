"""
HPE GreenLake SAML Checker — Flask Backend v8
FIXES over v7:
  - NameID "name" fallback uses EXACT match only (attr_name.lower() == "name").
    Previously find_plain_attr("name") ran all 3 passes — Pass 2 (token match)
    would fire on gl_first_name and gl_last_name because "name" is a token in
    both (["gl","first","name"], ["gl","last","name"]), incorrectly stealing
    the first/last name value for NameID. Exact-only match is unambiguous:
    an attribute literally named "name" is 99.9% a NameID/email carrier.
  - Priority contract enforced everywhere:
      1. Plain attributes (no schema URL) — specific keywords first, then "name"
      2. Schema-URL attributes — by last URL segment
      3. Secondary SAML (auth.hpe.com HPE Okta) — same order
      4. Warning/not-found if nothing matches
  - All v7 fixes retained.
"""

"""SAML validator module — provides parsing, validation, and Flask route registration.

Use `register_routes(app)` from the main Flask app to mount /api/health and /api/parse.
"""
import base64
import json
import re
import traceback
from datetime import datetime, timezone
from urllib.parse import unquote_plus

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from flask import jsonify, request
from lxml import etree

# ── Constants ─────────────────────────────────────────────────────────────────
EXPECTED_ENTITY   = "https://sso.common.cloud.hpe.com"
EXPECTED_ACS      = "https://sso.common.cloud.hpe.com/sp/ACS.saml2"
HPE_INTERNAL_ACS  = "auth.hpe.com/sso/saml2"

NS_ASSERT = "urn:oasis:names:tc:SAML:2.0:assertion"
NS_PROTO  = "urn:oasis:names:tc:SAML:2.0:protocol"
NS_DS     = "http://www.w3.org/2000/09/xmldsig#"

# ── IDP fingerprints ──────────────────────────────────────────────────────────
IDP_SIGNATURES = [
    (["accounts.google.com", "google-idp", "googleidp", "googleapis", "google.com/o/saml"],
     "Google Workspace", "🟦"),
    (["duo.com", "duosecurity", "duo"],
     "Duo Security", "🔒"),
    (["okta.com", "okta-", "oktapreview.com", "mylogin.hpe.com", "okta"],
     "Okta", "🟠"),
    (["sts.windows.net", "login.microsoftonline", "azure", "entra", "microsoft.com"],
     "Microsoft Azure AD / Entra ID", "🔷"),
    (["ping", "pingone", "pingfederate", "pingidentity"],
     "PingFederate / PingOne", "🟩"),
    (["adfs", "/adfs/"],
     "Microsoft ADFS", "🔵"),
    (["securid", "rsa.com", "auth.securid"],
     "RSA SecurID", "🔐"),
    (["shibboleth"],
     "Shibboleth", "🎓"),
    (["forgerock", "openam"],
     "ForgeRock / OpenAM", "🌿"),
    (["jumpcloud"],
     "JumpCloud", "☁️"),
    (["auth0"],
     "Auth0", "⬛"),
    (["keycloak"],
     "Keycloak", "🔑"),
    (["onelogin"],
     "OneLogin", "🔴"),
    (["cyberark"],
     "CyberArk", "🛡"),
]

# ── Masked value detection ────────────────────────────────────────────────────
_MASK_PATTERNS = re.compile(
    r"^\*+$|^MASKED$|^REDACTED$|^\[REDACTED\]$|^\[MASKED\]$|^<MASKED>$|^xxx+$",
    re.IGNORECASE,
)

def _is_masked(val: str) -> bool:
    return bool(_MASK_PATTERNS.match((val or "").strip()))


# ── Element helpers (pure iter — fully prefix-agnostic) ───────────────────────
def _local(el):
    return etree.QName(el.tag).localname

def _ns(el):
    return etree.QName(el.tag).namespace

def _iter_local(root, local):
    return [e for e in root.iter() if _local(e) == local]

def _find_local(root, local):
    return next((e for e in root.iter() if _local(e) == local), None)

def _txt(el):
    return (el.text or "").strip() if el is not None else ""

def _attr(el, name, default=""):
    return el.get(name, default) if el is not None else default


# ── Smart Issuer extraction ───────────────────────────────────────────────────
def get_issuer(root):
    assertion = _find_local(root, "Assertion")
    if assertion is not None:
        for child in assertion:
            if _local(child) == "Issuer":
                t = _txt(child)
                if t:
                    return t
    el = _find_local(root, "Issuer")
    return _txt(el)


# ── Smart ACS / Destination extraction ───────────────────────────────────────
def get_destination(root):
    if _local(root) == "Response":
        d = root.get("Destination", "")
        if d:
            return d
    scd = _find_local(root, "SubjectConfirmationData")
    if scd is not None:
        r = scd.get("Recipient", "")
        if r:
            return r
    for e in root.iter():
        if _local(e) == "Response":
            d = e.get("Destination", "")
            if d:
                return d
    return ""


# ── Smart Audience extraction ─────────────────────────────────────────────────
def get_audience(root):
    ar = _find_local(root, "AudienceRestriction")
    if ar is not None:
        aud = _find_local(ar, "Audience")
        if aud is not None:
            t = _txt(aud)
            if t:
                return t
    aud = _find_local(root, "Audience")
    return _txt(aud)


# ── X.509 certificate extraction ─────────────────────────────────────────────
def get_x509_certs(root):
    return [_txt(e) for e in _iter_local(root, "X509Certificate") if _txt(e)]


# ── IDP Detection ─────────────────────────────────────────────────────────────
def detect_idp(issuer: str, destination: str, cert_subject: str = "") -> dict:
    combined = " ".join([issuer, destination or "", cert_subject or ""]).lower()
    for keywords, name, icon in IDP_SIGNATURES:
        if any(kw.lower() in combined for kw in keywords):
            return {"name": name, "icon": icon, "detected": True, "issuer": issuer}
    display = issuer.strip() if issuer.strip() else destination.strip()
    return {"name": display or "(no issuer found)", "icon": "🏛", "detected": False,
            "issuer": issuer}


# ── Certificate validation ────────────────────────────────────────────────────
def validate_cert(cert_b64: str) -> dict:
    r = {"found": True, "valid": False, "expired": False,
         "not_before": None, "not_after": None, "subject": None,
         "issuer": None, "serial": None, "days_remaining": None,
         "algorithm": None, "error": None, "masked": False}
    if _is_masked(cert_b64):
        r["masked"] = True
        r["error"] = "Certificate value is masked/redacted in this export."
        return r
    try:
        raw = base64.b64decode(re.sub(r"\s+", "", cert_b64) + "==")
        cert = x509.load_der_x509_certificate(raw, default_backend())
        now = datetime.now(timezone.utc)

        def aw(dt):
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

        try:
            nb  = cert.not_valid_before_utc
            noa = cert.not_valid_after_utc
        except AttributeError:
            nb  = aw(cert.not_valid_before)
            noa = aw(cert.not_valid_after)
        r["not_before"]     = nb.strftime("%Y-%m-%d %H:%M:%S UTC")
        r["not_after"]      = noa.strftime("%Y-%m-%d %H:%M:%S UTC")
        r["subject"]        = cert.subject.rfc4514_string()
        r["issuer"]         = cert.issuer.rfc4514_string()
        r["serial"]         = format(cert.serial_number, "x").upper()
        r["expired"]        = now > noa
        r["not_yet_valid"]  = now < nb
        r["days_remaining"] = max(0, (noa - now).days)
        r["valid"]          = not r["expired"] and not r["not_yet_valid"]
        try:
            r["algorithm"] = cert.signature_hash_algorithm.name.upper()
        except Exception:
            r["algorithm"] = "Unknown"
    except Exception as e:
        r["error"] = str(e)
    return r


# ── Time window ───────────────────────────────────────────────────────────────
def check_time_window(root) -> dict:
    cond = _find_local(root, "Conditions")
    if cond is None:
        return {"pass": False,
                "error": "Conditions element not found in SAML assertion.",
                "not_before": None, "not_on_or_after": None,
                "diff_minutes": None, "diff_seconds": None}
    nb_str  = cond.get("NotBefore", "")
    noa_str = cond.get("NotOnOrAfter", "")
    if not nb_str or not noa_str:
        missing = []
        if not nb_str:  missing.append("NotBefore")
        if not noa_str: missing.append("NotOnOrAfter")
        return {"pass": False,
                "error": f"Missing attribute(s) on Conditions: {', '.join(missing)}",
                "not_before": nb_str or None, "not_on_or_after": noa_str or None,
                "diff_minutes": None, "diff_seconds": None}
    try:
        t1 = datetime.fromisoformat(nb_str.replace("Z", "+00:00"))
        t2 = datetime.fromisoformat(noa_str.replace("Z", "+00:00"))
        diff_sec = (t2 - t1).total_seconds()
        diff_min = round(diff_sec / 60, 2)
        ok = 0 < diff_min <= 74
        return {"pass": ok,
                "not_before": nb_str, "not_on_or_after": noa_str,
                "diff_seconds": int(diff_sec), "diff_minutes": diff_min,
                "error": None if ok else (
                    f"Window is {diff_min} min — exceeds 74-minute limit." if diff_min > 74
                    else "Invalid range: NotOnOrAfter is before NotBefore.")}
    except Exception as e:
        return {"pass": False, "error": str(e),
                "not_before": nb_str, "not_on_or_after": noa_str,
                "diff_minutes": None, "diff_seconds": None}


# ── Attribute extraction ──────────────────────────────────────────────────────

def _schema_segment(name: str) -> str:
    """
    Extract the last meaningful path segment from a schema URL attribute name.
    e.g. 'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname'
         → 'givenname'
    Returns '' for non-URL names.
    """
    if "://" not in name:
        return ""
    # last non-empty segment after splitting on / # : (any separator used in schema URLs)
    parts = re.split(r"[/:#]", name)
    parts = [p.strip() for p in parts if p.strip()]
    return parts[-1].lower() if parts else ""


def get_plain_attributes(root):
    """
    Return all SAML Attributes whose Name contains no URL schema (no '://').
    e.g. gl_first_name, FirstName, name, email — NOT http://schemas.xmlsoap.org/...
    Returns list of (attr_name, value) tuples in document order.
    """
    results = []
    for attr_el in _iter_local(root, "Attribute"):
        attr_name = attr_el.get("Name", "") or attr_el.get("FriendlyName", "")
        if not attr_name or "://" in attr_name:
            continue
        av_list = [e for e in attr_el if _local(e) == "AttributeValue"]
        value = _txt(av_list[0]) if av_list else _txt(attr_el)
        if value:
            results.append((attr_name, value))
    return results


def get_schema_attributes(root):
    """
    Return all SAML Attributes whose Name IS a schema URL.
    Returns list of (segment, full_attr_name, value) in document order.
    segment = last path component of the URL (e.g. 'givenname', 'surname')
    """
    results = []
    for attr_el in _iter_local(root, "Attribute"):
        attr_name = attr_el.get("Name", "") or attr_el.get("FriendlyName", "")
        if not attr_name or "://" not in attr_name:
            continue
        segment = _schema_segment(attr_name)
        if not segment:
            continue
        av_list = [e for e in attr_el if _local(e) == "AttributeValue"]
        value = _txt(av_list[0]) if av_list else _txt(attr_el)
        if value:
            results.append((segment, attr_name, value))
    return results


def find_plain_attr(plain_attrs, *keywords):
    """
    Search plain (name, value) pairs — 3 passes, most specific first.

    Pass 1 — exact match (case-insensitive)
    Pass 2 — word-boundary match: split on _ - . AND camelCase boundaries
              so 'LastName' → ['last','name'], 'gl_last_name' → ['gl','last','name']
    Pass 3 — substring match; only for keywords >= 4 chars to avoid false positives
              from short tokens like 'sur', 'uid', 'upn' matching unrelated names.
    Returns (value, attr_name) or ("", "").
    """
    lower_attrs = [(n, v, n.lower()) for n, v in plain_attrs]

    def tokenize(name_lower):
        # Split on separators first
        parts = re.split(r"[_\-.]", name_lower)
        # Then split each part on camelCase boundaries
        tokens = []
        for part in parts:
            # Insert split before uppercase-to-lowercase transitions
            sub = re.sub(r"([a-z])([A-Z])", r"\1_\2", part).lower()
            tokens.extend(t for t in re.split(r"[_]", sub) if t)
        return tokens

    # Pass 1 — exact name match
    for kw in keywords:
        kw_l = kw.lower()
        for attr_name, value, attr_lower in lower_attrs:
            if attr_lower == kw_l:
                return value, attr_name

    # Pass 2 — word-boundary token match (handles snake_case, camelCase, PascalCase)
    for kw in keywords:
        kw_l = kw.lower()
        for attr_name, value, attr_lower in lower_attrs:
            if kw_l in tokenize(attr_lower):
                return value, attr_name

    # Pass 3 — substring match; skip keywords shorter than 4 chars
    for kw in keywords:
        kw_l = kw.lower()
        if len(kw_l) < 4:
            continue
        for attr_name, value, attr_lower in lower_attrs:
            if kw_l in attr_lower:
                return value, attr_name

    return "", ""


def find_schema_attr(schema_attrs, *keywords):
    """
    Search schema (segment, full_name, value) pairs by segment keyword matching.

    Pass 1 — exact segment match
    Pass 2 — segment contains keyword as substring
    Returns (value, full_attr_name) or ("", "").
    """
    for kw in keywords:
        kw_l = kw.lower()
        for segment, full_name, value in schema_attrs:
            if segment == kw_l:
                return value, full_name

    for kw in keywords:
        kw_l = kw.lower()
        for segment, full_name, value in schema_attrs:
            if kw_l in segment:
                return value, full_name

    return "", ""


def find_attr_anywhere(plain_attrs, schema_attrs, *keywords):
    """
    Try plain attrs first (exact/word/substring), then schema attrs.
    Returns (value, display_attr_name, source) where source is 'plain' or 'schema'.
    """
    val, attr = find_plain_attr(plain_attrs, *keywords)
    if val:
        return val, attr, "plain"
    val, attr = find_schema_attr(schema_attrs, *keywords)
    if val:
        return val, attr, "schema"
    return "", "", ""


def get_attr_value(root, *names) -> str:
    """Exact-name lookup in all attributes (legacy, used for hpe_ccs_attribute)."""
    val, _ = get_attr_value_with_name(root, *names)
    return val


def get_attr_value_with_name(root, *names):
    """Return (value, matched_attribute_Name) or ("", "")."""
    for attr_el in _iter_local(root, "Attribute"):
        attr_name = attr_el.get("Name", "") or attr_el.get("FriendlyName", "")
        if attr_name in names:
            av_list = [e for e in attr_el if _local(e) == "AttributeValue"]
            if av_list:
                t = _txt(av_list[0])
                if t:
                    return t, attr_name
            t = _txt(attr_el)
            if t:
                return t, attr_name
    return "", ""


def get_hpe_ccs(root):
    """
    Get hpe_ccs_attribute value. Tries:
    1. Exact name match (plain or schema URL containing 'hpe_ccs' or 'ccs')
    2. Plain attr find_plain_attr with keyword 'hpe_ccs'
    3. Schema attr find_schema_attr with keyword 'hpe_ccs'
    """
    # 1. Exact names
    val = get_attr_value(root, "hpe_ccs_attribute", "hpeCcsAttribute", "ccs_attribute")
    if val:
        return val
    # 2. Plain attrs keyword search
    plain = get_plain_attributes(root)
    v, _ = find_plain_attr(plain, "hpe_ccs", "hpeccs", "ccs")
    if v:
        return v
    # 3. Schema attrs
    schema = get_schema_attributes(root)
    v, _ = find_schema_attr(schema, "hpe_ccs", "ccs")
    if v:
        return v
    return ""


def get_all_attributes(root) -> dict:
    result = {}
    for attr_el in _iter_local(root, "Attribute"):
        name = attr_el.get("Name") or attr_el.get("FriendlyName") or ""
        if not name:
            continue
        vals = [_txt(child) for child in attr_el if _local(child) == "AttributeValue"]
        vals = [v for v in vals if v]
        result[name] = vals[0] if len(vals) == 1 else (vals if vals else "")
    return result


# ── SAML extraction from any file format ──────────────────────────────────────
def extract_xml_bytes(raw: bytes, filename: str) -> bytes:
    text = raw.decode("utf-8", errors="replace").strip()

    stripped = text.lstrip()
    if stripped.startswith("<"):
        if b"urn:oasis:names:tc:SAML" in raw:
            result = _xml_if_saml_response(stripped)
            if result:
                return result

    try:
        dec = base64.b64decode(text + "==")
        if b"urn:oasis:names:tc:SAML" in dec or b"<saml" in dec[:300].lower():
            return dec
    except Exception:
        pass

    try:
        data = json.loads(text)
        result = _extract_from_json(data)
        if result:
            return result
    except Exception:
        pass

    if "SAMLResponse=" in text:
        r = _from_urlencoded(text)
        if r:
            return r

    raise ValueError(
        "Could not find a SAML Response in the uploaded file.\n"
        "Supported formats:\n"
        "  • Raw XML  (.xml)\n"
        "  • SAML Tracer export  (.json)\n"
        "  • HAR capture  (.har / .json)\n"
        "  • Base64-encoded XML  (.txt)\n"
        "  • URL-encoded POST body  (.txt / .log)"
    )


_secondary_root_cache = {"root": None}


def _extract_from_json(data) -> bytes:
    _secondary_root_cache["root"] = None

    if isinstance(data, dict):
        requests_list = data.get("requests")
        if isinstance(requests_list, list) and requests_list:
            r = _scan_saml_tracer_requests(requests_list)
            if r:
                _secondary_root_cache["root"] = _scan_saml_tracer_secondary(requests_list)
                return r

        entries = (data.get("log") or {}).get("entries", [])
        if entries:
            r = _scan_har_entries(entries)
            if r:
                return r

    if isinstance(data, list):
        r = _scan_bare_list(data)
        if r:
            return r

    return _deep_scan(data, 0)


def _scan_saml_tracer_requests(requests_list: list) -> bytes:
    from lxml import etree as _etree

    tier1 = []
    tier2 = []
    tier3 = []
    tier4 = []

    for req in requests_list:
        if not isinstance(req, dict):
            continue

        url    = req.get("url", "")
        method = req.get("method", "GET")

        saml_xml = req.get("saml", "")
        if saml_xml and isinstance(saml_xml, str) and not _is_masked(saml_xml):
            xml_bytes = _xml_if_saml_response(saml_xml)
            if xml_bytes:
                try:
                    root = _etree.fromstring(xml_bytes, _etree.XMLParser(recover=True))
                    dest = root.get("Destination", "")
                except Exception:
                    dest = ""

                if dest == EXPECTED_ACS:
                    tier1.append(xml_bytes)
                elif ("ACS" in url or "/sp/" in url or
                      "sso.common.cloud.hpe.com/sp" in url):
                    tier2.append(xml_bytes)
                else:
                    tier3.append(xml_bytes)

        if method == "POST":
            post_data = req.get("postData", "")
            if isinstance(post_data, str) and not _is_masked(post_data):
                if "SAMLResponse=" in post_data:
                    r = _from_urlencoded(post_data)
                    if r:
                        tier4.append(r)

            post_params = req.get("post", [])
            if isinstance(post_params, list):
                for p in post_params:
                    if isinstance(p, dict) and p.get("name") == "SAMLResponse":
                        val = p.get("value", "")
                        if val and not _is_masked(val):
                            r = _b64_or_xml(val)
                            if r:
                                tier4.append(r)

    return (tier1 or tier2 or tier3 or tier4 or [None])[0]


def _scan_saml_tracer_secondary(requests_list: list):
    from lxml import etree as _etree

    for req in requests_list:
        if not isinstance(req, dict):
            continue
        saml_xml = req.get("saml", "")
        if not saml_xml or _is_masked(saml_xml):
            continue
        url = req.get("url", "")
        if HPE_INTERNAL_ACS not in url:
            continue
        xml_bytes = _xml_if_saml_response(saml_xml)
        if xml_bytes:
            try:
                return _etree.fromstring(xml_bytes, _etree.XMLParser(recover=True))
            except Exception:
                pass
    return None


def _xml_if_saml_response(xml_str: str):
    xml_str = xml_str.strip()
    if not xml_str:
        return None
    if "urn:oasis:names:tc:SAML" not in xml_str and "saml" not in xml_str.lower():
        return None
    body = xml_str
    if body.startswith("<?"):
        end = body.find("?>")
        if end != -1:
            body = body[end + 2:].lstrip()
    first_tag_match = re.match(r"<(?:[A-Za-z0-9_-]+:)?([A-Za-z0-9_-]+)", body)
    if not first_tag_match:
        return None
    local = first_tag_match.group(1)
    if local not in ("Response", "ArtifactResponse"):
        return None
    return xml_str.encode("utf-8")


def _scan_bare_list(items: list) -> bytes:
    acs_results = []
    any_results = []

    for item in items:
        if not isinstance(item, dict):
            continue
        url = item.get("url", "")
        is_acs = "ACS" in url or "/sp/" in url

        saml_xml = item.get("saml", "")
        if saml_xml and not _is_masked(saml_xml):
            r = _xml_if_saml_response(saml_xml)
            if r:
                (acs_results if is_acs else any_results).append(r)

        for key in ["SAMLResponse", "samlResponse", "saml_response"]:
            val = item.get(key)
            if val and isinstance(val, str) and not _is_masked(val):
                r = _b64_or_xml(val)
                if r:
                    (acs_results if is_acs else any_results).append(r)

        for key in ["requestBody", "postBody", "body"]:
            val = item.get(key)
            if val and isinstance(val, str) and "SAMLResponse=" in val:
                r = _from_urlencoded(val)
                if r:
                    (acs_results if is_acs else any_results).append(r)

    return (acs_results or any_results or [None])[0]


def _scan_har_entries(entries) -> bytes:
    acs_results = []
    any_results = []

    for entry in entries:
        try:
            req = entry.get("request") or {}
            url = req.get("url", "")
            post = req.get("postData") or {}
            txt = post.get("text", "") if isinstance(post, dict) else str(post)
            params = post.get("params", []) if isinstance(post, dict) else []

            is_acs = ("ACS" in url or "acs" in url or
                      "sso.common.cloud.hpe.com/sp" in url or
                      "SAMLResponse" in txt)

            if "SAMLResponse=" in txt:
                r = _from_urlencoded(txt)
                if r:
                    (acs_results if is_acs else any_results).append(r)

            for p in params:
                if isinstance(p, dict) and p.get("name") == "SAMLResponse":
                    val = p.get("value", "")
                    if not _is_masked(val):
                        r = _b64_or_xml(val)
                        if r:
                            (acs_results if is_acs else any_results).append(r)
        except Exception:
            pass

    return (acs_results or any_results or [None])[0]


def _deep_scan(obj, depth) -> bytes:
    if depth > 10:
        return None
    if isinstance(obj, str):
        if _is_masked(obj):
            return None
        r = _b64_or_xml(obj)
        if r:
            return r
        if "SAMLResponse=" in obj:
            return _from_urlencoded(obj)
    elif isinstance(obj, dict):
        for key in ["SAMLResponse", "samlResponse", "saml_response",
                    "response", "Response", "assertion", "Assertion",
                    "saml", "token"]:
            if key in obj:
                r = _deep_scan(obj[key], depth + 1)
                if r:
                    return r
        for v in obj.values():
            r = _deep_scan(v, depth + 1)
            if r:
                return r
    elif isinstance(obj, list):
        for item in obj:
            r = _deep_scan(item, depth + 1)
            if r:
                return r
    return None


def _b64_or_xml(val: str) -> bytes:
    val = val.strip()
    if not val or _is_masked(val):
        return None
    if val.startswith("<") or val.lstrip().startswith("<?xml"):
        result = _xml_if_saml_response(val)
        if result:
            return result
    try:
        dec = base64.b64decode(val + "==")
        if b"urn:oasis:names:tc:SAML" in dec or b"<saml" in dec[:400].lower():
            return dec
    except Exception:
        pass
    return None


def _from_urlencoded(text: str) -> bytes:
    for part in re.split(r"[&\n]", text):
        if "SAMLResponse=" in part:
            val = part.split("SAMLResponse=", 1)[1]
            val = unquote_plus(val.strip())
            if _is_masked(val):
                return None
            return _b64_or_xml(val)
    return None


# ── Main parse & validate ─────────────────────────────────────────────────────
def parse_and_validate(raw: bytes, filename: str) -> dict:
    xml_bytes = extract_xml_bytes(raw, filename)

    parser = etree.XMLParser(recover=True, remove_comments=False)
    try:
        root = etree.fromstring(xml_bytes, parser)
    except Exception as e:
        raise ValueError(f"XML parse error after extraction: {e}")

    root_local = etree.QName(root.tag).localname
    if root_local not in ("Response", "ArtifactResponse"):
        raise ValueError(
            f"Extracted XML is a <{root_local}>, not a SAMLResponse. "
            "Please upload the SAML Response (POST to ACS), not a Request or metadata file."
        )

    # ── Core fields ──────────────────────────────────────────────────────────
    issuer      = get_issuer(root)
    destination = get_destination(root)
    audience    = get_audience(root)

    # ── Collect attrs from PRIMARY SAML ──────────────────────────────────────
    plain_attrs  = get_plain_attributes(root)   # (name, value) — no schema URLs
    schema_attrs = get_schema_attributes(root)  # (segment, full_name, value) — schema URLs only

    # ── Also check SECONDARY SAML (HPE internal Okta at auth.hpe.com) ────────
    secondary_root = _secondary_root_cache.get("root")
    secondary_plain  = get_plain_attributes(secondary_root)  if secondary_root is not None else []
    secondary_schema = get_schema_attributes(secondary_root) if secondary_root is not None else []

    def find_with_fallback(primary_plain, primary_schema, secondary_plain, secondary_schema, *keywords):
        """
        Search order:
          1. Primary plain attrs (no schema URLs)
          2. Primary schema attrs (by last-segment keyword)
          3. Secondary plain attrs
          4. Secondary schema attrs
        Returns (value, display_attr_name, from_secondary, source_type)
        """
        val, attr = find_plain_attr(primary_plain, *keywords)
        if val:
            return val, attr, False, "plain"

        val, attr = find_schema_attr(primary_schema, *keywords)
        if val:
            return val, attr, False, "schema"

        val, attr = find_plain_attr(secondary_plain, *keywords)
        if val:
            return val, attr, True, "plain"

        val, attr = find_schema_attr(secondary_schema, *keywords)
        if val:
            return val, attr, True, "schema"

        return "", "", False, ""

    # ── NameID ───────────────────────────────────────────────────────────────
    # Source: <AttributeStatement> ONLY.
    #
    # Priority (strictly enforced — plain beats schema every time):
    #   Step 1. Plain attrs — keyword search (nameid, email, emailaddress, …)
    #   Step 2. Plain attrs — exact name=="name" match
    #           ("name" cannot go through find_plain_attr because "name" is also
    #            a token inside gl_first_name / gl_last_name and would steal them
    #            via Pass 2 token-match; exact-only is safe and unambiguous)
    #   Step 3. Schema attrs — keyword search (only reached if BOTH plain passes miss)
    #   Steps 4-6. Repeat 1-3 for secondary SAML (HPE internal Okta)
    #   Step 7. Not found → warn.

    def _find_nameid_in(p_plain, p_schema):
        """Return (value, attr_name, source) or ('','','') for one SAML root."""
        kws = ["nameid", "nameidentifier", "email", "emailaddress",
               "uid", "login", "upn", "userprincipalname", "user"]
        # Step A: plain keyword search
        val, attr = find_plain_attr(p_plain, *kws)
        if val:
            return val, attr, "plain"
        # Step B: plain exact "name" — must be BEFORE schema fallback
        for attr_name, value in p_plain:
            if attr_name.lower() == "name" and value:
                return value, attr_name, "plain"
        # Step C: schema keyword search — only if both plain passes missed
        val, attr = find_schema_attr(p_schema, *kws)
        if val:
            return val, attr, "schema"
        return "", "", ""

    _nid_val, _nid_attr, _nid_src = _find_nameid_in(plain_attrs, schema_attrs)
    nameid_from_secondary = False
    if not _nid_val and secondary_root is not None:
        _nid_val, _nid_attr, _nid_src = _find_nameid_in(secondary_plain, secondary_schema)
        nameid_from_secondary = bool(_nid_val)
    nameid, nameid_attr = _nid_val, _nid_attr
    nameid_search_keywords = [
        "nameid",
        "nameidentifier",
        "email",
        "emailaddress",
        "uid",
        "login",
        "upn",
        "userprincipalname",
        "user",
        "name (exact)",
    ]

    # ── First Name ───────────────────────────────────────────────────────────
    # Keywords: first, given, fname — covers FirstName, gl_first_name,
    # givenname (schema URL segment), given_name, fname
    firstname, firstname_attr, fn_from_secondary, fn_source = find_with_fallback(
        plain_attrs, schema_attrs, secondary_plain, secondary_schema,
        "first", "given", "fname", "givenname")

    # ── Last Name ────────────────────────────────────────────────────────────
    # Keywords: last, sur, family, lname — covers LastName, gl_last_name,
    # surname (schema URL segment), family_name, lname
    lastname, lastname_attr, ln_from_secondary, ln_source = find_with_fallback(
        plain_attrs, schema_attrs, secondary_plain, secondary_schema,
        "last", "sur", "family", "lname", "surname")

    # ── hpe_ccs_attribute ────────────────────────────────────────────────────
    hpe_ccs = get_hpe_ccs(root)
    if not hpe_ccs and secondary_root is not None:
        hpe_ccs = get_hpe_ccs(secondary_root)

    all_attrs = get_all_attributes(root)

    # ── Certificates ─────────────────────────────────────────────────────────
    cert_b64_list = get_x509_certs(root)
    cert_results  = []
    cert_subjects = []
    any_masked    = False

    for cb64 in cert_b64_list:
        if _is_masked(cb64):
            any_masked = True
            cert_results.append({"found": True, "masked": True,
                                  "error": "Certificate value is masked in this export.",
                                  "valid": None})
        else:
            cr = validate_cert(cb64)
            cert_results.append(cr)
            if cr.get("subject"):
                cert_subjects.append(cr["subject"])

    primary_cert = cert_results[0] if cert_results else None

    if not cert_results:
        cert_status = "fail"
        cert_note   = "No X.509 certificate found in SAML response."
        cert_fix    = ("Ensure your IdP signs the SAML assertion and includes "
                       "the X.509 certificate in the ds:Signature block.")
    elif any_masked and not any(c.get("valid") for c in cert_results):
        cert_status = "warn"
        cert_note   = ("⚠️ Certificate value is masked in this export. "
                       "Cannot validate expiry automatically. "
                       "Please verify the certificate manually in your IdP.")
        cert_fix    = None
    elif primary_cert.get("masked"):
        cert_status = "warn"
        cert_note   = primary_cert.get("error", "Certificate masked.")
        cert_fix    = None
    elif primary_cert.get("expired"):
        cert_status = "fail"
        cert_note   = f"Certificate EXPIRED on {primary_cert['not_after']}."
        cert_fix    = ("Renew the signing certificate in your IdP and update "
                       "the certificate in your HPE GreenLake SSO configuration.")
    elif not primary_cert.get("valid"):
        cert_status = "fail"
        cert_note   = primary_cert.get("error") or "Certificate is not yet valid."
        cert_fix    = ("Check the certificate dates and ensure your IdP server "
                       "clock is correct.")
    else:
        cert_status = "pass"
        cert_note   = (f"Certificate is valid. Expires {primary_cert['not_after']} "
                       f"({primary_cert['days_remaining']} days remaining).")
        cert_fix    = None
        if primary_cert["days_remaining"] < 30:
            cert_note += " ⚠️ Certificate expiring soon — renew within 30 days."

    # ── IDP ──────────────────────────────────────────────────────────────────
    idp = detect_idp(issuer, destination, " ".join(cert_subjects))

    # ── Time window ───────────────────────────────────────────────────────────
    time_check = check_time_window(root)

    # ── Entity ID / ACS ───────────────────────────────────────────────────────
    entity_pass = (audience == EXPECTED_ENTITY)
    acs_pass    = (destination == EXPECTED_ACS)

    # ── Helper: build "not found" note with context ───────────────────────────
    def not_found_note(field_label, searched_keywords, plain_attrs, schema_attrs):
        """
        Build a helpful note when an attribute is not found.
        Shows what plain and schema attributes were actually present in the SAML.
        """
        plain_names  = [n for n, _ in plain_attrs]
        schema_names = [seg for seg, _, _ in schema_attrs]
        parts = []
        if plain_names:
            parts.append(f"Plain attrs present: {', '.join(plain_names)}")
        if schema_names:
            parts.append(f"Schema attr segments: {', '.join(schema_names)}")
        hint = " | ".join(parts) if parts else "No attributes found in this SAML response"
        return (f"{field_label} attribute not found. "
                f"Searched for: {', '.join(searched_keywords)}. "
                f"{hint}")

    # ── Build checks list ─────────────────────────────────────────────────────
    checks = [
        # 1 — IDP
        {
            "id": "idp", "step": 1, "type": "auto",
            "name": "Identity Provider (IdP)",
            "sub":  "Detect which IdP is sending the assertion",
            "icon": "🏛",
            "status":      "pass" if idp["detected"] else "warn",
            "found":       idp["name"],
            "idp_icon":    idp["icon"],
            "issuer":      issuer or "(not found)",
            "note":        (f"Detected IdP: {idp['name']}" if idp["detected"]
                            else f"IdP not recognised. Issuer: {issuer or '(empty)'}"),
            "fix":         None,
            "allow_manual": False,
        },
        # 2 — Entity ID
        {
            "id": "entity_id", "step": 2, "type": "auto",
            "name": "Identifier (Entity ID)",
            "sub":  "Audience restriction must match SP Entity ID",
            "icon": "🎯",
            "status":   "pass" if entity_pass else "fail",
            "found":    audience or "(not found)",
            "expected": EXPECTED_ENTITY,
            "note":     ("Audience restriction matches HPE GreenLake Entity ID."
                         if entity_pass
                         else f"Entity ID mismatch. Found: '{audience or '(empty)'}'. "
                              f"Expected: '{EXPECTED_ENTITY}'"),
            "fix":      (None if entity_pass
                         else f"In your IdP SAML app, set the Entity ID / Audience URI to:\n"
                              f"{EXPECTED_ENTITY}"),
            "allow_manual": True,
        },
        # 3 — ACS URL
        {
            "id": "acs_url", "step": 3, "type": "auto",
            "name": "Reply URL (ACS URL)",
            "sub":  "Assertion Consumer Service endpoint",
            "icon": "🔗",
            "status":   "pass" if acs_pass else "fail",
            "found":    destination or "(not found)",
            "expected": EXPECTED_ACS,
            "note":     ("ACS URL matches HPE GreenLake endpoint."
                         if acs_pass
                         else f"ACS URL mismatch. Found: '{destination or '(empty)'}'. "
                              f"Expected: '{EXPECTED_ACS}'"),
            "fix":      (None if acs_pass
                         else f"In your IdP SAML app, set the ACS / Reply URL to:\n"
                              f"{EXPECTED_ACS}"),
            "allow_manual": True,
        },
        # 4 — Time window
        {
            "id": "time_window", "step": 4, "type": "auto",
            "name": "Assertion Time Window",
            "sub":  "NotBefore → NotOnOrAfter must be ≤ 74 min",
            "icon": "⏱",
            "status":          "pass" if time_check["pass"] else "fail",
            "not_before":      time_check.get("not_before"),
            "not_on_or_after": time_check.get("not_on_or_after"),
            "diff_minutes":    time_check.get("diff_minutes"),
            "diff_seconds":    time_check.get("diff_seconds"),
            "note":            (f"Time window is {time_check.get('diff_minutes')} min "
                                f"— within the 74-minute limit."
                                if time_check["pass"]
                                else time_check.get("error", "Validation failed.")),
            "fix":             (None if time_check["pass"]
                                else "Reduce the Assertion Validity / Session Lifetime in your "
                                     "IdP to 74 minutes (4440 seconds) or less."),
            "allow_manual": True,
        },
        # 5 — Certificate
        {
            "id": "certificate", "step": 5, "type": "auto",
            "name": "X.509 Certificate",
            "sub":  "Signing certificate validity & expiry",
            "icon": "🔐",
            "status":       cert_status,
            "certs":        cert_results,
            "cert_count":   len(cert_results),
            "note":         cert_note,
            "fix":          cert_fix,
            "allow_manual": True,
        },
        # 6 — NameID
        {
            "id": "nameid", "step": 6, "type": "manual",
            "name": "NameID",
            "sub":  "User identifier — verify vs GreenLake profile",
            "icon": "👤",
            "status": "manual",
            "attr_name":   nameid_attr or "(not found)",
            "attr_label":  nameid_attr or "(not found)",
            "attr_source": "secondary" if nameid_from_secondary else "primary",
            "found":  nameid or "(not found)",
            "not_found_reason": (
                not_found_note("NameID", nameid_search_keywords, plain_attrs, schema_attrs)
                if not nameid else None
            ),
            "instruction": f"Confirm this <strong>{nameid_attr or 'NameID'}</strong> attribute matches with <strong>HPE GreenLake SSO connection</strong> attribute.",
            "allow_manual": True,
        },
        # 7 — FirstName
        {
            "id": "firstname", "step": 7, "type": "manual",
            "name": "First Name",
            "sub":  "Verify attribute vs GreenLake profile",
            "icon": "🪪",
            "status": "manual",
            "attr_name":   firstname_attr or "(not found)",
            "attr_label":  firstname_attr or "(not found)",
            "attr_source": "secondary" if fn_from_secondary else "primary",
            "attr_type":   fn_source,
            "found":  firstname or "(not found)",
            "not_found_reason": (
                not_found_note("First Name", ["first","given","fname","givenname"],
                               plain_attrs, schema_attrs)
                if not firstname else None
            ),
            "instruction": f"Confirm this <strong>{firstname_attr or 'First Name'}</strong> attribute matches with <strong>HPE GreenLake SSO connection</strong> attribute.",
            "allow_manual": True,
        },
        # 8 — LastName
        {
            "id": "lastname", "step": 8, "type": "manual",
            "name": "Last Name",
            "sub":  "Verify attribute vs GreenLake profile",
            "icon": "🪪",
            "status": "manual",
            "attr_name":   lastname_attr or "(not found)",
            "attr_label":  lastname_attr or "(not found)",
            "attr_source": "secondary" if ln_from_secondary else "primary",
            "attr_type":   ln_source,
            "found":  lastname or "(not found)",
            "not_found_reason": (
                not_found_note("Last Name", ["last","sur","family","lname","surname"],
                               plain_attrs, schema_attrs)
                if not lastname else None
            ),
            "instruction": f"Confirm this <strong>{lastname_attr or 'Last Name'}</strong> attribute matches with <strong>HPE GreenLake SSO connection</strong> attribute.",
            "allow_manual": True,
        },
        # 9 — hpe_ccs
        {
            "id": "hpe_ccs", "step": 9, "type": "skipped",
            "name": "hpe_ccs_attribute",
            "sub":  "Role & scope mapping — coming soon",
            "icon": "🔧",
            "status": "skipped",
            "found":  str(hpe_ccs) if hpe_ccs else "(not found)",
            "not_found_reason": (
                not_found_note("hpe_ccs_attribute", ["hpe_ccs","hpeccs","ccs"],
                               plain_attrs, schema_attrs)
                if not hpe_ccs else None
            ),
            "note":   "Full hpe_ccs_attribute validation will be implemented in a future release.",
            "allow_manual": False,
        },
    ]

    return {
        "success": True,
        "checks":  checks,
        "all_attributes": all_attrs,
        "raw": {
            "issuer": issuer, "destination": destination,
            "audience": audience, "nameid": nameid,
        }
    }


# ── Route registration ────────────────────────────────────────────────────────
def register_routes(flask_app):
    """Mount /api/health and /api/parse on an existing Flask app instance."""

    @flask_app.route("/api/health")
    def health():
        return jsonify({"status": "ok", "service": "HPE SAML Checker"})

    @flask_app.route("/api/parse", methods=["POST"])
    def api_parse():
        if "file" not in request.files:
            return jsonify({"success": False, "error": "No file uploaded."}), 400
        f = request.files["file"]
        raw = f.read()
        if not raw:
            return jsonify({"success": False, "error": "Uploaded file is empty."}), 400
        try:
            result = parse_and_validate(raw, f.filename or "upload")
            return jsonify(result)
        except ValueError as e:
            return jsonify({"success": False, "error": str(e)}), 422
        except Exception as e:
            return jsonify({
                "success": False,
                "error": f"Unexpected error: {e}",
                "trace": traceback.format_exc()
            }), 500