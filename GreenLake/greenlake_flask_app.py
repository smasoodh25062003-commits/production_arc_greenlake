"""Flask application factory (Platform Tools) — mounted at site root in combined ASGI mode."""
import os

from flask import Flask, redirect, send_from_directory
from werkzeug.middleware.dispatcher import DispatcherMiddleware

from deviceApp import device_bp
from subscriptionApp import subscription_bp
from userbaseApp import userbase_bp
from ccsTransferApp import ccs_bp
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

    app.register_blueprint(device_bp)
    app.register_blueprint(subscription_bp)
    app.register_blueprint(userbase_bp)
    app.register_blueprint(ccs_bp)

    @app.route("/")
    def home():
        return send_from_directory(BASE_DIR, "GreenLakeTools.html")

    @app.route("/GreenLakeTools.html")
    def greenlake_tools():
        return send_from_directory(BASE_DIR, "GreenLakeTools.html")

    @app.route("/DeviceManagement.html")
    def device_management():
        return send_from_directory(BASE_DIR, "DeviceManagement.html")

    @app.route("/Subscriptionmanagement.html")
    def subscription_management():
        return send_from_directory(BASE_DIR, "Subscriptionmanagement.html")

    @app.route("/UserManagement.html")
    def user_management():
        return send_from_directory(BASE_DIR, "UserManagement.html")

    @app.route("/TransferDevices.html")
    def transfer_devices_page():
        return send_from_directory(BASE_DIR, "TransferDevices.html")

    @app.route("/TransferSubscriptions.html")
    def transfer_subscriptions_page():
        return send_from_directory(BASE_DIR, "TransferSubscriptions.html")

    @app.route("/rohit")
    def mentor_rohit_portal():
        """Rohit: migrated GreenLake Dashboard (FastAPI) lives under /gldash/."""
        return redirect("/gldash/", code=302)

    if mount_sso_via_dispatcher:
        sso = build_sso_tools_app()
        app.wsgi_app = DispatcherMiddleware(app.wsgi_app, {"/sso-tools": sso})

    return app
