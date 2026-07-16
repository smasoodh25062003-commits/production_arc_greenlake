# GreenLake — AI Context Guide

Hand this file to an AI assistant when asking it to work on this repo. It describes architecture, entry points, modules, and conventions so changes stay consistent.

---

## What this project is

Internal **HPE GreenLake Platform Tools** web app used by engineers/support for:

- Device lookup / export
- Subscription lookup
- Workspace / userbase hierarchy
- User roles lookup
- Serial number checker (PDF/TXT)
- CCS device & subscription transfers
- SSO Tools (Okta role strings + SAML metadata validation)
- Embedded **GreenLake Dashboard** (FastAPI, login + pycentral APIs) under `/gldash/`

Stack: **Flask** (Platform Tools HTML + APIs) + **FastAPI** (dashboard) + **Starlette** (combined ASGI mount). Frontends are mostly static HTML/JS/CSS served by Flask.

---

## Repository layout

```
GreenLake/                          ← git root (parent may be production_greenlake_arc)
└── GreenLake/                      ← app root (run commands from here)
    ├── main.py                     ← preferred local entry: uvicorn combined ASGI
    ├── combined_asgi.py            ← single-port ASGI: /gldash + /sso-tools + /
    ├── greenlake_flask_app.py      ← Flask factory (Platform Tools)
    ├── deviceApp.py                ← Blueprint: device lookup APIs
    ├── subscriptionApp.py          ← Blueprint: subscription stream APIs
    ├── userbaseApp.py              ← Blueprint: workspace hierarchy stream
    ├── rolesApp.py                 ← Blueprint: roles stream
    ├── serialCheckerApp.py         ← Blueprint: serial check
    ├── ccsTransferApp.py           ← Blueprint: CCS transfer APIs
    ├── sso_tools/                  ← Flask SSO Tools (Okta + SAML)
    ├── gldashboard_bundle/         ← FastAPI GreenLake Dashboard + vendored pycentral
    ├── *.html                      ← Platform Tools pages
    ├── greenlake-theme.{css,js}    ← shared theme
    ├── greenlake-feedback.js       ← feedback widget
    ├── greenlake-usage.js          ← usage tracking
    ├── token.yaml                  ← GLP credentials (SECRET — do not commit/share)
    ├── requirements.txt
    ├── DEPLOYMENT.md               ← human deployment notes
    └── scripts/                    ← one-off HTML/CSS patch scripts
```

Working directory for run/install: **`GreenLake/GreenLake/`** (the inner folder containing `main.py`).

---

## How to run

```bash
cd GreenLake   # the folder with main.py
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
# or: uvicorn combined_asgi:application --host 127.0.0.1 --port 5000
```

Defaults: `HOST=127.0.0.1`, `PORT=5000`, reload on (`UVICORN_RELOAD=1`).

| URL | Purpose |
|-----|---------|
| `/` | Platform Tools home (`GreenLakeTools.html`) |
| `/DeviceManagement.html` | Device management UI |
| `/Subscriptionmanagement.html` | Subscription UI |
| `/UserManagement.html` | Userbase / workspace UI |
| `/UserRoles.html` | Roles UI |
| `/SerialChecker.html` | Serial checker UI |
| `/TransferDevices.html` | CCS device transfer UI |
| `/TransferSubscriptions.html` | CCS subscription transfer UI |
| `/sso-tools/` | SSO Tools (Okta role strings + SAML) |
| `/gldash/` | FastAPI GreenLake Dashboard |
| `/rohit` | Redirect → `/gldash/` |

**WSGI-only (Flask tools, no dashboard):** `gunicorn main:app` — SSO mounted via Werkzeug `DispatcherMiddleware` at `/sso-tools`.

**Full stack (recommended):** always use `combined_asgi:application` so `/gldash` and `/sso-tools` are Starlette mounts (avoids `WsgiToAsgi` PATH_INFO issues).

---

## Architecture (important)

```
combined_asgi:application  (Starlette)
├── Mount /gldash      → Lazy FastAPI app (gldashboard_bundle/app/main.py)
├── Mount /sso-tools   → Flask SSO Tools via WsgiToAsgi
└── Mount /            → Flask Platform Tools via WsgiToAsgi
```

- `greenlake_flask_app.build_flask_app(mount_sso_via_dispatcher=False)` when used from ASGI (SSO is mounted by Starlette).
- `mount_sso_via_dispatcher=True` (default) when using Flask-only WSGI (`main:app`).
- Dashboard import is **lazy** on first `/gldash/*` request (heavy pycentral load).
- Env set by `combined_asgi`: `TOKEN_FILE` → `gldashboard_bundle/token.yaml`, `GL_PREFIX=/gldash`.

### Flask Platform Tools blueprints

| Module | Blueprint | Key API routes |
|--------|-----------|----------------|
| `deviceApp.py` | `device_bp` | `POST /api/lookup`, `/api/export`, `/api/lookup-stream` |
| `subscriptionApp.py` | `subscription_bp` | `POST /api/subscription-stream` |
| `userbaseApp.py` | `userbase_bp` | `POST /api/workspace-stream` |
| `rolesApp.py` | `roles_bp` | `POST /api/roles-stream` |
| `serialCheckerApp.py` | `serial_checker_bp` | `POST /api/serial-check` |
| `ccsTransferApp.py` | `ccs_bp` | `POST /api/ccs/lookup-devices`, `/api/ccs/transfer-devices`, `/api/ccs/transfer-subscriptions` |

Many APIs are **streaming** (NDJSON / event-style responses) for long-running GreenLake lookups. Prefer streaming patterns when adding similar bulk tools.

### SSO Tools (`sso_tools/`)

- Factory: `sso_tools.webapp.build_sso_tools_app()`
- Standalone debug: `python -m sso_tools` (port `5051` by default)
- APIs (under `/sso-tools`): `POST /api/generate`, `/api/export/txt|json`, `POST /api/parse`, `GET /api/health`
- See `sso_tools/README.md`. Do not wire `legacy_exitsing_config/` into the live app.

### GreenLake Dashboard (`gldashboard_bundle/`)

- FastAPI app: `app.main:app` (path prefix `/gldash` via `GL_PREFIX`)
- Auth: session + RBAC (`app/auth/`), users in `app/config/users.yaml`
- API routers under `app/api/routers/`: devices, reports, bulk, auth, ccs_manager, feedback, usage, sites_groups
- Vendored library: `app/lib/pycentral/` (GLP + classic Central)
- Logs/DBs often under `gldashboard_bundle/logs/` (audit, feedback, usage) — treat as local runtime data

---

## Auth & secrets (critical)

- **Never commit, paste, or log** contents of `token.yaml`, `.env`, or client secrets / access tokens.
- Platform Tools pages typically send GreenLake session headers/cookies from the browser to Flask APIs (user-provided auth), then Flask calls HPE GreenLake APIs server-side.
- Dashboard uses `TOKEN_FILE` / env (`GLP_CLIENT_ID`, `GLP_CLIENT_SECRET`, `GLP_ACCESS_TOKEN`, etc.) via `gldashboard_bundle/app/core/config.py`.
- If asked to “fix credentials,” use placeholders or env vars only.

---

## Frontend conventions

- Home hub: `GreenLakeTools.html` — cards link to tools and `/sso-tools/`, `/gldash/`.
- Shared theme: `greenlake-theme.css` / `greenlake-theme.js` (light/dark).
- Brand colors lean **HPE green** (`#01A982`) — preserve existing visual language when editing UI.
- Prefer same-origin relative API paths where possible (avoid hardcoding `http://localhost:5000` for production).
- One-off HTML patches live in `scripts/` — prefer editing source HTML/CSS directly for lasting fixes.

---

## Conventions for AI code changes

1. **Minimal diffs** — change only what the task requires; no drive-by refactors.
2. **Match existing patterns** — Blueprint + HTML tool for Platform Tools; FastAPI router + Jinja template for dashboard.
3. **Keep mounts correct** — do not mount SSO both via DispatcherMiddleware and Starlette when using `combined_asgi`.
4. **Do not delete** `legacy_*` or backup `.bak` unless explicitly asked.
5. **Do not commit** secrets, logs (`*.log`, `*.db` under `logs/`), or local `.venv`.
6. **Tests** — if present under `tests/`, run the relevant ones after API changes.
7. **Python** — 3.10+; dependencies in `requirements.txt`.

---

## Typical task map

| User asks for… | Start here |
|----------------|------------|
| New Platform Tool page/API | New `*App.py` Blueprint + HTML; register in `greenlake_flask_app.py`; link from `GreenLakeTools.html` |
| Device/subscription lookup bugs | `deviceApp.py` / `subscriptionApp.py` + matching HTML |
| CCS transfer | `ccsTransferApp.py` + `Transfer*.html`; dashboard twin in `gldashboard_bundle/.../ccs_manager.py` |
| SSO / Okta role strings | `sso_tools/webapp.py` + templates/static |
| Dashboard feature | `gldashboard_bundle/app/` (routers, templates, auth) |
| Single-port / routing issues | `combined_asgi.py`, `greenlake_flask_app.py` |
| Deploy / run | `DEPLOYMENT.md`, `main.py`, `Procfile` / `start.bat` |

---

## Quick checklist before finishing a change

- [ ] App still starts via `python main.py`
- [ ] New Flask routes registered; new pages linked from home if user-facing
- [ ] ASGI vs WSGI SSO mounting not double-broken
- [ ] No secrets in code, docs, or commits
- [ ] UI matches existing theme/patterns

---

## One-line summary for the AI

> This is a combined Flask + FastAPI HPE GreenLake internal tools suite: Platform Tools at `/`, SSO at `/sso-tools/`, Dashboard at `/gldash/`, started from `combined_asgi` via `python main.py`; preserve mounts, streaming APIs, and never expose `token.yaml`.
