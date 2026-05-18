/**
 * Global feedback widget — submits to /gldash/api/feedback/submit
 */
(function () {
  "use strict";

  var API_BASE = "/gldash/api/feedback";

  function el(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }

  function injectStyles() {
    if (document.getElementById("gl-feedback-styles")) return;
    var link = document.createElement("link");
    link.id = "gl-feedback-styles";
    link.rel = "stylesheet";
    link.href = "/greenlake-feedback.css";
    document.head.appendChild(link);
  }

  function buildUI() {
    if (document.getElementById("gl-feedback-fab")) return;

    var fab = el("button", "gl-feedback-fab");
    fab.id = "gl-feedback-fab";
    fab.type = "button";
    fab.title = "Send feedback";
    fab.setAttribute("aria-label", "Send feedback");
    fab.innerHTML =
      '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';

    var backdrop = el("div", "gl-feedback-backdrop");
    backdrop.id = "gl-feedback-backdrop";

    var modal = el("div", "gl-feedback-modal");
    modal.id = "gl-feedback-modal";
    modal.innerHTML =
      '<div class="gl-feedback-modal-inner">' +
      '<button type="button" class="gl-feedback-close" aria-label="Close">&times;</button>' +
      "<h3>Send feedback</h3>" +
      '<p class="gl-feedback-hint">Help us improve GreenLake Platform Tools. Do not include passwords or tokens.</p>' +
      '<label>Category<select id="gl-fb-category">' +
      '<option value="bug">Bug</option>' +
      '<option value="feature">Feature request</option>' +
      '<option value="question">Question</option>' +
      '<option value="other" selected>Other</option>' +
      "</select></label>" +
      '<label>Message<textarea id="gl-fb-message" rows="5" placeholder="Describe your feedback…" maxlength="5000"></textarea></label>' +
      '<div class="gl-feedback-actions">' +
      '<button type="button" class="gl-feedback-cancel">Cancel</button>' +
      '<button type="button" class="gl-feedback-submit">Submit</button>' +
      "</div></div>";

    document.body.appendChild(fab);
    document.body.appendChild(backdrop);
    document.body.appendChild(modal);

    function open() {
      backdrop.classList.add("open");
      modal.classList.add("open");
      document.getElementById("gl-fb-message").focus();
    }
    function close() {
      backdrop.classList.remove("open");
      modal.classList.remove("open");
    }

    fab.addEventListener("click", open);
    backdrop.addEventListener("click", close);
    modal.querySelector(".gl-feedback-close").addEventListener("click", close);
    modal.querySelector(".gl-feedback-cancel").addEventListener("click", close);

    modal.querySelector(".gl-feedback-submit").addEventListener("click", function () {
      var msg = document.getElementById("gl-fb-message").value.trim();
      var cat = document.getElementById("gl-fb-category").value;
      if (!msg) {
        alert("Please enter a message.");
        return;
      }
      var btn = modal.querySelector(".gl-feedback-submit");
      btn.disabled = true;
      btn.textContent = "Sending…";

      var source = "platform-tools";
      if (window.__GL_PREFIX__) source = "dashboard";

      fetch(API_BASE + "/submit", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          category: cat,
          message: msg,
          page_url: window.location.href,
          source: source,
        }),
      })
        .then(function (r) {
          if (!r.ok) throw new Error("submit failed");
          return r.json();
        })
        .then(function () {
          document.getElementById("gl-fb-message").value = "";
          close();
          if (typeof window.showToast === "function") {
            window.showToast("Thank you — feedback submitted.");
          } else {
            alert("Thank you — feedback submitted.");
          }
        })
        .catch(function () {
          alert("Could not submit feedback. Try again later.");
        })
        .finally(function () {
          btn.disabled = false;
          btn.textContent = "Submit";
        });
    });
  }

  function checkAdminMentorCard() {
    var card = document.getElementById("tile-feedback-inbox");
    if (!card) return;
    fetch(API_BASE + "/whoami", { credentials: "include" })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        if (data && data.is_admin) card.style.display = "";
      })
      .catch(function () {});
  }

  function init() {
    injectStyles();
    buildUI();
    checkAdminMentorCard();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.GreenLakeFeedback = { init: init, apiBase: API_BASE };
})();
