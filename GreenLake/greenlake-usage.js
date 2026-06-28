/**
 * Lightweight usage tracking for Platform Tools.
 * No login required — uses a persistent visitor id + optional name label.
 */
(function () {
  "use strict";

  var API_BASE = "/gldash/api/usage";
  var VID_KEY = "gl_visitor_id";
  var LABEL_KEY = "gl_user_label";
  var LABEL_PROMPT_KEY = "gl_label_prompted";

  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "v-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
  }

  function getVisitorId() {
    try {
      var id = localStorage.getItem(VID_KEY);
      if (!id || id.length < 8) {
        id = uuid();
        localStorage.setItem(VID_KEY, id);
      }
      return id;
    } catch (e) {
      return "anon-" + Date.now();
    }
  }

  function getUserLabel() {
    try {
      return (localStorage.getItem(LABEL_KEY) || "").trim();
    } catch (e) {
      return "";
    }
  }

  function setUserLabel(label) {
    try {
      localStorage.setItem(LABEL_KEY, String(label || "").trim().slice(0, 128));
    } catch (e) {}
  }

  function maybePromptLabel() {
    try {
      if (localStorage.getItem(LABEL_PROMPT_KEY)) return;
      localStorage.setItem(LABEL_PROMPT_KEY, "1");
    } catch (e) {
      return;
    }
    setTimeout(function () {
      if (getUserLabel()) return;
      var name = window.prompt(
        "Optional: enter your name or email so admins can see who used the tools (stored locally only). Leave blank to skip."
      );
      if (name && name.trim()) setUserLabel(name.trim());
    }, 1500);
  }

  function toolFromPath() {
    var p = (window.location.pathname || "").toLowerCase();
    if (p.indexOf("devicemanagement") >= 0) return "devices";
    if (p.indexOf("subscription") >= 0) return "subscriptions";
    if (p.indexOf("usermanagement") >= 0) return "user-hierarchy";
    if (p.indexOf("userroles") >= 0) return "user-roles";
    if (p.indexOf("transferdevices") >= 0) return "transfer-devices";
    if (p.indexOf("transfersubscriptions") >= 0) return "transfer-subscriptions";
    if (p.indexOf("sso-tools") >= 0) return "sso-tools";
    if (p.indexOf("greenlaketools") >= 0) return "home";
    if (p.indexOf("/gldash") >= 0) return "dashboard";
    return "unknown";
  }

  function sendEvent(action, detail) {
    var payload = {
      visitor_id: getVisitorId(),
      action: action || "event",
      tool: toolFromPath(),
      user_label: getUserLabel(),
      page: window.location.href,
      detail: detail ? String(detail).slice(0, 200) : null,
    };
    var body = JSON.stringify(payload);
    if (navigator.sendBeacon) {
      try {
        var blob = new Blob([body], { type: "application/json" });
        if (navigator.sendBeacon(API_BASE + "/event", blob)) return;
      } catch (e) {}
    }
    fetch(API_BASE + "/event", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: body,
      keepalive: true,
    }).catch(function () {});
  }

  function track(action, detail) {
    sendEvent(action, detail);
  }

  function wrapFetch() {
    if (window.__glUsageFetchWrapped) return;
    window.__glUsageFetchWrapped = true;
    var orig = window.fetch;
    window.fetch = function (input, init) {
      var url = typeof input === "string" ? input : (input && input.url) || "";
      if (url.indexOf("/api/") >= 0) {
        var endpoint = url.split("?")[0].replace(window.location.origin, "");
        track("api_call", endpoint.slice(0, 200));
      }
      return orig.apply(this, arguments);
    };
  }

  function checkAdminCards() {
    fetch("/gldash/api/feedback/whoami", { credentials: "include" })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (!data || !data.is_admin) return;
        var fb = document.getElementById("tile-feedback-inbox");
        var us = document.getElementById("tile-usage-log");
        if (fb) fb.style.display = "";
        if (us) us.style.display = "";
      })
      .catch(function () {});
  }

  function init() {
    getVisitorId();
    wrapFetch();
    track("page_view");
    maybePromptLabel();
    checkAdminCards();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.GreenLakeUsage = {
    track: track,
    getVisitorId: getVisitorId,
    getUserLabel: getUserLabel,
    setUserLabel: setUserLabel,
    toolFromPath: toolFromPath,
  };
})();
