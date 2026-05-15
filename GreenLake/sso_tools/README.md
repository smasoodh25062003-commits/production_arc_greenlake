# SSO Tools (HPE Role String Generator + SAML checker)

This package is the **full migration** of the standalone `stringjoin` project into GreenLake. It lives only under this repo; you do not need the Desktop copy to run it.

## Embedded (recommended)

With the rest of Platform Tools on one port:

1. From the `GreenLake` directory: `pip install -r requirements.txt`
2. Run `python main.py` (or `uvicorn` as documented in `main.py`).
3. Open **Platform Tools → Engineers → SSO Tools**, or browse to **`/sso-tools/`** on the same host and port (for example `http://127.0.0.1:5000/sso-tools/`).

API routes (same-origin under the prefix):

- `POST /sso-tools/api/generate`
- `POST /sso-tools/api/export/txt` and `/json`
- `GET /sso-tools/api/health`, `POST /sso-tools/api/parse` (SAML)

## Standalone (optional, for local debugging only)

Run only this Flask app (no GreenLake dashboard):

```powershell
cd path\to\GreenLake
python -m sso_tools
```

Defaults: `http://127.0.0.1:5051/`. Override with `SSO_TOOLS_PORT` and `SSO_TOOLS_HOST`.

## Layout

```
sso_tools/
  __init__.py
  __main__.py          # python -m sso_tools
  webapp.py            # Flask factory (was stringjoin/app.py)
  saml_validator.py
  templates/
  static/
```

## Original features

- Okta role-assignment strings (with / without groups), live preview, export, history, themes
- SAML IdP metadata validation UI
## Legacy archive

The folder `legacy_exitsing_config/` is a verbatim copy of the old `exitsing config` snapshot from the original Desktop project. It is not wired into the app; keep it only for reference.
