"""User feedback persistence (SQLite + JSON append log)."""
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

FEEDBACK_DB = LOG_DIR / "feedback.db"
FEEDBACK_JSON_LOG = LOG_DIR / "feedback.log"

_json_logger = logging.getLogger("feedback")
_json_logger.setLevel(logging.INFO)
if not _json_logger.handlers:
    _fh = logging.FileHandler(FEEDBACK_JSON_LOG)
    _fh.setFormatter(logging.Formatter("%(message)s"))
    _json_logger.addHandler(_fh)
    _json_logger.propagate = False

VALID_CATEGORIES = {"bug", "feature", "question", "other"}
VALID_STATUSES = {"new", "reviewed", "closed"}


def _get_db():
    conn = sqlite3.connect(str(FEEDBACK_DB))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ts           TEXT NOT NULL,
            username     TEXT NOT NULL DEFAULT 'anonymous',
            display_name TEXT NOT NULL DEFAULT 'Anonymous',
            role         TEXT,
            source       TEXT NOT NULL DEFAULT 'platform-tools',
            page_url     TEXT,
            category     TEXT NOT NULL DEFAULT 'other',
            message      TEXT NOT NULL,
            status       TEXT NOT NULL DEFAULT 'new'
        )
        """
    )
    conn.commit()
    return conn


def save_feedback(
    *,
    message: str,
    category: str = "other",
    source: str = "platform-tools",
    page_url: Optional[str] = None,
    user: Optional[dict] = None,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    cat = category if category in VALID_CATEGORIES else "other"
    username = "anonymous"
    display_name = "Anonymous"
    role = None
    if user:
        username = user.get("username", "anonymous")
        display_name = user.get("display_name", username)
        role = user.get("role")

    record = {
        "ts": now,
        "username": username,
        "display_name": display_name,
        "role": role,
        "source": source,
        "page_url": page_url,
        "category": cat,
        "message": message.strip(),
        "status": "new",
    }
    _json_logger.info(json.dumps(record))

    conn = _get_db()
    cur = conn.execute(
        """
        INSERT INTO feedback
        (ts, username, display_name, role, source, page_url, category, message, status)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            now,
            record["username"],
            record["display_name"],
            role,
            source,
            page_url,
            cat,
            record["message"],
            "new",
        ),
    )
    fid = cur.lastrowid
    conn.commit()
    conn.close()
    return fid


def get_recent_feedback(limit: int = 500, status: Optional[str] = None) -> list:
    try:
        conn = _get_db()
        if status and status in VALID_STATUSES:
            rows = conn.execute(
                "SELECT * FROM feedback WHERE status = ? ORDER BY id DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM feedback ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def update_feedback_status(feedback_id: int, status: str) -> bool:
    if status not in VALID_STATUSES:
        return False
    try:
        conn = _get_db()
        cur = conn.execute(
            "UPDATE feedback SET status = ? WHERE id = ?", (status, feedback_id)
        )
        conn.commit()
        ok = cur.rowcount > 0
        conn.close()
        return ok
    except Exception:
        return False


def get_feedback_stats() -> dict:
    try:
        conn = _get_db()
        total = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today = conn.execute(
            "SELECT COUNT(*) FROM feedback WHERE ts LIKE ?", (today_str + "%",)
        ).fetchone()[0]
        by_status = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT status, COUNT(*) FROM feedback GROUP BY status"
            ).fetchall()
        }
        by_category = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT category, COUNT(*) FROM feedback GROUP BY category"
            ).fetchall()
        }
        conn.close()
        return {
            "total": total,
            "today": today,
            "by_status": by_status,
            "by_category": by_category,
        }
    except Exception:
        return {}
