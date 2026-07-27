"""Session management using itsdangerous signed cookies (no Redis needed for this approach).

Also accepts Platform Tools ``pt_session`` so Mentor tools do not require a second
``/gldash/login`` after signing in at ``/login``.
"""
import os
import sys
from pathlib import Path
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import Request, Response
from typing import Optional

SECRET_KEY = os.environ.get("SESSION_SECRET_KEY", "dev-secret-change-in-production")
SESSION_COOKIE = "gl_session"
PLATFORM_COOKIE = "pt_session"
SESSION_MAX_AGE = 8 * 3600  # 8 hours

_serializer = URLSafeTimedSerializer(SECRET_KEY)
_platform_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="platform-tools-auth")

# GreenLake app root (parent of gldashboard_bundle)
_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def create_session_cookie(user: dict) -> str:
    """Create a signed session token containing user info."""
    return _serializer.dumps(user)


def _read_gl_session(request: Request) -> Optional[dict]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        user = _serializer.loads(token, max_age=SESSION_MAX_AGE)
        if isinstance(user, dict) and user.get("username"):
            user = dict(user)
            user.setdefault("auth_source", "gldash")
            return user
    except (BadSignature, SignatureExpired):
        return None
    return None


def _read_platform_session(request: Request) -> Optional[dict]:
    """Map Platform Tools pt_session → dashboard user shape."""
    token = request.cookies.get(PLATFORM_COOKIE)
    if not token:
        return None
    try:
        data = _platform_serializer.loads(token, max_age=SESSION_MAX_AGE)
        if not isinstance(data, dict) or not data.get("username"):
            return None
    except (BadSignature, SignatureExpired):
        return None

    try:
        from platform_auth import (
            ROLE_SUPER_ADMIN,
            effective_tiles,
            get_user_record,
            is_super_admin,
        )

        record = get_user_record(data["username"])
        if not record or not record.get("enabled"):
            return None
        tiles = effective_tiles(record)
        role = "admin" if is_super_admin(record) else "operator"
        return {
            "username": record["username"],
            "display_name": record.get("display_name") or record["username"],
            "role": role,
            "auth_source": "platform",
            "tiles": tiles,
            "platform_role": record.get("role"),
            "must_change_password": bool(record.get("must_change_password")),
        }
    except Exception:
        # Fallback from cookie payload alone
        role = "admin" if data.get("role") == "super_admin" else "operator"
        return {
            "username": data["username"],
            "display_name": data.get("display_name") or data["username"],
            "role": role,
            "auth_source": "platform",
            "tiles": [],
            "platform_role": data.get("role"),
        }


def read_session(request: Request) -> Optional[dict]:
    """Read gl_session, or fall back to Platform Tools pt_session."""
    user = _read_gl_session(request)
    if user:
        return user
    return _read_platform_session(request)


def user_has_platform_tile(user: Optional[dict], tile_id: str) -> bool:
    """True if platform user has tile (super_admin has all). Gldash users: True."""
    if not user:
        return False
    if user.get("auth_source") != "platform":
        return True
    if user.get("platform_role") == "super_admin" or user.get("role") == "admin":
        return True
    return tile_id in (user.get("tiles") or [])


def _cookie_path() -> str:
    p = (os.environ.get("GL_PREFIX") or "").rstrip("/") or "/"
    return p if p.startswith("/") else "/" + p


def set_session(response: Response, user: dict):
    """Write the signed session cookie to the response."""
    token = create_session_cookie(user)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=SESSION_MAX_AGE,
        path=_cookie_path(),
    )


def clear_session(response: Response):
    """Delete the session cookie."""
    response.delete_cookie(key=SESSION_COOKIE, path=_cookie_path())
