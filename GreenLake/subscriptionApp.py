from flask import Blueprint, request, jsonify, Response
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import requests
import json
import re

PARALLEL_WORKERS = 20  # parallel key lookups in subscription key mode

# ─── Config ───────────────────────────────────────────────────────────────────
CUSTOMERS_API = "https://aquila-user-api.common.cloud.hpe.com/support-assistant/v1alpha1/customers"
SUB_URL_KEY = (
    "https://aquila-user-api.common.cloud.hpe.com"
    "/support-assistant/v1alpha1/subscriptions"
    "?limit=500&offset=0&subscription_key_pattern={}"
)
SUB_URL_WORKSPACE = (
    "https://aquila-user-api.common.cloud.hpe.com"
    "/support-assistant/v1alpha1/subscriptions"
    "?limit=500&offset={offset}&platform_customer_id={customer_id}"
)
TIMEOUT = 10

subscription_bp = Blueprint('subscription', __name__)


# ─── Workspace name resolution ────────────────────────────────────────────────
def get_workspace_name(workspace_id, extra_headers):
    """Fetch the company name for a single workspace ID."""
    try:
        resp = requests.get(
            CUSTOMERS_API,
            headers=extra_headers,
            params={"limit": 10, "offset": 0, "search_string": workspace_id},
            timeout=TIMEOUT
        )
        resp.raise_for_status()
        customers = resp.json().get("customers", [])
        for cust in customers:
            if cust.get("customer_id") == workspace_id:
                name = cust.get("contact", {}).get("company_name", "")
                if name:
                    return name
        if customers:
            return customers[0].get("contact", {}).get("company_name", "") or ""
    except Exception:
        pass
    return ""


def resolve_workspace_names(workspace_ids, extra_headers):
    """Resolve a list of unique workspace IDs to names in parallel."""
    unique_ids = list(set(i for i in workspace_ids if i))
    name_map   = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(get_workspace_name, wid, extra_headers): wid for wid in unique_ids}
        for future, wid in futures.items():
            name_map[wid] = future.result()
    return name_map


def enrich_workspace_names(records, extra_headers):
    """Add Workspace Name to each record dict in-place."""
    ids      = [r.get("Workspace ID") or "" for r in records]
    name_map = resolve_workspace_names(ids, extra_headers)
    for r in records:
        wid = r.get("Workspace ID") or ""
        r["Workspace Name"] = name_map.get(wid, "")


# ─── Helpers ──────────────────────────────────────────────────────────────────
def parse_sub(sub, key_input=None):
    """Parse a subscription dict into a result record."""
    appointments = sub.get("appointments", {})
    start_epoch  = appointments.get("subscription_start")
    end_epoch    = appointments.get("subscription_end")

    start_str = datetime.utcfromtimestamp(start_epoch / 1000).strftime("%Y-%m-%d") if start_epoch else ""
    end_str   = datetime.utcfromtimestamp(end_epoch   / 1000).strftime("%Y-%m-%d") if end_epoch   else ""

    eval_type = sub.get("evaluation_type", "")
    if eval_type == "NONE":
        eval_type = "PAID"

    is_valid = (end_epoch and datetime.utcnow().date() <= datetime.utcfromtimestamp(end_epoch / 1000).date())
    status   = "VALID" if is_valid else "EXPIRED"
    sub_key  = sub.get("subscription_key")
    quote    = sub.get("quote")

    return {
        "sub_key": sub_key,
        "quote":   quote,
        "record": {
            "Subscription Key": sub_key,
            "Key Description":  sub.get("product_description", ""),
            "Type":             eval_type,
            "Quantity":         sub.get("quantity", ""),
            "Open Seats":       sub.get("available_quantity") if sub.get("available_quantity") is not None else "—",
            "Start Date":       start_str,
            "End Date":         end_str,
            "Valid/Expired":    status,
            "Order ID":         quote,
            "Product SKU":      sub.get("product_sku", ""),
            "EndUser Name":     sub.get("end_user_name", ""),
            "Workspace ID":     sub.get("platform_customer_id", ""),
            "Workspace Name":   "",
        }
    }


# ─── Routes ───────────────────────────────────────────────────────────────────
@subscription_bp.route("/api/subscription-stream", methods=["POST"])
def subscription_stream():
    body          = request.get_json(force=True)
    raw_keys      = body.get("keys", "")
    extra_headers = body.get("parsed_headers", {}) or {}
    lookup_type   = body.get("lookup_type", "subkey")

    # ── Workspace mode: paginate all subscriptions for each workspace ID ───────
    if lookup_type == "workspace":
        workspace_ids = [w.strip() for w in re.split(r"[,\n]+", raw_keys) if w.strip()]
        if not workspace_ids:
            return jsonify({"error": "No workspace ID provided."}), 400

        def generate_workspace():
            results, seen = [], set()

            for ws_idx, workspace_id in enumerate(workspace_ids):
                offset   = 0
                ws_label = f"Workspace {ws_idx + 1}/{len(workspace_ids)}"
                print(f"Starting {ws_label}: {workspace_id}")

                while True:
                    url = SUB_URL_WORKSPACE.format(offset=offset, customer_id=workspace_id)
                    yield f"data: {json.dumps({'type':'progress','pct':-1,'queried':len(results),'total':-1,'message':f'{ws_label} — offset {offset}'})}\n\n"

                    try:
                        response = requests.get(url, headers=extra_headers, timeout=TIMEOUT)
                        response.raise_for_status()
                        subscriptions = response.json().get("subscriptions", [])

                        if not subscriptions:
                            break

                        for sub in subscriptions:
                            parsed  = parse_sub(sub)
                            sub_key = parsed["sub_key"]
                            if sub_key and sub_key not in seen:
                                seen.add(sub_key)
                                results.append(parsed["record"])

                        print(f"{ws_label} offset {offset}: {len(subscriptions)} keys.")
                        offset += len(subscriptions)

                    except requests.exceptions.RequestException as e:
                        status_code = getattr(getattr(e, 'response', None), 'status_code', None)
                        if status_code in (401, 403):
                            yield f"data: {json.dumps({'type':'auth_error','status':status_code,'message':'Authentication failed — your Authorization/Cookie headers are expired or invalid. Please update and retry.'})}\n\n"
                            return
                        print(f"{ws_label} offset {offset} error: {e}")
                        break

            results.sort(key=lambda r: (r.get("Workspace ID") or "").lower())
            enrich_workspace_names(results, extra_headers)

            valid_count   = sum(1 for r in results if r["Valid/Expired"] == "VALID")
            expired_count = sum(1 for r in results if r["Valid/Expired"] == "EXPIRED")
            total         = len(results)

            result = {
                "total":         total,
                "valid":         valid_count,
                "expired":       expired_count,
                "missing_count": 0,
                "valid_pct":     round(valid_count   / total * 100, 1) if total else 0,
                "expired_pct":   round(expired_count / total * 100, 1) if total else 0,
                "missing_pct":   0,
                "subscriptions": results,
                "missing":       [],
            }
            yield f"data: {json.dumps({'type':'progress','pct':100,'queried':total,'total':total})}\n\n"
            yield f"data: {json.dumps({'type':'done','data':result})}\n\n"

        return Response(generate_workspace(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ── Subscription key mode: lookup each key individually ───────────────────
    keys = list(set([k.strip() for k in re.split(r"[,\n]+", raw_keys) if k.strip()]))
    if not keys:
        return jsonify({"error": "No subscription keys provided."}), 400

    def fetch_key(key):
        """Fetch a single subscription key. Returns (key, records, missing, auth_err)."""
        try:
            response = requests.get(
                SUB_URL_KEY.format(key),
                headers=extra_headers,
                timeout=TIMEOUT
            )
            response.raise_for_status()
            subscriptions = response.json().get("subscriptions", [])
            if not subscriptions:
                return key, [], [key], None
            records = []
            for sub in subscriptions:
                parsed = parse_sub(sub, key)
                records.append(parsed["record"])
            return key, records, [], None
        except requests.exceptions.RequestException as e:
            status_code = getattr(getattr(e, 'response', None), 'status_code', None)
            if status_code in (401, 403):
                return key, [], [], status_code
            return key, [], [key], None

    def generate():
        total_keys   = len(keys)
        results      = []
        missing_keys = []
        queried      = 0

        for chunk_start in range(0, total_keys, PARALLEL_WORKERS):
            chunk = keys[chunk_start: chunk_start + PARALLEL_WORKERS]

            with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as executor:
                future_to_idx = {executor.submit(fetch_key, key): i for i, key in enumerate(chunk)}
                chunk_results = [None] * len(chunk)
                for future in future_to_idx:
                    chunk_results[future_to_idx[future]] = future.result()

            for key, records, missing, auth_err in chunk_results:
                if auth_err in (401, 403):
                    yield f"data: {json.dumps({'type':'auth_error','status':auth_err,'message':'Authentication failed — your Authorization/Cookie headers are expired or invalid. Please update and retry.'})}\n\n"
                    return
                results.extend(records)
                missing_keys.extend(missing)

            queried += len(chunk)
            pct = round(queried / total_keys * 100)
            yield f"data: {json.dumps({'type':'progress','pct':pct,'queried':queried,'total':total_keys})}\n\n"

        seen, deduped = set(), []
        for r in results:
            k = r["Subscription Key"]
            if k not in seen:
                seen.add(k)
                deduped.append(r)

        missing_keys = list(set(missing_keys))
        deduped.sort(key=lambda r: (r.get("Workspace ID") or "").lower())
        enrich_workspace_names(deduped, extra_headers)

        valid_count   = sum(1 for r in deduped if r["Valid/Expired"] == "VALID")
        expired_count = sum(1 for r in deduped if r["Valid/Expired"] == "EXPIRED")
        missing_count = len(missing_keys)
        total         = valid_count + expired_count + missing_count

        result = {
            "total":         total,
            "valid":         valid_count,
            "expired":       expired_count,
            "missing_count": missing_count,
            "valid_pct":     round(valid_count   / total * 100, 1) if total else 0,
            "expired_pct":   round(expired_count / total * 100, 1) if total else 0,
            "missing_pct":   round(missing_count / total * 100, 1) if total else 0,
            "subscriptions": deduped,
            "missing":       missing_keys,
        }
        yield f"data: {json.dumps({'type':'progress','pct':100,'queried':total_keys,'total':total_keys})}\n\n"
        yield f"data: {json.dumps({'type':'done','data':result})}\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
