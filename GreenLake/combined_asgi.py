"""
Single ASGI entry: FastAPI GreenLake Dashboard under /gldash/, Flask tools at /.

Run:  uvicorn combined_asgi:application --host 127.0.0.1 --port 5000

Flask serves Platform Tools at /. SSO Tools (Okta + SAML) are mounted at ``/sso-tools/``
by Starlette (not inside the root Flask ``WsgiToAsgi`` wrapper) so paths work reliably.
Or:   python main.py

The bundled dashboard (pycentral, routers) is imported lazily on the first /gldash/*
request so `import combined_asgi` and Flask-only paths stay responsive.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from asgiref.wsgi import WsgiToAsgi
from starlette.applications import Starlette
from starlette.routing import Mount

_ROOT = Path(__file__).resolve().parent
_BUNDLE = _ROOT / "gldashboard_bundle"

os.environ.setdefault("TOKEN_FILE", str(_BUNDLE / "token.yaml"))
os.environ.setdefault("GL_PREFIX", "/gldash")

if str(_BUNDLE) not in sys.path:
    sys.path.insert(0, str(_BUNDLE))

from greenlake_flask_app import build_flask_app
from sso_tools.webapp import build_sso_tools_app

_flask = build_flask_app(mount_sso_via_dispatcher=False)
_flask_asgi = WsgiToAsgi(_flask)
_sso_raw = WsgiToAsgi(build_sso_tools_app())


async def _sso_asgi(scope, receive, send):
    """Require Platform Tools session + sso-tools tile (or super_admin)."""
    if scope["type"] != "http":
        await _sso_raw(scope, receive, send)
        return

    from urllib.parse import quote

    from itsdangerous import BadSignature, SignatureExpired
    from platform_auth import (
        SESSION_COOKIE,
        SESSION_MAX_AGE,
        _serializer,
        get_user_record,
        is_super_admin,
    )

    headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers") or []}
    cookie_header = headers.get("cookie", "")
    token = None
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith(SESSION_COOKIE + "="):
            token = part[len(SESSION_COOKIE) + 1 :]
            break

    allowed = False
    must_change = False
    if token:
        try:
            data = _serializer().loads(token, max_age=SESSION_MAX_AGE)
            if isinstance(data, dict) and data.get("username"):
                record = get_user_record(data["username"])
                if record and record.get("enabled"):
                    must_change = bool(record.get("must_change_password"))
                    if is_super_admin(record) or "sso-tools" in (record.get("tiles") or []):
                        allowed = True
        except (BadSignature, SignatureExpired, Exception):
            allowed = False

    if must_change:
        await send(
            {
                "type": "http.response.start",
                "status": 302,
                "headers": [
                    (b"location", b"/change-password"),
                    (b"content-type", b"text/plain"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b"Password change required"})
        return

    if not allowed:
        loc = "/login?next=" + quote("/sso-tools/")
        await send(
            {
                "type": "http.response.start",
                "status": 302,
                "headers": [
                    (b"location", loc.encode()),
                    (b"content-type", b"text/plain"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b"Authentication required"})
        return

    await _sso_raw(scope, receive, send)


class _LazyGldashASGI:
    """Defer `app.main` import until first request under /gldash (heavy pycentral load)."""

    __slots__ = ("_inner",)

    def __init__(self) -> None:
        self._inner = None

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            from itsdangerous import BadSignature, SignatureExpired
            from platform_auth import (
                SESSION_COOKIE,
                SESSION_MAX_AGE,
                _serializer,
                get_user_record,
            )

            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers") or []}
            cookie_header = headers.get("cookie", "")
            token = None
            for part in cookie_header.split(";"):
                part = part.strip()
                if part.startswith(SESSION_COOKIE + "="):
                    token = part[len(SESSION_COOKIE) + 1 :]
                    break
            if token:
                try:
                    data = _serializer().loads(token, max_age=SESSION_MAX_AGE)
                    if isinstance(data, dict) and data.get("username"):
                        record = get_user_record(data["username"])
                        if record and record.get("enabled") and record.get("must_change_password"):
                            await send(
                                {
                                    "type": "http.response.start",
                                    "status": 302,
                                    "headers": [
                                        (b"location", b"/change-password"),
                                        (b"content-type", b"text/plain"),
                                    ],
                                }
                            )
                            await send(
                                {
                                    "type": "http.response.body",
                                    "body": b"Password change required",
                                }
                            )
                            return
                except (BadSignature, SignatureExpired, Exception):
                    pass

        if self._inner is None:
            from app.main import app as inner_app

            self._inner = inner_app
        await self._inner(scope, receive, send)


application = Starlette(
    routes=[
        Mount("/gldash", _LazyGldashASGI()),
        Mount("/sso-tools", _sso_asgi),
        Mount("/", _flask_asgi),
    ]
)
