"""Admin-only Platform Tools activity viewer."""
from __future__ import annotations

import os

from flask import Blueprint, jsonify, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from platform_activity import get_activities_for_actor, get_stats, get_user_summaries

activity_bp = Blueprint("activity", __name__)

SECRET_KEY = os.environ.get("SESSION_SECRET_KEY", "dev-secret-change-in-production")
SESSION_COOKIE = "gl_session"
SESSION_MAX_AGE = 8 * 3600
ROLE_HIERARCHY = {"viewer": 0, "operator": 1, "admin": 2}

_serializer = URLSafeTimedSerializer(SECRET_KEY)


def _read_session() -> dict | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        return _serializer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def _is_admin(user: dict | None) -> bool:
    if not user:
        return False
    role = user.get("role", "viewer")
    return ROLE_HIERARCHY.get(role, -1) >= ROLE_HIERARCHY.get("admin", 2)


def _admin_required():
    user = _read_session()
    if not _is_admin(user):
        return None, (jsonify({"error": "Admin login required. Sign in at /gldash/login first."}), 403)
    return user, None


@activity_bp.route("/api/admin/activity/me", methods=["GET"])
def activity_me():
    user = _read_session()
    if not user:
        return jsonify({"authenticated": False, "admin": False})
    return jsonify({
        "authenticated": True,
        "admin": _is_admin(user),
        "username": user.get("username"),
        "display_name": user.get("display_name"),
        "role": user.get("role"),
    })


@activity_bp.route("/api/admin/activity/stats", methods=["GET"])
def activity_stats():
    _, err = _admin_required()
    if err:
        return err
    return jsonify(get_stats())


@activity_bp.route("/api/admin/activity/users", methods=["GET"])
def activity_users():
    _, err = _admin_required()
    if err:
        return err
    return jsonify({"users": get_user_summaries(limit=200)})


@activity_bp.route("/api/admin/activity/user/<path:actor>", methods=["GET"])
def activity_user_detail(actor: str):
    _, err = _admin_required()
    if err:
        return err
    return jsonify({"actor": actor, "activities": get_activities_for_actor(actor, limit=150)})
