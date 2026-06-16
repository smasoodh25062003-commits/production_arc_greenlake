"""Lightweight request tracking (SQLite) for the admin monitoring page."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


LOG_DIR = Path(os.path.join(os.path.dirname(__file__), "../logs")).resolve()
LOG_DIR.mkdir(exist_ok=True)

TRAFFIC_DB = LOG_DIR / "traffic.db"


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(TRAFFIC_DB))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS traffic (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ts           TEXT NOT NULL,
            path         TEXT NOT NULL,
            method       TEXT NOT NULL,
            status_code  INTEGER NOT NULL,
            duration_ms  INTEGER,
            username     TEXT,
            role         TEXT,
            ip           TEXT,
            user_agent   TEXT
        )
        """
    )
    conn.commit()
    return conn


def log_request(
    *,
    path: str,
    method: str,
    status_code: int,
    duration_ms: Optional[int] = None,
    username: Optional[str] = None,
    role: Optional[str] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """Persist one request row; best-effort (never raises)."""
    try:
        ts = datetime.now(timezone.utc).isoformat()
        conn = _get_db()
        conn.execute(
            """
            INSERT INTO traffic
            (ts, path, method, status_code, duration_ms, username, role, ip, user_agent)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                ts,
                path[:512],
                method[:16],
                int(status_code),
                int(duration_ms) if duration_ms is not None else None,
                (username or "")[:128],
                (role or "")[:32],
                (ip or "")[:64],
                (user_agent or "")[:512],
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        # Intentionally swallow errors (monitoring must not break the app).
        return


def get_traffic_stats() -> dict[str, Any]:
    """Return aggregate traffic stats for the admin monitoring page."""
    try:
        conn = _get_db()

        total = conn.execute("SELECT COUNT(*) FROM traffic").fetchone()[0]

        active_5m = conn.execute(
            """
            SELECT COUNT(DISTINCT COALESCE(NULLIF(username,''), ip))
            FROM traffic
            WHERE ts >= datetime('now', '-5 minutes')
            """
        ).fetchone()[0]

        req_1h = conn.execute(
            "SELECT COUNT(*) FROM traffic WHERE ts >= datetime('now', '-1 hour')"
        ).fetchone()[0]

        uniq_1h = conn.execute(
            """
            SELECT COUNT(DISTINCT COALESCE(NULLIF(username,''), ip))
            FROM traffic
            WHERE ts >= datetime('now', '-1 hour')
            """
        ).fetchone()[0]

        errors_1h = conn.execute(
            """
            SELECT COUNT(*) FROM traffic
            WHERE ts >= datetime('now', '-1 hour') AND status_code >= 400
            """
        ).fetchone()[0]

        top_paths = [
            {"path": r[0], "count": r[1]}
            for r in conn.execute(
                """
                SELECT path, COUNT(*) as cnt
                FROM traffic
                WHERE ts >= datetime('now', '-1 hour')
                GROUP BY path
                ORDER BY cnt DESC
                LIMIT 8
                """
            ).fetchall()
        ]

        status_buckets = {
            "2xx": conn.execute(
                "SELECT COUNT(*) FROM traffic WHERE ts >= datetime('now', '-1 hour') AND status_code BETWEEN 200 AND 299"
            ).fetchone()[0],
            "3xx": conn.execute(
                "SELECT COUNT(*) FROM traffic WHERE ts >= datetime('now', '-1 hour') AND status_code BETWEEN 300 AND 399"
            ).fetchone()[0],
            "4xx": conn.execute(
                "SELECT COUNT(*) FROM traffic WHERE ts >= datetime('now', '-1 hour') AND status_code BETWEEN 400 AND 499"
            ).fetchone()[0],
            "5xx": conn.execute(
                "SELECT COUNT(*) FROM traffic WHERE ts >= datetime('now', '-1 hour') AND status_code >= 500"
            ).fetchone()[0],
        }

        hourly = conn.execute(
            """
            SELECT substr(ts, 1, 13) as hour, COUNT(*) as cnt
            FROM traffic
            WHERE ts >= datetime('now', '-24 hours')
            GROUP BY hour
            ORDER BY hour ASC
            """
        ).fetchall()
        hourly_data = {r[0] + ":00": r[1] for r in hourly}

        conn.close()
        return {
            "total": total,
            "active_5m": active_5m,
            "requests_1h": req_1h,
            "unique_1h": uniq_1h,
            "errors_1h": errors_1h,
            "top_paths_1h": top_paths,
            "status_buckets_1h": status_buckets,
            "hourly_24h": hourly_data,
        }
    except Exception:
        return {}

