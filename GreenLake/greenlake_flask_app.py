"""Flask application factory (Platform Tools) — mounted at site root in combined ASGI mode."""
import os

from flask import Flask, redirect, send_from_directory
from werkzeug.middleware.dispatcher import DispatcherMiddleware

from deviceApp import device_bp
from subscriptionApp import subscription_bp
from userbaseApp import userbase_bp
from ccsTransferApp import ccs_bp
from rolesApp import roles_bp
from serialCheckerApp import serial_checker_bp
from humioApp import humio_bp
from dsatApp import dsat_bp
from platform_auth import platform_auth_bp, install_auth_guards
from platform_audit import platform_audit_bp
from sso_tools.webapp import build_sso_tools_app

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def build_flask_app(*, mount_sso_via_dispatcher: bool = True) -> Flask:
    """Build Platform Tools Flask app.

    When ``mount_sso_via_dispatcher`` is True (default), SSO Tools is mounted at
    ``/sso-tools`` via Werkzeug ``DispatcherMiddleware`` — use this for WSGI-only
    servers (for example ``gunicorn main:app``).

    When False, the returned app has no ``/sso-tools`` mount; the ASGI stack in
    ``combined_asgi`` mounts SSO Tools with ``Starlette.routing.Mount`` instead,
    which avoids PATH_INFO issues with ``WsgiToAsgi``.
    """
    app = Flask(__name__, static_folder=BASE_DIR, static_url_path="")
    from flask_cors import CORS

    CORS(app)

    app.register_blueprint(platform_auth_bp)
    app.register_blueprint(platform_audit_bp)
    app.register_blueprint(device_bp)
    app.register_blueprint(subscription_bp)
    app.register_blueprint(userbase_bp)
    app.register_blueprint(ccs_bp)
    app.register_blueprint(roles_bp)
    app.register_blueprint(serial_checker_bp)
    app.register_blueprint(humio_bp)
    app.register_blueprint(dsat_bp)

    install_auth_guards(app)

    @app.route("/")
    def home():
        return send_from_directory(BASE_DIR, "GreenLakeTools.html")

    @app.route("/GreenLakeTools.html")
    def greenlake_tools():
        return send_from_directory(BASE_DIR, "GreenLakeTools.html")

    @app.route("/favicon.ico")
    def favicon_ico():
        # Serve PNG for /favicon.ico so browsers pick it up automatically.
        return send_from_directory(BASE_DIR, "favicon.png")

    @app.route("/favicon.png")
    def favicon_png():
        return send_from_directory(BASE_DIR, "favicon.png")

    @app.route("/DeviceManagement.html")
    def device_management():
        return send_from_directory(BASE_DIR, "DeviceManagement.html")

    @app.route("/Subscriptionmanagement.html")
    def subscription_management():
        return send_from_directory(BASE_DIR, "Subscriptionmanagement.html")

    @app.route("/UserManagement.html")
    def user_management():
        return send_from_directory(BASE_DIR, "UserManagement.html")

    @app.route("/UserRoles.html")
    def user_roles():
        return send_from_directory(BASE_DIR, "UserRoles.html")

    @app.route("/SerialChecker.html")
    def serial_checker():
        return send_from_directory(BASE_DIR, "SerialChecker.html")

    @app.route("/HumioRplLogs.html")
    def humio_rpl_logs():
        return send_from_directory(BASE_DIR, "HumioRplLogs.html")

    @app.route("/TransferDevices.html")
    def transfer_devices_page():
        return send_from_directory(BASE_DIR, "TransferDevices.html")

    @app.route("/TransferSubscriptions.html")
    def transfer_subscriptions_page():
        return send_from_directory(BASE_DIR, "TransferSubscriptions.html")

    @app.route("/DsatAlertAnalyzer.html")
    def dsat_alert_analyzer():
        """RBAC: DSAT lives under /gldash session cookie path — redirect to mentors page."""
        return redirect("/gldash/mentors/dsat", code=302)

    @app.route("/rohit")
    def mentor_rohit_portal():
        """Rohit: migrated GreenLake Dashboard (FastAPI) lives under /gldash/."""
        return redirect("/gldash/", code=302)

    if mount_sso_via_dispatcher:
        sso = build_sso_tools_app()

        def _sso_guard(environ, start_response):
            """Require Platform Tools session + sso-tools tile before SSO app."""
            # Build a minimal request context for cookie reading is awkward in raw WSGI;
            # parse Cookie header for pt_session and verify via platform_auth helpers.
            from platform_auth import (
                SESSION_COOKIE,
                SESSION_MAX_AGE,
                _serializer,
                get_user_record,
                is_super_admin,
                ALL_TILE_IDS,
            )
            from itsdangerous import BadSignature, SignatureExpired
            from urllib.parse import quote

            raw = environ.get("HTTP_COOKIE") or ""
            token = None
            for part in raw.split(";"):
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
                loc = "/change-password"
                start_response(
                    "302 Found",
                    [("Location", loc), ("Content-Type", "text/plain")],
                )
                return [b"Password change required"]
            if not allowed:
                loc = "/login?next=" + quote("/sso-tools/")
                start_response(
                    "302 Found",
                    [("Location", loc), ("Content-Type", "text/plain")],
                )
                return [b"Authentication required"]
            return sso(environ, start_response)

        app.wsgi_app = DispatcherMiddleware(app.wsgi_app, {"/sso-tools": _sso_guard})

    return app
