/**
 * Global light/dark theme for GreenLake Platform Tools, Dashboard, and SSO Tools.
 * Default: light (unchanged). Dark palette matches /sso-tools/ (data-theme="dark").
 */
(function () {
  "use strict";

  var KEY = "greenlake-theme";
  var LEGACY = "okta-role-string-theme";

  function readStored() {
    try {
      var v = localStorage.getItem(KEY);
      if (v === "light" || v === "dark") return v;
      v = localStorage.getItem(LEGACY);
      if (v === "light" || v === "dark") {
        localStorage.setItem(KEY, v);
        return v;
      }
    } catch (e) {}
    return null;
  }

  function apply(theme) {
    if (theme !== "light" && theme !== "dark") theme = "light";
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem(KEY, theme);
    } catch (e) {}
  }

  function current() {
    return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
  }

  function init() {
    apply(readStored() || "light");
  }

  function bindToggles() {
    var nodes = document.querySelectorAll("#themeToggle, [data-gl-theme-toggle]");
    for (var i = 0; i < nodes.length; i++) {
      var btn = nodes[i];
      if (btn.getAttribute("data-gl-theme-bound") === "1") continue;
      btn.setAttribute("data-gl-theme-bound", "1");
      btn.addEventListener("click", function () {
        apply(current() === "dark" ? "light" : "dark");
      });
    }
  }

  init();

  window.GreenLakeTheme = {
    KEY: KEY,
    apply: apply,
    init: init,
    bindToggles: bindToggles,
    current: current,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindToggles);
  } else {
    bindToggles();
  }
})();
