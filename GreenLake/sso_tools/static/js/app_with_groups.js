(function () {
  "use strict";

  const CONFIG = window.__APP_CONFIG__ || {
    defaultRole: "Workspace Administrator",
    defaultScope: "ALL_SCOPES",
    zeroUuid: "00000000-0000-0000-0000-000000000000",
  };

  const STORAGE_KEYS = {
    history: "okta-role-string-history-with-groups-v2",
    theme: "greenlake-theme",
  };

  const MAX_HISTORY = 20;

  // ---- Service catalog (role names are case-sensitive) ----
  const DATA_SERVICES_ROLES = [
    "Data Ops Manager Administrator",
    "Data Ops Manager Operator",
    "Data Services Administrator",
  ];

  const PRIVATE_CLOUD_ROLES = [
    "Private Cloud AI Administrator",
    "Private Cloud AI Cloud Administrator",
    "Private Cloud AI User",
    "Private Cloud Business Edition Administrator",
    "Private Cloud Business Edition Network Administrator",
    "Private Cloud Business Edition Network Operator",
    "Private Cloud Business Edition Operator",
  ];

  const SERVICE_CATALOG = [
    {
      name: "Compute Ops Management",
      id: "b394fa01-8858-4d73-8818-eadaf12eaf37",
      roles: [
        "Compute Ops Management administrator",
        "Compute Ops Management operator",
        "Compute Ops Management viewer",
      ],
    },
    { name: "Wellness Dashboard", id: "00000000-0000-0000-0000-000000000000", roles: [] },
    {
      name: "Consumption Analytics",
      id: "7d3c098a-199f-42b9-83ae-ed172c81fcba",
      roles: [
        "Consumption Analytics Administrator",
        "Consumption Analytics Billing Contributor",
        "Consumption Analytics Billing Usage Viewer",
        "Consumption Analytics Billing Viewer",
        "Consumption Analytics Capacity Planning Viewer",
        "Consumption Analytics Contributor",
        "Consumption Analytics Viewer",
      ],
    },
    { name: "OpsRamp", id: "f4cc7322-f1c0-4546-a06c-74e0c3894769", roles: ["Opsramp Access", "Opsramp Administrator"] },
    { name: "HPE Sustainability Insight Center", id: "f95f0184-3142-40cd-ba34-e00ef25f0c23", roles: ["Sustainability Insight Center Administrator"] },
    {
      name: "HPE Aruba Networking Central",
      id: "683da368-66cb-4ee7-90a9-ec1964768092",
      roles: [
        "Aruba Central Administrator",
        "Aruba Central Guest Operator",
        "Aruba Central Operator",
        "Aruba Central view edit role",
        "Aruba Central View Only",
        "NetInsight Campus Admin",
        "NetInsight Campus Viewonly",
      ],
    },
    {
      name: "User Experience Insight",
      id: "7cc837db-2045-4d58-a16f-b167bb9fd0d2",
      roles: ["UXI Administrator", "UXI Read Only Restricted", "UXI Read Only Unrestricted"],
    },
    { name: "Private Cloud AI", id: "980eea3c-b063-451e-8a45-ebcaa54fd561", roles: PRIVATE_CLOUD_ROLES },
    { name: "Private Cloud Business Edition", id: "980eea3c-b063-451e-8a45-ebcaa54fd561", roles: PRIVATE_CLOUD_ROLES },
    { name: "Data Services", id: "980eea3c-b063-451e-8a45-ebcaa54fd561", roles: DATA_SERVICES_ROLES },
    { name: "File Storage", id: "980eea3c-b063-451e-8a45-ebcaa54fd561", roles: DATA_SERVICES_ROLES },
    { name: "Object Service", id: "980eea3c-b063-451e-8a45-ebcaa54fd561", roles: DATA_SERVICES_ROLES },
    { name: "Block Storage", id: "980eea3c-b063-451e-8a45-ebcaa54fd561", roles: DATA_SERVICES_ROLES },
    { name: "Data Ops Manager", id: "980eea3c-b063-451e-8a45-ebcaa54fd561", roles: DATA_SERVICES_ROLES },
    { name: "VMware Cloud Foundation, Managed for you", id: "980eea3c-b063-451e-8a45-ebcaa54fd561", roles: DATA_SERVICES_ROLES },
  ];

  const CUSTOM = "__custom__";
  const NONE = "";

  const WS_TYPE_STANDALONE = "standalone";
  const WS_TYPE_MSP = "msp";
  const WS_TYPE_TENANT = "tenant";
  const SCOPE_MSP_TENANT = "msp_tenant";
  const SCOPE_MSP_ONLY = "msp_only";
  const MSP_SUFFIX = "|MSP|";
  const TENANT_SUFFIX = "|TENANT|";

  function normalizeWsType(value) {
    const v = String(value || "").toLowerCase();
    if (v === WS_TYPE_MSP || v === "managed") return WS_TYPE_MSP;
    if (v === WS_TYPE_TENANT || v === "tenant_dedicated" || v === "dedicated_tenant") {
      return WS_TYPE_TENANT;
    }
    return WS_TYPE_STANDALONE;
  }

  function isWorkspaceSegmentReady(w) {
    return !!(w.workspace_id || "").trim();
  }

  function normalizeScopeTarget(value) {
    const v = String(value || "").toLowerCase().replace(/-/g, "_");
    if (v === SCOPE_MSP_ONLY || v === "msp" || v === "only_msp") return SCOPE_MSP_ONLY;
    return SCOPE_MSP_TENANT;
  }

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const form = $("#roleForm");
  const withOrgToggle = $("#withOrgToggle");
  const orgSection = $("#orgSection");
  const orgIdInput = $("#organizationId");
  const orgRoleSelect = $("#organizationRole");
  const customOrgRoleInput = $("#customOrgRole");

  const groupsList = $("#groupsList");
  const groupsEmpty = $("#groupsEmpty");
  const groupsCount = $("#groupsCount");
  const addGroupBtn = $("#addGroupBtn");
  const addGroupEmpty = $("#addGroupEmpty");
  const groupTemplate = $("#groupTemplate");
  const workspaceTemplate = $("#workspaceTemplate");

  const serviceTemplate = $("#serviceTemplate");

  const resetBtn = $("#resetBtn");
  const output = $("#output");
  const statusEl = $("#status");
  const copyBtn = $("#copyBtn");
  const exportTxtBtn = $("#exportTxtBtn");
  const exportJsonBtn = $("#exportJsonBtn");
  const historyList = $("#historyList");
  const clearHistoryBtn = $("#clearHistoryBtn");
  const themeToggle = $("#themeToggle");
  const diagramModal = $("#diagramModal");
  const diagramMount = $("#diagramMount");
  const diagramBtn = $("#diagramBtn");
  const diagramModalCloseBtn = $("#diagramModalClose");
  const diagramToolbar = $("#diagramToolbar");

  const liveDiagram = $("#liveDiagram");
  const liveDiagramMount = $("#liveDiagramMount");
  const liveDiagramToolbar = $("#liveDiagramToolbar");

  const wsTypeModal = $("#wsTypeModal");
  const wsTypeModalCloseBtn = $("#wsTypeModalClose");

  const statsBar = $("#statsBar");
  const statGroups = $("#statGroups");
  const statWorkspaces = $("#statWorkspaces");
  const statServices = $("#statServices");
  const statGroupsPill = $("#statGroupsPill");
  const statWorkspacesPill = $("#statWorkspacesPill");
  const statServicesPill = $("#statServicesPill");
  const statsStatus = $("#statsStatus");

  let currentResult = "";
  let statusTimer = null;
  let diagramBuildFn = null;
  let diagramToolbarBound = false;
  let liveDiagramToolbarBound = false;
  let liveDiagramTimer = null;

  function buildWithGroupsPayload() {
    return {
      org: getOrgPayload(),
      groups: $$(".group-card", groupsList).map(readGroupPayload),
    };
  }

  function refreshLiveDiagram() {
    if (!liveDiagramMount || !window.RoleDiagram) return;
    if (liveDiagram && !liveDiagram.open) return;
    if (liveDiagramTimer) clearTimeout(liveDiagramTimer);
    liveDiagramTimer = setTimeout(() => {
      try {
        RoleDiagram.render(
          liveDiagramMount,
          RoleDiagram.buildWithGroupsDiagram(buildWithGroupsPayload())
        );
      } catch (e) {
        liveDiagramMount.innerHTML =
          '<p class="diagram-loading">' + (e && e.message ? e.message : String(e)) + "</p>";
      }
    }, 120);
  }

  function initLiveDiagram() {
    if (!liveDiagramMount || !window.RoleDiagram) return;
    if (!liveDiagramToolbarBound && liveDiagramToolbar && RoleDiagram.attachToolbar) {
      RoleDiagram.attachToolbar(liveDiagramToolbar, liveDiagramMount, {
        filename: "role-hierarchy-with-groups.svg",
      });
      liveDiagramToolbarBound = true;
    }
    if (liveDiagram) {
      liveDiagram.addEventListener("toggle", () => {
        if (liveDiagram.open) refreshLiveDiagram();
      });
    }
    refreshLiveDiagram();
  }

  // ----- Theme -----
  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem(STORAGE_KEYS.theme, theme); } catch (e) {}
  }

  function initTheme() {
    if (window.GreenLakeTheme) {
      window.GreenLakeTheme.bindToggles();
      return;
    }
    let theme = null;
    try { theme = localStorage.getItem(STORAGE_KEYS.theme); } catch (e) {}
    if (!theme) {
      try {
        const leg = localStorage.getItem("okta-role-string-theme");
        if (leg === "light" || leg === "dark") theme = leg;
      } catch (e) {}
    }
    if (!theme) theme = "light";
    applyTheme(theme);
  }

  function closeDiagramModal() {
    if (!diagramModal) return;
    diagramModal.hidden = true;
    document.body.classList.remove("modal-open");
    diagramBuildFn = null;
  }

  async function refreshDiagram() {
    if (!diagramMount || !diagramBuildFn || !window.RoleDiagram) return;
    diagramMount.innerHTML = '<p class="diagram-loading">Rendering…</p>';
    await RoleDiagram.render(diagramMount, diagramBuildFn());
  }

  async function openDiagramModal(buildFn) {
    if (!diagramModal || !diagramMount) return;
    if (!window.RoleDiagram) {
      setStatus("Diagram library not loaded.", "error");
      return;
    }
    diagramBuildFn = buildFn;
    diagramModal.hidden = false;
    document.body.classList.add("modal-open");
    if (!diagramToolbarBound && diagramToolbar && RoleDiagram.attachToolbar) {
      RoleDiagram.attachToolbar(diagramToolbar, diagramMount, {
        filename: "role-hierarchy-with-groups.svg",
      });
      diagramToolbarBound = true;
    }
    await refreshDiagram();
  }

  if (themeToggle) {
    themeToggle.addEventListener("click", () => {
      const next =
        document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      if (window.GreenLakeTheme) window.GreenLakeTheme.apply(next);
      else applyTheme(next);
      if (diagramModal && !diagramModal.hidden && diagramBuildFn) refreshDiagram();
      refreshLiveDiagram();
    });
  }

  if (diagramBtn && diagramModal) {
    diagramBtn.addEventListener("click", () => {
      openDiagramModal(() =>
        RoleDiagram.buildWithGroupsDiagram({
          org: getOrgPayload(),
          groups: $$(".group-card", groupsList).map(readGroupPayload),
        })
      );
    });
  }
  if (diagramModalCloseBtn) diagramModalCloseBtn.addEventListener("click", closeDiagramModal);
  if (diagramModal) {
    const diagramBackdrop = diagramModal.querySelector("[data-close-diagram]");
    if (diagramBackdrop) diagramBackdrop.addEventListener("click", closeDiagramModal);
  }

  // ----- Status helpers -----
  function setStatus(message, kind) {
    statusEl.textContent = message || "";
    statusEl.classList.remove("success", "error");
    if (kind) statusEl.classList.add(kind);
    if (statusTimer) { clearTimeout(statusTimer); statusTimer = null; }
    if (message && kind) {
      statusTimer = setTimeout(() => {
        statusEl.textContent = "";
        statusEl.classList.remove("success", "error");
      }, 2800);
    }
  }

  // ----- Organization toggle -----
  function updateOrgVisibility(focusOnOpen) {
    const on = withOrgToggle.checked;
    orgSection.hidden = !on;
    if (on && focusOnOpen) orgIdInput.focus();
    updateCustomOrgRoleVisibility();
    updatePreview();
  }

  function updateCustomOrgRoleVisibility() {
    const isCustom = orgRoleSelect.value === CUSTOM;
    customOrgRoleInput.hidden = !isCustom;
  }

  withOrgToggle.addEventListener("change", () => updateOrgVisibility(true));
  orgRoleSelect.addEventListener("change", () => {
    updateCustomOrgRoleVisibility();
    updatePreview();
  });
  [orgIdInput, customOrgRoleInput].forEach((el) =>
    el.addEventListener("input", updatePreview)
  );

  const DEFAULT_ORG_ROLE = "Organization administrator";
  function getOrgRole() {
    if (orgRoleSelect.value === CUSTOM) {
      return customOrgRoleInput.value.trim() || DEFAULT_ORG_ROLE;
    }
    return orgRoleSelect.value || DEFAULT_ORG_ROLE;
  }

  // ----- Workspace type modal + MSP UI -----
  let _wsTypeResolver = null;

  function openWsTypeModal(options) {
    const opts = options || {};
    if (!wsTypeModal) return Promise.resolve(WS_TYPE_STANDALONE);
    const title = wsTypeModal.querySelector("#wsTypeModalTitle");
    if (title) title.textContent = opts.title || "New workspace";
    wsTypeModal.hidden = false;
    document.body.classList.add("modal-open");
    return new Promise((resolve) => {
      _wsTypeResolver = resolve;
    });
  }

  function closeWsTypeModal(choice) {
    if (!wsTypeModal) return;
    wsTypeModal.hidden = true;
    document.body.classList.remove("modal-open");
    const resolver = _wsTypeResolver;
    _wsTypeResolver = null;
    if (resolver) resolver(choice || null);
  }

  if (wsTypeModal) {
    wsTypeModal.querySelectorAll("[data-ws-type-choice]").forEach((btn) => {
      btn.addEventListener("click", () => {
        closeWsTypeModal(normalizeWsType(btn.getAttribute("data-ws-type-choice")));
      });
    });
    const backdrop = wsTypeModal.querySelector("[data-close-ws-type]");
    if (backdrop) backdrop.addEventListener("click", () => closeWsTypeModal(null));
    if (wsTypeModalCloseBtn) wsTypeModalCloseBtn.addEventListener("click", () => closeWsTypeModal(null));
  }

  function readWsTypeFromBlock(block) {
    return normalizeWsType(block.getAttribute("data-ws-type"));
  }

  function applyWsTypeToBlock(block, type) {
    const t = normalizeWsType(type);
    block.setAttribute("data-ws-type", t);
    setWsMspSwitch(block, t);
    const pill = $('[data-role="ws-type-pill"]', block);
    if (pill) {
      pill.classList.remove(
        "ws-type-pill-standalone",
        "ws-type-pill-msp",
        "ws-type-pill-tenant",
        "pill-info",
        "pill-success"
      );
      if (t === WS_TYPE_MSP) {
        pill.classList.add("ws-type-pill-msp", "pill-info");
        pill.textContent = "MSP";
      } else if (t === WS_TYPE_TENANT) {
        pill.classList.add("ws-type-pill-tenant");
        pill.textContent = "Tenant";
      } else {
        pill.classList.add("ws-type-pill-standalone");
        pill.textContent = "Standalone";
      }
    }
    const layoutRow = $(".ws-layout-toggle-row", block);
    if (layoutRow) {
      const hideMspSwitch = t === WS_TYPE_TENANT;
      layoutRow.hidden = hideMspSwitch;
      if (hideMspSwitch) layoutRow.setAttribute("hidden", "");
      else layoutRow.removeAttribute("hidden");
    }
    const mspWrap = $(".ws-msp-only", block);
    if (mspWrap) {
      if (t === WS_TYPE_MSP) {
        mspWrap.hidden = false;
        mspWrap.removeAttribute("hidden");
      } else {
        mspWrap.hidden = true;
      }
    }
    $$(".service-row", block).forEach((row) => applyMspToServiceRow(row, t));
  }

  function applyMspToServiceRow(row, wsType) {
    const wrap = $(".svc-msp-only", row);
    if (!wrap) return;
    if (normalizeWsType(wsType) === WS_TYPE_MSP) {
      wrap.hidden = false;
      wrap.removeAttribute("hidden");
    } else {
      wrap.hidden = true;
    }
  }

  function setWsMspSwitch(block, type) {
    const sw = $(".ws-msp-switch", block);
    if (!sw) return;
    sw.checked = normalizeWsType(type) === WS_TYPE_MSP;
  }

  function readWsMspFromBlock(block) {
    const sw = $(".ws-msp-switch", block);
    if (!sw) return normalizeWsType(block.getAttribute("data-ws-type"));
    return sw.checked ? WS_TYPE_MSP : WS_TYPE_STANDALONE;
  }

  function setWsTenantToggleFromScope(block, scopeTarget) {
    const el = $(".ws-tenant-toggle", block);
    if (!el) return;
    el.checked = normalizeScopeTarget(scopeTarget) === SCOPE_MSP_TENANT;
  }

  function readWsTenantToggle(block) {
    const el = $(".ws-tenant-toggle", block);
    if (!el) return SCOPE_MSP_TENANT;
    return el.checked ? SCOPE_MSP_TENANT : SCOPE_MSP_ONLY;
  }

  function setSvcTenantToggleFromScope(row, scopeTarget) {
    const el = $(".svc-tenant-toggle", row);
    if (!el) return;
    el.checked = normalizeScopeTarget(scopeTarget) === SCOPE_MSP_TENANT;
  }

  function readSvcTenantToggle(row) {
    const el = $(".svc-tenant-toggle", row);
    if (!el) return SCOPE_MSP_TENANT;
    return el.checked ? SCOPE_MSP_TENANT : SCOPE_MSP_ONLY;
  }

  // ----- Services (per workspace within a group) -----
  function findCatalogByName(name) {
    return SERVICE_CATALOG.find((s) => s.name === name) || null;
  }

  function findCatalogByIdAndRole(id, role) {
    const idTrim = (id || "").trim();
    const roleTrim = (role || "").trim();
    for (const svc of SERVICE_CATALOG) {
      if (svc.id === idTrim && svc.roles.includes(roleTrim)) return svc;
    }
    for (const svc of SERVICE_CATALOG) {
      if (svc.id === idTrim && svc.roles.length > 0) return svc;
    }
    for (const svc of SERVICE_CATALOG) {
      if (svc.id === idTrim) return svc;
    }
    return null;
  }

  function populateServicePicker(selectEl, selectedName) {
    selectEl.innerHTML = "";
    const placeholder = document.createElement("option");
    placeholder.value = NONE;
    placeholder.textContent = "Select a service...";
    selectEl.appendChild(placeholder);

    SERVICE_CATALOG.forEach((svc) => {
      const opt = document.createElement("option");
      opt.value = svc.name;
      opt.textContent = svc.name;
      selectEl.appendChild(opt);
    });

    const customOpt = document.createElement("option");
    customOpt.value = CUSTOM;
    customOpt.textContent = "Custom...";
    selectEl.appendChild(customOpt);

    if (selectedName) selectEl.value = selectedName;
  }

  function populateRoleSelect(selectEl, roles, selectedRole) {
    selectEl.innerHTML = "";

    if (!roles || roles.length === 0) {
      const customOnly = document.createElement("option");
      customOnly.value = CUSTOM;
      customOnly.textContent = "Custom...";
      selectEl.appendChild(customOnly);
      selectEl.value = CUSTOM;
      return;
    }

    const placeholder = document.createElement("option");
    placeholder.value = NONE;
    placeholder.textContent = "Select a role...";
    selectEl.appendChild(placeholder);

    roles.forEach((role) => {
      const opt = document.createElement("option");
      opt.value = role;
      opt.textContent = role;
      selectEl.appendChild(opt);
    });

    const customOpt = document.createElement("option");
    customOpt.value = CUSTOM;
    customOpt.textContent = "Custom...";
    selectEl.appendChild(customOpt);

    if (selectedRole && roles.includes(selectedRole)) {
      selectEl.value = selectedRole;
    } else if (selectedRole) {
      selectEl.value = CUSTOM;
    }
  }

  function toggleSvcCustomRoleInput(row) {
    const sel = $(".svc-role-select", row);
    const customInput = $(".svc-role-custom", row);
    const showCustom = sel.value === CUSTOM;
    customInput.hidden = !showCustom;
  }

  function readServiceRow(row) {
    const roleSel = $(".svc-role-select", row);
    const roleCustom = $(".svc-role-custom", row);
    const role = roleSel.value === CUSTOM
      ? roleCustom.value.trim()
      : (roleSel.value === NONE ? "" : roleSel.value);
    const out = {
      service_id: $(".svc-id", row).value.trim(),
      service_role: role,
      scope: $(".svc-scope", row).value.trim() || CONFIG.defaultScope,
    };
    const wsBlock = row.closest(".workspace-block");
    if (wsBlock && readWsTypeFromBlock(wsBlock) === WS_TYPE_MSP) {
      out.scope_target = readSvcTenantToggle(row);
    }
    return out;
  }

  function reindexWorkspaceServices(wsBlock) {
    const listEl = $(".ws-services-list", wsBlock);
    const countEl = $(".ws-service-count", wsBlock);
    const emptyEl = $(".ws-services-empty", wsBlock);
    const rows = $$(".service-row", listEl);
    rows.forEach((row, i) => {
      const label = $(".service-index", row);
      if (label) label.textContent = String(i + 1);
    });
    if (countEl) countEl.textContent = rows.length ? `(${rows.length})` : "";
    if (emptyEl) emptyEl.hidden = rows.length > 0;
  }

  function toggleWsCustomRole(block) {
    const sel = $(".ws-role", block);
    const wrap = $(".ws-custom-role-wrap", block);
    const isCustom = sel.value === CUSTOM;
    wrap.hidden = !isCustom;
    if (!isCustom) $(".ws-custom-role", block).value = "";
  }

  function addServiceToWorkspace(wsBlock, prefill) {
    const listEl = $(".ws-services-list", wsBlock);
    const node = serviceTemplate.content.firstElementChild.cloneNode(true);

    const picker = $(".svc-picker", node);
    const idInput = $(".svc-id", node);
    const roleSel = $(".svc-role-select", node);
    const roleCustom = $(".svc-role-custom", node);
    const scopeField = $(".svc-scope", node);
    const svcTenantToggle = $(".svc-tenant-toggle", node);

    let initialServiceName = null;
    let initialRole = null;
    let initialScopeTarget = SCOPE_MSP_TENANT;

    if (prefill) {
      if (prefill.service_id || prefill.service_role) {
        const match = findCatalogByIdAndRole(
          (prefill.service_id || "").trim(),
          (prefill.service_role || "").trim()
        );
        if (match) initialServiceName = match.name;
      }
      if (prefill.service_id) idInput.value = prefill.service_id;
      initialRole = prefill.service_role || null;
      if (prefill.scope) scopeField.value = prefill.scope;
      if (prefill.scope_target) initialScopeTarget = normalizeScopeTarget(prefill.scope_target);
    }

    setSvcTenantToggleFromScope(node, initialScopeTarget);

    populateServicePicker(picker, initialServiceName);
    if (initialServiceName) {
      const svc = findCatalogByName(initialServiceName);
      populateRoleSelect(roleSel, svc ? svc.roles : [], initialRole);
    } else {
      populateRoleSelect(roleSel, [], initialRole);
    }

    if (initialRole && roleSel.value === CUSTOM) {
      roleCustom.value = initialRole;
    }
    toggleSvcCustomRoleInput(node);

    picker.addEventListener("change", () => {
      const val = picker.value;
      if (val === NONE) {
        idInput.value = "";
        idInput.readOnly = false;
        populateRoleSelect(roleSel, [], null);
      } else if (val === CUSTOM) {
        idInput.value = "";
        idInput.readOnly = false;
        populateRoleSelect(roleSel, [], null);
        idInput.focus();
      } else {
        const svc = findCatalogByName(val);
        if (svc) {
          idInput.value = svc.id;
          idInput.readOnly = false;
          populateRoleSelect(roleSel, svc.roles, null);
        }
      }
      roleCustom.value = "";
      toggleSvcCustomRoleInput(node);
      updatePreview();
    });

    roleSel.addEventListener("change", () => {
      toggleSvcCustomRoleInput(node);
      updatePreview();
    });

    [idInput, roleCustom, scopeField].forEach((el) =>
      el.addEventListener("input", updatePreview)
    );

    if (svcTenantToggle) svcTenantToggle.addEventListener("change", updatePreview);

    $(".remove-service", node).addEventListener("click", () => {
      node.remove();
      reindexWorkspaceServices(wsBlock);
      updatePreview();
    });

    listEl.appendChild(node);
    applyMspToServiceRow(node, wsBlock.getAttribute("data-ws-type") || WS_TYPE_STANDALONE);
    reindexWorkspaceServices(wsBlock);
    updatePreview();
    return node;
  }

  function reindexGroupWorkspaces(groupNode) {
    const list = $(".grp-workspaces-list", groupNode);
    const blocks = $$(".workspace-block", list);
    blocks.forEach((b, i) => {
      const idx = $(".ws-block-index", b);
      if (idx) idx.textContent = String(i + 1);
    });
    const cnt = $(".grp-ws-count", groupNode);
    if (cnt) cnt.textContent = blocks.length ? `(${blocks.length})` : "";
    const emptyHint = $(".grp-ws-empty", groupNode);
    if (emptyHint) emptyHint.hidden = blocks.length > 0;
  }

  function addWorkspaceToGroup(groupNode, prefill) {
    if (!workspaceTemplate || !groupNode) return null;
    const wsRoot = $(".grp-workspaces-list", groupNode);
    const node = workspaceTemplate.content.firstElementChild.cloneNode(true);
    const wsId = $(".ws-workspace-id", node);
    const roleSel = $(".ws-role", node);
    const scopeInput = $(".ws-scope", node);
    const customRole = $(".ws-custom-role", node);

    const wsMspSwitch = $(".ws-msp-switch", node);

    const initialType = prefill && prefill.type
      ? normalizeWsType(prefill.type)
      : WS_TYPE_STANDALONE;
    if (prefill && prefill.scope_target) {
      setWsTenantToggleFromScope(node, prefill.scope_target);
    } else {
      setWsTenantToggleFromScope(node, SCOPE_MSP_TENANT);
    }
    applyWsTypeToBlock(node, initialType);

    scopeInput.value = CONFIG.defaultScope;
    roleSel.value = CONFIG.defaultRole;
    customRole.value = "";

    if (prefill) {
      if (prefill.workspace_id) wsId.value = prefill.workspace_id;
      if (prefill.scope) scopeInput.value = prefill.scope;
      if (prefill.role) {
        const builtin = Array.from(roleSel.options).map((o) => o.value);
        if (builtin.includes(prefill.role)) roleSel.value = prefill.role;
        else {
          roleSel.value = CUSTOM;
          customRole.value = prefill.role;
        }
      }
    }
    toggleWsCustomRole(node);

    const bump = () => updatePreview();
    [wsId, scopeInput, customRole].forEach((el) => el.addEventListener("input", bump));
    roleSel.addEventListener("change", () => {
      toggleWsCustomRole(node);
      bump();
    });
    if (wsMspSwitch) {
      wsMspSwitch.addEventListener("change", () => {
        if (readWsTypeFromBlock(node) === WS_TYPE_TENANT) return;
        applyWsTypeToBlock(node, readWsMspFromBlock(node));
        bump();
      });
    }
    const wsTenantToggle = $(".ws-tenant-toggle", node);
    if (wsTenantToggle) wsTenantToggle.addEventListener("change", bump);

    const changeTypeBtn = $(".ws-change-type", node);
    if (changeTypeBtn) {
      changeTypeBtn.addEventListener("click", async () => {
        const choice = await openWsTypeModal({ title: "Workspace type" });
        if (!choice) return;
        applyWsTypeToBlock(node, choice);
        bump();
      });
    }

    $(".ws-add-service", node).addEventListener("click", () => {
      const row = addServiceToWorkspace(node);
      if (row) $(".svc-picker", row).focus();
    });
    $(".remove-workspace", node).addEventListener("click", () => {
      node.remove();
      reindexGroupWorkspaces(groupNode);
      bump();
    });

    const collapseBtn = $(".ws-collapse", node);
    if (collapseBtn) {
      collapseBtn.addEventListener("click", () => {
        node.classList.toggle("is-collapsed");
      });
    }

    const dupBtn = $(".duplicate-workspace", node);
    if (dupBtn) {
      dupBtn.addEventListener("click", () => {
        const snapshot = readWorkspaceBlock(node);
        addWorkspaceToGroup(groupNode, snapshot);
      });
    }

    wsRoot.appendChild(node);
    reindexGroupWorkspaces(groupNode);
    if (prefill && Array.isArray(prefill.services)) {
      prefill.services.forEach((svc) => addServiceToWorkspace(node, svc));
    }
    bump();
    return node;
  }

  // ----- Groups -----
  function reindexGroups() {
    const cards = $$(".group-card", groupsList);
    cards.forEach((card, i) => {
      const idx = $(".group-index", card);
      if (idx) idx.textContent = String(i + 1);
    });
    groupsCount.textContent = cards.length ? `(${cards.length})` : "";
    groupsEmpty.hidden = cards.length > 0;
  }

  function addGroup(prefill) {
    const node = groupTemplate.content.firstElementChild.cloneNode(true);

    const nameInput = $(".grp-name", node);
    const enableInput = $(".grp-enable", node);

    if (prefill) {
      if (typeof prefill.name === "string") nameInput.value = prefill.name;
      if (typeof prefill.enabled === "boolean") enableInput.checked = prefill.enabled;
    }

    const bump = () => updatePreview();
    [nameInput, enableInput].forEach((el) => {
      el.addEventListener(el === enableInput ? "change" : "input", bump);
    });

    $(".grp-add-workspace", node).addEventListener("click", async () => {
      const choice = await openWsTypeModal({ title: "New workspace" });
      if (!choice) return;
      const ws = addWorkspaceToGroup(node, { type: choice });
      if (ws) {
        const wsIdEl = $(".ws-workspace-id", ws);
        if (wsIdEl) wsIdEl.focus();
      }
    });

    $(".remove-group", node).addEventListener("click", () => {
      node.remove();
      reindexGroups();
      bump();
    });

    const groupCollapseBtn = $(".group-collapse", node);
    if (groupCollapseBtn) {
      groupCollapseBtn.addEventListener("click", () => {
        node.classList.toggle("is-collapsed");
      });
    }

    const dupGroupBtn = $(".duplicate-group", node);
    if (dupGroupBtn) {
      dupGroupBtn.addEventListener("click", () => {
        const snapshot = readGroupPayload(node);
        addGroup(snapshot);
      });
    }

    groupsList.appendChild(node);
    reindexGroups();

    let wsPrefills = [];
    if (prefill && Array.isArray(prefill.workspaces) && prefill.workspaces.length) {
      wsPrefills = prefill.workspaces;
    } else if (prefill && (prefill.workspace_id || "").trim()) {
      wsPrefills = [{
        workspace_id: prefill.workspace_id,
        type: prefill.type,
        scope_target: prefill.scope_target,
        role: prefill.role,
        scope: prefill.scope,
        services: prefill.services || [],
      }];
    } else {
      wsPrefills = [];
    }
    wsPrefills.forEach((w) => addWorkspaceToGroup(node, w));
    reindexGroupWorkspaces(node);
    bump();
    return node;
  }

  function addGroupAndFocus() {
    const node = addGroup();
    $(".grp-name", node).focus();
  }

  addGroupBtn.addEventListener("click", addGroupAndFocus);
  addGroupEmpty.addEventListener("click", addGroupAndFocus);

  // ----- Okta EL wrapping -----
  function escapeELSingleQuoted(str) {
    return String(str).replace(/\\/g, "\\\\").replace(/'/g, "\\'");
  }

  /** For isMemberOfGroupName("...") — escape backslashes and double quotes only. */
  function escapeGroupNameForDoubleQuotes(name) {
    return String(name).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  }

  /**
   * @param {"multiline"|"inline"} servicesSep - one top-level Okta group in the join → newline before services; several → ": ".
   */
  function appendServicesToSegment(segmentBase, services, servicesSep) {
    const svcParts = [];
    (services || []).forEach((svc) => {
      const id = (svc.service_id || "").trim();
      const svcRole = (svc.service_role || "").trim();
      const svcScope = (svc.scope || "").trim() || CONFIG.defaultScope;
      if (!id && !svcRole) return;
      if (!id || !svcRole) throw new Error("Each service must include both a Service ID and a Service Role.");
      svcParts.push(id, svcRole, svcScope);
    });
    if (!svcParts.length) return segmentBase;
    const between = servicesSep === "multiline" ? ":\n" : ": ";
    return segmentBase + between + svcParts.join(":");
  }

  function buildMspWorkspaceSegmentInner(wsPayload) {
    const workspaceId = (wsPayload.workspace_id || "").trim();
    if (!workspaceId) throw new Error("Workspace ID is required for each workspace in an active group.");

    const role = (wsPayload.role || CONFIG.defaultRole).trim() || CONFIG.defaultRole;
    const scope = (wsPayload.scope || CONFIG.defaultScope).trim() || CONFIG.defaultScope;
    const groupUuid = CONFIG.zeroUuid;
    const wsScopeTarget = normalizeScopeTarget(wsPayload.scope_target);

    const parts = [workspaceId, groupUuid, role + MSP_SUFFIX, scope];
    if (wsScopeTarget === SCOPE_MSP_TENANT) {
      parts.push(groupUuid, role + TENANT_SUFFIX, scope);
    }

    (wsPayload.services || []).forEach((svc) => {
      const id = (svc.service_id || "").trim();
      const svcRole = (svc.service_role || "").trim();
      const svcScope = (svc.scope || "").trim() || CONFIG.defaultScope;
      if (!id && !svcRole) return;
      if (!id || !svcRole) {
        throw new Error("Each service must include both a Service ID and a Service Role.");
      }
      const svcTarget = normalizeScopeTarget(svc.scope_target || wsScopeTarget);
      parts.push(id, svcRole + MSP_SUFFIX, svcScope);
      if (svcTarget === SCOPE_MSP_TENANT) {
        parts.push(id, svcRole + TENANT_SUFFIX, svcScope);
      }
    });

    return parts.join(":");
  }

  function buildTenantWorkspaceSegmentInner(wsPayload, servicesSep) {
    const tid = (wsPayload.workspace_id || "").trim();
    if (!tid) throw new Error("Workspace ID is required for tenant-type workspaces.");
    const role = (wsPayload.role || CONFIG.defaultRole).trim() || CONFIG.defaultRole;
    const scope = (wsPayload.scope || CONFIG.defaultScope).trim() || CONFIG.defaultScope;
    const groupUuid = CONFIG.zeroUuid;
    const workspaceBlock = `${tid}:${groupUuid}:${role}${TENANT_SUFFIX}:${scope}`;
    const svcParts = [];
    (wsPayload.services || []).forEach((svc) => {
      const id = (svc.service_id || "").trim();
      const svcRole = (svc.service_role || "").trim();
      const svcScope = (svc.scope || "").trim() || CONFIG.defaultScope;
      if (!id && !svcRole) return;
      if (!id || !svcRole) {
        throw new Error("Each service must include both a Service ID and a Service Role.");
      }
      svcParts.push(id, svcRole + TENANT_SUFFIX, svcScope);
    });
    if (!svcParts.length) return workspaceBlock;
    const between = servicesSep === "multiline" ? ":\n" : ": ";
    return workspaceBlock + between + svcParts.join(":");
  }

  function buildWorkspaceSegmentV2(wsPayload, servicesSep) {
    const wst = normalizeWsType(wsPayload.type);
    if (wst === WS_TYPE_MSP) {
      return buildMspWorkspaceSegmentInner(wsPayload);
    }
    if (wst === WS_TYPE_TENANT) {
      return buildTenantWorkspaceSegmentInner(wsPayload, servicesSep);
    }

    const workspaceId = (wsPayload.workspace_id || "").trim();
    if (!workspaceId) throw new Error("Workspace ID is required for each workspace in an active group.");

    const role = (wsPayload.role || CONFIG.defaultRole).trim() || CONFIG.defaultRole;
    const scope = (wsPayload.scope || CONFIG.defaultScope).trim() || CONFIG.defaultScope;
    const groupUuid = CONFIG.zeroUuid;
    const workspaceBlock = `${workspaceId}:${groupUuid}:${role}:${scope}`;
    return appendServicesToSegment(workspaceBlock, wsPayload.services, servicesSep);
  }

  function buildGroupRoleStringV2(groupPayload, orgPayload, outerServicesSep) {
    const wsList = (groupPayload.workspaces || []).filter(isWorkspaceSegmentReady);
    if (!wsList.length) {
      throw new Error(
        "Each active group needs at least one workspace ID."
      );
    }

    const hasMsp = wsList.some((w) => normalizeWsType(w.type) === WS_TYPE_MSP);
    const versionPrefix = hasMsp ? "version_2" : "version_1";

    const segments = wsList.map((ws) => {
      const innerSep = wsList.length === 1 ? outerServicesSep : "inline";
      return buildWorkspaceSegmentV2(ws, innerSep);
    });
    const core = segments.join("#");

    if (orgPayload && orgPayload.with_org) {
      const orgId = (orgPayload.organization_id || "").trim();
      if (!orgId) throw new Error("Organization ID is required.");
      const orgRole = (orgPayload.organization_role || DEFAULT_ORG_ROLE).trim() || DEFAULT_ORG_ROLE;
      const orgScope = CONFIG.defaultScope;
      const orgGroupUuid = CONFIG.zeroUuid;
      const orgBlock = `${orgId}:${orgGroupUuid}:${orgRole}:${orgScope}`;
      return `${versionPrefix}#${orgBlock}#${core}`;
    }
    return `${versionPrefix}#${core}`;
  }

  function buildOktaJoinExpression(groups, orgPayload) {
    const active = (groups || []).filter(
      (g) =>
        g.enabled &&
        g.name &&
        (g.workspaces || []).some((w) => isWorkspaceSegmentReady(w))
    );
    if (!active.length) return "";

    const servicesSep = active.length === 1 ? "multiline" : "inline";

    const parts = active.map((g) => {
      const roleString = buildGroupRoleStringV2(g, orgPayload, servicesSep);
      const inner = escapeELSingleQuoted(roleString);
      const gn = escapeGroupNameForDoubleQuotes(g.name);
      return `isMemberOfGroupName("${gn}") ? '${inner}' : ''`;
    });
    return `String.join('',${parts.join(",")})`;
  }

  function readWorkspaceBlock(block) {
    const roleSel = $(".ws-role", block);
    const customRole = $(".ws-custom-role", block);
    const role = roleSel.value === CUSTOM
      ? (customRole.value.trim() || CONFIG.defaultRole)
      : (roleSel.value || CONFIG.defaultRole);
    const listEl = $(".ws-services-list", block);
    const services = $$(".service-row", listEl).map(readServiceRow);
    const wt = readWsTypeFromBlock(block);
    const out = {
      workspace_id: $(".ws-workspace-id", block).value.trim(),
      type: wt,
      role,
      scope: $(".ws-scope", block).value.trim() || CONFIG.defaultScope,
      services,
    };
    if (wt === WS_TYPE_MSP) {
      out.scope_target = readWsTenantToggle(block);
    }
    return out;
  }

  function readGroupPayload(card) {
    const wsRoot = $(".grp-workspaces-list", card);
    const workspaces = $$(".workspace-block", wsRoot).map(readWorkspaceBlock);
    return {
      name: $(".grp-name", card).value.trim(),
      enabled: !!$(".grp-enable", card).checked,
      workspaces,
    };
  }

  function getOrgPayload() {
    return {
      with_org: !!withOrgToggle.checked,
      organization_id: orgIdInput.value.trim(),
      organization_role: getOrgRole(),
    };
  }

  // ----- Live UI feedback (status dots, summary chips, stats bar) -----
  function describeWorkspace(ws) {
    const parts = [];
    const wid = (ws.workspace_id || "").trim();
    if (wid) parts.push(shortId(wid));
    else parts.push("no id");
    const svcCount = (ws.services || []).filter(
      (s) => (s.service_id || "").trim() && (s.service_role || "").trim()
    ).length;
    parts.push(svcCount + " svc");
    return parts.join(" · ");
  }

  function shortId(id) {
    if (!id) return "—";
    const t = String(id).trim();
    if (t.length <= 14) return t;
    return t.slice(0, 6) + "…" + t.slice(-4);
  }

  function setSummary(el, text, opts) {
    if (!el) return;
    el.textContent = text || "";
    el.classList.toggle("is-disabled", !!(opts && opts.disabled));
    el.hidden = !text;
  }

  function setStatusDot(el, state) {
    if (!el) return;
    el.classList.remove("is-ready", "is-incomplete", "is-disabled");
    if (state) el.classList.add("is-" + state);
  }

  function refreshWorkspaceFeedback(wsBlock, isInBranch) {
    const ws = readWorkspaceBlock(wsBlock);
    const services = ws.services || [];
    const svcValid = services.every(
      (s) => !((s.service_id || s.service_role)) || ((s.service_id || "").trim() && (s.service_role || "").trim())
    );
    const svcCountValid = services.filter(
      (s) => (s.service_id || "").trim() && (s.service_role || "").trim()
    ).length;
    const ready = isWorkspaceSegmentReady(ws) && svcValid;
    const dot = $(".ws-status", wsBlock);
    setStatusDot(dot, !isInBranch ? "disabled" : (ready ? "ready" : "incomplete"));
    const summary = $(".ws-summary", wsBlock);
    const wt = normalizeWsType(ws.type);
    const typeTag = wt === WS_TYPE_MSP ? " · MSP" : (wt === WS_TYPE_TENANT ? " · Tenant" : "");
    const idLabel = (ws.workspace_id || "").trim();
    const summaryText = idLabel
      ? shortId(idLabel) + typeTag + " · " + (ws.role || "") + (svcCountValid ? " · " + svcCountValid + " svc" : "")
      : "Workspace ID needed";
    setSummary(summary, summaryText, { disabled: !isInBranch });
    return { ready, svcCountValid };
  }

  function refreshGroupFeedback(groupCard) {
    const payload = readGroupPayload(groupCard);
    const enabled = !!payload.enabled;
    const named = !!payload.name;
    const wsBlocks = $$(".workspace-block", groupCard);
    let svcTotal = 0;
    let wsValidCount = 0;
    wsBlocks.forEach((wsB) => {
      const r = refreshWorkspaceFeedback(wsB, enabled);
      if (r.ready) wsValidCount += 1;
      svcTotal += r.svcCountValid;
    });
    const groupReady = enabled && named && wsValidCount > 0;
    const dot = $(".grp-status", groupCard);
    setStatusDot(dot, !enabled ? "disabled" : (groupReady ? "ready" : "incomplete"));

    const summary = $(".grp-summary", groupCard);
    const wsCount = wsBlocks.length;
    const wsLabel =
      wsCount === 0 ? "No workspaces" : wsCount === 1 ? "1 workspace" : wsCount + " workspaces";
    const svcLabel = svcTotal === 1 ? "1 service" : svcTotal + " services";
    let summaryText = wsLabel + " · " + svcLabel;
    if (!enabled) summaryText = "Disabled · " + summaryText;
    else if (!named) summaryText = "Needs a name · " + summaryText;
    setSummary(summary, summaryText, { disabled: !enabled });
    return { groupReady, wsCount, svcTotal, named, enabled };
  }

  function refreshStatsBar(groups) {
    if (!statsBar) return;
    const totals = groups.reduce(
      (acc, g) => {
        acc.groups += 1;
        acc.workspaces += (g.workspaces || []).filter((w) => isWorkspaceSegmentReady(w)).length;
        acc.services += (g.workspaces || []).reduce((s, w) => {
          return s + (w.services || []).filter(
            (sv) => (sv.service_id || "").trim() && (sv.service_role || "").trim()
          ).length;
        }, 0);
        acc.activeGroups += (g.enabled && (g.name || "").trim() && (g.workspaces || []).some((w) => isWorkspaceSegmentReady(w))) ? 1 : 0;
        return acc;
      },
      { groups: 0, workspaces: 0, services: 0, activeGroups: 0 }
    );
    if (statGroups) statGroups.textContent = String(totals.groups);
    if (statWorkspaces) statWorkspaces.textContent = String(totals.workspaces);
    if (statServices) statServices.textContent = String(totals.services);
    if (statGroupsPill) statGroupsPill.classList.toggle("is-active", totals.activeGroups > 0);
    if (statWorkspacesPill) statWorkspacesPill.classList.toggle("is-active", totals.workspaces > 0);
    if (statServicesPill) statServicesPill.classList.toggle("is-active", totals.services > 0);

    statsBar.classList.remove("is-ready", "is-incomplete");
    if (totals.activeGroups > 0) {
      statsBar.classList.add("is-ready");
      if (statsStatus) statsStatus.textContent =
        totals.activeGroups === 1 ? "1 active branch" : totals.activeGroups + " active branches";
    } else if (totals.groups > 0) {
      statsBar.classList.add("is-incomplete");
      if (statsStatus) statsStatus.textContent = "Fill in group name + workspace ID to activate";
    } else {
      if (statsStatus) statsStatus.textContent = "Add a group to get started";
    }
  }

  function updatePreview() {
    try {
      const cards = $$(".group-card", groupsList);
      cards.forEach(refreshGroupFeedback);
      const groups = cards.map(readGroupPayload);
      refreshStatsBar(groups);
      const expr = buildOktaJoinExpression(groups, getOrgPayload());
      currentResult = expr;
      if (!expr) {
        output.innerHTML = '<span class="placeholder">Add at least one group with a name and at least one workspace ID to see the string.</span>';
      } else {
        output.textContent = expr;
      }
      if (diagramModal && !diagramModal.hidden && diagramBuildFn) refreshDiagram();
      refreshLiveDiagram();
    } catch (err) {
      currentResult = "";
      output.innerHTML = `<span class="placeholder">${escapeHtml(err.message)}</span>`;
      refreshLiveDiagram();
    }
  }

  function escapeHtml(str) {
    return String(str).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  // ----- Generate (just saves current preview) -----
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    updatePreview();
    if (!currentResult) {
      setStatus("Add at least one valid group first.", "error");
      return;
    }
    setStatus("String generated.", "success");
    saveToHistory(currentResult, buildExportPayload());
  });

  // ----- Reset -----
  resetBtn.addEventListener("click", () => {
    form.reset();
    orgIdInput.value = "";
    orgRoleSelect.value = DEFAULT_ORG_ROLE;
    customOrgRoleInput.value = "";
    updateCustomOrgRoleVisibility();
    orgSection.hidden = true;

    groupsList.innerHTML = "";
    reindexGroups();

    currentResult = "";
    output.innerHTML = '<span class="placeholder">Add at least one group with a name and at least one workspace ID to see the string.</span>';
    setStatus("Form reset.", "success");
  });

  // ----- Copy -----
  copyBtn.addEventListener("click", async () => {
    if (!currentResult) { setStatus("Nothing to copy yet.", "error"); return; }
    try {
      await navigator.clipboard.writeText(currentResult);
      setStatus("Copied to clipboard.", "success");
    } catch (err) {
      const ta = document.createElement("textarea");
      ta.value = currentResult;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); setStatus("Copied to clipboard.", "success"); }
      catch (e) { setStatus("Copy failed.", "error"); }
      document.body.removeChild(ta);
    }
  });

  // ----- Export -----
  function downloadBlob(content, filename, mime) {
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  function buildExportPayload() {
    return {
      org: getOrgPayload(),
      groups: $$(".group-card", groupsList).map(readGroupPayload),
      generated_string: currentResult,
    };
  }

  exportTxtBtn.addEventListener("click", () => {
    updatePreview();
    if (!currentResult) { setStatus("Generate a string first.", "error"); return; }
    downloadBlob(currentResult, "okta-role-string.txt", "text/plain");
    setStatus("Downloaded TXT.", "success");
  });

  exportJsonBtn.addEventListener("click", () => {
    updatePreview();
    if (!currentResult) { setStatus("Generate a string first.", "error"); return; }
    const body = JSON.stringify(buildExportPayload(), null, 2);
    downloadBlob(body, "okta-role-string.json", "application/json");
    setStatus("Downloaded JSON.", "success");
  });

  // ----- History -----
  function loadHistory() {
    try {
      const raw = localStorage.getItem(STORAGE_KEYS.history);
      return raw ? JSON.parse(raw) : [];
    } catch (e) { return []; }
  }

  function persistHistory(items) {
    try { localStorage.setItem(STORAGE_KEYS.history, JSON.stringify(items)); } catch (e) {}
  }

  function saveToHistory(str, payload) {
    const items = loadHistory().filter((it) => it.string !== str);
    items.unshift({ string: str, payload, at: new Date().toISOString() });
    while (items.length > MAX_HISTORY) items.pop();
    persistHistory(items);
    renderHistory();
  }

  function formatTime(iso) {
    try {
      const d = new Date(iso);
      return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    } catch (e) { return iso; }
  }

  function renderHistory() {
    const items = loadHistory();
    historyList.innerHTML = "";
    if (!items.length) {
      const empty = document.createElement("li");
      empty.className = "history-empty";
      empty.textContent = "No saved strings yet.";
      historyList.appendChild(empty);
      return;
    }
    items.forEach((item, idx) => {
      const li = document.createElement("li");
      li.className = "history-item";

      const meta = document.createElement("div");
      meta.className = "history-meta";
      const s = document.createElement("div");
      s.className = "history-string";
      s.textContent = item.string;
      s.title = item.string;
      const t = document.createElement("div");
      t.className = "history-time";
      t.textContent = formatTime(item.at);
      meta.appendChild(s);
      meta.appendChild(t);

      const actions = document.createElement("div");
      actions.className = "history-actions";

      const loadBtn = document.createElement("button");
      loadBtn.type = "button";
      loadBtn.className = "chip";
      loadBtn.textContent = "Load";
      loadBtn.addEventListener("click", () => loadFromHistory(item));

      const copyOne = document.createElement("button");
      copyOne.type = "button";
      copyOne.className = "chip";
      copyOne.textContent = "Copy";
      copyOne.addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(item.string); setStatus("Copied to clipboard.", "success"); }
        catch (e) { setStatus("Copy failed.", "error"); }
      });

      const delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.className = "chip danger";
      delBtn.textContent = "Delete";
      delBtn.addEventListener("click", () => {
        const arr = loadHistory();
        arr.splice(idx, 1);
        persistHistory(arr);
        renderHistory();
      });

      actions.appendChild(loadBtn);
      actions.appendChild(copyOne);
      actions.appendChild(delBtn);

      li.appendChild(meta);
      li.appendChild(actions);
      historyList.appendChild(li);
    });
  }

  function loadFromHistory(item) {
    const p = item.payload || {};

    withOrgToggle.checked = !!(p.org && p.org.with_org);
    orgIdInput.value = (p.org && p.org.organization_id) || "";
    if (p.org && p.org.organization_role) {
      const orgBuiltin = Array.from(orgRoleSelect.options).map((o) => o.value);
      if (orgBuiltin.includes(p.org.organization_role)) {
        orgRoleSelect.value = p.org.organization_role;
        customOrgRoleInput.value = "";
      } else {
        orgRoleSelect.value = CUSTOM;
        customOrgRoleInput.value = p.org.organization_role;
      }
    }
    updateOrgVisibility(false);

    groupsList.innerHTML = "";
    (p.groups || []).forEach(addGroup);
    reindexGroups();

    currentResult = item.string || "";
    if (currentResult) output.textContent = currentResult;
    setStatus("Loaded from history.", "success");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  clearHistoryBtn.addEventListener("click", () => {
    if (!loadHistory().length) { setStatus("History is already empty.", "error"); return; }
    if (!confirm("Clear all saved strings from history?")) return;
    persistHistory([]);
    renderHistory();
    setStatus("History cleared.", "success");
  });

  // ----- Keyboard shortcuts -----
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && wsTypeModal && !wsTypeModal.hidden) {
      e.preventDefault();
      closeWsTypeModal(null);
      return;
    }
    if (e.key === "Escape" && diagramModal && !diagramModal.hidden) {
      e.preventDefault();
      closeDiagramModal();
      return;
    }
    const mod = e.metaKey || e.ctrlKey;
    if (!mod) return;
    if (e.key === "Enter") {
      e.preventDefault();
      if (typeof form.requestSubmit === "function") form.requestSubmit();
      else form.dispatchEvent(new Event("submit", { cancelable: true }));
    } else if (e.shiftKey && (e.key === "C" || e.key === "c")) {
      e.preventDefault();
      copyBtn.click();
    }
  });

  // ----- Init -----
  initTheme();
  updateCustomOrgRoleVisibility();
  updateOrgVisibility(false);
  reindexGroups();
  renderHistory();
  updatePreview();
  initLiveDiagram();
})();

