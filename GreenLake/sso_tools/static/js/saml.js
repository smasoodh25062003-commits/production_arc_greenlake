(function () {
  "use strict";

  const THEME_KEY = "greenlake-theme";
  const LEGACY_THEME_KEY = "okta-role-string-theme";

  const SECTIONS = [
    { id: "identity", title: "Identity & SSO", ids: ["idp", "entity_id", "acs_url"] },
    { id: "security", title: "Security & timing", ids: ["time_window", "certificate"] },
    { id: "attributes", title: "User attributes", ids: ["nameid", "firstname", "lastname", "hpe_ccs"] },
  ];

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  const els = {
    themeToggle: $("#themeToggle"),
    dropzone: $("#samlDropzone"),
    fileInput: $("#samlFileInput"),
    btnRun: $("#samlRunBtn"),
    btnReset: $("#samlResetBtn"),
    btnExport: $("#samlExportBtn"),
    loaded: $("#samlLoaded"),
    loadedName: $("#samlLoadedName"),
    loadedSize: $("#samlLoadedSize"),
    overlay: $("#samlOverlay"),
    dashboard: $("#samlDashboard"),
    ring: $("#samlProgressRing"),
    ringFg: $("#samlRingFg"),
    ringText: $("#samlRingText"),
    verdict: $("#samlVerdict"),
    verdictText: $("#samlVerdictText"),
    counters: $("#samlCounters"),
    filterBar: $("#samlFilterBar"),
    results: $("#samlResults"),
    empty: $("#samlEmpty"),
    apiDot: $("#samlApiDot"),
  };

  let state = {
    file: null,
    checks: [],
    allAttrs: null,
    filter: "all",
    wizardIndex: 0,
    expanded: new Set(),
    manualDecision: Object.create(null),
  };

  function applyTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem(THEME_KEY, t); } catch (e) {}
  }

  function initTheme() {
    if (window.GreenLakeTheme) {
      window.GreenLakeTheme.bindToggles();
      return;
    }
    let t = null;
    try { t = localStorage.getItem(THEME_KEY); } catch (e) {}
    if (!t) {
      try {
        const leg = localStorage.getItem(LEGACY_THEME_KEY);
        if (leg === "light" || leg === "dark") t = leg;
      } catch (e) {}
    }
    if (!t) t = "light";
    applyTheme(t);
  }

  function apiBase() {
    if (typeof window !== "undefined" && window.__SSO_TOOLS_ROOT__) {
      return String(window.__SSO_TOOLS_ROOT__).replace(/\/$/, "");
    }
    return "";
  }

  async function ping() {
    if (!els.apiDot) return;
    els.apiDot.className = "saml-api-dot";
    els.apiDot.style.opacity = "0.7";
    els.apiDot.style.background = "";
    try {
      const r = await fetch(apiBase() + "/api/health", { signal: AbortSignal.timeout(4000) });
      els.apiDot.style.opacity = r.ok ? "1" : "0.6";
      els.apiDot.style.background = r.ok ? "var(--primary)" : "var(--danger)";
    } catch (e) {
      els.apiDot.style.opacity = "0.6";
      els.apiDot.style.background = "var(--danger)";
    }
  }

  function formatBytes(n) {
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / (1024 * 1024)).toFixed(1) + " MB";
  }

  function pickFile(file) {
    state.file = file;
    if (els.loadedName) els.loadedName.textContent = file.name;
    if (els.loadedSize) els.loadedSize.textContent = formatBytes(file.size);
    if (els.loaded) els.loaded.hidden = false;
    state.checks = [];
    state.allAttrs = null;
    state.wizardIndex = 0;
    state.expanded.clear();
    state.manualDecision = Object.create(null);
    render();
  }

  function resetAll() {
    state.file = null;
    state.checks = [];
    state.allAttrs = null;
    state.wizardIndex = 0;
    state.expanded.clear();
    state.manualDecision = Object.create(null);
    if (els.fileInput) els.fileInput.value = "";
    if (els.loaded) els.loaded.hidden = true;
    render();
  }

  function getCheckById(id) {
    return state.checks.find((c) => c.id === id);
  }

  function effectiveStatus(c) {
    if (!c) return "pending";
    if (c.type === "manual" && state.manualDecision[c.id]) {
      return state.manualDecision[c.id] === "pass" ? "pass" : "fail";
    }
    return c.status || "pending";
  }

  function cardClassForStatus(st) {
    if (st === "pass") return "is-pass";
    if (st === "fail") return "is-fail";
    if (st === "warn") return "is-warn";
    if (st === "manual") return "is-manual";
    if (st === "skipped") return "is-skipped";
    return "is-pending";
  }

  function pillClassForStatus(st) {
    if (st === "pass") return "is-pass";
    if (st === "fail") return "is-fail";
    if (st === "warn") return "is-warn";
    if (st === "manual") return "is-manual";
    if (st === "skipped") return "is-skipped";
    return "is-pending";
  }

  function pillLabel(st) {
    if (st === "pass") return "Pass";
    if (st === "fail") return "Fail";
    if (st === "warn") return "Warn";
    if (st === "manual") return "Review";
    if (st === "skipped") return "Skipped";
    return "Pending";
  }

  function needsUserAction(c) {
    if (!c) return false;
    const st = effectiveStatus(c);
    if (st === "fail") return true;
    if (c.type === "manual" && c.status === "manual" && !state.manualDecision[c.id]) return true;
    return false;
  }

  function passesFilter(c) {
    const st = effectiveStatus(c);
    const f = state.filter;
    if (f === "all") return true;
    if (f === "fail") return needsUserAction(c);
    if (f === "warn") return st === "warn";
    if (f === "manual") return c.type === "manual";
    if (f === "auto") return c.type === "auto" || c.type === "skipped";
    return true;
  }

  function getOrderedFilteredChecks() {
    const sectionIds = new Set();
    SECTIONS.forEach((sec) => sec.ids.forEach((id) => sectionIds.add(id)));

    const out = [];
    SECTIONS.forEach((sec) => {
      sec.ids.forEach((id) => {
        const c = getCheckById(id);
        if (c && passesFilter(c)) {
          out.push({ check: c, sectionTitle: sec.title, sectionId: sec.id });
        }
      });
    });

    const extra = state.checks
      .filter((c) => c && c.id && !sectionIds.has(c.id) && passesFilter(c))
      .slice()
      .sort((a, b) => (Number(a.step) || 0) - (Number(b.step) || 0));
    extra.forEach((c) => {
      out.push({ check: c, sectionTitle: "Other", sectionId: "other" });
    });

    return out;
  }

  function clampWizardIndex() {
    const list = getOrderedFilteredChecks();
    if (!list.length) {
      state.wizardIndex = 0;
      return;
    }
    if (state.wizardIndex < 0) state.wizardIndex = 0;
    if (state.wizardIndex >= list.length) state.wizardIndex = list.length - 1;
  }

  function computeCounts() {
    const n = state.checks.length;
    let pass = 0, fail = 0, warn = 0, manual = 0, skipped = 0, pending = 0;
    state.checks.forEach((c) => {
      const st = effectiveStatus(c);
      if (c.type === "manual" && !state.manualDecision[c.id] && c.status === "manual") manual += 1;
      if (st === "pass") pass += 1;
      else if (st === "fail") fail += 1;
      else if (st === "warn") warn += 1;
      else if (st === "skipped") skipped += 1;
      else if (st === "manual") { /* counted above */ }
      else pending += 1;
    });
    const actionableFail = state.checks.some((c) => effectiveStatus(c) === "fail");
    const needsReview = state.checks.some(
      (c) => c.type === "manual" && c.status === "manual" && !state.manualDecision[c.id]
    );
    const hasWarn = state.checks.some((c) => effectiveStatus(c) === "warn");
    return { n, pass, fail, warn, manual, skipped, pending, actionableFail, needsReview, hasWarn };
  }

  function updateDashboard() {
    if (!els.dashboard || !state.checks.length) {
      if (els.dashboard) els.dashboard.hidden = true;
      return;
    }
    els.dashboard.hidden = false;
    const cnt = computeCounts();
    const completed = cnt.pass + cnt.fail + cnt.warn + cnt.skipped;
    const pct = cnt.n ? Math.round((completed / cnt.n) * 100) : 0;

    const r = 36;
    const circ = 2 * Math.PI * r;
    if (els.ringFg) {
      els.ringFg.style.strokeDasharray = String(circ);
      els.ringFg.style.strokeDashoffset = String(circ * (1 - pct / 100));
    }
    if (els.ringText) els.ringText.textContent = pct + "%";
    if (els.ring) {
      els.ring.classList.remove("is-fail", "is-warn");
      if (cnt.actionableFail) els.ring.classList.add("is-fail");
      else if (cnt.hasWarn || cnt.needsReview) els.ring.classList.add("is-warn");
    }

    let verdict = "All checks passed";
    let vClass = "saml-verdict is-ready";
    if (cnt.actionableFail) {
      verdict = "Blocked — fix failing checks";
      vClass = "saml-verdict is-blocked";
    } else if (cnt.needsReview) {
      verdict = "Needs your review on manual steps";
      vClass = "saml-verdict is-attention";
    } else if (cnt.hasWarn) {
      verdict = "OK with warnings — review details";
      vClass = "saml-verdict is-attention";
    }
    if (els.verdict) els.verdict.className = vClass;
    if (els.verdictText) els.verdictText.textContent = verdict;

    if (els.counters) {
      els.counters.innerHTML = [
        `<span class="saml-counter is-pass"><strong>${cnt.pass}</strong> pass</span>`,
        `<span class="saml-counter is-fail"><strong>${cnt.fail}</strong> fail</span>`,
        `<span class="saml-counter is-warn"><strong>${cnt.warn}</strong> warn</span>`,
        `<span class="saml-counter"><strong>${cnt.skipped}</strong> skipped</span>`,
        `<span class="saml-counter"><strong>${cnt.manual}</strong> to verify</span>`,
      ].join("");
    }
  }

  function copyText(btn, text) {
    const t = String(text || "");
    const done = () => {
      btn.classList.add("is-copied");
      btn.textContent = "Copied";
      setTimeout(() => { btn.classList.remove("is-copied"); btn.textContent = "Copy"; }, 1600);
    };
    function fallback() {
      try {
        const ta = document.createElement("textarea");
        ta.value = t;
        ta.setAttribute("readonly", "");
        ta.style.position = "fixed";
        ta.style.left = "-9999px";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
        done();
      } catch (e) {}
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(t).then(done).catch(fallback);
    } else {
      fallback();
    }
  }

  function renderCardBody(c) {
    const st = effectiveStatus(c);
    const parts = [];

    const addRow = (label, value, copyable) => {
      if (value === undefined || value === null || value === "") return;
      const v = String(value);
      const copyBtn = copyable
        ? `<button type="button" class="saml-copy" data-copy="${encodeURIComponent(v)}">Copy</button>`
        : "";
      parts.push(
        `<div class="saml-row"><div class="saml-row-label">${escapeHtml(label)}</div>` +
        `<div class="saml-row-value"><span>${escapeHtml(v)}</span>${copyBtn}</div></div>`
      );
    };

    if (c.id === "entity_id" || c.id === "acs_url") {
      if (c.status === "fail" && c.expected && c.found) {
        parts.push(
          `<div class="saml-diff">` +
          `<div class="saml-diff-col col-expected"><span class="saml-diff-label">Expected</span>` +
          `<span class="saml-diff-val">${escapeHtml(c.expected)}</span></div>` +
          `<div class="saml-diff-col col-found"><span class="saml-diff-label">Found</span>` +
          `<span class="saml-diff-val">${escapeHtml(c.found)}</span></div></div>`
        );
      } else {
        addRow("Found", c.found, true);
        addRow("Expected", c.expected, true);
      }
    } else if (c.id === "idp") {
      addRow("Detected", c.found || c.name, false);
      addRow("Issuer", c.issuer, true);
    } else if (c.id === "time_window") {
      addRow("Not before", c.not_before, true);
      addRow("Not on or after", c.not_on_or_after, true);
      if (c.diff_minutes != null) addRow("Window (min)", String(c.diff_minutes), false);
    } else if (c.id === "certificate") {
      const certs = c.certs || [];
      if (certs.length) {
        const items = certs.map((cr, i) => {
          if (cr.masked) {
            return `<div class="saml-cert-item"><strong>Certificate ${i + 1}</strong> — masked in export</div>`;
          }
          const rows = [];
          if (cr.not_after) rows.push(`<div class="cert-row"><span class="label">Expires</span><span class="value">${escapeHtml(cr.not_after)}</span></div>`);
          if (cr.days_remaining != null) rows.push(`<div class="cert-row"><span class="label">Days left</span><span class="value">${cr.days_remaining}</span></div>`);
          if (cr.subject) rows.push(`<div class="cert-row"><span class="label">Subject</span><span class="value">${escapeHtml(cr.subject)}</span></div>`);
          if (cr.error) rows.push(`<div class="cert-row"><span class="label">Error</span><span class="value">${escapeHtml(cr.error)}</span></div>`);
          return `<div class="saml-cert-item"><strong>Certificate ${i + 1}</strong>${rows.join("")}</div>`;
        });
        parts.push(`<div class="saml-cert-list">${items.join("")}</div>`);
      }
    } else {
      addRow("Attribute", c.attr_label || c.attr_name, true);
      addRow("Value", c.found, true);
      if (c.not_found_reason) {
        parts.push(`<div class="saml-note">${escapeHtml(c.not_found_reason)}</div>`);
      }
      if (c.instruction) {
        parts.push(`<div class="saml-note">${escapeHtml(stripTags(c.instruction))}</div>`);
      }
    }

    if (c.note) {
      const fixCls = c.status === "pass" ? "saml-fix is-pass" : "saml-fix";
      parts.push(`<div class="${fixCls}"><span class="saml-fix-label">Note</span>${escapeHtml(c.note)}</div>`);
    }
    if (c.fix) {
      parts.push(`<div class="saml-fix"><span class="saml-fix-label">How to fix</span>${escapeHtml(c.fix)}</div>`);
    }

    if (c.type === "manual" && c.status === "manual") {
      const d = state.manualDecision[c.id];
      parts.push(
        `<div class="saml-manual-actions" data-manual-id="${escapeAttr(c.id)}">` +
        `<button type="button" class="${d === "pass" ? "is-pass" : ""}" data-decision="pass">Verified OK</button>` +
        `<button type="button" class="${d === "fail" ? "is-fail" : ""}" data-decision="fail">Mismatch</button>` +
        `</div>`
      );
    }

    return parts.join("");
  }

  function stripTags(s) {
    return String(s).replace(/<[^>]*>/g, "");
  }

  function decodeCopyAttr(raw) {
    if (raw == null || raw === "") return "";
    try {
      return decodeURIComponent(raw);
    } catch (e) {
      return raw;
    }
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));
  }

  function escapeAttr(s) {
    return escapeHtml(s).replace(/"/g, "&quot;");
  }

  function renderOneCardArticle(c, opts) {
    const st = effectiveStatus(c);
    const wizard = opts && opts.wizard;
    const inline = (c.found && String(c.found).length > 48)
      ? String(c.found).slice(0, 45) + "…"
      : (c.found || c.note || "—");
    const foot = wizard
      ? ""
      : (
        `<div class="saml-card-foot">` +
        `<span class="saml-found-inline" title="${escapeAttr(inline)}">${escapeHtml(inline)}</span>` +
        `<span class="saml-toggle-chev" aria-hidden="true">▾</span></div>`
      );
    const expandedClass = wizard || state.expanded.has(c.id) ? "is-expanded" : "";
    return (
      `<article class="saml-card ${wizard ? "saml-card-wizard" : ""} ${cardClassForStatus(st)} ${expandedClass}" data-card-id="${escapeAttr(c.id)}" tabindex="0">` +
      `<div class="saml-card-head">` +
      `<span class="saml-step-num">${c.step}</span>` +
      `<span class="saml-card-icon" aria-hidden="true">${c.icon || "•"}</span>` +
      `<div class="saml-card-title-wrap">` +
      `<div class="saml-card-title">${escapeHtml(c.name)}</div>` +
      `<div class="saml-card-sub">${escapeHtml(c.sub || "")}</div></div>` +
      `<span class="saml-status-pill ${pillClassForStatus(st)}">${pillLabel(st)}</span></div>` +
      foot +
      `<div class="saml-card-body">${renderCardBody(c)}</div></article>`
    );
  }

  function renderCards() {
    if (!els.results) return;
    if (!state.checks.length) {
      els.results.innerHTML = "";
      if (els.empty) els.empty.classList.remove("is-hidden");
      return;
    }
    if (els.empty) els.empty.classList.add("is-hidden");

    const list = getOrderedFilteredChecks();
    clampWizardIndex();

    if (!list.length) {
      els.results.innerHTML = `<div class="saml-wizard-empty"><p>No checks match this filter.</p><p class="saml-wizard-empty-hint">Choose another filter or reset filters to <strong>All</strong>.</p></div>`;
      return;
    }

    const idx = state.wizardIndex;
    const n = list.length;
    const pos = idx + 1;
    const { check: c, sectionTitle } = list[idx];

    const dots = list.map((_, i) => {
      const cur = i === idx ? ' aria-current="step"' : "";
      return (
        `<button type="button" class="saml-wizard-dot ${i === idx ? "is-current" : ""}" data-wizard-jump="${i}"` +
        ` aria-label="Go to check ${i + 1} of ${n}"${cur}></button>`
      );
    }).join("");

    const navHtml =
      `<div class="saml-wizard" role="region" aria-label="Validation checks, one at a time">` +
      `<div class="saml-wizard-nav">` +
      `<button type="button" class="btn btn-secondary saml-wizard-prev" ${idx <= 0 ? "disabled" : ""}>Previous</button>` +
      `<div class="saml-wizard-meta">` +
      `<span class="saml-wizard-pos"><strong>${pos}</strong><span class="saml-wizard-pos-sep">/</span>${n}</span>` +
      `<span class="saml-wizard-section">${escapeHtml(sectionTitle)}</span>` +
      `</div>` +
      `<button type="button" class="btn btn-secondary saml-wizard-next" ${idx >= n - 1 ? "disabled" : ""}>Next</button>` +
      `</div>` +
      `<div class="saml-wizard-dots" role="tablist" aria-label="Jump to check">${dots}</div>` +
      `<div class="saml-wizard-card-wrap">${renderOneCardArticle(c, { wizard: true })}</div>` +
      `</div>`;

    els.results.innerHTML = navHtml;

    const prev = $(".saml-wizard-prev", els.results);
    const next = $(".saml-wizard-next", els.results);
    if (prev) {
      prev.addEventListener("click", () => {
        state.wizardIndex = Math.max(0, idx - 1);
        render();
      });
    }
    if (next) {
      next.addEventListener("click", () => {
        state.wizardIndex = Math.min(n - 1, idx + 1);
        render();
      });
    }
    $$("[data-wizard-jump]", els.results).forEach((b) => {
      b.addEventListener("click", () => {
        const j = parseInt(b.getAttribute("data-wizard-jump"), 10);
        if (!Number.isNaN(j)) {
          state.wizardIndex = j;
          render();
        }
      });
    });

    $$(".saml-copy", els.results).forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        copyText(btn, decodeCopyAttr(btn.getAttribute("data-copy")));
      });
    });

    $$(".saml-manual-actions", els.results).forEach((wrap) => {
      wrap.querySelectorAll("button[data-decision]").forEach((b) => {
        b.addEventListener("click", (e) => {
          e.stopPropagation();
          const id = wrap.getAttribute("data-manual-id");
          const dec = b.getAttribute("data-decision");
          state.manualDecision[id] = dec;
          render();
        });
      });
    });
  }

  function renderFilters() {
    if (!els.filterBar || !state.checks.length) {
      if (els.filterBar) els.filterBar.innerHTML = "";
      return;
    }
    const cnt = computeCounts();
    const filters = [
      { id: "all", label: "All", count: state.checks.length },
      { id: "fail", label: "Action", count: state.checks.filter(needsUserAction).length },
      { id: "warn", label: "Warnings", count: cnt.warn },
      { id: "manual", label: "Manual", count: state.checks.filter((c) => c.type === "manual").length },
      { id: "auto", label: "Automated", count: state.checks.filter((c) => c.type === "auto" || c.type === "skipped").length },
    ];
    els.filterBar.innerHTML = filters.map((f) =>
      `<button type="button" class="saml-filter-chip ${state.filter === f.id ? "is-active" : ""}" data-filter="${f.id}">` +
      `${escapeHtml(f.label)}<span class="filter-count">${f.count}</span></button>`
    ).join("");

    $$("[data-filter]", els.filterBar).forEach((btn) => {
      btn.addEventListener("click", () => {
        state.filter = btn.getAttribute("data-filter");
        state.wizardIndex = 0;
        renderFilters();
        renderCards();
      });
    });
  }

  function render() {
    updateDashboard();
    renderFilters();
    renderCards();
    if (els.btnRun) els.btnRun.disabled = !state.file;
    if (els.btnExport) els.btnExport.disabled = !state.checks.length;
  }

  async function runChecks() {
    if (!state.file) return;
    if (els.overlay) els.overlay.hidden = false;
    try {
      const fd = new FormData();
      fd.append("file", state.file);
      const resp = await fetch(apiBase() + "/api/parse", { method: "POST", body: fd });
      const data = await resp.json();
      if (!data.success) throw new Error(data.error || "Parse failed");
      state.checks = data.checks || [];
      state.allAttrs = data.all_attributes || null;
      state.manualDecision = Object.create(null);
      state.expanded.clear();
      state.wizardIndex = 0;
      state.filter = "all";
      render();
    } catch (err) {
      alert(err.message || String(err));
    } finally {
      if (els.overlay) els.overlay.hidden = true;
    }
  }

  function exportReport() {
    if (!state.checks.length) return;
    const lines = [];
    lines.push("HPE GreenLake — SAML validation report");
    lines.push("Generated: " + new Date().toISOString());
    if (state.file) lines.push("File: " + state.file.name);
    lines.push("");
    state.checks.forEach((c) => {
      lines.push(`--- ${c.step}. ${c.name} (${c.id}) ---`);
      lines.push("Status: " + effectiveStatus(c));
      lines.push("Type: " + c.type);
      if (c.found != null) lines.push("Found: " + c.found);
      if (c.expected != null) lines.push("Expected: " + c.expected);
      if (c.note) lines.push("Note: " + c.note);
      if (c.fix) lines.push("Fix: " + c.fix);
      lines.push("");
    });
    if (state.allAttrs && typeof state.allAttrs === "object") {
      lines.push("--- All SAML attributes ---");
      Object.keys(state.allAttrs).forEach((k) => {
        lines.push(k + ": " + state.allAttrs[k]);
      });
    }
    const blob = new Blob([lines.join("\n")], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "saml-validation-report.txt";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  function setupDrop() {
    const z = els.dropzone;
    if (!z) return;
    z.addEventListener("dragover", (e) => { e.preventDefault(); z.classList.add("is-over"); });
    z.addEventListener("dragleave", () => z.classList.remove("is-over"));
    z.addEventListener("drop", (e) => {
      e.preventDefault();
      z.classList.remove("is-over");
      const f = e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) pickFile(f);
    });
    z.addEventListener("click", () => els.fileInput && els.fileInput.click());
  }

  function init() {
    initTheme();
    if (els.themeToggle) {
      els.themeToggle.addEventListener("click", () => {
        const next =
          document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
        if (window.GreenLakeTheme) window.GreenLakeTheme.apply(next);
        else applyTheme(next);
      });
    }
    setupDrop();
    if (els.fileInput) {
      els.fileInput.addEventListener("change", (e) => {
        const f = e.target.files && e.target.files[0];
        if (f) pickFile(f);
      });
    }
    if (els.btnRun) els.btnRun.addEventListener("click", runChecks);
    if (els.btnReset) els.btnReset.addEventListener("click", resetAll);
    if (els.btnExport) els.btnExport.addEventListener("click", exportReport);
    ping();
    render();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
