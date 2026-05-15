"""Add Enter Manually UI to ccs_devices.html and ccs_users.html."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEVICES = ROOT / "gldashboard_bundle" / "app" / "templates" / "ccs_devices.html"
USERS = ROOT / "gldashboard_bundle" / "app" / "templates" / "ccs_users.html"

MANUAL_JS = r'''
    // ── Upload CSV vs paste manually (same API: synthetic CSV file) ───────────
    function _inputModeIsManual(targetKey) {
        const t = document.querySelector('.input-mode-toggle[data-target="' + targetKey + '"]');
        if (!t) return false;
        const a = t.querySelector('.mode-btn.active');
        return !!(a && a.getAttribute('data-mode') === 'manual');
    }

    var _FILE_LABEL_BY_INPUT = {
        devFile: 'devFileLabel', devQueryFile: 'devQueryFileLabel',
        unclaimFile: 'unclaimFileLabel', claimFile: 'claimFileLabel',
        usrFile: 'usrFileLabel', delUserFile: 'delUserFileLabel'
    };

    function setInputMode(targetKey, mode) {
        document.querySelectorAll('.input-mode-toggle[data-target="' + targetKey + '"] .mode-btn').forEach(function (btn) {
            btn.classList.toggle('active', btn.getAttribute('data-mode') === mode);
        });
        document.querySelectorAll('.file-drop-wrap[data-target="' + targetKey + '"]').forEach(function (el) {
            el.style.display = (mode === 'file') ? '' : 'none';
        });
        document.querySelectorAll('.manual-entry-wrap[data-target="' + targetKey + '"]').forEach(function (el) {
            el.style.display = (mode === 'manual') ? 'block' : 'none';
        });
        var inputByTarget = {
            dev: 'devFile', devQuery: 'devQueryFile', unclaim: 'unclaimFile', claim: 'claimFile',
            usr: 'usrFile', delUser: 'delUserFile'
        };
        var fid = inputByTarget[targetKey];
        if (fid) {
            var mc = document.getElementById('mapper-' + fid);
            if (mc) mc.style.display = (mode === 'file') ? '' : 'none';
        }
    }

    function _manualLinesFromTextarea(ta) {
        if (!ta || !ta.value) return [];
        return ta.value.split(/\r?\n/).map(function (line) {
            return (line.split('\t')[0] || '').trim();
        }).filter(Boolean);
    }

    function updateManualCount(textareaId, countPillId) {
        var ta = document.getElementById(textareaId);
        var pill = document.getElementById(countPillId);
        if (!ta || !pill) return;
        var n = _manualLinesFromTextarea(ta).length;
        pill.textContent = n + (n === 1 ? ' entry' : ' entries');
    }

    function clearManualEntry(textareaId, countPillId) {
        var ta = document.getElementById(textareaId);
        if (ta) ta.value = '';
        updateManualCount(textareaId, countPillId);
    }

    function _csvEscapeCell(s) {
        s = String(s == null ? '' : s);
        if (/[",\r\n]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
        return s;
    }

    function _injectManualAsCsvFile(fileInputId, headerName, lines) {
        var csv = headerName + '\n' + lines.map(_csvEscapeCell).join('\n');
        var blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
        var file = new File([blob], 'pasted-input.csv', { type: 'text/csv' });
        var input = document.getElementById(fileInputId);
        if (!input) return false;
        try {
            var dt = new DataTransfer();
            dt.items.add(file);
            input.files = dt.files;
        } catch (e) {
            alert('Could not build CSV from pasted text in this browser: ' + e.message);
            return false;
        }
        var lab = _FILE_LABEL_BY_INPUT[fileInputId];
        if (lab) handleFileSelect(input, lab);
        return true;
    }

    function runWithInput(targetKey, fileInputId, manualTextareaId, headerName, title, onConfirm) {
        if (_inputModeIsManual(targetKey)) {
            var lines = _manualLinesFromTextarea(document.getElementById(manualTextareaId));
            if (!lines.length) {
                alert('Enter at least one value (one per line) or switch to Upload CSV.');
                return;
            }
            if (!_injectManualAsCsvFile(fileInputId, headerName, lines)) return;
        }
        previewThenRun(fileInputId, title, onConfirm);
    }

'''

MANUAL_UI = {
    "dev": {
        "file_drop_id": "devFileDrop",
        "file_input_id": "devFile",
        "label_id": "devFileLabel",
        "label_text": "Click or drag CSV with serial numbers",
        "icon": "upload-cloud",
        "manual_id": "devManualText",
        "count_id": "devManualCount",
        "field_label": "Serial Numbers (one per line)",
        "placeholder": "CN12345678&#10;CN87654321&#10;...",
        "hint": "Paste serials directly, no CSV needed",
        "csv_header": "Serial Number",
        "btn_id": "devTransferBtn",
        "title": "Transfer Devices",
        "fn": "transferDevices",
    },
    "devQuery": {
        "file_drop_id": "devQueryFileDrop",
        "file_input_id": "devQueryFile",
        "label_id": "devQueryFileLabel",
        "label_text": "Click or drag CSV with Serial Numbers",
        "icon": "server",
        "manual_id": "devQueryManualText",
        "count_id": "devQueryManualCount",
        "field_label": "Serial Numbers (one per line)",
        "placeholder": "CN12345678&#10;CN87654321&#10;...",
        "hint": "Paste serials directly, no CSV needed",
        "csv_header": "Serial Number",
        "btn_id": "devQueryBtn",
        "title": "Query Devices",
        "fn": "queryDevices",
    },
    "unclaim": {
        "file_drop_id": "unclaimFileDrop",
        "file_input_id": "unclaimFile",
        "label_id": "unclaimFileLabel",
        "label_text": "Click or drag CSV with Serial Numbers",
        "icon": "upload-cloud",
        "manual_id": "unclaimManualText",
        "count_id": "unclaimManualCount",
        "field_label": "Serial Numbers (one per line)",
        "placeholder": "CN12345678&#10;CN87654321&#10;...",
        "hint": "Paste serials directly, no CSV needed",
        "csv_header": "Serial Number",
        "btn_id": "unclaimBtn",
        "title": "Unclaim Devices",
        "fn": "unclaimDevices",
    },
    "claim": {
        "file_drop_id": "claimFileDrop",
        "file_input_id": "claimFile",
        "label_id": "claimFileLabel",
        "label_text": "Click or drag CSV with Serial Numbers",
        "icon": "download-cloud",
        "manual_id": "claimManualText",
        "count_id": "claimManualCount",
        "field_label": "Serial Numbers (one per line)",
        "placeholder": "CN12345678&#10;CN87654321&#10;...",
        "hint": "Paste serials directly, no CSV needed",
        "csv_header": "Serial Number",
        "btn_id": "claimBtn",
        "title": "Claim Devices",
        "fn": "claimDevices",
    },
    "usr": {
        "file_drop_id": "usrFileDrop",
        "file_input_id": "usrFile",
        "label_id": "usrFileLabel",
        "label_text": "Click or drag CSV with emails/Usernames",
        "icon": "users",
        "manual_id": "usrManualText",
        "count_id": "usrManualCount",
        "field_label": "Emails / Usernames (one per line)",
        "placeholder": "user@example.com&#10;jdoe&#10;...",
        "hint": "Paste emails or usernames directly, no CSV needed",
        "csv_header": "Email",
        "btn_id": "usrQueryBtn",
        "title": "Query Users",
        "fn": "queryUsers",
    },
    "delUser": {
        "file_drop_id": "delUserFileDrop",
        "file_input_id": "delUserFile",
        "label_id": "delUserFileLabel",
        "label_text": "Click or drag CSV with emails/Usernames",
        "icon": "user-x",
        "manual_id": "delUserManualText",
        "count_id": "delUserManualCount",
        "field_label": "Emails / Usernames (one per line)",
        "placeholder": "user@example.com&#10;jdoe&#10;...",
        "hint": "Paste emails or usernames directly, no CSV needed",
        "csv_header": "Email",
        "btn_id": "delUserBtn",
        "title": "Delete Users",
        "fn": "deleteUsers",
    },
}


def build_manual_block(key: str, cfg: dict) -> str:
    k = key
    return f'''            <motion.div class="input-mode-toggle" data-target="{k}">
                <button type="button" class="mode-btn active" data-mode="file"
                    onclick="setInputMode('{k}','file')">📂 Upload CSV</button>
                <button type="button" class="mode-btn" data-mode="manual"
                    onclick="setInputMode('{k}','manual')">⌨️ Enter Manually</button>
            </div>

            <div class="file-drop-wrap" data-target="{k}">
            <div class="file-drop" id="{cfg['file_drop_id']}" onclick="document.getElementById('{cfg['file_input_id']}').click()">
                <i data-feather="{cfg['icon']}"></i>
                <p id="{cfg['label_id']}">{cfg['label_text']}</p>
                <input type="file" id="{cfg['file_input_id']}" accept=".csv" style="display:none"
                    onchange="handleFileSelect(this, '{cfg['label_id']}')">
            </div>
            </div>

            <div class="manual-entry-wrap" data-target="{k}" style="display:none;">
                <label style="display:block; font-size:0.78rem; font-weight:500; color:rgba(255,255,255,0.6); margin-bottom:6px; text-transform:uppercase; letter-spacing:0.04em;">{cfg['field_label']}</label>
                <textarea id="{cfg['manual_id']}" placeholder="{cfg['placeholder']}"
                    oninput="updateManualCount('{cfg['manual_id']}','{cfg['count_id']}')"></textarea>
                <div class="manual-entry-meta">
                    <span><span class="count-pill" id="{cfg['count_id']}">0 entries</span> · {cfg['hint']}</span>
                    <button type="button" class="clear-link" onclick="clearManualEntry('{cfg['manual_id']}','{cfg['count_id']}')">Clear</button>
                </div>
            </div>

'''.replace('<motion.div class="input-mode-toggle"', '<motion.div class="input-mode-toggle"'.replace('motion.', ''))


def patch_file(path: Path, keys: list[str]):
    t = path.read_text(encoding="utf-8")
    if "function runWithInput(" in t:
        print(path.name, "already has runWithInput")
        return

    for key in keys:
        cfg = MANUAL_UI[key]
        old = f'''            <div class="file-drop" id="{cfg['file_drop_id']}" onclick="document.getElementById('{cfg['file_input_id']}').click()">
                <i data-feather="{cfg['icon']}"></i>
                <p id="{cfg['label_id']}">{cfg['label_text']}</p>
                <input type="file" id="{cfg['file_input_id']}" accept=".csv" style="display:none"
                    onchange="handleFileSelect(this, '{cfg['label_id']}')">
            </div>

'''
        if old not in t:
            raise SystemExit(f"{path.name}: missing file-drop block for {key}")
        t = t.replace(old, build_manual_block(key, cfg), 1)

        old_btn = f"onclick=\"previewThenRun('{cfg['file_input_id']}', '{cfg['title']}', {cfg['fn']})\""
        new_btn = (
            f"onclick=\"runWithInput('{key}','{cfg['file_input_id']}','{cfg['manual_id']}',"
            f"'{cfg['csv_header']}','{cfg['title']}', {cfg['fn']})\""
        )
        if old_btn not in t:
            raise SystemExit(f"{path.name}: missing button for {key}")
        t = t.replace(old_btn, new_btn, 1)

    marker = "        // ── Drag-and-drop (this page only) ─────────────────────────────────────"
    if MANUAL_JS.strip() not in t:
        if marker not in t:
            raise SystemExit(f"{path.name}: drag-drop marker not found")
        t = t.replace(marker, MANUAL_JS + "\n" + marker, 1)

    path.write_text(t, encoding="utf-8")
    print(path.name, "patched OK")


def main():
    # fix build_manual_block typo - use div not motion.div
    global build_manual_block
    def build_manual_block(key: str, cfg: dict) -> str:
        k = key
        return f'''            <div class="input-mode-toggle" data-target="{k}">
                <button type="button" class="mode-btn active" data-mode="file"
                    onclick="setInputMode('{k}','file')">📂 Upload CSV</button>
                <button type="button" class="mode-btn" data-mode="manual"
                    onclick="setInputMode('{k}','manual')">⌨️ Enter Manually</button>
            </div>

            <div class="file-drop-wrap" data-target="{k}">
            <div class="file-drop" id="{cfg['file_drop_id']}" onclick="document.getElementById('{cfg['file_input_id']}').click()">
                <i data-feather="{cfg['icon']}"></i>
                <p id="{cfg['label_id']}">{cfg['label_text']}</p>
                <input type="file" id="{cfg['file_input_id']}" accept=".csv" style="display:none"
                    onchange="handleFileSelect(this, '{cfg['label_id']}')">
            </div>
            </motion.div>

            <div class="manual-entry-wrap" data-target="{k}" style="display:none;">
                <label style="display:block; font-size:0.78rem; font-weight:500; color:rgba(255,255,255,0.6); margin-bottom:6px; text-transform:uppercase; letter-spacing:0.04em;">{cfg['field_label']}</label>
                <textarea id="{cfg['manual_id']}" placeholder="{cfg['placeholder']}"
                    oninput="updateManualCount('{cfg['manual_id']}','{cfg['count_id']}')"></textarea>
                <div class="manual-entry-meta">
                    <span><span class="count-pill" id="{cfg['count_id']}">0 entries</span> · {cfg['hint']}</span>
                    <button type="button" class="clear-link" onclick="clearManualEntry('{cfg['manual_id']}','{cfg['count_id']}')">Clear</button>
                </div>
            </div>

'''.replace('</motion.div>', '</motion.div>'.replace('motion.', '')).replace('</motion.div>\n\n            <div class="manual-entry-wrap"', '</div>\n\n            <div class="manual-entry-wrap"')

    patch_file(DEVICES, ["dev", "devQuery", "unclaim", "claim"])
    patch_file(USERS, ["usr", "delUser"])


if __name__ == "__main__":
    main()
