/**
 * Build Mermaid flowcharts from role-builder payloads and render with mermaid.js (global).
 *
 * Visual design:
 *   - Distinct node "types" (root / org / group / workspace / service / output) get
 *     different fills, strokes, and shapes.
 *   - Each Okta group is wrapped in its own labeled `subgraph`, and the resulting
 *     role-string for that branch is emitted as a final node so users can see how
 *     their inputs map to the output.
 *   - A small zoom/pan/download toolbar is exposed via `RoleDiagram.attachToolbar`.
 */
(function (global) {
  "use strict";

  // ---------- Label helpers ----------
  function shortId(id) {
    if (!id) return "—";
    const t = String(id).trim();
    if (t.length <= 14) return t;
    return t.slice(0, 6) + "…" + t.slice(-4);
  }

  function clean(text) {
    return String(text || "").replace(/["\n\r]/g, " ").replace(/\s+/g, " ").trim();
  }

  function trunc(text, max) {
    const s = clean(text);
    if (s.length <= max) return s;
    return s.slice(0, Math.max(1, max - 1)) + "…";
  }

  // Mermaid uses <br/> for line breaks inside quoted node labels.
  function mlabel(lines) {
    return lines
      .filter((l) => l !== null && l !== undefined && String(l).length > 0)
      .map((l) => clean(l))
      .join("<br/>");
  }

  // ---------- Theme-aware classDefs ----------
  function diagramClassDefs() {
    const dark = document.documentElement.getAttribute("data-theme") === "dark";
    if (dark) {
      return [
        "  classDef root fill:#0f3a2c,stroke:#4be0b7,stroke-width:2px,color:#ecf5f1,rx:10,ry:10",
        "  classDef org fill:#172d3d,stroke:#7ab2ff,stroke-width:1.5px,color:#dbe8ff,rx:8,ry:8",
        "  classDef grp fill:#152a24,stroke:#1ed19f,stroke-width:1.5px,color:#ecf5f1,rx:8,ry:8",
        "  classDef ws fill:#11231e,stroke:#4be0b7,stroke-width:1px,color:#ecf5f1,rx:6,ry:6",
        "  classDef svc fill:#0c1916,stroke:#65766f,stroke-width:1px,color:#cfdcd6,stroke-dasharray:4 3,rx:6,ry:6",
        "  classDef out fill:#1a2f28,stroke:#1ed19f,stroke-width:1.5px,color:#ecf5f1,rx:4,ry:4",
        "  classDef empty fill:#11231e,stroke:#65766f,stroke-width:1px,color:#9ab0a8,stroke-dasharray:3 3,rx:6,ry:6",
      ];
    }
    return [
      "  classDef root fill:#01A982,stroke:#00765C,stroke-width:2px,color:#ffffff,rx:10,ry:10",
      "  classDef org fill:#eaf2ff,stroke:#2563eb,stroke-width:1.5px,color:#11305c,rx:8,ry:8",
      "  classDef grp fill:#E6F7F2,stroke:#01A982,stroke-width:1.5px,color:#0b1f1a,rx:8,ry:8",
      "  classDef ws fill:#ffffff,stroke:#01A982,stroke-width:1px,color:#0b1f1a,rx:6,ry:6",
      "  classDef svc fill:#f7f7f6,stroke:#8b9591,stroke-width:1px,color:#0b1f1a,stroke-dasharray:4 3,rx:6,ry:6",
      "  classDef out fill:#0b1f1a,stroke:#01A982,stroke-width:1.5px,color:#e6efeb,rx:4,ry:4",
      "  classDef empty fill:#ffffff,stroke:#c0392b,stroke-width:1px,color:#c0392b,stroke-dasharray:3 3,rx:6,ry:6",
    ];
  }

  // ---------- Role-string builders (mirror of app logic, kept simple for preview) ----------
  function buildSegmentString(ws, groupUuid, defaults) {
    const wid = clean(ws.workspace_id);
    const tid = clean(ws.tenant_id);
    const wtype = String(ws.type || "").toLowerCase();
    const role = clean(ws.role) || defaults.role;
    const scope = clean(ws.scope) || defaults.scope;
    const TEN = "|TENANT|";
    let firstSeg = wid;
    if (wtype === "tenant") {
      firstSeg = wid || tid;
    }
    let block = firstSeg + ":" + groupUuid + ":" + role + (wtype === "tenant" ? TEN : "") + ":" + scope;
    (ws.services || []).forEach((s) => {
      const sid = clean(s.service_id);
      const srole = clean(s.service_role);
      const sscope = clean(s.scope) || defaults.scope;
      if (!sid || !srole) return;
      block += ":" + sid + ":" + srole + (wtype === "tenant" ? TEN : "") + ":" + sscope;
    });
    return block;
  }

  function buildOrgString(org, groupUuid, defaults) {
    if (!org || !org.with_org) return "";
    const oid = clean(org.organization_id);
    if (!oid) return "";
    const role = clean(org.organization_role) || defaults.orgRole;
    return oid + ":" + groupUuid + ":" + role + ":" + defaults.scope;
  }

  // ---------- Diagram builders ----------
  /**
   * No-groups diagram: optional org -> workspace[s] -> services
   *                    -> final role string.
   */
  function buildNoGroupsDiagram(payload) {
    const defaults = {
      role: "Workspace Administrator",
      scope: "ALL_SCOPES",
      orgRole: "Organization administrator",
      zeroUuid: "00000000-0000-0000-0000-000000000000",
    };
    const lines = ["flowchart TB"];
    lines.push.apply(lines, diagramClassDefs());

    const orgOn = !!(payload.with_org && clean(payload.organization_id));
    lines.push('  R["' + mlabel(["<b>Output</b>", "version_1 role string"]) + '"]:::root');

    let upstream = "R";
    let orgString = "";
    if (orgOn) {
      orgString = buildOrgString(
        {
          with_org: true,
          organization_id: payload.organization_id,
          organization_role: payload.organization_role,
        },
        payload.group_id || defaults.zeroUuid,
        defaults
      );
      lines.push(
        '  ORG["' +
          mlabel([
            "<b>Organization</b>",
            "id: " + shortId(payload.organization_id),
            "role: " + trunc(payload.organization_role || defaults.orgRole, 28),
          ]) +
          '"]:::org'
      );
      lines.push("  R --> ORG");
      upstream = "ORG";
    }

    const groupUuid = clean(payload.group_id) || defaults.zeroUuid;

    function workspaceRowReady(w) {
      const wid = clean(w.workspace_id);
      const tid = clean(w.tenant_id);
      const wt = String(w.type || "").toLowerCase();
      if (wt === "tenant" || wt === "tenant_dedicated" || wt === "dedicated_tenant") return wid || tid;
      return wid;
    }

    const wss = (payload.workspaces || []).filter(workspaceRowReady);

    if (!wss.length) {
      lines.push('  EMP["' + mlabel(["No workspace yet", "Add a workspace ID"]) + '"]:::empty');
      lines.push("  " + upstream + " --> EMP");
      return lines.join("\n");
    }

    const segmentStrings = [];
    wss.forEach((ws, i) => {
      const wid = "W" + i;
      const wsType = String(ws.type || "").toLowerCase();
      const primaryId =
        wsType === "tenant" || wsType === "tenant_dedicated" || wsType === "dedicated_tenant"
          ? clean(ws.workspace_id) || clean(ws.tenant_id)
          : clean(ws.workspace_id);
      lines.push(
        '  ' +
          wid +
          '["' +
          mlabel([
            "<b>Workspace " + (i + 1) + "</b>",
            "id: " + shortId(primaryId),
            "role: " + trunc(ws.role || defaults.role, 28),
            "scope: " + trunc(ws.scope || defaults.scope, 22),
          ]) +
          '"]:::ws'
      );
      if (i === 0) lines.push("  " + upstream + " --> " + wid);
      else lines.push("  W" + (i - 1) + ' -.->|"#"| ' + wid);

      const svcs = (ws.services || []).filter(
        (s) => clean(s.service_id) && clean(s.service_role)
      );
      svcs.forEach((s, j) => {
        const sid = wid + "_S" + j;
        lines.push(
          '  ' +
            sid +
            '["' +
            mlabel([
              "<b>Service " + (j + 1) + "</b>",
              "id: " + shortId(s.service_id),
              "role: " + trunc(s.service_role, 28),
              "scope: " + trunc(s.scope || defaults.scope, 22),
            ]) +
            '"]:::svc'
        );
        lines.push("  " + wid + " --> " + sid);
      });

      segmentStrings.push(buildSegmentString(ws, groupUuid, defaults));
    });

    // Resulting role string preview node
    const core = segmentStrings.join("#");
    const hasMsp = wss.some((w) => String(w.type || "").toLowerCase() === "msp");
    const ver = hasMsp ? "version_2" : "version_1";
    const finalStr = orgOn && orgString
      ? ver + "#" + orgString + "#" + core
      : ver + "#" + core;
    lines.push(
      '  OUT["' +
        mlabel(["<b>Resulting role string</b>", trunc(finalStr, 110)]) +
        '"]:::out'
    );
    lines.push("  W" + (wss.length - 1) + " --> OUT");

    return lines.join("\n");
  }

  /**
   * With-groups diagram: String.join -> isMemberOfGroupName branches.
   * Each branch is a labeled subgraph containing its workspaces (and services),
   * plus a role-string preview node at the end of the branch.
   */
  function buildWithGroupsDiagram(payload) {
    const defaults = {
      role: "Workspace Administrator",
      scope: "ALL_SCOPES",
      orgRole: "Organization administrator",
      zeroUuid: "00000000-0000-0000-0000-000000000000",
    };
    const lines = ["flowchart TB"];
    lines.push.apply(lines, diagramClassDefs());

    const org = payload.org || {};
    const orgOn = !!(org.with_org && clean(org.organization_id));

    lines.push('  J["' + mlabel(["<b>Okta join</b>", "String.join( … )"]) + '"]:::root');

    if (orgOn) {
      lines.push(
        '  ORG["' +
          mlabel([
            "<b>Organization</b>",
            "id: " + shortId(org.organization_id),
            "role: " + trunc(org.organization_role || defaults.orgRole, 28),
            "(prepended in each branch)",
          ]) +
          '"]:::org'
      );
      lines.push("  J -.- ORG");
    }

    function diagramWorkspaceReady(w) {
      const wid = clean(w.workspace_id);
      const tid = clean(w.tenant_id);
      const wt = String(w.type || "").toLowerCase();
      if (wt === "tenant" || wt === "tenant_dedicated" || wt === "dedicated_tenant") return wid || tid;
      return wid;
    }

    const groups = (payload.groups || []).filter(
      (g) =>
        g.enabled &&
        clean(g.name) &&
        (g.workspaces || []).some((w) => diagramWorkspaceReady(w))
    );

    if (!groups.length) {
      lines.push(
        '  EMP["' +
          mlabel([
            "No active branches yet",
            "Add a named, enabled group with at least one workspace",
          ]) +
          '"]:::empty'
      );
      lines.push("  J --> EMP");
      return lines.join("\n");
    }

    const groupUuid = defaults.zeroUuid; // current builder uses zero UUID inside group branches
    const orgString = orgOn ? buildOrgString(org, groupUuid, defaults) : "";

    groups.forEach((g, gi) => {
      const gKey = "G" + gi;
      const branchLabel = mlabel([
        "<b>Branch " + (gi + 1) + "</b>",
        'isMemberOfGroupName("' + trunc(g.name, 28) + '")',
      ]);

      lines.push("  subgraph " + gKey + '["' + branchLabel + '"]');
      lines.push("    direction TB");

      const wss = (g.workspaces || []).filter((w) => diagramWorkspaceReady(w));
      const segmentStrings = [];

      wss.forEach((ws, wi) => {
        const wKey = gKey + "_W" + wi;
        const wsType = String(ws.type || "").toLowerCase();
        const primaryId =
          wsType === "tenant" || wsType === "tenant_dedicated" || wsType === "dedicated_tenant"
            ? clean(ws.workspace_id) || clean(ws.tenant_id)
            : clean(ws.workspace_id);
        lines.push(
          '    ' +
            wKey +
            '["' +
            mlabel([
              "<b>Workspace " + (wi + 1) + "</b>",
              "id: " + shortId(primaryId),
              "role: " + trunc(ws.role || defaults.role, 28),
              "scope: " + trunc(ws.scope || defaults.scope, 22),
            ]) +
            '"]:::ws'
        );
        if (wi > 0) lines.push("    " + gKey + "_W" + (wi - 1) + ' -.->|"#"| ' + wKey);

        const svcs = (ws.services || []).filter(
          (s) => clean(s.service_id) && clean(s.service_role)
        );
        svcs.forEach((s, sj) => {
          const sKey = wKey + "_S" + sj;
          lines.push(
            '    ' +
              sKey +
              '["' +
              mlabel([
                "<b>Service " + (sj + 1) + "</b>",
                "id: " + shortId(s.service_id),
                "role: " + trunc(s.service_role, 28),
              ]) +
              '"]:::svc'
          );
          lines.push("    " + wKey + " --> " + sKey);
        });

        segmentStrings.push(buildSegmentString(ws, groupUuid, defaults));
      });

      // Branch role-string preview
      const core = segmentStrings.join("#");
      const branchStr = orgOn && orgString
        ? "version_2#" + orgString + "#" + core
        : "version_2#" + core;
      const oKey = gKey + "_OUT";
      lines.push(
        '    ' +
          oKey +
          '["' +
          mlabel(["<b>Branch role string</b>", trunc(branchStr, 96)]) +
          '"]:::out'
      );
      if (wss.length > 0) lines.push("    " + gKey + "_W" + (wss.length - 1) + " --> " + oKey);

      lines.push("  end");
      lines.push("  class " + gKey + " grp");
      lines.push("  J --> " + gKey);
      if (orgOn) lines.push("  ORG -.-> " + gKey);
    });

    return lines.join("\n");
  }

  // ---------- Renderer + zoom toolbars (per-mount state) ----------
  // Map<HTMLElement (mount), { scale, filename }>
  const _states = new WeakMap();

  function getState(mountEl) {
    if (!mountEl) return null;
    let st = _states.get(mountEl);
    if (!st) {
      st = { scale: 1, filename: "role-hierarchy.svg" };
      _states.set(mountEl, st);
    }
    return st;
  }

  function applyTransform(mountEl) {
    if (!mountEl) return;
    const state = getState(mountEl);
    const svg = mountEl.querySelector("svg");
    if (!svg) return;
    svg.style.transformOrigin = "top left";
    svg.style.transform = "scale(" + state.scale.toFixed(3) + ")";
  }

  function setScale(mountEl, next) {
    if (!mountEl) return;
    const state = getState(mountEl);
    state.scale = Math.min(3, Math.max(0.4, next));
    applyTransform(mountEl);
  }

  function fit(mountEl) {
    if (!mountEl) return;
    setScale(mountEl, 1);
    if (mountEl.scrollTo) mountEl.scrollTo({ top: 0, left: 0 });
  }
  function zoomIn(mountEl) { if (mountEl) setScale(mountEl, getState(mountEl).scale * 1.2); }
  function zoomOut(mountEl) { if (mountEl) setScale(mountEl, getState(mountEl).scale / 1.2); }

  function downloadSvg(mountEl, filename) {
    if (!mountEl) return;
    const svg = mountEl.querySelector("svg");
    if (!svg) return;
    const clone = svg.cloneNode(true);
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    const xml = new XMLSerializer().serializeToString(clone);
    const blob = new Blob([xml], { type: "image/svg+xml;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename || getState(mountEl).filename || "role-hierarchy.svg";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  function attachToolbar(toolbarEl, mountEl, opts) {
    if (!toolbarEl || !mountEl) return;
    const state = getState(mountEl);
    if (opts && opts.filename) state.filename = opts.filename;
    const handlers = {
      "zoom-in": () => zoomIn(mountEl),
      "zoom-out": () => zoomOut(mountEl),
      fit: () => fit(mountEl),
      download: () => downloadSvg(mountEl, state.filename),
    };
    toolbarEl.querySelectorAll("[data-action]").forEach((btn) => {
      if (btn.dataset.bound === "1") return;
      btn.dataset.bound = "1";
      const action = btn.getAttribute("data-action");
      if (handlers[action]) {
        btn.addEventListener("click", (e) => {
          e.preventDefault();
          handlers[action]();
        });
      }
    });
  }

  async function render(container, definition) {
    if (!container) return;
    container.innerHTML = "";
    if (!definition || !String(definition).trim()) {
      container.innerHTML = '<p class="diagram-loading">Nothing to diagram yet.</p>';
      return;
    }
    const m = global.mermaid;
    if (!m || typeof m.render !== "function") {
      container.innerHTML = '<p class="diagram-loading">Diagram library not loaded.</p>';
      return;
    }
    const theme = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "default";
    m.initialize({
      startOnLoad: false,
      theme,
      securityLevel: "loose",
      themeVariables: {
        fontFamily: 'Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
        fontSize: "13px",
      },
      flowchart: {
        curve: "basis",
        padding: 18,
        nodeSpacing: 36,
        rankSpacing: 56,
        htmlLabels: true,
        useMaxWidth: false,
      },
    });
    const graphId = "roleflow-" + Math.random().toString(36).slice(2);
    try {
      const { svg } = await m.render(graphId, definition);
      container.innerHTML = svg;
      const svgEl = container.querySelector("svg");
      if (svgEl) {
        svgEl.removeAttribute("style");
        svgEl.style.maxWidth = "none";
        svgEl.style.height = "auto";
      }
      const state = _states.get(container);
      if (state) applyTransform(container);
    } catch (e) {
      container.innerHTML =
        '<p class="diagram-loading">' +
        ((e && e.message) ? e.message : String(e)) +
        "</p>";
    }
  }

  global.RoleDiagram = {
    shortId,
    buildNoGroupsDiagram,
    buildWithGroupsDiagram,
    render,
    attachToolbar,
    fit,
    zoomIn,
    zoomOut,
    downloadSvg,
  };
})(typeof window !== "undefined" ? window : this);
