"""Humio / LogScale RPL & embargo status lookup for CCS portal logs."""
from __future__ import annotations

import os
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Blueprint, jsonify, request

load_dotenv(Path(__file__).resolve().parent / ".env")

humio_bp = Blueprint("humio", __name__)

DEFAULT_BASE_URL = "https://aquila-us-east-2.cloudops.common.cloud.hpe.com/logs"
DEFAULT_REPOSITORY = "ccsportal"
ALLOWED_STARTS = frozenset({"1h", "6h", "12h", "1d", "3d", "7d", "30d"})


def _build_query(customer_id: str) -> str:
    return f"""
"{customer_id}"
| regex("embargo_status': '(?<embargo_status>[^']+)'")
| regex("embargo_timestamp': '(?<embargo_timestamp>[^']+)'")
| regex("rpl_status': '(?<rpl_status>[^']+)'")
| regex("rpl_timestamp': '(?<rpl_timestamp>[^']+)'")
| regex("gts_status': '(?<gts_status>[^']+)'")
| select([@timestamp, embargo_status, embargo_timestamp, rpl_status, rpl_timestamp, gts_status])
""".strip()


def _resolve_token(body: dict) -> str:
    token = (body.get("api_token") or "").strip()
    if token:
        return token
    return (os.environ.get("HUMIO_API_TOKEN") or "").strip()


@humio_bp.route("/api/humio/rpl-query", methods=["POST"])
def humio_rpl_query():
    body = request.get_json(silent=True) or {}
    customer_id = (body.get("customer_id") or "").strip()
    if not customer_id:
        return jsonify({"error": "Please enter a customer ID."}), 400

    start = (body.get("start") or "1d").strip()
    if start not in ALLOWED_STARTS:
        return jsonify({"error": f"Invalid time range. Use one of: {', '.join(sorted(ALLOWED_STARTS))}"}), 400

    token = _resolve_token(body)
    if not token:
        return jsonify({
            "error": "Humio API token not configured. Set HUMIO_API_TOKEN in .env or paste a token in the form."
        }), 400

    base_url = (os.environ.get("HUMIO_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    repository = os.environ.get("HUMIO_REPOSITORY") or DEFAULT_REPOSITORY
    endpoint = f"{base_url}/api/v1/repositories/{repository}/query"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "queryString": _build_query(customer_id),
        "start": start,
        "isLive": False,
    }

    try:
        response = requests.post(endpoint, headers=headers, json=payload, timeout=120)
    except requests.RequestException as exc:
        return jsonify({"error": f"Failed to reach Humio: {exc}"}), 502

    if response.status_code != 200:
        detail = (response.text or "").strip()
        if len(detail) > 800:
            detail = detail[:800] + "…"
        return jsonify({
            "error": f"Humio query failed (HTTP {response.status_code}).",
            "detail": detail,
        }), 502

    try:
        events = response.json()
    except ValueError:
        return jsonify({"error": "Humio returned a non-JSON response."}), 502

    if not isinstance(events, list):
        return jsonify({"error": "Unexpected Humio response shape (expected a list of events)."}), 502

    return jsonify({
        "customer_id": customer_id,
        "repository": repository,
        "start": start,
        "count": len(events),
        "events": events,
    })
