"""Lightweight Platform Tools activity log (SQLite, auto-pruned)."""
from __future__ import annotations

import base64
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
ACTIVITY_DB = LOG_DIR / "platform_activity.db"

MAX_ROWS = 3000
RETENTION_DAYS = 90
DETAIL_MAX = 200


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(ACTIVITY_DB))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS activity (
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            ts     TEXT NOT NULL,
            actor  TEXT NOT NULL,
            tool   TEXT NOT NULL,
            action TEXT NOT NULL,
            detail TEXT,
            status TEXT NOT NULL DEFAULT 'ok',
            ip     TEXT
        )
        """
    )
    conn.commit()
    return conn


def _prune(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM activity WHERE ts < datetime('now', ?)",
        (f"-{RETENTION_DAYS} days",),
    )
    count = conn.execute("SELECT COUNT(*) FROM activity").fetchone()[0]
    if count > MAX_ROWS:
        excess = count - MAX_ROWS
        conn.execute(
            """
            DELETE FROM activity WHERE id IN (
                SELECT id FROM activity ORDER BY id ASC LIMIT ?
            )
            """,
            (excess,),
        )


def actor_from_headers(parsed_headers: dict | None) -> str:
    auth = (parsed_headers or {}).get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        return "unknown"
    token = auth.split(" ", 1)[1].strip()
    parts = token.split(".")
    if len(parts) < 2:
        return "unknown"
    try:
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return str(payload.get("email") or payload.get("sub") or "unknown").lower()
    except Exception:
        return "unknown"


def log_activity(
    *,
    actor: str,
    tool: str,
    action: str,
    detail: str | None = None,
    status: str = "ok",
    ip: str | None = None,
) -> None:
    try:
        ts = datetime.now(timezone.utc).isoformat()
        actor = (actor or "unknown")[:128]
        tool = (tool or "unknown")[:64]
        action = (action or "unknown")[:64]
        detail = (detail or "")[:DETAIL_MAX] or None
        conn = _get_db()
        conn.execute(
            """
            INSERT INTO activity (ts, actor, tool, action, detail, status, ip)
            VALUES (?,?,?,?,?,?,?)
            """,
            (ts, actor, tool, action, detail, status[:32], (ip or "")[:64] or None),
        )
        _prune(conn)
        conn.commit()
        conn.close()
    except Exception:
        return


def get_user_summaries(limit: int = 100) -> list[dict[str, Any]]:
    try:
        conn = _get_db()
        rows = conn.execute(
            """
            SELECT
                actor,
                COUNT(*) AS action_count,
                MAX(ts) AS last_seen,
                GROUP_CONCAT(DISTINCT tool) AS tools
            FROM activity
            GROUP BY actor
            ORDER BY last_seen DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_activities_for_actor(actor: str, limit: int = 100) -> list[dict[str, Any]]:
    try:
        conn = _get_db()
        rows = conn.execute(
            """
            SELECT id, ts, actor, tool, action, detail, status, ip
            FROM activity
            WHERE actor = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (actor, limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_stats() -> dict[str, Any]:
    try:
        conn = _get_db()
        total = conn.execute("SELECT COUNT(*) FROM activity").fetchone()[0]
        users = conn.execute("SELECT COUNT(DISTINCT actor) FROM activity").fetchone()[0]
        today = conn.execute(
            "SELECT COUNT(*) FROM activity WHERE date(ts) = date('now')"
        ).fetchone()[0]
        conn.close()
        return {"total": total, "users": users, "today": today}
    except Exception:
        return {"total": 0, "users": 0, "today": 0}
