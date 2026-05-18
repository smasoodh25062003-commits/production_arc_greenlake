/**
 * Global progress bar for CCS Manager API calls.
 * Loaded after page scripts; provides window.setLoader and window.CCSProgress.
 */
(function () {
  "use strict";

  var PREFIX = function () {
    return window.__GL_PREFIX__ || "/gldash";
  };

  var activeLoaders = new Set();
  var determinate = false;

  var OP_LABELS = {
    validateLoader: "Validating session",
    devLoader: "Transferring devices",
    bulkMoveLoader: "Bulk moving devices",
    subLoader: "Transferring subscriptions",
    usrLoader: "Querying users",
    devQueryLoader: "Querying devices",
    unclaimLoader: "Unclaiming devices",
    claimLoader: "Claiming devices",
    ordLoader: "Querying orders",
    subQueryLoader: "Querying subscriptions",
    delUserLoader: "Processing users",
    auditLoader: "Auditing customer apps",
  };

  var LOCAL_PROGRESS = {
    devQueryLoader: { bar: "devQueryProgressBar", text: "devQueryLoaderText" },
    delUserLoader: { bar: "delUserProgressBar", text: "delUserLoaderText" },
  };

  function el(id) {
    return document.getElementById(id);
  }

  function root() {
    return el("ccsGlobalProgress");
  }

  function bar() {
    return el("ccsGlobalProgressBar");
  }

  function label() {
    return el("ccsGlobalProgressLabel");
  }

  function pctEl() {
    return el("ccsGlobalProgressPct");
  }

  function show() {
    var r = root();
    if (!r) return;
    r.hidden = false;
    r.classList.add("is-active");
    r.setAttribute("aria-hidden", "false");
  }

  function hide() {
    var r = root();
    if (!r) return;
    r.classList.remove("is-active", "is-indeterminate");
    r.hidden = true;
    r.setAttribute("aria-hidden", "true");
    if (bar()) bar().style.width = "0%";
    if (pctEl()) pctEl().textContent = "";
  }

  function setBar(pct, message) {
    var b = bar();
    var r = root();
    if (!b || !r) return;
    var p = Math.max(0, Math.min(100, pct));
    r.classList.remove("is-indeterminate");
    b.style.width = p + "%";
    if (label() && message) label().textContent = message;
    if (pctEl()) pctEl().textContent = p > 0 && p < 100 ? p + "%" : p >= 100 ? "100%" : "";
  }

  function setIndeterminate(message) {
    var r = root();
    if (!r) return;
    r.classList.add("is-indeterminate");
    if (bar()) bar().style.width = "";
    if (label()) label().textContent = message || "Working…";
    if (pctEl()) pctEl().textContent = "";
  }

  function syncFromLoaders() {
    if (activeLoaders.size === 0) {
      hide();
      determinate = false;
      return;
    }
    var first = activeLoaders.values().next().value;
    var msg = OP_LABELS[first] || "Working…";
    show();
    if (determinate) {
      /* keep current % until next progress event */
      if (label()) label().textContent = msg + "…";
    } else {
      setIndeterminate(msg + "…");
    }
  }

  window.CCSProgress = {
    beginOperation: function (message, isDeterminate) {
      show();
      determinate = !!isDeterminate;
      if (isDeterminate) {
        setBar(0, message || "Starting…");
      } else {
        setIndeterminate(message || "Working…");
      }
    },

    set: function (pct, message) {
      show();
      determinate = true;
      root() && root().classList.remove("is-indeterminate");
      setBar(pct, message);
    },

    applyStreamEvent: function (event, loaderId) {
      if (!event || event.type !== "progress") return;
      var total = event.total || 1;
      var current = event.current || 0;
      var pct = Math.round((current / total) * 100) || 0;
      var msg =
        event.message ||
        (OP_LABELS[loaderId] || "Processing") + " (" + current + "/" + total + ")";

      setBar(pct, msg);

      var local = LOCAL_PROGRESS[loaderId];
      if (local) {
        if (local.bar && el(local.bar)) el(local.bar).style.width = pct + "%";
        if (local.text && el(local.text)) el(local.text).textContent = msg;
      }
    },

    done: function () {
      if (activeLoaders.size > 0) return;
      setBar(100, "Complete");
      setTimeout(hide, 400);
    },

    /**
     * Read NDJSON stream from a fetch Response; invokes onProgress / onComplete.
     */
    readNdjsonResponse: async function (response, options) {
      options = options || {};
      var loaderId = options.loaderId;
      var onProgress = options.onProgress;
      var onComplete = options.onComplete;

      if (!response.ok) {
        var text = await response.text();
        throw new Error(text || "HTTP " + response.status);
      }

      var reader = response.body.getReader();
      var decoder = new TextDecoder("utf-8");
      var buffer = "";

      while (true) {
        var chunk = await reader.read();
        if (chunk.done) break;

        buffer += decoder.decode(chunk.value, { stream: true });
        var lines = buffer.split("\n");
        buffer = lines.pop();

        for (var i = 0; i < lines.length; i++) {
          var line = lines[i].trim();
          if (!line) continue;
          var event = JSON.parse(line);

          if (event.type === "progress") {
            window.CCSProgress.applyStreamEvent(event, loaderId);
            if (onProgress) onProgress(event);
          } else if (event.type === "complete") {
            setBar(100, "Complete");
            if (onComplete) return onComplete(event);
            return event;
          }
        }
      }
      return null;
    },
  };

  window.setLoader = function (id, show) {
    var node = el(id);
    if (node) node.classList.toggle("visible", !!show);

    if (show) {
      activeLoaders.add(id);
      determinate = Object.prototype.hasOwnProperty.call(LOCAL_PROGRESS, id);
      syncFromLoaders();
      if (determinate) setBar(0, (OP_LABELS[id] || "Working") + "…");
    } else {
      activeLoaders.delete(id);
      syncFromLoaders();
      window.CCSProgress.done();
    }
  };

  /* Optional: wrap fetch for calls that don't use setLoader */
  window.ccsFetch = async function (url, options, label) {
    window.CCSProgress.beginOperation(label || "Request in progress", false);
    try {
      return await fetch(url, options);
    } finally {
      activeLoaders.clear();
      window.CCSProgress.done();
    }
  };

  /* If page script ran before this file, nothing to patch */
})();
