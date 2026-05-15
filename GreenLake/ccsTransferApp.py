"""
CCS-style device transfer: Aquila support-assistant devices-to-customer,
with GreenLake public API fallback (PATCH devices) when base_url is global.api.

Auth model mirrors deviceApp.py: the frontend posts a `parsed_headers` JSON
object (Authorization + Cookie + Accept) which is merged into every outbound
request — no separate bearer_token / cookie fields.
"""
from flask import Blueprint, request, jsonify
import csv
import io
import time
import json
import requests
from typing import List, Optional

ccs_bp = Blueprint("ccs", __name__)

API_BASE = "https://global.api.greenlake.hpe.com"


def is_aquila_url(base_url: str) -> bool:
    return "aquila-user-api" in (base_url or "")


def _extract_csrf(cookie: str) -> str:
    for part in (cookie or "").split(";"):
        part = part.strip()
        if part.lower().startswith("ccs-csrftoken="):
            return part.split("=", 1)[1].strip()
    return ""


def build_request_headers(
    extra_headers: dict,
    content_type: str = "application/json",
    base_url: str = "",
) -> dict:
    """
    Build outbound request headers by layering the user-supplied headers
    (Authorization / Cookie / Accept) on top of our defaults.
    For Aquila endpoints we also inject Origin/Referer and X-CSRF-Token
    extracted from the cookie, matching the CCS-Manager browser session.
    """
    headers = {
        "Content-Type": content_type,
        "Accept": "application/json",
    }
    extra = extra_headers or {}
    for k, v in extra.items():
        if v is None or v == "":
            continue
        headers[k] = v

    cookie = headers.get("Cookie", "") or headers.get("cookie", "")
    if base_url and is_aquila_url(base_url):
        headers.setdefault("Origin", "https://common.cloud.hpe.com")
        headers.setdefault("Referer", "https://common.cloud.hpe.com/")
        csrf = _extract_csrf(cookie)
        if csrf:
            headers.setdefault("X-CSRF-Token", csrf)
    return headers


def parse_csv_column(file_content: bytes, columns: List[str]) -> List[str]:
    try:
        text_stream = io.TextIOWrapper(io.BytesIO(file_content), encoding="utf-8-sig")
        reader = csv.DictReader(text_stream)
        values = []
        for row in reader:
            for col in columns:
                if col in row and row[col] and str(row[col]).strip():
                    values.append(str(row[col]).strip())
                    break
        return values
    except Exception:
        return []


def get_device_id_by_serial(
    extra_headers: dict, serial: str, base_url: str
) -> Optional[str]:
    headers = build_request_headers(extra_headers, base_url=base_url)

    if is_aquila_url(base_url):
        url = f"{base_url}/ui-doorway/ui/v1/devices"
        params = {"serial_number": serial, "limit": 100}
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("devices", data.get("items", []))
                if isinstance(items, list):
                    for item in items:
                        sn = item.get("serial_number") or item.get("serialNumber", "")
                        if str(sn).upper() == serial.upper():
                            return (
                                item.get("resource_id")
                                or item.get("id")
                                or item.get("device_id")
                            )
                    if len(items) == 1:
                        item = items[0]
                        return (
                            item.get("resource_id")
                            or item.get("id")
                            or item.get("device_id")
                        )
        except Exception:
            pass
        return None

    url = f"{base_url}/devices/v1beta1/devices"
    params = {"filter": f"serialNumber eq '{serial}'", "limit": 5}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("items", [])
            if items:
                return items[0].get("id")
    except Exception:
        pass
    return None


def _transfer_batch_with_retry(
    headers: dict,
    endpoint: str,
    base_payload: dict,
    batch: List[str],
    results: dict,
    level: int = 1,
):
    if not batch:
        return

    payload = base_payload.copy()
    payload["devices"] = [{"serial_number": s} for s in batch]

    try:
        resp = requests.post(endpoint, headers=headers, json=payload, timeout=45)

        if resp.status_code in [200, 201]:
            results["successful"] += len(batch)
            for serial in batch:
                results["details"].append(
                    {"serial": serial, "success": True, "status": "Transferred"}
                )

        elif resp.status_code == 202:
            results["successful"] += len(batch)
            for serial in batch:
                results["details"].append(
                    {"serial": serial, "success": True, "status": "Accepted (async)"}
                )

        elif resp.status_code == 409:
            if len(batch) > 1:
                mid = len(batch) // 2
                _transfer_batch_with_retry(
                    headers, endpoint, base_payload, batch[:mid], results, level + 1
                )
                _transfer_batch_with_retry(
                    headers, endpoint, base_payload, batch[mid:], results, level + 1
                )
            else:
                error_msg = (
                    "Conflict: Device cannot be transferred "
                    "(e.g., active subscription or locked)."
                )
                try:
                    err = resp.json()
                    error_msg = err.get(
                        "message", err.get("detail", err.get("error", error_msg))
                    )
                except Exception:
                    pass
                results["failed"] += 1
                results["details"].append(
                    {"serial": batch[0], "success": False, "error": error_msg}
                )

        elif resp.status_code in (401, 403):
            try:
                err = resp.json()
                msg = err.get(
                    "message", err.get("detail", err.get("error", "Unauthorized"))
                )
            except Exception:
                msg = "Unauthorized — check Authorization / Cookie headers."
            results["failed"] += len(batch)
            for serial in batch:
                results["details"].append(
                    {"serial": serial, "success": False, "error": msg}
                )

        else:
            error_msg = f"HTTP {resp.status_code}"
            try:
                err = resp.json()
                error_msg = err.get(
                    "message", err.get("detail", err.get("error", str(err)))
                )
            except Exception:
                error_msg = (resp.text or "")[:300] or error_msg
            results["failed"] += len(batch)
            for serial in batch:
                results["details"].append(
                    {"serial": serial, "success": False, "error": error_msg}
                )

    except Exception as e:
        results["failed"] += len(batch)
        for serial in batch:
            results["details"].append(
                {"serial": serial, "success": False, "error": str(e)}
            )


def _parse_keys_from_request() -> List[str]:
    """Read subscription keys from either an uploaded CSV (`file`) or a form field (`keys`)."""
    keys: List[str] = []
    upload = request.files.get("file")
    if upload and upload.filename:
        content = upload.read()
        keys = parse_csv_column(
            content,
            ["Subscription Key", "SubscriptionKey", "Key", "key", "subscription_key"],
        )
        if not keys:
            try:
                text = content.decode("utf-8-sig", errors="ignore")
            except Exception:
                text = ""
            for tok in text.replace("\r", "\n").replace(",", "\n").split("\n"):
                t = tok.strip()
                if t:
                    keys.append(t)
    else:
        raw = request.form.get("keys", "") or ""
        seen = set()
        for tok in raw.replace("\r", "\n").replace(",", "\n").split("\n"):
            t = tok.strip()
            if t and t.lower() not in seen:
                seen.add(t.lower())
                keys.append(t)
    return keys


def _parse_serials_from_request() -> List[str]:
    """Read serials from either an uploaded CSV (`file`) or a form field (`serials`)."""
    serials: List[str] = []
    upload = request.files.get("file")
    if upload and upload.filename:
        content = upload.read()
        serials = parse_csv_column(
            content,
            [
                "Serial Number", "SerialNumber", "Serial", "SN", "serial", "SERIAL",
            ],
        )
        if not serials:
            try:
                text = content.decode("utf-8-sig", errors="ignore")
            except Exception:
                text = ""
            for tok in text.replace("\r", "\n").replace(",", "\n").split("\n"):
                t = tok.strip()
                if t:
                    serials.append(t)
    else:
        raw = request.form.get("serials", "") or ""
        seen = set()
        for tok in raw.replace("\r", "\n").replace(",", "\n").split("\n"):
            t = tok.strip()
            if t and t.lower() not in seen:
                seen.add(t.lower())
                serials.append(t)
    return serials


def _lookup_activate_devices(
    parsed_headers: dict, base_url: str, serials: List[str]
) -> dict:
    """
    Call GET /support-assistant/v1alpha1/activate-devices for the provided serials,
    batched with comma-joined serial_number param. Returns flattened device rows
    plus a list of serials that did not match anything.
    """
    if not is_aquila_url(base_url):
        base_url = "https://aquila-user-api.common.cloud.hpe.com"

    endpoint = f"{base_url}/support-assistant/v1alpha1/activate-devices"
    headers = build_request_headers(parsed_headers, base_url=base_url)

    BATCH_SIZE = 250
    found_devices: List[dict] = []
    matched_serials = set()

    for i in range(0, len(serials), BATCH_SIZE):
        batch = serials[i : i + BATCH_SIZE]
        joined = ",".join(batch)
        page = 0
        limit = max(len(batch), 10)
        while True:
            params = {"limit": limit, "page": page, "serial_number": joined}
            try:
                resp = requests.get(endpoint, headers=headers, params=params, timeout=30)
            except Exception as e:
                return {"error": f"Lookup request failed: {e}"}

            if resp.status_code in (401, 403):
                try:
                    err = resp.json()
                    msg = err.get(
                        "message",
                        err.get("detail", err.get("error", "Unauthorized")),
                    )
                except Exception:
                    msg = "Unauthorized — check Authorization / Cookie headers."
                return {"error": msg, "status": resp.status_code}

            if resp.status_code != 200:
                try:
                    err = resp.json()
                    msg = err.get(
                        "message",
                        err.get("detail", err.get("error", f"HTTP {resp.status_code}")),
                    )
                except Exception:
                    msg = (resp.text or "")[:300] or f"HTTP {resp.status_code}"
                return {"error": msg, "status": resp.status_code}

            try:
                data = resp.json()
            except Exception:
                data = {}
            devs = data.get("devices", []) or []
            pagination = data.get("pagination", {}) or {}

            for d in devs:
                sn = (d.get("serial_number") or "").strip()
                if sn:
                    matched_serials.add(sn.upper())
                folder = d.get("folder") or {}
                found_devices.append(
                    {
                        "serial_number": sn,
                        "mac_address": d.get("mac_address") or "",
                        "part_number": d.get("part_number") or "",
                        "device_type": d.get("device_type") or "",
                        "device_model": d.get("device_model") or "",
                        "entitlement_id": d.get("entitlement_id") or "",
                        "folder_name": folder.get("folder_name", ""),
                        "folder_id": folder.get("folder_id", ""),
                        "platform_customer_id": d.get("platform_customer_id") or "",
                        "activate_customer_id": d.get("activate_customer_id") or "",
                        "archived": bool(d.get("archived")),
                        "gts_status": d.get("gts_status") or "",
                        "subscription_tier": d.get("subscription_tier") or "",
                    }
                )

            fetched = (page + 1) * limit
            total = pagination.get("total_count", len(devs))
            if not devs or fetched >= total or len(devs) < limit:
                break
            page += 1

    missing = [s for s in serials if s.upper() not in matched_serials]
    return {
        "total": len(serials),
        "found": len(found_devices),
        "missing_count": len(missing),
        "devices": found_devices,
        "missing": missing,
    }


@ccs_bp.route("/api/ccs/lookup-devices", methods=["POST"])
def ccs_lookup_devices():
    parsed_headers = _coerce_parsed_headers(request.form.get("parsed_headers"))
    if not parsed_headers.get("Authorization") and not parsed_headers.get("Cookie"):
        return jsonify({"error": "No Authorization or Cookie header provided"}), 401

    base_url = (request.form.get("base_url") or "").strip() or (
        "https://aquila-user-api.common.cloud.hpe.com"
    )

    serials = _parse_serials_from_request()
    if not serials:
        return jsonify({"error": "No serial numbers provided"}), 400

    result = _lookup_activate_devices(parsed_headers, base_url, serials)
    if result.get("error"):
        return jsonify(result), result.get("status") or 500
    return jsonify(result)


def _coerce_parsed_headers(raw) -> dict:
    """Accept either a JSON string or a dict-like form payload."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


@ccs_bp.route("/api/ccs/transfer-devices", methods=["POST"])
def ccs_transfer_devices():
    parsed_headers = _coerce_parsed_headers(request.form.get("parsed_headers"))
    if not parsed_headers.get("Authorization") and not parsed_headers.get("Cookie"):
        return jsonify({"error": "No Authorization or Cookie header provided"}), 401

    source_workspace_id = request.form.get("source_workspace_id", "") or ""
    dest_workspace_id = (request.form.get("dest_workspace_id") or "").strip()
    if not dest_workspace_id:
        return jsonify({"error": "dest_workspace_id required"}), 400

    base_url = (request.form.get("base_url") or API_BASE).strip() or API_BASE
    folder = (request.form.get("folder") or "default").strip() or "default"

    serials: List[str] = []
    upload = request.files.get("file")
    if upload and upload.filename:
        content = upload.read()
        serials = parse_csv_column(
            content,
            [
                "Serial Number",
                "SerialNumber",
                "Serial",
                "SN",
                "serial",
                "SERIAL",
            ],
        )
        if not serials:
            try:
                text = content.decode("utf-8-sig", errors="ignore")
            except Exception:
                text = ""
            for tok in text.replace("\r", "\n").replace(",", "\n").split("\n"):
                t = tok.strip()
                if t:
                    serials.append(t)
    else:
        raw = request.form.get("serials", "") or ""
        seen = set()
        for tok in raw.replace("\r", "\n").replace(",", "\n").split("\n"):
            t = tok.strip()
            if t and t.lower() not in seen:
                seen.add(t.lower())
                serials.append(t)

    if not serials:
        return jsonify({"error": "No serial numbers provided"}), 400

    start_time = time.time()
    results = {
        "total": len(serials),
        "successful": 0,
        "failed": 0,
        "details": [],
        "elapsed_seconds": 0,
    }
    if source_workspace_id:
        results["note"] = (
            "source_workspace_id accepted for UI parity; "
            "Aquila devices-to-customer uses destination only."
        )

    use_aquila = is_aquila_url(base_url)

    if use_aquila:
        headers = build_request_headers(
            parsed_headers, "application/json", base_url
        )
        folder_id = ""
        try:
            folder_url = f"{base_url}/support-assistant/v1alpha1/user-folders"
            f_params = {
                "limit": 50,
                "page": 0,
                "platform_customer_id": dest_workspace_id,
            }
            f_resp = requests.get(
                folder_url, headers=headers, params=f_params, timeout=15
            )
            if f_resp.status_code == 200:
                f_data = f_resp.json()
                items = (
                    f_data
                    if isinstance(f_data, list)
                    else f_data.get(
                        "items",
                        f_data.get("folders", f_data.get("data", [])),
                    )
                )
                for f in items:
                    name = f.get("name", f.get("folder_name", f.get("folderName", "")))
                    if str(name).lower() == folder.lower():
                        folder_id = f.get(
                            "id", f.get("folder_id", f.get("folderId", ""))
                        )
                        break
                if not folder_id and items:
                    folder_id = items[0].get(
                        "id",
                        items[0].get("folder_id", items[0].get("folderId", "")),
                    )
                    folder = items[0].get(
                        "name", items[0].get("folder_name", folder)
                    )
        except Exception:
            pass

        fid = 0
        if folder_id:
            try:
                fid = int(folder_id)
            except ValueError:
                pass

        batch_size = 250
        endpoint = f"{base_url}/support-assistant/v1alpha1/devices-to-customer"

        for i in range(0, len(serials), batch_size):
            batch = serials[i : i + batch_size]
            base_payload = {
                "folder_name": folder,
                "folder_id": fid,
                "platform_customer_id": dest_workspace_id,
            }
            if not fid:
                base_payload.pop("folder_id", None)

            _transfer_batch_with_retry(headers, endpoint, base_payload, batch, results)
            time.sleep(1.0)

    else:
        for serial in serials:
            device_id = get_device_id_by_serial(parsed_headers, serial, base_url)
            if not device_id:
                results["failed"] += 1
                results["details"].append(
                    {"serial": serial, "success": False, "error": "Device not found"}
                )
                continue
            try:
                patch_url = f"{base_url}/devices/v1beta1/devices"
                headers = build_request_headers(
                    parsed_headers, "application/merge-patch+json", base_url
                )
                payload = {"workspace": {"id": dest_workspace_id}}
                resp = requests.patch(
                    patch_url,
                    headers=headers,
                    params={"id": device_id},
                    json=payload,
                    timeout=30,
                )
                if resp.status_code in [200, 202]:
                    results["successful"] += 1
                    results["details"].append(
                        {"serial": serial, "success": True, "status": "Transferred"}
                    )
                else:
                    results["failed"] += 1
                    results["details"].append(
                        {
                            "serial": serial,
                            "success": False,
                            "error": f"HTTP {resp.status_code}: {(resp.text or '')[:200]}",
                        }
                    )
            except Exception as e:
                results["failed"] += 1
                results["details"].append(
                    {"serial": serial, "success": False, "error": str(e)}
                )
            time.sleep(0.3)

    results["elapsed_seconds"] = round(time.time() - start_time, 2)
    return jsonify(results)


# ============================================================
# TRANSFER SUBSCRIPTIONS
# ============================================================

@ccs_bp.route("/api/ccs/transfer-subscriptions", methods=["POST"])
def ccs_transfer_subscriptions():
    """
    Transfer subscriptions (by subscription key) from source → destination workspace.
    Mirrors the CCS-Manager UI "subscription-transfer" action when using an Aquila
    session; falls back to the public GreenLake API
    (GET /subscriptions/v1/subscriptions → POST /{id}/transfer).
    """
    parsed_headers = _coerce_parsed_headers(request.form.get("parsed_headers"))
    if not parsed_headers.get("Authorization") and not parsed_headers.get("Cookie"):
        return jsonify({"error": "No Authorization or Cookie header provided"}), 401

    source_workspace_id = (request.form.get("source_workspace_id") or "").strip()
    dest_workspace_id = (request.form.get("dest_workspace_id") or "").strip()
    if not source_workspace_id:
        return jsonify({"error": "source_workspace_id required"}), 400
    if not dest_workspace_id:
        return jsonify({"error": "dest_workspace_id required"}), 400

    base_url = (request.form.get("base_url") or API_BASE).strip() or API_BASE

    keys = _parse_keys_from_request()
    if not keys:
        return jsonify({"error": "No subscription keys provided"}), 400

    start_time = time.time()
    results = {
        "total": len(keys),
        "successful": 0,
        "failed": 0,
        "details": [],
        "elapsed_seconds": 0,
    }

    use_aquila = is_aquila_url(base_url)

    if use_aquila:
        transfer_url = f"{base_url}/support-assistant/v1alpha1/subscription-transfer"
        headers = build_request_headers(
            parsed_headers, "application/json", base_url
        )

        for key in keys:
            payload = {
                "subscription_key": key,
                "platform_customer_id": source_workspace_id,
                "new_customer_id": dest_workspace_id,
            }
            try:
                resp = requests.post(
                    transfer_url, headers=headers, json=payload, timeout=30
                )
                # Endpoint historically flip-flopped between POST / PUT / PATCH
                if resp.status_code == 405:
                    resp = requests.put(
                        transfer_url, headers=headers, json=payload, timeout=30
                    )
                if resp.status_code == 405:
                    resp = requests.patch(
                        transfer_url, headers=headers, json=payload, timeout=30
                    )

                if resp.status_code in (200, 201, 204):
                    results["successful"] += 1
                    results["details"].append(
                        {"key": key, "success": True, "status": "Transferred"}
                    )
                elif resp.status_code == 202:
                    results["successful"] += 1
                    results["details"].append(
                        {
                            "key": key,
                            "success": True,
                            "status": "Processing — verify manually",
                        }
                    )
                elif resp.status_code in (401, 403):
                    try:
                        err = resp.json()
                        msg = err.get(
                            "message",
                            err.get("detail", err.get("error", "Unauthorized")),
                        )
                    except Exception:
                        msg = "Unauthorized — check Authorization / Cookie headers."
                    results["failed"] += 1
                    results["details"].append(
                        {"key": key, "success": False, "error": msg}
                    )
                else:
                    error_msg = f"HTTP {resp.status_code}"
                    try:
                        err = resp.json()
                        error_msg = err.get(
                            "message",
                            err.get("detail", err.get("error", str(err))),
                        )
                    except Exception:
                        error_msg = (resp.text or "")[:200] or error_msg
                    results["failed"] += 1
                    results["details"].append(
                        {"key": key, "success": False, "error": error_msg}
                    )
            except Exception as e:
                results["failed"] += 1
                results["details"].append(
                    {"key": key, "success": False, "error": str(e)}
                )
            time.sleep(0.3)

    else:
        subs_base_url = f"{base_url}/subscriptions/v1/subscriptions"
        headers = build_request_headers(parsed_headers, base_url=base_url)

        for key in keys:
            sub_id = None
            try:
                resp = requests.get(
                    subs_base_url,
                    headers=headers,
                    params={"filter": f"key eq '{key}'", "limit": 5},
                    timeout=30,
                )
                if resp.status_code == 200:
                    items = resp.json().get("items", [])
                    if items:
                        sub_id = items[0].get("id")
            except Exception:
                pass

            if not sub_id:
                results["failed"] += 1
                results["details"].append(
                    {
                        "key": key,
                        "success": False,
                        "error": "Subscription key not found",
                    }
                )
                continue

            try:
                transfer_url = f"{subs_base_url}/{sub_id}/transfer"
                payload = {"destinationWorkspaceId": dest_workspace_id}
                resp = requests.post(
                    transfer_url, headers=headers, json=payload, timeout=30
                )

                if resp.status_code in (200, 201, 204):
                    results["successful"] += 1
                    results["details"].append(
                        {"key": key, "success": True, "status": "Transferred"}
                    )
                elif resp.status_code == 202:
                    results["successful"] += 1
                    results["details"].append(
                        {
                            "key": key,
                            "success": True,
                            "status": "Processing — verify manually",
                        }
                    )
                else:
                    error_msg = f"HTTP {resp.status_code}"
                    try:
                        err = resp.json()
                        error_msg = err.get(
                            "message",
                            err.get("detail", err.get("error", str(err))),
                        )
                    except Exception:
                        error_msg = (resp.text or "")[:200] or error_msg
                    results["failed"] += 1
                    results["details"].append(
                        {"key": key, "success": False, "error": error_msg}
                    )
            except Exception as e:
                results["failed"] += 1
                results["details"].append(
                    {"key": key, "success": False, "error": str(e)}
                )
            time.sleep(0.3)

    results["elapsed_seconds"] = round(time.time() - start_time, 2)
    return jsonify(results)
