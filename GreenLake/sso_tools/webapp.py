"""Flask sub-app: Okta role string builder + SAML validator (from stringjoin)."""
from __future__ import annotations

import json
import os
from typing import Any

from flask import Flask, Response, jsonify, redirect, render_template, request, url_for

from . import saml_validator

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))

ZERO_UUID = "00000000-0000-0000-0000-000000000000"
DEFAULT_SCOPE = "ALL_SCOPES"
DEFAULT_ROLE = "Workspace Administrator"
DEFAULT_ORG_ROLE = "Organization administrator"

# MSP scope targets
MSP_ONLY = "msp_only"
MSP_TENANT = "msp_tenant"

MSP_SUFFIX = "|MSP|"
TENANT_SUFFIX = "|TENANT|"


def _clean(value: Any) -> str:
    return str(value or "").strip()

_WS_ROLE_ALIASES = {
    "workspace read only": "Workspace Observers",
}


def _normalize_ws_role(value: Any) -> str:
    raw = _clean(value)
    if not raw:
        return raw
    lowered = raw.lower()
    if lowered == "super administrator":
        raise ValueError("Super Administrator is not a valid workspace role.")
    return _WS_ROLE_ALIASES.get(lowered, raw)


def _scope_target(value: Any, default: str = MSP_TENANT) -> str:
    v = _clean(value).lower().replace("-", "_")
    if v in (MSP_ONLY, "msp", "only_msp"):
        return MSP_ONLY
    if v in (MSP_TENANT, "tenant", "whole_tenant", "msp_and_tenant"):
        return MSP_TENANT
    return default


def _ws_type(value: Any) -> str:
    v = _clean(value).lower()
    if v in ("msp", "managed"):
        return "msp"
    if v in ("tenant", "tenant_dedicated", "dedicated_tenant"):
        return "tenant"
    return "standalone"


def _standalone_workspace_segment(
    workspace_id: str,
    group_id: str,
    role: str,
    scope: str,
    services: list[Any] | None,
) -> str:
    workspace_block = f"{workspace_id}:{group_id}:{role}:{scope}"
    svc_parts: list[str] = []
    for svc in services or []:
        svc_id = _clean(svc.get("service_id"))
        svc_role = _clean(svc.get("service_role"))
        svc_scope = _clean(svc.get("scope")) or DEFAULT_SCOPE
        if not svc_id and not svc_role:
            continue
        if not svc_id or not svc_role:
            raise ValueError(
                "Each service must include both a Service ID and a Service Role."
            )
        svc_parts.extend([svc_id, svc_role, svc_scope])
    if svc_parts:
        return workspace_block + ":" + ":".join(svc_parts)
    return workspace_block


def _tenant_workspace_segment(
    tenant_id: str,
    group_id: str,
    role: str,
    scope: str,
    services: list[Any] | None,
) -> str:
    """version_1-style block: workspace id first, |TENANT| on workspace and service roles."""
    workspace_block = f"{tenant_id}:{group_id}:{role}{TENANT_SUFFIX}:{scope}"
    svc_parts: list[str] = []
    for svc in services or []:
        svc_id = _clean(svc.get("service_id"))
        svc_role = _clean(svc.get("service_role"))
        svc_scope = _clean(svc.get("scope")) or DEFAULT_SCOPE
        if not svc_id and not svc_role:
            continue
        if not svc_id or not svc_role:
            raise ValueError(
                "Each service must include both a Service ID and a Service Role."
            )
        svc_parts.extend([svc_id, f"{svc_role}{TENANT_SUFFIX}", svc_scope])
    if svc_parts:
        return workspace_block + ":" + ":".join(svc_parts)
    return workspace_block


def _msp_workspace_segment(
    workspace_id: str,
    group_id: str,
    role: str,
    scope: str,
    ws_scope_target: str,
    services: list[Any] | None,
) -> str:
    parts: list[str] = [
        workspace_id,
        group_id,
        f"{role}{MSP_SUFFIX}",
        scope,
    ]
    if ws_scope_target == MSP_TENANT:
        parts.extend([group_id, f"{role}{TENANT_SUFFIX}", scope])

    for svc in services or []:
        svc_id = _clean(svc.get("service_id"))
        svc_role = _clean(svc.get("service_role"))
        svc_scope = _clean(svc.get("scope")) or DEFAULT_SCOPE
        svc_target = _scope_target(svc.get("scope_target"), ws_scope_target)
        if not svc_id and not svc_role:
            continue
        if not svc_id or not svc_role:
            raise ValueError(
                "Each service must include both a Service ID and a Service Role."
            )
        parts.extend([svc_id, f"{svc_role}{MSP_SUFFIX}", svc_scope])
        if svc_target == MSP_TENANT:
            parts.extend([svc_id, f"{svc_role}{TENANT_SUFFIX}", svc_scope])

    return ":".join(parts)


def _workspace_segment(
    workspace_id: str,
    group_id: str,
    role: str,
    scope: str,
    services: list[Any] | None,
    ws_type: str = "standalone",
    ws_scope_target: str = MSP_TENANT,
    tenant_id: str = "",
) -> str:
    if ws_type == "msp":
        return _msp_workspace_segment(
            workspace_id, group_id, role, scope, ws_scope_target, services
        )
    if ws_type == "tenant":
        tid = _clean(tenant_id) or _clean(workspace_id)
        if not tid:
            raise ValueError(
                "Workspace ID is required for tenant-type workspaces."
            )
        return _tenant_workspace_segment(tid, group_id, role, scope, services)
    return _standalone_workspace_segment(
        workspace_id, group_id, role, scope, services
    )


def build_role_string(payload: dict[str, Any]) -> str:
    group_id = _clean(payload.get("group_id")) or ZERO_UUID

    workspaces_raw = payload.get("workspaces")
    segments: list[str] = []
    has_msp = False

    if workspaces_raw and isinstance(workspaces_raw, list) and len(workspaces_raw) > 0:
        for ws in workspaces_raw:
            if not isinstance(ws, dict):
                continue
            wid = _clean(ws.get("workspace_id"))
            tid = _clean(ws.get("tenant_id"))
            wtype = _ws_type(ws.get("type"))
            if wtype == "tenant":
                if not tid and not wid:
                    continue
            elif not wid:
                continue
            role = _normalize_ws_role(ws.get("role")) or DEFAULT_ROLE
            scope = _clean(ws.get("scope")) or DEFAULT_SCOPE
            wscope_target = _scope_target(ws.get("scope_target"))
            if wtype == "msp":
                has_msp = True
            segments.append(
                _workspace_segment(
                    wid,
                    group_id,
                    role,
                    scope,
                    ws.get("services"),
                    ws_type=wtype,
                    ws_scope_target=wscope_target,
                    tenant_id=tid,
                )
            )
        if not segments:
            raise ValueError(
                "At least one workspace is required (each row needs a workspace ID)."
            )
    else:
        workspace_id = _clean(payload.get("workspace_id"))
        tid = _clean(payload.get("tenant_id"))
        wtype = _ws_type(payload.get("type"))
        if wtype == "tenant":
            if not workspace_id and not tid:
                raise ValueError(
                    "Tenant-type workspaces require a workspace ID as the first segment."
                )
        elif not workspace_id:
            raise ValueError("Workspace ID is required.")
        role = _normalize_ws_role(payload.get("role")) or DEFAULT_ROLE
        scope = _clean(payload.get("scope")) or DEFAULT_SCOPE
        wscope_target = _scope_target(payload.get("scope_target"))
        if wtype == "msp":
            has_msp = True
        segments.append(
            _workspace_segment(
                workspace_id,
                group_id,
                role,
                scope,
                payload.get("services"),
                ws_type=wtype,
                ws_scope_target=wscope_target,
                tenant_id=tid,
            )
        )

    core = "#".join(segments)
    version_prefix = "version_2" if has_msp else "version_1"

    if payload.get("with_org"):
        org_id = _clean(payload.get("organization_id"))
        if not org_id:
            raise ValueError("Organization ID is required.")
        org_role = _clean(payload.get("organization_role")) or DEFAULT_ORG_ROLE
        org_scope = _clean(payload.get("organization_scope")) or DEFAULT_SCOPE
        org_group_id = (
            _clean(payload.get("organization_group_id")) or group_id
        )
        org_block = f"{org_id}:{org_group_id}:{org_role}:{org_scope}"
        return f"{version_prefix}#{org_block}#{core}"
    return f"{version_prefix}#{core}"


def build_sso_tools_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=os.path.join(_PKG_DIR, "templates"),
        static_folder=os.path.join(_PKG_DIR, "static"),
        static_url_path="/static",
    )
    saml_validator.register_routes(app)

    @app.route("/")
    def landing() -> str:
        return render_template("start.html")

    @app.route("/new")
    def idp_select_page():
        return redirect(url_for("okta_select"))

    @app.route("/existing")
    def existing_config() -> str:
        return render_template("existing.html")

    @app.route("/okta")
    def okta_select() -> str:
        return render_template("okta_select.html")

    @app.route("/okta/no-groups")
    def okta_no_groups() -> str:
        return render_template(
            "okta_no_groups.html",
            default_role=DEFAULT_ROLE,
            default_scope=DEFAULT_SCOPE,
            zero_uuid=ZERO_UUID,
            with_groups=False,
        )

    @app.route("/okta/with-groups")
    def okta_with_groups() -> str:
        return render_template(
            "okta_with_groups.html",
            default_role=DEFAULT_ROLE,
            default_scope=DEFAULT_SCOPE,
            zero_uuid=ZERO_UUID,
            with_groups=True,
        )

    @app.post("/api/generate")
    def api_generate():
        payload = request.get_json(silent=True) or {}
        try:
            result = build_role_string(payload)
        except ValueError as err:
            return jsonify({"ok": False, "error": str(err)}), 400
        return jsonify({"ok": True, "result": result})

    @app.post("/api/export/<fmt>")
    def api_export(fmt: str):
        payload = request.get_json(silent=True) or {}
        try:
            result = build_role_string(payload)
        except ValueError as err:
            return jsonify({"ok": False, "error": str(err)}), 400

        fmt = fmt.lower()
        if fmt == "txt":
            return Response(
                result,
                mimetype="text/plain",
                headers={"Content-Disposition": "attachment; filename=okta-role-string.txt"},
            )
        if fmt == "json":
            body = json.dumps(
                {
                    "workspace_id": _clean(payload.get("workspace_id")),
                    "group_id": _clean(payload.get("group_id")) or ZERO_UUID,
                    "role": _clean(payload.get("role")) or DEFAULT_ROLE,
                    "scope": _clean(payload.get("scope")) or DEFAULT_SCOPE,
                    "services": payload.get("services") or [],
                    "workspaces": payload.get("workspaces"),
                    "generated_string": result,
                },
                indent=2,
            )
            return Response(
                body,
                mimetype="application/json",
                headers={"Content-Disposition": "attachment; filename=okta-role-string.json"},
            )
        return jsonify({"ok": False, "error": f"Unsupported format: {fmt}"}), 400

    return app
