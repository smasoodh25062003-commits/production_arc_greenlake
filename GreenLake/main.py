"""
GreenLake Platform Tools (Flask) + embedded Rohit / GreenLake Dashboard (FastAPI).

- WSGI entry `app` is the Flask tools app only (for legacy `gunicorn main:app` if needed).
- Full stack (single port): run `python main.py` or `uvicorn combined_asgi:application --host 0.0.0.0 --port 5000`.
- **SSO Tools** (Okta role strings + SAML metadata): mounted at `/sso-tools/` on the same server (see `sso_tools/`).
"""
import os

from greenlake_flask_app import build_flask_app

# Flask-only WSGI app (dashboard is not mounted here)
app = build_flask_app()


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "5000"))
    host = os.environ.get("HOST", "127.0.0.1")
    uvicorn.run(
        "combined_asgi:application",
        host=host,
        port=port,
        reload=os.environ.get("UVICORN_RELOAD", "1") == "1",
    )
