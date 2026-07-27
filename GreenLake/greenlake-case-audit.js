/**
 * Shared compulsory case-number prompt for Platform Tools audit logging.
 * Usage:
 *   const caseNumber = await window.promptCaseNumber();
 *   if (!caseNumber) return;
 */
(function () {
  function ensureModal() {
    if (document.getElementById("caseModal")) return;
    var wrap = document.createElement("div");
    wrap.id = "caseModal";
    wrap.style.cssText =
      "display:none;position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.45);align-items:center;justify-content:center;padding:20px;";
    wrap.innerHTML =
      '<div style="width:100%;max-width:420px;background:var(--panel,#fff);border:1px solid var(--border,#d5d5d5);border-radius:10px;padding:24px 22px;box-shadow:0 12px 40px rgba(0,0,0,.18);">' +
      '<div style="font-size:1.05rem;font-weight:700;margin-bottom:6px;color:var(--text,#2c2c2c);">Case number required</div>' +
      '<p style="font-size:.88rem;color:var(--muted,#666);margin-bottom:16px;line-height:1.45;">Enter the support case number for this action. It is saved in the audit log.</p>' +
      '<label for="caseNumberInput" style="display:block;font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px;color:var(--text,#444);">Case number</label>' +
      '<input id="caseNumberInput" type="text" maxlength="64" placeholder="e.g. 5301234567" autocomplete="off" ' +
      'style="width:100%;padding:11px 12px;border:1px solid var(--border,#d5d5d5);border-radius:8px;font-family:inherit;font-size:.95rem;background:var(--surface,#f7f7f7);color:var(--text,#2c2c2c);margin-bottom:8px;" />' +
      '<div id="caseNumberError" style="display:none;color:#a82a2a;font-size:.82rem;margin-bottom:10px;"></div>' +
      '<div style="display:flex;justify-content:flex-end;gap:8px;margin-top:12px;">' +
      '<button type="button" id="caseModalCancel" style="border:1px solid #d5d5d5;background:#fff;border-radius:6px;padding:8px 14px;font-weight:600;cursor:pointer;">Cancel</button>' +
      '<button type="button" id="caseModalConfirm" style="border:none;background:#01a982;color:#fff;border-radius:6px;padding:8px 14px;font-weight:700;cursor:pointer;">Continue</button>' +
      "</div></div>";
    document.body.appendChild(wrap);
  }

  window.promptCaseNumber = function () {
    ensureModal();
    return new Promise(function (resolve) {
      var modal = document.getElementById("caseModal");
      var input = document.getElementById("caseNumberInput");
      var err = document.getElementById("caseNumberError");
      var btnOk = document.getElementById("caseModalConfirm");
      var btnCancel = document.getElementById("caseModalCancel");

      function cleanup() {
        modal.style.display = "none";
        document.removeEventListener("keydown", onKey);
        btnOk.onclick = null;
        btnCancel.onclick = null;
        modal.onclick = null;
      }

      function onKey(e) {
        if (e.key === "Escape") {
          cleanup();
          resolve(null);
        }
        if (e.key === "Enter") {
          e.preventDefault();
          submit();
        }
      }

      function submit() {
        var val = (input.value || "").trim();
        if (!val) {
          err.textContent = "Case number is compulsory.";
          err.style.display = "block";
          input.focus();
          return;
        }
        cleanup();
        resolve(val);
      }

      err.style.display = "none";
      err.textContent = "";
      modal.style.display = "flex";
      btnOk.onclick = submit;
      btnCancel.onclick = function () {
        cleanup();
        resolve(null);
      };
      modal.onclick = function (e) {
        if (e.target === modal) {
          cleanup();
          resolve(null);
        }
      };
      document.addEventListener("keydown", onKey);
      setTimeout(function () {
        input.focus();
        input.select();
      }, 40);
    });
  };
})();
