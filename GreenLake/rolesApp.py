"""Bulk user role lookup across GreenLake workspaces."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests
import urllib3
from flask import Blueprint, Response, jsonify, request
from requests.adapters import HTTPAdapter

from platform_activity import actor_from_headers, log_activity

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

AQUILA_BASE = "https://aquila-user-api.common.cloud.hpe.com"
CUSTOMERS_URL = f"{AQUILA_BASE}/support-assistant/v1alpha1/customers"
ROLES_URL = f"{AQUILA_BASE}/support-assistant/v1alpha1/user-role-assignments"
TIMEOUT = 15
MAX_WORKERS = 12

roles_bp = Blueprint("roles", __name__)


def _parse_emails(raw: Any) -> list[str]:
    if isinstance(raw, list):
        items = raw
    else:
        items = str(raw or "").replace(",", "\n").split("\n")
    seen: set[str] = set()
    emails: list[str] = []
    for item in items:
        email = str(item or "").strip().lower()
        if email and email not in seen:
            seen.add(email)
            emails.append(email)
    return emails


def _headers(parsed_headers: dict) -> dict:
    extra = dict(parsed_headers or {})
    extra.setdefault("Accept", "application/json")
    return extra


def _build_session(parsed_headers: dict) -> requests.Session:
    session = requests.Session()
    session.headers.update(_headers(parsed_headers))
    adapter = HTTPAdapter(pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _fetch_customers(session: requests.Session, email: str) -> tuple[str, list | str, int | None]:
    """Return (email, customers_list | error_label, http_status_or_none)."""
    try:
        response = session.get(
            CUSTOMERS_URL,
            params={"username": email, "limit": 100, "offset": 0},
            timeout=TIMEOUT,
            verify=False,
        )
    except requests.exceptions.Timeout:
        return email, "Timed Out", None
    except requests.exceptions.RequestException as exc:
        return email, str(exc), None

    if response.status_code in (401, 403):
        return email, "auth_error", response.status_code

    if response.status_code != 200:
        return email, f"HTTP {response.status_code}", response.status_code

    return email, response.json().get("customers", []), None


def _fetch_roles_row(session: requests.Session, email: str, customer: dict) -> dict[str, str]:
    customer_id = customer.get("customer_id", "")
    workspace_name = customer.get("contact", {}).get("company_name", "Unknown Workspace")

    try:
        role_response = session.get(
            ROLES_URL,
            params={"platform_customer_id": customer_id, "username": email},
            timeout=TIMEOUT,
            verify=False,
        )
    except requests.exceptions.Timeout:
        roles_text = "Timed Out"
    except requests.exceptions.RequestException as exc:
        roles_text = str(exc)
    else:
        if role_response.status_code == 200:
            role_names = [
                r.get("role_name", "")
                for r in role_response.json().get("roles", [])
                if r.get("role_name")
            ]
            roles_text = ", ".join(role_names) if role_names else "No Roles"
        elif role_response.status_code == 403:
            roles_text = "HTTP 403"
        else:
            roles_text = f"HTTP {role_response.status_code}"

    return {
        "user": email,
        "workspace": workspace_name,
        "customer_id": customer_id,
        "roles": roles_text,
    }


@roles_bp.route("/api/roles-stream", methods=["POST"])
def roles_stream():
    body = request.get_json(force=True)
    parsed_headers = body.get("parsed_headers", {}) or {}
    emails = _parse_emails(body.get("emails"))

    if not emails:
        return jsonify({"error": "At least one user email is required."}), 400

    extra_headers = _headers(parsed_headers)

    def generate():
        rows: list[dict[str, str]] = []
        total_users = len(emails)

        yield f"data: {json.dumps({'type': 'progress', 'pct': 5, 'phase': 'fetch', 'msg': f'Fetching workspaces for {total_users} user(s)...'})}\n\n"

        session = _build_session(parsed_headers)

        # Phase 1 — fetch all workspaces per user in parallel
        user_workspaces: dict[str, list | str] = {}
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, total_users)) as executor:
            futures = {
                executor.submit(_fetch_customers, session, email): email
                for email in emails
            }
            done = 0
            for future in as_completed(futures):
                email, result, status = future.result()
                user_workspaces[email] = result
                done += 1
                pct = 5 + round((done / total_users) * 25)
                yield f"data: {json.dumps({'type': 'progress', 'pct': pct, 'phase': 'fetch', 'user': email, 'current': done, 'total': total_users})}\n\n"

                if result == "auth_error":
                    yield f"data: {json.dumps({'type': 'auth_error', 'status': status, 'message': 'Authentication failed — your Authorization/Cookie headers are expired or invalid. Please update and retry.'})}\n\n"
                    return

        # Build role-lookup tasks and emit immediate error/no-workspace rows
        tasks: list[tuple[str, dict]] = []
        for email in emails:
            result = user_workspaces.get(email, "Unknown error")
            if isinstance(result, str):
                row = {
                    "user": email,
                    "workspace": "",
                    "customer_id": "",
                    "roles": result,
                }
                rows.append(row)
                yield f"data: {json.dumps({'type': 'row', 'row': row})}\n\n"
            elif not result:
                row = {
                    "user": email,
                    "workspace": "",
                    "customer_id": "",
                    "roles": "No Workspaces",
                }
                rows.append(row)
                yield f"data: {json.dumps({'type': 'row', 'row': row})}\n\n"
            else:
                for customer in result:
                    tasks.append((email, customer))

        total_tasks = len(tasks)
        if not total_tasks:
            yield f"data: {json.dumps({'type': 'progress', 'pct': 100, 'phase': 'done'})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'data': {'rows': rows, 'user_count': total_users, 'row_count': len(rows)}})}\n\n"
            return

        yield f"data: {json.dumps({'type': 'progress', 'pct': 30, 'phase': 'roles', 'msg': f'Fetching roles for {total_tasks} workspace(s)...'})}\n\n"

        # Phase 2 — fetch all role assignments in parallel
        completed = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(_fetch_roles_row, session, email, customer): (email, customer)
                for email, customer in tasks
            }
            for future in as_completed(futures):
                row = future.result()
                rows.append(row)
                completed += 1
                pct = 30 + round((completed / total_tasks) * 65)
                yield f"data: {json.dumps({'type': 'progress', 'pct': pct, 'phase': 'workspace', 'user': row['user'], 'workspace': row['workspace'], 'current': completed, 'total': total_tasks})}\n\n"
                yield f"data: {json.dumps({'type': 'row', 'row': row})}\n\n"

        yield f"data: {json.dumps({'type': 'progress', 'pct': 100, 'phase': 'done', 'current': total_users, 'total': total_users})}\n\n"
        yield f"data: {json.dumps({'type': 'done', 'data': {'rows': rows, 'user_count': total_users, 'row_count': len(rows)}})}\n\n"
        log_activity(
            actor=actor_from_headers(parsed_headers),
            tool="User Roles",
            action="fetch_roles",
            detail=f"{total_users} user(s), {len(rows)} row(s)",
            ip=request.remote_addr,
        )

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
