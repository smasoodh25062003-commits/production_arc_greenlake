"""Platform Tools authentication — bcrypt passwords, signed cookies, SQLite users + tiles."""
from __future__ import annotations

import csv
import io
import os
import sqlite3
import time
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Optional

import bcrypt
from flask import (
    Blueprint,
    jsonify,
    redirect,
    request,
    send_from_directory,
    make_response,
)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "platform_auth.db"

SESSION_COOKIE = "pt_session"
SESSION_MAX_AGE = 8 * 3600  # 8 hours
ROLE_SUPER_ADMIN = "super_admin"
ROLE_USER = "user"

TILE_CATALOG: list[dict[str, str]] = [
    {"id": "device", "label": "Device Management"},
    {"id": "sub", "label": "Subscription Management"},
    {"id": "user", "label": "User / Workspace"},
    {"id": "roles", "label": "User Roles"},
    {"id": "sso-tools", "label": "SSO Tools"},
    {"id": "serial-checker", "label": "Serial Checker"},
    {"id": "humio-rpl", "label": "Humio RPL Logs"},
    {"id": "mentor-tool", "label": "GreenLake Dashboard"},
    {"id": "feedback-inbox", "label": "Feedback Inbox"},
    {"id": "dsat-analyzer", "label": "DSAT Analyzer"},
]
ALL_TILE_IDS = [t["id"] for t in TILE_CATALOG]

# HTML path → required tile (None = any authenticated user)
PAGE_TILE_MAP: dict[str, Optional[str]] = {
    "/": None,  # home: session only; tiles filtered client-side
    "/GreenLakeTools.html": None,
    "/DeviceManagement.html": "device",
    "/Subscriptionmanagement.html": "sub",
    "/UserManagement.html": "user",
    "/UserRoles.html": "roles",
    "/SerialChecker.html": "serial-checker",
    "/HumioRplLogs.html": "humio-rpl",
    "/TransferDevices.html": None,
    "/TransferSubscriptions.html": None,
    "/DsatAlertAnalyzer.html": "dsat-analyzer",
    "/rohit": "mentor-tool",
    "/admin/users": None,  # role-checked separately
    "/admin/audit": None,  # super_admin page
    "/admin/audit-dashboard": None,  # super_admin analytics
}

# API path prefix → required tile
API_TILE_PREFIXES: list[tuple[str, str]] = [
    ("/api/lookup", "device"),
    ("/api/export", "device"),
    ("/api/lookup-stream", "device"),
    ("/api/subscription-stream", "sub"),
    ("/api/workspace-stream", "user"),
    ("/api/roles-stream", "roles"),
    ("/api/serial-check", "serial-checker"),
    ("/api/humio/", "humio-rpl"),
    ("/api/ccs/", "device"),  # CCS transfer: require device tile
]

platform_auth_bp = Blueprint("platform_auth", __name__)

# In-memory login rate limit: ip -> list of failure timestamps
_login_failures: dict[str, list[float]] = {}
_LOGIN_WINDOW_SEC = 300
_LOGIN_MAX_FAILURES = 10
# Precomputed bcrypt hash for timing-safe failed logins
_DUMMY_HASH = bcrypt.hashpw(b"dummy-timing-pad", bcrypt.gensalt(12)).decode("utf-8")


def _secret_key() -> str:
    return os.environ.get("SESSION_SECRET_KEY", "dev-secret-change-in-production")


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_secret_key(), salt="platform-tools-auth")


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(12)).decode("utf-8")


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def _ensure_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            display_name  TEXT NOT NULL DEFAULT '',
            role          TEXT NOT NULL DEFAULT 'user',
            enabled       INTEGER NOT NULL DEFAULT 1,
            must_change_password INTEGER NOT NULL DEFAULT 0,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        )
        """
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "must_change_password" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0"
        )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_tiles (
            username TEXT NOT NULL COLLATE NOCASE,
            tile_id  TEXT NOT NULL,
            PRIMARY KEY (username, tile_id),
            FOREIGN KEY (username) REFERENCES users(username) ON DELETE CASCADE ON UPDATE CASCADE
        )
        """
    )
    conn.commit()
    _seed_if_empty(conn)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_if_empty(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    if row and row["n"] > 0:
        return
    # Prefer migrating hash from users.yaml admin; fallback password "admin"
    password_hash = None
    display_name = "Platform Admin"
    username = "admin"
    yaml_path = (
        BASE_DIR / "gldashboard_bundle" / "app" / "config" / "users.yaml"
    )
    try:
        import yaml

        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for u in data.get("users", []) or []:
            if u.get("username") == "admin" and u.get("password_hash"):
                password_hash = u["password_hash"]
                display_name = u.get("display_name") or display_name
                break
    except Exception:
        pass
    if not password_hash:
        password_hash = hash_password("admin")
    ts = _now()
    conn.execute(
        """
        INSERT INTO users (username, password_hash, display_name, role, enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, 1, ?, ?)
        """,
        (username, password_hash, display_name, ROLE_SUPER_ADMIN, ts, ts),
    )
    for tile_id in ALL_TILE_IDS:
        conn.execute(
            "INSERT INTO user_tiles (username, tile_id) VALUES (?, ?)",
            (username, tile_id),
        )
    conn.commit()


def get_user_record(username: str) -> Optional[dict[str, Any]]:
    conn = _ensure_db()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if not row:
            return None
        tiles = [
            r["tile_id"]
            for r in conn.execute(
                "SELECT tile_id FROM user_tiles WHERE username = ? ORDER BY tile_id",
                (row["username"],),
            ).fetchall()
        ]
        return {
            "username": row["username"],
            "password_hash": row["password_hash"],
            "display_name": row["display_name"] or row["username"],
            "role": row["role"],
            "enabled": bool(row["enabled"]),
            "must_change_password": bool(row["must_change_password"])
            if "must_change_password" in row.keys()
            else False,
            "tiles": tiles,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    finally:
        conn.close()


def authenticate_user(username: str, password: str) -> Optional[dict[str, Any]]:
    user = get_user_record(username)
    if not user or not user["enabled"]:
        return None
    if not verify_password(password, user["password_hash"]):
        return None
    return {
        "username": user["username"],
        "display_name": user["display_name"],
        "role": user["role"],
        "tiles": list(user["tiles"]),
        "must_change_password": bool(user.get("must_change_password")),
    }


def session_user_payload(user: dict[str, Any]) -> dict[str, Any]:
    """Minimal payload stored in the signed cookie (no password)."""
    return {
        "username": user["username"],
        "display_name": user.get("display_name", user["username"]),
        "role": user.get("role", ROLE_USER),
    }


def create_session_token(user: dict[str, Any]) -> str:
    return _serializer().dumps(session_user_payload(user))


def read_session() -> Optional[dict[str, Any]]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=SESSION_MAX_AGE)
        if not isinstance(data, dict) or not data.get("username"):
            return None
        # Re-load from DB so disabled users / tile changes take effect
        record = get_user_record(data["username"])
        if not record or not record["enabled"]:
            return None
        return {
            "username": record["username"],
            "display_name": record["display_name"],
            "role": record["role"],
            "tiles": list(record["tiles"]),
            "must_change_password": bool(record.get("must_change_password")),
        }
    except (BadSignature, SignatureExpired):
        return None


def _wants_secure_cookie() -> bool:
    if request.is_secure:
        return True
    proto = (request.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip()
    return proto.lower() == "https"


def set_session_cookie(response, user: dict[str, Any]) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=create_session_token(user),
        httponly=True,
        samesite="Lax",
        max_age=SESSION_MAX_AGE,
        path="/",
        secure=_wants_secure_cookie(),
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(key=SESSION_COOKIE, path="/")


def is_super_admin(user: Optional[dict[str, Any]]) -> bool:
    return bool(user and user.get("role") == ROLE_SUPER_ADMIN)


def effective_tiles(user: Optional[dict[str, Any]]) -> list[str]:
    if not user:
        return []
    if is_super_admin(user):
        return list(ALL_TILE_IDS)
    return list(user.get("tiles") or [])


def user_has_tile(user: Optional[dict[str, Any]], tile_id: Optional[str]) -> bool:
    if not user:
        return False
    if tile_id is None:
        return True
    if is_super_admin(user):
        return True
    return tile_id in (user.get("tiles") or [])


def _client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _prune_failures(ip: str) -> None:
    now = time.time()
    stamps = [t for t in _login_failures.get(ip, []) if now - t < _LOGIN_WINDOW_SEC]
    if stamps:
        _login_failures[ip] = stamps
    else:
        _login_failures.pop(ip, None)


def login_rate_limited(ip: str) -> bool:
    _prune_failures(ip)
    return len(_login_failures.get(ip, [])) >= _LOGIN_MAX_FAILURES


def record_login_failure(ip: str) -> None:
    _prune_failures(ip)
    _login_failures.setdefault(ip, []).append(time.time())


def clear_login_failures(ip: str) -> None:
    _login_failures.pop(ip, None)


def list_users() -> list[dict[str, Any]]:
    conn = _ensure_db()
    try:
        rows = conn.execute(
            """
            SELECT username, display_name, role, enabled, must_change_password,
                   created_at, updated_at
            FROM users ORDER BY username
            """
        ).fetchall()
        out = []
        for row in rows:
            tiles = [
                r["tile_id"]
                for r in conn.execute(
                    "SELECT tile_id FROM user_tiles WHERE username = ? ORDER BY tile_id",
                    (row["username"],),
                ).fetchall()
            ]
            out.append(
                {
                    "username": row["username"],
                    "display_name": row["display_name"] or row["username"],
                    "role": row["role"],
                    "enabled": bool(row["enabled"]),
                    "must_change_password": bool(row["must_change_password"])
                    if "must_change_password" in row.keys()
                    else False,
                    "tiles": tiles,
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return out
    finally:
        conn.close()


def count_enabled_super_admins(exclude_username: Optional[str] = None) -> int:
    conn = _ensure_db()
    try:
        if exclude_username:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n FROM users
                WHERE role = ? AND enabled = 1 AND username != ?
                """,
                (ROLE_SUPER_ADMIN, exclude_username),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE role = ? AND enabled = 1",
                (ROLE_SUPER_ADMIN,),
            ).fetchone()
        return int(row["n"] if row else 0)
    finally:
        conn.close()


def create_user(
    username: str,
    password: str,
    display_name: str = "",
    role: str = ROLE_USER,
    tiles: Optional[list[str]] = None,
    enabled: bool = True,
) -> dict[str, Any]:
    username = (username or "").strip()
    if not username:
        raise ValueError("Username is required.")
    if not password or len(password) < 6:
        raise ValueError("Password must be at least 6 characters.")
    if role not in (ROLE_USER, ROLE_SUPER_ADMIN):
        raise ValueError("Invalid role.")
    tile_ids = [t for t in (tiles or []) if t in ALL_TILE_IDS]
    if get_user_record(username):
        raise ValueError("Username already exists.")
    ts = _now()
    conn = _ensure_db()
    try:
        conn.execute(
            """
            INSERT INTO users (
                username, password_hash, display_name, role, enabled,
                must_change_password, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                username,
                hash_password(password),
                (display_name or username).strip(),
                role,
                1 if enabled else 0,
                ts,
                ts,
            ),
        )
        for tile_id in tile_ids:
            conn.execute(
                "INSERT INTO user_tiles (username, tile_id) VALUES (?, ?)",
                (username, tile_id),
            )
        conn.commit()
    finally:
        conn.close()
    record = get_user_record(username)
    assert record is not None
    return {k: v for k, v in record.items() if k != "password_hash"}


USER_CSV_TEMPLATE = """username,password,display_name,role,enabled,tiles
jdoe,ChangeMe123!,Jane Doe,user,1,"device|sub|user"
asmith,TempPass99,Alex Smith,user,1,"roles|sso-tools"
mentor1,Welcome1!,Mentor One,user,1,mentor-tool
"""


def _parse_bool(value: Any, default: bool = True) -> bool:
    if value is None or str(value).strip() == "":
        return default
    s = str(value).strip().lower()
    if s in ("1", "true", "yes", "y", "on", "enabled"):
        return True
    if s in ("0", "false", "no", "n", "off", "disabled"):
        return False
    return default


def _parse_tiles(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(t).strip() for t in value if str(t).strip()]
    raw = str(value).strip()
    if not raw:
        return []
    for sep in ("|", ";", ","):
        if sep in raw:
            return [p.strip() for p in raw.split(sep) if p.strip()]
    return [raw]


def _normalize_csv_headers(fieldnames: Optional[list[str]]) -> dict[str, str]:
    aliases = {
        "username": "username",
        "user": "username",
        "user_name": "username",
        "login": "username",
        "password": "password",
        "temp_password": "password",
        "temporary_password": "password",
        "display_name": "display_name",
        "displayname": "display_name",
        "name": "display_name",
        "full_name": "display_name",
        "role": "role",
        "enabled": "enabled",
        "active": "enabled",
        "status": "enabled",
        "tiles": "tiles",
        "tile": "tiles",
        "permissions": "tiles",
        "access": "tiles",
    }
    mapping: dict[str, str] = {}
    for name in fieldnames or []:
        key = str(name or "").strip().lower().replace(" ", "_")
        if key in aliases:
            mapping[name] = aliases[key]
    return mapping


def create_users_from_csv(text: str) -> dict[str, Any]:
    """
    Bulk-create users from CSV text.
    Required: username, password
    Optional: display_name, role (user|super_admin), enabled, tiles
    """
    if not (text or "").strip():
        raise ValueError("CSV is empty.")
    if text.startswith("\ufeff"):
        text = text[1:]

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSV has no header row.")

    header_map = _normalize_csv_headers(list(reader.fieldnames))
    if "username" not in header_map.values() or "password" not in header_map.values():
        raise ValueError(
            "CSV must include username and password columns "
            "(optional: display_name, role, enabled, tiles)."
        )

    created: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    skipped = 0
    row_num = 1

    for raw in reader:
        row_num += 1
        row = {header_map.get(k, k): (v if v is not None else "") for k, v in raw.items()}
        username = str(row.get("username") or "").strip()
        password = str(row.get("password") or "").strip()
        if not username and not password:
            skipped += 1
            continue
        try:
            role_raw = str(row.get("role") or ROLE_USER).strip().lower().replace(" ", "_")
            if role_raw in ("admin", "superadmin", "super-admin"):
                role_raw = ROLE_SUPER_ADMIN
            user = create_user(
                username=username,
                password=password,
                display_name=str(row.get("display_name") or "").strip(),
                role=role_raw or ROLE_USER,
                tiles=_parse_tiles(row.get("tiles")),
                enabled=_parse_bool(row.get("enabled"), True),
            )
            created.append({"row": row_num, "username": user["username"]})
        except ValueError as exc:
            errors.append({"row": row_num, "username": username or "(blank)", "error": str(exc)})

    return {
        "created_count": len(created),
        "error_count": len(errors),
        "skipped_count": skipped,
        "created": created,
        "errors": errors[:100],
    }


def update_user(
    username: str,
    *,
    display_name: Optional[str] = None,
    role: Optional[str] = None,
    enabled: Optional[bool] = None,
    tiles: Optional[list[str]] = None,
) -> dict[str, Any]:
    existing = get_user_record(username)
    if not existing:
        raise ValueError("User not found.")
    new_role = role if role is not None else existing["role"]
    new_enabled = existing["enabled"] if enabled is None else bool(enabled)
    if new_role not in (ROLE_USER, ROLE_SUPER_ADMIN):
        raise ValueError("Invalid role.")
    # Prevent removing last super admin
    demoting = existing["role"] == ROLE_SUPER_ADMIN and (
        new_role != ROLE_SUPER_ADMIN or not new_enabled
    )
    if demoting and count_enabled_super_admins(exclude_username=existing["username"]) < 1:
        raise ValueError("Cannot disable or demote the last super admin.")
    ts = _now()
    conn = _ensure_db()
    try:
        if display_name is not None:
            conn.execute(
                "UPDATE users SET display_name = ?, updated_at = ? WHERE username = ?",
                (display_name.strip() or existing["username"], ts, username),
            )
        if role is not None:
            conn.execute(
                "UPDATE users SET role = ?, updated_at = ? WHERE username = ?",
                (new_role, ts, username),
            )
        if enabled is not None:
            conn.execute(
                "UPDATE users SET enabled = ?, updated_at = ? WHERE username = ?",
                (1 if new_enabled else 0, ts, username),
            )
        if tiles is not None:
            tile_ids = [t for t in tiles if t in ALL_TILE_IDS]
            conn.execute(
                "DELETE FROM user_tiles WHERE username = ?",
                (username,),
            )
            for tile_id in tile_ids:
                conn.execute(
                    "INSERT INTO user_tiles (username, tile_id) VALUES (?, ?)",
                    (existing["username"], tile_id),
                )
            conn.execute(
                "UPDATE users SET updated_at = ? WHERE username = ?",
                (ts, username),
            )
        conn.commit()
    finally:
        conn.close()
    record = get_user_record(username)
    assert record is not None
    return {k: v for k, v in record.items() if k != "password_hash"}


def set_user_password(
    username: str,
    password: str,
    *,
    must_change: bool = True,
) -> None:
    if not password or len(password) < 6:
        raise ValueError("Password must be at least 6 characters.")
    existing = get_user_record(username)
    if not existing:
        raise ValueError("User not found.")
    conn = _ensure_db()
    try:
        conn.execute(
            """
            UPDATE users
            SET password_hash = ?, must_change_password = ?, updated_at = ?
            WHERE username = ?
            """,
            (hash_password(password), 1 if must_change else 0, _now(), username),
        )
        conn.commit()
    finally:
        conn.close()


def change_own_password(username: str, new_password: str, confirm_password: str) -> None:
    """First-login / self-service password set. Clears must_change_password."""
    if not new_password or len(new_password) < 6:
        raise ValueError("Password must be at least 6 characters.")
    if new_password != confirm_password:
        raise ValueError("Passwords do not match.")
    existing = get_user_record(username)
    if not existing:
        raise ValueError("User not found.")
    if verify_password(new_password, existing["password_hash"]):
        raise ValueError("Choose a new password different from the temporary one.")
    set_user_password(username, new_password, must_change=False)


def delete_user(username: str) -> None:
    existing = get_user_record(username)
    if not existing:
        raise ValueError("User not found.")
    if existing["role"] == ROLE_SUPER_ADMIN and existing["enabled"]:
        if count_enabled_super_admins(exclude_username=existing["username"]) < 1:
            raise ValueError("Cannot delete the last super admin.")
    conn = _ensure_db()
    try:
        conn.execute(
            "DELETE FROM user_tiles WHERE username = ?",
            (username,),
        )
        conn.execute(
            "DELETE FROM users WHERE username = ?",
            (username,),
        )
        conn.commit()
    finally:
        conn.close()


def require_login_json(fn: Callable):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = read_session()
        if not user:
            return jsonify({"error": "Authentication required.", "login": "/login"}), 401
        if user.get("must_change_password"):
            return jsonify({
                "error": "Password change required.",
                "redirect": "/change-password",
            }), 403
        return fn(user, *args, **kwargs)

    return wrapper


def require_super_admin_json(fn: Callable):
    @wraps(fn)
    def wrapper(*args, **kwargs):
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
        return fn(user, *args, **kwargs)

    return wrapper


def check_page_access(path: str) -> Optional[tuple]:
    """
    Return a Flask response (redirect/403) if access denied, else None.
    Used by greenlake_flask_app route handlers.
    """
    user = read_session()
    if path in ("/login", "/change-password"):
        return None
    if path.startswith("/api/auth/") or path in ("/logout",):
        return None
    if path.startswith("/api/admin/"):
        if not user:
            return jsonify({"error": "Authentication required."}), 401
        if not is_super_admin(user):
            return jsonify({"error": "Super admin access required."}), 403
        if user.get("must_change_password"):
            return jsonify({
                "error": "Password change required.",
                "redirect": "/change-password",
            }), 403
        return None

    # Force first-login password change before any tools / admin pages
    if user and user.get("must_change_password"):
        accept = request.headers.get("Accept", "")
        if "application/json" in accept and "text/html" not in accept:
            return jsonify({
                "error": "Password change required.",
                "redirect": "/change-password",
            }), 403
        return redirect("/change-password")

    # Home / tools pages
    if path in PAGE_TILE_MAP:
        if not user:
            return redirect("/login?next=" + path)
        tile = PAGE_TILE_MAP[path]
        if path in ("/admin/users", "/admin/audit", "/admin/audit-dashboard"):
            if not is_super_admin(user):
                return redirect("/GreenLakeTools.html")
            return None
        if not user_has_tile(user, tile):
            accept = request.headers.get("Accept", "")
            if "text/html" in accept or request.method == "GET":
                return redirect("/GreenLakeTools.html")
            return jsonify({"error": "You do not have access to this tool."}), 403
        return None

    # API tile checks
    for prefix, tile_id in API_TILE_PREFIXES:
        if path == prefix or path.startswith(prefix):
            if not user:
                return jsonify({"error": "Authentication required.", "login": "/login"}), 401
            if not user_has_tile(user, tile_id):
                return jsonify({"error": "You do not have access to this API."}), 403
            return None

    return None


# ── Routes ────────────────────────────────────────────────────────────────────


@platform_auth_bp.route("/login", methods=["GET"])
def login_page():
    user = read_session()
    if user:
        if user.get("must_change_password"):
            return redirect("/change-password")
        nxt = request.args.get("next") or "/GreenLakeTools.html"
        if not nxt.startswith("/"):
            nxt = "/GreenLakeTools.html"
        return redirect(nxt)
    return send_from_directory(str(BASE_DIR), "PlatformLogin.html")


@platform_auth_bp.route("/login", methods=["POST"])
def login_submit():
    ip = _client_ip()
    if login_rate_limited(ip):
        # Prefer JSON for fetch clients; form posts get HTML redirect with query
        if request.is_json or request.headers.get("Accept", "").find("application/json") >= 0:
            return jsonify({"ok": False, "error": "Too many failed attempts. Try again later."}), 429
        return redirect("/login?error=rate")

    data = request.get_json(silent=True) if request.is_json else None
    if data:
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        nxt = data.get("next") or "/GreenLakeTools.html"
    else:
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        nxt = request.form.get("next") or request.args.get("next") or "/GreenLakeTools.html"

    if not nxt.startswith("/"):
        nxt = "/GreenLakeTools.html"

    user = authenticate_user(username, password)
    if not user:
        # Dummy bcrypt work to reduce timing oracle on missing users
        verify_password(password or "x", _DUMMY_HASH)
        record_login_failure(ip)
        if data is not None or request.is_json:
            return jsonify({"ok": False, "error": "Invalid username or password."}), 401
        return redirect("/login?error=invalid&next=" + nxt)

    clear_login_failures(ip)
    redirect_to = "/change-password" if user.get("must_change_password") else nxt
    if data is not None or request.is_json:
        resp = make_response(jsonify({
            "ok": True,
            "user": {
                "username": user["username"],
                "display_name": user["display_name"],
                "role": user["role"],
                "tiles": effective_tiles(user),
                "must_change_password": bool(user.get("must_change_password")),
            },
            "redirect": redirect_to,
            "must_change_password": bool(user.get("must_change_password")),
        }))
        set_session_cookie(resp, user)
        return resp

    resp = make_response(redirect(redirect_to))
    set_session_cookie(resp, user)
    return resp


@platform_auth_bp.route("/change-password", methods=["GET"])
def change_password_page():
    user = read_session()
    if not user:
        return redirect("/login?next=/change-password")
    if not user.get("must_change_password"):
        return redirect("/GreenLakeTools.html")
    return send_from_directory(str(BASE_DIR), "PlatformChangePassword.html")


@platform_auth_bp.route("/api/auth/change-password", methods=["POST"])
def api_change_password():
    user = read_session()
    if not user:
        return jsonify({"ok": False, "error": "Authentication required.", "login": "/login"}), 401
    data = request.get_json(silent=True) or {}
    try:
        change_own_password(
            user["username"],
            data.get("password") or data.get("new_password") or "",
            data.get("confirm_password") or data.get("confirm") or "",
        )
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    # Refresh session after password change
    refreshed = get_user_record(user["username"])
    assert refreshed is not None
    payload = {
        "username": refreshed["username"],
        "display_name": refreshed["display_name"],
        "role": refreshed["role"],
        "tiles": list(refreshed["tiles"]),
        "must_change_password": False,
    }
    resp = make_response(jsonify({
        "ok": True,
        "redirect": "/GreenLakeTools.html",
        "user": {
            "username": payload["username"],
            "display_name": payload["display_name"],
            "role": payload["role"],
            "tiles": effective_tiles(payload),
            "must_change_password": False,
        },
    }))
    set_session_cookie(resp, payload)
    return resp


@platform_auth_bp.route("/logout", methods=["GET", "POST"])
def logout():
    resp = make_response(redirect("/login"))
    clear_session_cookie(resp)
    return resp


@platform_auth_bp.route("/api/auth/me", methods=["GET"])
def auth_me():
    user = read_session()
    if not user:
        return jsonify({"authenticated": False}), 401
    return jsonify(
        {
            "authenticated": True,
            "username": user["username"],
            "display_name": user["display_name"],
            "role": user["role"],
            "is_super_admin": is_super_admin(user),
            "must_change_password": bool(user.get("must_change_password")),
            "tiles": effective_tiles(user),
            "tile_catalog": TILE_CATALOG,
        }
    )


@platform_auth_bp.route("/admin/users", methods=["GET"])
def admin_users_page():
    user = read_session()
    if not user:
        return redirect("/login?next=/admin/users")
    if user.get("must_change_password"):
        return redirect("/change-password")
    if not is_super_admin(user):
        return redirect("/GreenLakeTools.html")
    return send_from_directory(str(BASE_DIR), "PlatformAdmin.html")


@platform_auth_bp.route("/api/admin/tiles", methods=["GET"])
@require_super_admin_json
def admin_tile_catalog(user):
    return jsonify({"tiles": TILE_CATALOG})


@platform_auth_bp.route("/api/admin/users", methods=["GET"])
@require_super_admin_json
def admin_list_users(user):
    return jsonify({"users": list_users(), "tiles": TILE_CATALOG})


@platform_auth_bp.route("/api/admin/users", methods=["POST"])
@require_super_admin_json
def admin_create_user(user):
    data = request.get_json(silent=True) or {}
    try:
        created = create_user(
            username=data.get("username") or "",
            password=data.get("password") or "",
            display_name=data.get("display_name") or "",
            role=data.get("role") or ROLE_USER,
            tiles=data.get("tiles") or [],
            enabled=bool(data.get("enabled", True)),
        )
        return jsonify({"ok": True, "user": created}), 201
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@platform_auth_bp.route("/api/admin/users/csv-template", methods=["GET"])
@require_super_admin_json
def admin_users_csv_template(user):
    from flask import Response

    return Response(
        USER_CSV_TEMPLATE,
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=platform_users_template.csv"
        },
    )


@platform_auth_bp.route("/api/admin/users/bulk", methods=["POST"])
@require_super_admin_json
def admin_users_bulk(user):
    """Upload a CSV file (multipart field `file`) or JSON `{csv: "..."}`."""
    text = ""
    if request.files.get("file"):
        raw = request.files["file"].read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
    else:
        data = request.get_json(silent=True) or {}
        text = data.get("csv") or data.get("text") or ""

    try:
        result = create_users_from_csv(text)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    return jsonify({"ok": True, **result})


@platform_auth_bp.route("/api/admin/users/<username>", methods=["PATCH"])
@require_super_admin_json
def admin_patch_user(user, username: str):
    data = request.get_json(silent=True) or {}
    try:
        updated = update_user(
            username,
            display_name=data.get("display_name") if "display_name" in data else None,
            role=data.get("role") if "role" in data else None,
            enabled=data.get("enabled") if "enabled" in data else None,
            tiles=data.get("tiles") if "tiles" in data else None,
        )
        return jsonify({"ok": True, "user": updated})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@platform_auth_bp.route("/api/admin/users/<username>/password", methods=["POST"])
@require_super_admin_json
def admin_set_password(user, username: str):
    data = request.get_json(silent=True) or {}
    try:
        set_user_password(username, data.get("password") or "")
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@platform_auth_bp.route("/api/admin/users/<username>", methods=["DELETE"])
@require_super_admin_json
def admin_delete_user(user, username: str):
    try:
        delete_user(username)
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


def install_auth_guards(app) -> None:
    """Register before_request guard on the Flask app for pages + APIs."""

    @app.before_request
    def _platform_auth_guard():
        # Skip static-ish assets and auth endpoints themselves
        path = request.path or "/"
        if path.startswith("/static") or path.endswith(
            (".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2", ".map")
        ):
            return None
        if path.startswith("/api/admin/"):
            return None
        if path in (
            "/login",
            "/logout",
            "/change-password",
            "/favicon.ico",
            "/favicon.png",
            "/PlatformLogin.html",
            "/PlatformChangePassword.html",
            "/PlatformAdmin.html",
            "/PlatformAudit.html",
            "/PlatformAuditDashboard.html",
        ):
            return None
        if path.startswith("/api/auth/"):
            return None
        # Admin APIs / pages still go through check_page_access
        return check_page_access(path)
