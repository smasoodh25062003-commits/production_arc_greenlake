from pathlib import Path
import os

from fastapi import FastAPI, Request, Form
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from app.api.endpoints import router as api_router
from app.api.routers import devices as devices_router
from app.core.client import get_glp_client
from app.auth.users import authenticate_user
from app.auth.session import read_session, set_session, clear_session
from app.auth.rbac import get_current_user, require_role
from app.audit.logger import get_recent_logs, get_log_stats
from app.feedback.logger import get_recent_feedback, get_feedback_stats
from app.monitoring.traffic import log_request, get_traffic_stats
from app.monitoring.server_health import get_server_health

_BUNDLE_ROOT = Path(__file__).resolve().parent.parent
_APP_DIR = _BUNDLE_ROOT / "app"


def _gl_prefix() -> str:
    return (os.environ.get("GL_PREFIX") or "").rstrip("/") or ""


def _url(path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    pfx = _gl_prefix()
    return pfx + path if pfx else path


app = FastAPI(title="GreenLake Dashboard")

app.mount(
    "/static",
    StaticFiles(directory=str(_APP_DIR / "static")),
    name="static",
)

templates = Jinja2Templates(directory=str(_APP_DIR / "templates"))

app.include_router(api_router, prefix="/api")
from app.api.routers.reports import router as reports_router
from app.api.routers.bulk import router as bulk_router
from app.api.routers.auth import router as auth_router
from app.api.routers.ccs_manager import router as ccs_router
from app.api.routers.feedback import router as feedback_router

app.include_router(devices_router.router, prefix="/api/devices", tags=["devices"])
app.include_router(reports_router, prefix="/api/reports", tags=["reports"])
app.include_router(bulk_router, prefix="/api/bulk", tags=["bulk"])
app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(ccs_router, prefix="/api/ccs", tags=["ccs-manager"])
app.include_router(feedback_router, prefix="/api/feedback", tags=["feedback"])
from app.api.routers import sites_groups

app.include_router(sites_groups.router, prefix="/api", tags=["sites-groups"])

@app.middleware("http")
async def _traffic_middleware(request: Request, call_next):
    """Best-effort request tracking for the admin monitoring page."""
    # Skip static assets early (avoid noise + extra IO)
    path = request.url.path or ""
    if path.startswith("/static") or path.endswith((".css", ".js", ".map", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2")):
        return await call_next(request)

    from time import perf_counter

    t0 = perf_counter()
    response = await call_next(request)
    dur_ms = int((perf_counter() - t0) * 1000)

    user = read_session(request) or {}
    username = user.get("username") if isinstance(user, dict) else None
    role = user.get("role") if isinstance(user, dict) else None
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")

    log_request(
        path=path,
        method=request.method,
        status_code=getattr(response, "status_code", 0) or 0,
        duration_ms=dur_ms,
        username=username,
        role=role,
        ip=ip,
        user_agent=ua,
    )
    return response


# ── Auth Routes ────────────────────────────────────────────────────────────────


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = read_session(request)
    if user:
        return RedirectResponse(url=_url("/"), status_code=302)
    return templates.TemplateResponse(
        request, "login.html", _ctx(request, error=None)
    )


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    user = authenticate_user(username, password)
    if not user:
        return templates.TemplateResponse(
            request,
            "login.html",
            _ctx(request, error="Invalid username or password."),
            status_code=401,
        )
    response = RedirectResponse(url=_url("/"), status_code=302)
    set_session(response, user)
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse(url=_url("/login"), status_code=302)
    clear_session(response)
    return response


# ── Helper: session-aware template context ─────────────────────────────────────


def _ctx(request: Request, **kwargs):
    """Build a template context that always includes the current user."""
    user = read_session(request)
    return {
        "request": request,
        "current_user": user,
        "gl_prefix": _gl_prefix(),
        **kwargs,
    }


def _require_login(request: Request):
    """Return user or redirect to login for page routes."""
    user = read_session(request)
    if not user:
        return None
    return user


# ── Page Routes ────────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    user = _require_login(request)
    if not user:
        return RedirectResponse(url=_url("/login"), status_code=302)
    client = get_glp_client()
    configured = client is not None
    return templates.TemplateResponse(
        request, "index.html", _ctx(request, configured=configured)
    )


@app.get("/devices", response_class=HTMLResponse)
async def read_devices(request: Request):
    user = _require_login(request)
    if not user:
        return RedirectResponse(url=_url("/login"), status_code=302)
    client = get_glp_client()
    configured = client is not None
    devices = []
    if configured:
        from pycentral.glp.devices import Devices
        from pycentral.glp.subscriptions import Subscriptions

        try:
            devices_api = Devices()
            subs_api = Subscriptions()
            devices = devices_api.get_all_devices(client)
            try:
                subscriptions = subs_api.get_all_subscriptions(client)
                sub_map = {s.get("key"): s for s in subscriptions if s.get("key")}
                from datetime import datetime

                now = datetime.utcnow()
                for device in devices:
                    dev_sub_data = device.get("subscription")
                    if isinstance(dev_sub_data, list) and len(dev_sub_data) > 0:
                        dev_sub = dev_sub_data[0]
                        device["subscription"] = dev_sub
                    elif isinstance(dev_sub_data, dict):
                        dev_sub = dev_sub_data
                    else:
                        continue
                    sub_key = dev_sub.get("key")
                    if sub_key and sub_key in sub_map:
                        full_sub = sub_map[sub_key]
                        dev_sub["startsAt"] = full_sub.get("startsAt")
                        dev_sub["expiresAt"] = full_sub.get("expiresAt")
                        dev_sub["status"] = full_sub.get("status")
                        dev_sub["tier"] = full_sub.get("tier")
                        dev_sub["skuDescription"] = full_sub.get(
                            "skuDescription", full_sub.get("description", "N/A")
                        )
                        dev_sub["subscriptionStatus"] = full_sub.get(
                            "subscriptionStatus"
                        )
                        dev_sub["availableQuantity"] = full_sub.get("availableQuantity")
                        dev_sub["quantity"] = full_sub.get("quantity")
                        expires_at = full_sub.get("expiresAt")
                        if expires_at:
                            try:
                                dt_str = expires_at.replace("Z", "")
                                if "T" in dt_str:
                                    exp_dt = datetime.fromisoformat(dt_str)
                                else:
                                    exp_dt = datetime.strptime(
                                        dt_str.split(" ")[0], "%Y-%m-%d"
                                    )
                                dev_sub["calculatedStatus"] = (
                                    "Expired" if exp_dt < now else "Active"
                                )
                            except Exception:
                                dev_sub["calculatedStatus"] = "Active"
                        else:
                            dev_sub["calculatedStatus"] = "Active"
            except Exception as sub_err:
                print(f"Error enriching subscriptions: {sub_err}")
        except Exception as e:
            print(f"Error fetching devices: {e}")
    return templates.TemplateResponse(
        request, "devices.html", _ctx(request, configured=configured, devices=devices)
    )


@app.get("/reports", response_class=HTMLResponse)
async def read_reports(request: Request):
    user = _require_login(request)
    if not user:
        return RedirectResponse(url=_url("/login"), status_code=302)
    client = get_glp_client()
    return templates.TemplateResponse(
        request, "reports.html", _ctx(request, configured=client is not None)
    )


@app.get("/bulk", response_class=HTMLResponse)
async def read_bulk(request: Request):
    user = _require_login(request)
    if not user:
        return RedirectResponse(url=_url("/login"), status_code=302)
    client = get_glp_client()
    return templates.TemplateResponse(
        request, "bulk.html", _ctx(request, configured=client is not None)
    )


@app.get("/sites-groups", response_class=HTMLResponse)
async def read_sites_groups(request: Request):
    user = _require_login(request)
    if not user:
        return RedirectResponse(url=_url("/login"), status_code=302)
    client = get_glp_client()
    return templates.TemplateResponse(
        request, "sites_groups.html", _ctx(request, configured=client is not None)
    )


# ── CCS Manager Routes (login required) ───────────────────────────────────────


@app.get("/ccs-manager")
async def redirect_ccs_manager(request: Request):
    user = _require_login(request)
    if not user:
        return RedirectResponse(url=_url("/login"), status_code=302)
    return RedirectResponse(url=_url("/ccs-manager/devices"))


@app.get("/ccs-manager/devices", response_class=HTMLResponse)
async def read_ccs_manager_devices(request: Request):
    user = _require_login(request)
    if not user:
        return RedirectResponse(url=_url("/login"), status_code=302)
    return templates.TemplateResponse(request, "ccs_devices.html", _ctx(request))


@app.get("/ccs-manager/subscriptions", response_class=HTMLResponse)
async def read_ccs_manager_subscriptions(request: Request):
    user = _require_login(request)
    if not user:
        return RedirectResponse(url=_url("/login"), status_code=302)
    return templates.TemplateResponse(request, "ccs_subscriptions.html", _ctx(request))


@app.get("/ccs-manager/users", response_class=HTMLResponse)
async def read_ccs_manager_users(request: Request):
    user = _require_login(request)
    if not user:
        return RedirectResponse(url=_url("/login"), status_code=302)
    return templates.TemplateResponse(request, "ccs_users.html", _ctx(request))


@app.get("/ccs-manager/audit-logs", response_class=HTMLResponse)
async def read_audit_logs(request: Request):
    user = _require_login(request)
    if not user:
        return RedirectResponse(url=_url("/login"), status_code=302)
    from app.auth.users import role_gte

    if not role_gte(user.get("role", "viewer"), "admin"):
        return HTMLResponse("<h2>403 — Admin access required.</h2>", status_code=403)
    logs = get_recent_logs(limit=500)
    stats = get_log_stats()
    return templates.TemplateResponse(
        request, "admin_logs.html", _ctx(request, logs=logs, stats=stats)
    )

@app.get("/ccs-manager/monitoring", response_class=HTMLResponse)
async def read_admin_monitoring(request: Request):
    """Admin-only monitoring: traffic + usage summaries."""
    user = _require_login(request)
    if not user:
        return RedirectResponse(url=_url("/login"), status_code=302)
    from app.auth.users import role_gte

    if not role_gte(user.get("role", "viewer"), "admin"):
        return HTMLResponse("<h2>403 — Admin access required.</h2>", status_code=403)

    traffic = get_traffic_stats()
    server = get_server_health()

    # Best-effort subscription usage summary (treats quantity-available as "used")
    usage = {"configured": False}
    client = get_glp_client()
    if client is not None:
        usage["configured"] = True
        try:
            from pycentral.glp.subscriptions import Subscriptions

            subs_api = Subscriptions()
            subs = subs_api.get_all_subscriptions(client) or []

            def _to_int(v):
                try:
                    return int(float(v))
                except Exception:
                    return None

            qty_total = 0
            qty_used = 0
            for s in subs:
                q = _to_int(s.get("quantity"))
                a = _to_int(s.get("availableQuantity"))
                if q is None:
                    continue
                qty_total += q
                if a is not None:
                    qty_used += max(0, q - a)

            util_pct = int(round((qty_used / qty_total) * 100)) if qty_total else 0
            usage.update(
                {
                    "subscriptions_total": len(subs),
                    "qty_total": qty_total,
                    "qty_used": qty_used,
                    "util_pct": util_pct,
                }
            )
        except Exception:
            usage["configured"] = False

    return templates.TemplateResponse(
        request,
        "admin_monitoring.html",
        _ctx(request, traffic=traffic, usage=usage, server=server),
    )


@app.get("/mentors/feedback", response_class=HTMLResponse)
async def read_mentor_feedback(request: Request):
    """Admin-only feedback inbox (Mentors module)."""
    user = _require_login(request)
    if not user:
        return RedirectResponse(url=_url("/login"), status_code=302)
    from app.auth.users import role_gte

    if not role_gte(user.get("role", "viewer"), "admin"):
        return HTMLResponse("<h2>403 — Admin access required.</h2>", status_code=403)
    items = get_recent_feedback(limit=500)
    stats = get_feedback_stats()
    return templates.TemplateResponse(
        request, "mentor_feedback.html", _ctx(request, items=items, stats=stats)
    )
