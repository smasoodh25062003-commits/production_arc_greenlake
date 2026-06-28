from flask import Blueprint, request, jsonify, Response
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import urllib3
import json

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─── Config ───────────────────────────────────────────────────────────────────
CUSTOMERS_URL = "https://common.cloud.hpe.com/api/glp/support-assistant/v1alpha1/customers"
DETAIL_URL = "https://common.cloud.hpe.com/api/glp/support-assistant/v1alpha1/customer-detail"
PAGE_SIZE = 50
TIMEOUT = 30
MAX_WORKERS = 15

userbase_bp = Blueprint('userbase', __name__)


def get_status_color(status):
    status = (status or "").upper()
    if status == "ACTIVE":
        return "#2e7d32"
    elif status == "SUSPENDED":
        return "#ef6c00"
    elif status == "BLOCKED":
        return "#c62828"
    return "#616161"


def get_customer_status(customer_id, extra_headers):
    try:
        r = requests.get(
            DETAIL_URL,
            headers=extra_headers,
            params={"platform_customer_id": customer_id},
            timeout=TIMEOUT,
            verify=False,
        )
        if r.status_code == 200:
            data = r.json()
            return data.get("account", {}).get("status", "UNKNOWN")
        if r.status_code in (401, 403):
            raise requests.exceptions.HTTPError(response=r)
        return "UNKNOWN"
    except requests.exceptions.HTTPError:
        raise
    except Exception:
        return "UNKNOWN"


def fetch_status(customer, extra_headers):
    customer_id = customer.get("customer_id", "")
    try:
        status = get_customer_status(customer_id, extra_headers)
    except requests.exceptions.HTTPError as e:
        status_code = getattr(getattr(e, "response", None), "status_code", None)
        if status_code in (401, 403):
            return ("auth_error", status_code, None, None)
        status = "UNKNOWN"
    return ("ok", customer_id, status, None)


def build_hierarchy(all_customers, status_map):
    standalone = []
    msp_dict = {}
    tenant_map = {}

    for c in all_customers:
        name = c.get("contact", {}).get("company_name", "")
        customer_id = c.get("customer_id", "")
        status = status_map.get(customer_id, "UNKNOWN")
        color = get_status_color(status)
        msp_parent_id = c.get("msp_id", "") or c.get("parent_customer_id", "")
        acc_type = c.get("account_type", "")

        if not acc_type:
            continue

        acc_type_lower = acc_type.lower()

        if "standalone" in acc_type_lower:
            standalone.append({"name": name, "customer_id": customer_id, "status": status, "color": color})
        elif "msp" in acc_type_lower:
            msp_dict[customer_id] = {"name": name, "customer_id": customer_id, "status": status, "color": color}
        elif "tenant" in acc_type_lower and msp_parent_id:
            tenant_map.setdefault(msp_parent_id, []).append({
                "name": name, "customer_id": customer_id, "status": status, "color": color
            })

    sheets_rows = []
    for sa in standalone:
        sheets_rows.append({"type": "Standalone", "name": sa["name"], "id": sa["customer_id"], "status": sa["status"], "color": sa["color"], "parent_id": ""})
    for msp_id, msp_info in msp_dict.items():
        sheets_rows.append({"type": "MSP", "name": msp_info["name"], "id": msp_id, "status": msp_info["status"], "color": msp_info["color"], "parent_id": ""})
        for t in tenant_map.get(msp_id, []):
            sheets_rows.append({"type": "Tenant", "name": t["name"], "id": t["customer_id"], "status": t["status"], "color": t["color"], "parent_id": msp_id})

    return {
        "standalone": standalone,
        "msp_dict": msp_dict,
        "tenant_map": tenant_map,
        "sheets_rows": sheets_rows,
    }


# ─── Routes ───────────────────────────────────────────────────────────────────
@userbase_bp.route("/api/workspace-stream", methods=["POST"])
def workspace_stream():
    body = request.get_json(force=True)
    parsed_headers = body.get("parsed_headers", {})
    username = (body.get("username") or "").strip()

    if not username:
        return jsonify({"error": "Username is required."}), 400

    extra_headers = dict(parsed_headers) if parsed_headers else {}
    extra_headers.setdefault("Accept", "application/json")
    extra_headers.setdefault("Content-Type", "application/json")

    def generate():
        all_customers = []
        offset = 0
        total_count = 0

        # Phase 1: Fetch customers (pagination)
        while True:
            params = {"limit": PAGE_SIZE, "offset": offset, "username": username}
            try:
                response = requests.get(
                    CUSTOMERS_URL,
                    headers=extra_headers,
                    params=params,
                    timeout=TIMEOUT,
                    verify=False,
                )
            except requests.exceptions.RequestException as e:
                yield f"data: {json.dumps({'type':'error','error':str(e)})}\n\n"
                return

            if response.status_code in (401, 403):
                yield f"data: {json.dumps({'type':'auth_error','status':response.status_code,'message':'Authentication failed — your Authorization/Cookie headers are expired or invalid. Please update and retry.'})}\n\n"
                return

            if response.status_code != 200:
                yield f"data: {json.dumps({'type':'error','error':f'API returned {response.status_code}'})}\n\n"
                return

            data = response.json()
            customers = data.get("customers", [])
            pagination = data.get("pagination", {})

            if not customers:
                break

            all_customers.extend(customers)
            total_count = pagination.get("total_count", 0)
            count_per_page = pagination.get("count_per_page", PAGE_SIZE)
            offset += count_per_page

            pct = min(30, round((offset / max(total_count, 1)) * 30))
            yield f"data: {json.dumps({'type':'progress','phase':'fetch','pct':pct,'fetched':len(all_customers),'total':total_count})}\n\n"

            if offset >= total_count:
                break

        total_workspaces = len(all_customers)
        if total_workspaces == 0:
            hierarchy = build_hierarchy([], {})
            yield "data: " + json.dumps({"type": "progress", "phase": "status", "pct": 100, "processed": 0, "total": 0}) + "\n\n"
            payload = {"type": "done", "data": {"hierarchy": hierarchy, "total": 0, "standalone_count": 0, "msp_count": 0, "tenant_count": 0}}
            yield "data: " + json.dumps(payload) + "\n\n"
            return

        # Phase 2: Fetch status in parallel
        status_map = {}
        processed = 0

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(fetch_status, c, extra_headers) for c in all_customers]

            for future in as_completed(futures):
                result = future.result()
                if result[0] == "auth_error":
                    yield f"data: {json.dumps({'type':'auth_error','status':result[1],'message':'Authentication failed — your Authorization/Cookie headers are expired or invalid. Please update and retry.'})}\n\n"
                    return

                _, customer_id, status, _ = result
                status_map[customer_id] = status
                processed += 1
                pct = 30 + round((processed / total_workspaces) * 70)
                yield f"data: {json.dumps({'type':'progress','phase':'status','pct':pct,'processed':processed,'total':total_workspaces})}\n\n"

        hierarchy = build_hierarchy(all_customers, status_map)
        sa_count = len(hierarchy["standalone"])
        msp_count = len(hierarchy["msp_dict"])
        tenant_count = sum(len(v) for v in hierarchy["tenant_map"].values())

        yield "data: " + json.dumps({"type": "progress", "phase": "status", "pct": 100, "processed": total_workspaces, "total": total_workspaces}) + "\n\n"
        payload = {"type": "done", "data": {"hierarchy": hierarchy, "total": total_workspaces, "standalone_count": sa_count, "msp_count": msp_count, "tenant_count": tenant_count}}
        yield "data: " + json.dumps(payload) + "\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
