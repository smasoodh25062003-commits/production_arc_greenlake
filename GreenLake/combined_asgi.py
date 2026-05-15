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
_sso_asgi = WsgiToAsgi(build_sso_tools_app())


class _LazyGldashASGI:
    """Defer `app.main` import until first request under /gldash (heavy pycentral load)."""

    __slots__ = ("_inner",)

    def __init__(self) -> None:
        self._inner = None

    async def __call__(self, scope, receive, send):
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
