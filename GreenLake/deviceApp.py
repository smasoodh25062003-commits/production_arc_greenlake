from flask import Blueprint, request, jsonify, Response
import requests
import pandas as pd
import io
import json
from concurrent.futures import ThreadPoolExecutor

# ─── Config ───────────────────────────────────────────────────────────────────
BASE_URL_SERIAL    = "https://aquila-user-api.common.cloud.hpe.com/support-assistant/v1alpha1/activate-devices?limit={limit}&page={page}&serial_number={devices}"
BASE_URL_MAC       = "https://aquila-user-api.common.cloud.hpe.com/support-assistant/v1alpha1/activate-devices?limit={limit}&page={page}&mac_address={devices}"
BASE_URL_WORKSPACE = "https://aquila-user-api.common.cloud.hpe.com/support-assistant/v1alpha1/activate-devices?limit={limit}&page={page}&platform_customer_id={customer_id}"
CUSTOMERS_API      = "https://aquila-user-api.common.cloud.hpe.com/support-assistant/v1alpha1/customers"
BATCH_SIZE    = 250
WS_PAGE_LIMIT = 500
SLEEP_SEC     = 0
TIMEOUT       = 15

device_bp = Blueprint('device', __name__)


# ─── Workspace name resolution ────────────────────────────────────────────────
def get_workspace_name(workspace_id, extra_headers):
    """Fetch the company name for a single workspace ID from the customers API."""
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
    """Resolve a list of unique workspace IDs to names in parallel. Returns dict {id: name}."""
    unique_ids = list(set(workspace_ids))
    name_map   = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(get_workspace_name, wid, extra_headers): wid for wid in unique_ids}
        for future, wid in futures.items():
            name_map[wid] = future.result()
    return name_map


# ─── Helpers ──────────────────────────────────────────────────────────────────
def sort_priority(row):
    folder     = row["Folder Name"]
    is_default = folder == "default"
    is_aruba   = "Aruba Factory" in folder
    if is_default and is_aruba:
        return 0
    elif is_default:
        return 1
    return 2


def parse_device(dev):
    serial_number  = dev.get("serial_number")
    mac_address    = dev.get("mac_address")
    platform_id    = dev.get("platform_customer_id")
    folder_name    = dev.get("folder", {}).get("folder_name", "")
    return {
        "serial_number": serial_number,
        "mac_address":   mac_address,
        "platform_id":   platform_id,
        "folder_name":   folder_name,
        "record": {
            "Serial Number":  serial_number,
            "MAC Address":    mac_address                or "—",
            "Entitlement ID": dev.get("entitlement_id") or "—",
            "Device Type":    dev.get("device_type")    or "—",
            "Device Model":   dev.get("device_model")   or "—",
            "Part Number":    dev.get("part_number")    or "—",
            "Folder Name":    folder_name,
            "Workspace ID":   platform_id,
            "Workspace Name": "",
        }
    }


def sort_df(df):
    if df.empty:
        return df
    df["sort_order"] = df.apply(sort_priority, axis=1)
    df = df.sort_values(by=["sort_order", "Workspace ID"])
    df.drop(columns="sort_order", inplace=True)
    return df


def enrich_workspace_names(records, extra_headers):
    """Resolve workspace names for a list of record dicts in-place."""
    ids     = [r.get("Workspace ID") or "" for r in records]
    unique  = [i for i in set(ids) if i]
    if not unique:
        return
    name_map = resolve_workspace_names(unique, extra_headers)
    for r in records:
        wid = r.get("Workspace ID") or ""
        r["Workspace Name"] = name_map.get(wid, "")


def fetch_batch(base_url, batch, lookup_type, extra_headers):
    """
    Fetch all pages for a single batch of serial/mac devices.
    Returns (records_list, received_set).
    """
    devices_str      = ",".join(batch)
    records          = []
    received_devices = set()
    page             = 0

    while True:
        url      = base_url.format(limit=len(batch), page=page, devices=devices_str)
        resp     = requests.get(url, headers=extra_headers, timeout=TIMEOUT)
        resp.raise_for_status()
        body         = resp.json()
        devices_data = body.get("devices", [])
        pagination   = body.get("pagination", {})

        for dev in devices_data:
            parsed  = parse_device(dev)
            tracked = (parsed["mac_address"] if lookup_type == "mac" else parsed["serial_number"]) or ""
            if tracked:
                received_devices.add(tracked.upper())
            if parsed["serial_number"]:
                records.append(parsed["record"])

        total_count    = pagination.get("total_count", 0)
        fetched_so_far = page * len(batch) + len(devices_data)
        if not devices_data or len(devices_data) < len(batch) or fetched_so_far >= total_count:
            break
        page += 1

    return records, received_devices


def process_devices(device_list, lookup_type, extra_headers):
    """Non-streaming batch lookup for serial/mac (used by /api/export)."""
    base_url                = BASE_URL_SERIAL if lookup_type == "serial" else BASE_URL_MAC
    platform_device_records = []
    missing_devices         = []

    for batch_start in range(0, len(device_list), BATCH_SIZE):
        batch = device_list[batch_start: batch_start + BATCH_SIZE]
        try:
            records, received = fetch_batch(base_url, batch, lookup_type, extra_headers)
            platform_device_records.extend(records)
            for dev in batch:
                if dev.upper() not in received:
                    missing_devices.append(dev)
        except requests.exceptions.RequestException as e:
            print(f"Request error: {e}")
            missing_devices.extend(batch)

    enrich_workspace_names(platform_device_records, extra_headers)
    df = sort_df(pd.DataFrame(platform_device_records))
    return df, missing_devices


def process_workspace(workspace_id, extra_headers):
    """Non-streaming: fetch ALL devices for a workspace via pagination."""
    records, seen = [], set()
    page = 0
    while True:
        url = BASE_URL_WORKSPACE.format(limit=WS_PAGE_LIMIT, page=page, customer_id=workspace_id)
        try:
            resp         = requests.get(url, headers=extra_headers, timeout=TIMEOUT)
            resp.raise_for_status()
            devices_data = resp.json().get("devices", [])
            if not devices_data:
                break
            for dev in devices_data:
                parsed = parse_device(dev)
                sn     = parsed["serial_number"]
                if sn and sn not in seen:
                    seen.add(sn)
                    records.append(parsed["record"])
            print(f"Workspace {workspace_id} page {page + 1}: {len(devices_data)} devices.")
            page += 1
        except requests.exceptions.RequestException as e:
            print(f"Workspace fetch error page {page}: {e}")
            break

    enrich_workspace_names(records, extra_headers)
    return sort_df(pd.DataFrame(records))


# ─── Routes ───────────────────────────────────────────────────────────────────
@device_bp.route("/api/lookup", methods=["POST"])
def lookup():
    body          = request.get_json(force=True)
    raw_input     = body.get("devices", "")
    lookup_type   = body.get("type", "serial")
    extra_headers = body.get("parsed_headers", {}) or {}

    if lookup_type == "workspace":
        workspace_ids = [d.strip() for d in raw_input.replace("\n", ",").split(",") if d.strip()]
        if not workspace_ids:
            return jsonify({"error": "No workspace ID provided"}), 400
        all_records = []
        for wid in workspace_ids:
            df = process_workspace(wid, extra_headers)
            if not df.empty:
                all_records.extend(df.to_dict(orient="records"))
        found = len(all_records)
        return jsonify({"total": found, "found": found, "missing_count": 0,
                        "found_pct": 100.0 if found else 0, "missing_pct": 0,
                        "devices": all_records, "missing": []})

    device_list = [d.strip() for d in raw_input.replace("\n", ",").split(",") if d.strip()]
    if not device_list:
        return jsonify({"error": "No devices provided"}), 400
    df, missing = process_devices(device_list, lookup_type, extra_headers)
    records     = df.to_dict(orient="records") if not df.empty else []
    total       = len(device_list)
    found       = len(records)
    return jsonify({"total": total, "found": found, "missing_count": len(missing),
                    "found_pct":   round(found          / total * 100, 1) if total else 0,
                    "missing_pct": round(len(missing)   / total * 100, 1) if total else 0,
                    "devices": records, "missing": missing})


@device_bp.route("/api/export", methods=["POST"])
def export():
    body          = request.get_json(force=True)
    raw_input     = body.get("devices", "")
    lookup_type   = body.get("type", "serial")
    extra_headers = body.get("parsed_headers", {}) or {}
    export_type   = body.get("export", "found")
    columns       = body.get("columns", None)
    COL_ORDER     = ['Serial Number','MAC Address','Entitlement ID','Device Type',
                     'Device Model','Part Number','Folder Name','Workspace ID','Workspace Name']

    if lookup_type == "workspace":
        workspace_ids = [d.strip() for d in raw_input.replace("\n", ",").split(",") if d.strip()]
        all_records   = []
        for wid in workspace_ids:
            df = process_workspace(wid, extra_headers)
            if not df.empty:
                all_records.extend(df.to_dict(orient="records"))
        out_df = pd.DataFrame(all_records)
        if columns and not out_df.empty:
            valid_cols = [c for c in COL_ORDER if c in columns and c in out_df.columns]
            if valid_cols:
                out_df = out_df[valid_cols]
    else:
        device_list = [d.strip() for d in raw_input.replace("\n", ",").split(",") if d.strip()]
        df, missing = process_devices(device_list, lookup_type, extra_headers)
        if export_type == "missing":
            out_df = pd.DataFrame({"Missing Device": missing})
        else:
            out_df = df if not df.empty else pd.DataFrame()
            if columns and not out_df.empty:
                valid_cols = [c for c in COL_ORDER if c in columns and c in out_df.columns]
                if valid_cols:
                    out_df = out_df[valid_cols]

    buf = io.StringIO()
    out_df.to_csv(buf, index=False)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={export_type}_devices.csv"})


@device_bp.route("/api/lookup-stream", methods=["POST"])
def lookup_stream():
    body          = request.get_json(force=True)
    raw_input     = body.get("devices", "")
    lookup_type   = body.get("type", "serial")
    extra_headers = body.get("parsed_headers", {}) or {}

    # ── Workspace mode ────────────────────────────────────────────────────────
    if lookup_type == "workspace":
        workspace_ids = [d.strip() for d in raw_input.replace("\n", ",").split(",") if d.strip()]
        if not workspace_ids:
            return jsonify({"error": "No workspace ID provided"}), 400

        def generate_workspace():
            all_records, seen = [], set()
            for ws_idx, workspace_id in enumerate(workspace_ids):
                page     = 0
                ws_label = f"Workspace {ws_idx + 1}/{len(workspace_ids)}"
                while True:
                    yield f"data: {json.dumps({'type':'progress','pct':-1,'queried':len(all_records),'total':-1,'found':len(all_records),'batch':page+1,'total_batches':-1,'message':f'{ws_label} — page {page+1}'})}\n\n"
                    url = BASE_URL_WORKSPACE.format(limit=WS_PAGE_LIMIT, page=page, customer_id=workspace_id)
                    try:
                        resp         = requests.get(url, headers=extra_headers, timeout=TIMEOUT)
                        resp.raise_for_status()
                        devices_data = resp.json().get("devices", [])
                        if not devices_data:
                            break
                        for dev in devices_data:
                            parsed = parse_device(dev)
                            sn     = parsed["serial_number"]
                            if sn and sn not in seen:
                                seen.add(sn)
                                all_records.append(parsed["record"])
                        page += 1
                    except requests.exceptions.RequestException as e:
                        status_code = getattr(getattr(e, 'response', None), 'status_code', None)
                        if status_code in (401, 403):
                            yield f"data: {json.dumps({'type':'auth_error','status':status_code,'message':'Authentication failed — your Authorization/Cookie headers are expired or invalid. Please update and retry.'})}\n\n"
                            return
                        break

            df    = sort_df(pd.DataFrame(all_records))
            records_list = df.to_dict(orient="records") if not df.empty else []
            enrich_workspace_names(records_list, extra_headers)
            found = len(records_list)
            result = {"total": found, "found": found, "missing_count": 0,
                      "found_pct": 100.0 if found else 0, "missing_pct": 0,
                      "devices": records_list,
                      "missing": []}
            yield f"data: {json.dumps({'type':'progress','pct':100,'queried':found,'total':found,'found':found,'batch':1,'total_batches':1})}\n\n"
            yield f"data: {json.dumps({'type':'done','data':result})}\n\n"

        return Response(generate_workspace(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ── Serial / MAC mode ─────────────────────────────────────────────────────
    device_list   = [d.strip() for d in raw_input.replace("\n", ",").split(",") if d.strip()]
    if not device_list:
        return jsonify({"error": "No devices provided"}), 400

    base_url      = BASE_URL_SERIAL if lookup_type == "serial" else BASE_URL_MAC
    total_devices = len(device_list)
    total_batches = (total_devices + BATCH_SIZE - 1) // BATCH_SIZE

    def generate():
        platform_device_records, missing_devices = [], []

        for batch_idx, batch_start in enumerate(range(0, total_devices, BATCH_SIZE)):
            batch     = device_list[batch_start: batch_start + BATCH_SIZE]
            batch_num = batch_idx + 1
            pct       = round(batch_start / total_devices * 100)
            yield f"data: {json.dumps({'type':'progress','pct':pct,'queried':batch_start,'total':total_devices,'found':len(platform_device_records),'batch':batch_num,'total_batches':total_batches})}\n\n"

            try:
                records, received = fetch_batch(base_url, batch, lookup_type, extra_headers)
                platform_device_records.extend(records)
                for dev in batch:
                    if dev.upper() not in received:
                        missing_devices.append(dev)
            except requests.exceptions.RequestException as e:
                status_code = getattr(getattr(e, 'response', None), 'status_code', None)
                if status_code in (401, 403):
                    yield f"data: {json.dumps({'type':'auth_error','status':status_code,'message':'Authentication failed — your Authorization/Cookie headers are expired or invalid. Please update and retry.'})}\n\n"
                    return
                missing_devices.extend(batch)

            queried_now = batch_start + len(batch)
            yield f"data: {json.dumps({'type':'progress','pct':round(queried_now/total_devices*100),'queried':queried_now,'total':total_devices,'found':len(platform_device_records),'batch':batch_num,'total_batches':total_batches})}\n\n"

        df            = sort_df(pd.DataFrame(platform_device_records))
        records       = df.to_dict(orient="records") if not df.empty else []
        enrich_workspace_names(records, extra_headers)
        found         = len(records)
        missing_count = len(missing_devices)
        result = {
            "total":         total_devices,
            "found":         found,
            "missing_count": missing_count,
            "found_pct":     round(found          / total_devices * 100, 1) if total_devices else 0,
            "missing_pct":   round(missing_count  / total_devices * 100, 1) if total_devices else 0,
            "devices":       records,
            "missing":       missing_devices,
        }
        yield f"data: {json.dumps({'type':'progress','pct':100,'queried':total_devices,'total':total_devices,'found':found,'batch':total_batches,'total_batches':total_batches})}\n\n"
        yield f"data: {json.dumps({'type':'done','data':result})}\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
