"""Platform Tools action audit log — who did what, with compulsory case numbers."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from flask import Blueprint, jsonify, request, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "platform_audit.db"

# Keep audit payloads readable without blowing up SQLite / UI.
AUDIT_MAX_CHARS = 12000
AUDIT_SAMPLE_ROWS = 25

# Never persist auth material in audit logs.
_SENSITIVE_KEYS = {
    "parsed_headers",
    "headers",
    "authorization",
    "cookie",
    "api_token",
    "token",
    "password",
    "secret",
    "client_secret",
}

platform_audit_bp = Blueprint("platform_audit", __name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_columns(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(audit_events)").fetchall()}
    if "request_input" not in cols:
        conn.execute("ALTER TABLE audit_events ADD COLUMN request_input TEXT")
    if "response_output" not in cols:
        conn.execute("ALTER TABLE audit_events ADD COLUMN response_output TEXT")


def _ensure_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_events (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ts            TEXT NOT NULL,
            username      TEXT NOT NULL,
            display_name  TEXT NOT NULL DEFAULT '',
            tool          TEXT NOT NULL,
            action        TEXT NOT NULL,
            case_number   TEXT NOT NULL,
            detail        TEXT,
            status        TEXT NOT NULL DEFAULT 'ok',
            page          TEXT,
            ip            TEXT,
            request_input TEXT,
            response_output TEXT
        )
        """
    )
    _ensure_columns(conn)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_events(ts DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_events(username)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_case ON audit_events(case_number)"
    )
    conn.commit()
    return conn


def normalize_case_number(value: Any) -> str:
    return str(value or "").strip()


def require_case_number(body: Optional[dict]) -> tuple[Optional[str], Optional[tuple]]:
    """
    Validate compulsory case_number from JSON body.
    Returns (case_number, None) or (None, (jsonify_response, status)).
    """
    case_number = normalize_case_number((body or {}).get("case_number"))
    if not case_number:
        return None, (
            jsonify(
                {
                    "error": "Case number is required before running this action.",
                    "field": "case_number",
                }
            ),
            400,
        )
    if len(case_number) > 64:
        return None, (
            jsonify({"error": "Case number is too long (max 64 characters).", "field": "case_number"}),
            400,
        )
    return case_number, None


def sanitize_audit_value(value: Any) -> Any:
    """Strip secrets / auth headers from values destined for the audit log."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            lk = str(key).lower()
            if lk in _SENSITIVE_KEYS or "authorization" in lk or lk.endswith("_token"):
                out[key] = "[redacted]"
            elif lk == "case_number":
                continue
            else:
                out[key] = sanitize_audit_value(item)
        return out
    if isinstance(value, list):
        return [sanitize_audit_value(v) for v in value]
    return value


def clip_for_audit(value: Any, max_chars: int = AUDIT_MAX_CHARS) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(sanitize_audit_value(value), ensure_ascii=False, default=str, indent=2)
        except Exception:
            text = str(value)
    if len(text) > max_chars:
        return text[:max_chars] + f"\n… [truncated, {len(text)} chars total]"
    return text


def summarize_list_payload(
    result: Optional[dict],
    *,
    list_keys: tuple[str, ...] = ("devices", "subscriptions", "rows", "events"),
    sample: int = AUDIT_SAMPLE_ROWS,
) -> Any:
    """Keep counts + a short sample of large result lists for the audit log."""
    if not isinstance(result, dict):
        return result
    out = dict(result)
    for key in list_keys:
        items = out.get(key)
        if isinstance(items, list) and len(items) > sample:
            out[key] = items[:sample]
            out[f"{key}_sample"] = sample
            out[f"{key}_total"] = len(items)
            out[f"{key}_truncated"] = True
    missing = out.get("missing")
    if isinstance(missing, list) and len(missing) > 50:
        out["missing"] = missing[:50]
        out["missing_total"] = len(missing)
        out["missing_truncated"] = True
    return out


def log_audit_event(
    *,
    username: str,
    display_name: str = "",
    tool: str,
    action: str,
    case_number: str,
    detail: str = "",
    status: str = "ok",
    page: Optional[str] = None,
    ip: Optional[str] = None,
    request_input: Any = None,
    response_output: Any = None,
) -> int:
    conn = _ensure_db()
    try:
        cur = conn.execute(
            """
            INSERT INTO audit_events
                (ts, username, display_name, tool, action, case_number, detail, status, page, ip,
                 request_input, response_output)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _now(),
                username or "unknown",
                display_name or username or "",
                tool,
                action,
                normalize_case_number(case_number),
                (detail or "")[:2000],
                status,
                (page or "")[:500] or None,
                ip,
                clip_for_audit(request_input) if request_input not in (None, "") else None,
                clip_for_audit(response_output) if response_output not in (None, "") else None,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_audit_events(
    *,
    limit: int = 200,
    username: Optional[str] = None,
    tool: Optional[str] = None,
    case_number: Optional[str] = None,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 200), 2000))
    conn = _ensure_db()
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if username:
            clauses.append("username = ?")
            params.append(username)
        if tool:
            clauses.append("tool = ?")
            params.append(tool)
        if case_number:
            clauses.append("case_number LIKE ?")
            params.append(f"%{case_number.strip()}%")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"""
            SELECT id, ts, username, display_name, tool, action, case_number,
                   detail, status, page, ip, request_input, response_output
            FROM audit_events
            {where}
            ORDER BY id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def audit_analytics(*, days: int = 30) -> dict[str, Any]:
    """Aggregate audit stats for a Power BI–style usage dashboard."""
    days = int(days or 30)
    if days < 1:
        days = 1
    if days > 3650:
        days = 3650

    conn = _ensure_db()
    try:
        # ISO timestamps — compare as text works for our UTC isoformat storage
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        today = datetime.now(timezone.utc).date().isoformat()

        where = "WHERE ts >= ?"
        params: tuple[Any, ...] = (cutoff,)

        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM audit_events {where}", params
        ).fetchone()["n"]

        engineers = conn.execute(
            f"SELECT COUNT(DISTINCT username) AS n FROM audit_events {where}", params
        ).fetchone()["n"]

        cases = conn.execute(
            f"""
            SELECT COUNT(DISTINCT case_number) AS n FROM audit_events
            {where} AND case_number IS NOT NULL AND trim(case_number) != ''
            """,
            params,
        ).fetchone()["n"]

        tools_n = conn.execute(
            f"SELECT COUNT(DISTINCT tool) AS n FROM audit_events {where}", params
        ).fetchone()["n"]

        today_n = conn.execute(
            """
            SELECT COUNT(*) AS n FROM audit_events
            WHERE substr(ts, 1, 10) = ?
            """,
            (today,),
        ).fetchone()["n"]

        by_engineer = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT username,
                       COALESCE(NULLIF(display_name, ''), username) AS display_name,
                       COUNT(*) AS actions,
                       COUNT(DISTINCT case_number) AS cases,
                       COUNT(DISTINCT tool) AS tools,
                       MAX(ts) AS last_action
                FROM audit_events
                {where}
                GROUP BY username
                ORDER BY actions DESC, username ASC
                LIMIT 50
                """,
                params,
            ).fetchall()
        ]

        by_tool = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT tool,
                       COUNT(*) AS actions,
                       COUNT(DISTINCT username) AS engineers,
                       COUNT(DISTINCT case_number) AS cases
                FROM audit_events
                {where}
                GROUP BY tool
                ORDER BY actions DESC, tool ASC
                """,
                params,
            ).fetchall()
        ]

        by_day = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT substr(ts, 1, 10) AS day, COUNT(*) AS actions
                FROM audit_events
                {where}
                GROUP BY substr(ts, 1, 10)
                ORDER BY day ASC
                """,
                params,
            ).fetchall()
        ]

        by_action = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT action, COUNT(*) AS count
                FROM audit_events
                {where}
                GROUP BY action
                ORDER BY count DESC
                LIMIT 20
                """,
                params,
            ).fetchall()
        ]

        engineer_tool = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT username,
                       COALESCE(NULLIF(display_name, ''), username) AS display_name,
                       tool,
                       COUNT(*) AS count
                FROM audit_events
                {where}
                GROUP BY username, tool
                ORDER BY count DESC
                LIMIT 100
                """,
                params,
            ).fetchall()
        ]

        top_cases = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT case_number, COUNT(*) AS count,
                       COUNT(DISTINCT username) AS engineers
                FROM audit_events
                {where} AND case_number IS NOT NULL AND trim(case_number) != ''
                GROUP BY case_number
                ORDER BY count DESC
                LIMIT 15
                """,
                params,
            ).fetchall()
        ]

        top = by_engineer[0] if by_engineer else None

        return {
            "days": days,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total_actions": int(total or 0),
                "unique_engineers": int(engineers or 0),
                "unique_cases": int(cases or 0),
                "unique_tools": int(tools_n or 0),
                "actions_today": int(today_n or 0),
                "top_engineer": top,
            },
            "by_engineer": by_engineer,
            "by_tool": by_tool,
            "by_day": by_day,
            "by_action": by_action,
            "engineer_tool": engineer_tool,
            "top_cases": top_cases,
        }
    finally:
        conn.close()


def current_user_for_audit() -> dict[str, str]:
    """Best-effort identity from Platform Tools session."""
    try:
        from platform_auth import read_session

        user = read_session()
        if user:
            return {
                "username": user.get("username") or "unknown",
                "display_name": user.get("display_name") or user.get("username") or "",
            }
    except Exception:
        pass
    return {"username": "unknown", "display_name": ""}


def client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or ""


def capture_audit_actor() -> dict[str, Optional[str]]:
    """
    Snapshot identity / page / IP while the Flask request context is alive.
    Use this before streaming generators, then pass into log_audit_event.
    """
    ident = current_user_for_audit()
    return {
        "username": ident["username"],
        "display_name": ident["display_name"],
        "page": (request.headers.get("Referer") or "")[:500] or None,
        "ip": client_ip(),
    }


def log_from_request(
    *,
    tool: str,
    action: str,
    case_number: str,
    detail: str = "",
    status: str = "ok",
    request_input: Any = None,
    response_output: Any = None,
    actor: Optional[dict[str, Optional[str]]] = None,
) -> int:
    if actor is None:
        actor = capture_audit_actor()
    return log_audit_event(
        username=actor.get("username") or "unknown",
        display_name=actor.get("display_name") or "",
        tool=tool,
        action=action,
        case_number=case_number,
        detail=detail,
        status=status,
        page=actor.get("page"),
        ip=actor.get("ip"),
        request_input=request_input,
        response_output=response_output,
    )


# ── Admin routes ───────────────────────────────────────────────────────────────


@platform_audit_bp.route("/admin/audit", methods=["GET"])
def admin_audit_page():
    from platform_auth import is_super_admin, read_session

    user = read_session()
    if not user:
        from flask import redirect

        return redirect("/login?next=/admin/audit")
    if not is_super_admin(user):
        from flask import redirect

        return redirect("/GreenLakeTools.html")
    return send_from_directory(str(BASE_DIR), "PlatformAudit.html")


@platform_audit_bp.route("/admin/audit-dashboard", methods=["GET"])
def admin_audit_dashboard_page():
    from platform_auth import is_super_admin, read_session

    user = read_session()
    if not user:
        from flask import redirect

        return redirect("/login?next=/admin/audit-dashboard")
    if user.get("must_change_password"):
        from flask import redirect

        return redirect("/change-password")
    if not is_super_admin(user):
        from flask import redirect

        return redirect("/GreenLakeTools.html")
    return send_from_directory(str(BASE_DIR), "PlatformAuditDashboard.html")


@platform_audit_bp.route("/api/admin/audit", methods=["GET"])
def admin_audit_list():
    from platform_auth import is_super_admin, read_session

    user = read_session()
    if not user:
        return jsonify({"error": "Authentication required.", "login": "/login"}), 401
    if not is_super_admin(user):
        return jsonify({"error": "Super admin access required."}), 403
    items = list_audit_events(
        limit=request.args.get("limit", 500, type=int),
        username=request.args.get("username") or None,
        tool=request.args.get("tool") or None,
        case_number=request.args.get("case_number") or None,
    )
    return jsonify({"items": items, "total": len(items)})


@platform_audit_bp.route("/api/admin/audit/analytics", methods=["GET"])
def admin_audit_analytics():
    from platform_auth import is_super_admin, read_session

    user = read_session()
    if not user:
        return jsonify({"error": "Authentication required.", "login": "/login"}), 401
    if user.get("must_change_password"):
        return jsonify({
            "error": "Password change required.",
            "redirect": "/change-password",
        }), 403
    if not is_super_admin(user):
        return jsonify({"error": "Super admin access required."}), 403
    days = request.args.get("days", 30, type=int) or 30
    return jsonify(audit_analytics(days=days))
