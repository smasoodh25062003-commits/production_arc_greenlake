"""Lightweight platform-tools usage log (SQLite, size-capped)."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

USAGE_DB = LOG_DIR / "usage.db"
USAGE_JSON_LOG = LOG_DIR / "usage.log"
MAX_ROWS = 5000

_json_logger = logging.getLogger("usage")
_json_logger.setLevel(logging.INFO)
if not _json_logger.handlers:
    _fh = logging.FileHandler(USAGE_JSON_LOG)
    _fh.setFormatter(logging.Formatter("%(message)s"))
    _json_logger.addHandler(_fh)
    _json_logger.propagate = False


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(USAGE_DB))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_events (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ts           TEXT NOT NULL,
            visitor_id   TEXT NOT NULL,
            user_label   TEXT NOT NULL DEFAULT '',
            session_user TEXT,
            tool         TEXT NOT NULL DEFAULT 'unknown',
            action       TEXT NOT NULL DEFAULT 'page_view',
            page         TEXT,
            detail       TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_events(ts DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_usage_visitor ON usage_events(visitor_id)"
    )
    conn.commit()
    return conn


def _prune(conn: sqlite3.Connection) -> None:
    count = conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0]
    if count <= MAX_ROWS:
        return
    excess = count - MAX_ROWS
    conn.execute(
        """
        DELETE FROM usage_events
        WHERE id IN (
            SELECT id FROM usage_events ORDER BY id ASC LIMIT ?
        )
        """,
        (excess,),
    )


def save_usage_event(
    *,
    visitor_id: str,
    action: str,
    tool: str = "unknown",
    user_label: str = "",
    session_user: Optional[str] = None,
    page: Optional[str] = None,
    detail: Optional[str] = None,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    vid = (visitor_id or "unknown")[:64]
    act = (action or "event")[:64]
    t = (tool or "unknown")[:64]
    label = (user_label or "")[:128]
    sess = (session_user or "")[:128] or None
    pg = (page or "")[:512] or None
    det = (detail or "")[:200] or None

    record = {
        "ts": now,
        "visitor_id": vid,
        "user_label": label,
        "session_user": sess,
        "tool": t,
        "action": act,
        "page": pg,
        "detail": det,
    }
    _json_logger.info(json.dumps(record))

    conn = _get_db()
    cur = conn.execute(
        """
        INSERT INTO usage_events
        (ts, visitor_id, user_label, session_user, tool, action, page, detail)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (now, vid, label, sess, t, act, pg, det),
    )
    eid = cur.lastrowid
    _prune(conn)
    conn.commit()
    conn.close()
    return eid


def get_recent_usage(
    limit: int = 500,
    tool: Optional[str] = None,
    visitor_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    try:
        conn = _get_db()
        q = "SELECT * FROM usage_events WHERE 1=1"
        params: list[Any] = []
        if tool:
            q += " AND tool = ?"
            params.append(tool)
        if visitor_id:
            q += " AND visitor_id = ?"
            params.append(visitor_id)
        q += " ORDER BY id DESC LIMIT ?"
        params.append(min(limit, 2000))
        rows = conn.execute(q, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_usage_stats() -> dict[str, Any]:
    try:
        conn = _get_db()
        total = conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0]
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today = conn.execute(
            "SELECT COUNT(*) FROM usage_events WHERE ts LIKE ?",
            (today_str + "%",),
        ).fetchone()[0]
        unique_today = conn.execute(
            """
            SELECT COUNT(DISTINCT visitor_id) FROM usage_events
            WHERE ts LIKE ?
            """,
            (today_str + "%",),
        ).fetchone()[0]
        by_tool = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT tool, COUNT(*) FROM usage_events GROUP BY tool ORDER BY 2 DESC"
            ).fetchall()
        }
        by_action = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT action, COUNT(*) FROM usage_events GROUP BY action ORDER BY 2 DESC LIMIT 12"
            ).fetchall()
        }
        top_users = [
            {
                "label": r[0] or r[1][:12],
                "visitor_id": r[1],
                "count": r[2],
            }
            for r in conn.execute(
                """
                SELECT
                    COALESCE(NULLIF(user_label, ''), NULLIF(session_user, ''), visitor_id) AS who,
                    visitor_id,
                    COUNT(*) AS cnt
                FROM usage_events
                GROUP BY visitor_id
                ORDER BY cnt DESC
                LIMIT 15
                """
            ).fetchall()
        ]
        conn.close()
        return {
            "total": total,
            "today": today,
            "unique_today": unique_today,
            "by_tool": by_tool,
            "by_action": by_action,
            "top_users": top_users,
            "max_rows": MAX_ROWS,
        }
    except Exception:
        return {}
