from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "gldashboard_bundle" / "app" / "templates"

D = "div"

OLD_TOPBAR = f"""                <{D} class="user-profile">
                    <{D} class="status-indicator {{{{ 'on' if configured else 'off' }}}}"></{D}>
                    <span>{{{{ 'Connected' if configured else 'Not Configured' }}}}</span>
                </{D}>"""

NEW_TOPBAR = f"""                <{D} class="top-bar-actions" style="display:flex;align-items:center;gap:12px;">
                    <button type="button" id="themeToggle" class="gl-theme-toggle" aria-label="Toggle theme" title="Toggle light/dark mode">
                        <span class="theme-icon theme-icon-light" aria-hidden="true">&#9728;</span>
                        <span class="theme-icon theme-icon-dark" aria-hidden="true">&#9790;</span>
                    </button>
                    <{D} class="user-profile">
                        <{D} class="status-indicator {{{{ 'on' if configured else 'off' }}}}"></{D}>
                        <span>{{{{ 'Connected' if configured else 'Not Configured' }}}}</span>
                    </{D}>
                </{D}>"""

CCS_TOGGLE = """        <button type="button" id="themeToggle" class="gl-theme-toggle" aria-label="Toggle theme" title="Toggle light/dark mode">
            <span class="theme-icon theme-icon-light" aria-hidden="true">&#9728;</span>
            <span class="theme-icon theme-icon-dark" aria-hidden="true">&#9790;</span>
        </button>
"""

THEME_HEAD = """    <script src="/greenlake-theme.js"></script>
    <link rel="stylesheet" href="/greenlake-theme.css">
    <link rel="stylesheet" href="{{ gl_prefix }}/static/css/dashboard-dark.css">
"""

FLOAT_TOGGLE = (
    '<button type="button" id="themeToggle" class="gl-theme-toggle" '
    'style="position:fixed;top:20px;right:20px;z-index:100" '
    'aria-label="Toggle theme" title="Toggle light/dark mode">'
    '<span class="theme-icon theme-icon-light" aria-hidden="true">&#9728;</span>'
    '<span class="theme-icon theme-icon-dark" aria-hidden="true">&#9790;</span>'
    "</button>\n"
)


def patch_layout():
    p = TPL / "layout.html"
    t = p.read_text(encoding="utf-8")
    if 'id="themeToggle"' not in t and OLD_TOPBAR in t:
        t = t.replace(OLD_TOPBAR, NEW_TOPBAR)
    p.write_text(t, encoding="utf-8")


def _inject_head(t: str) -> str:
    if "/greenlake-theme.js" in t:
        return t
    return t.replace("<head>", "<head>\n" + THEME_HEAD, 1)


def patch_login():
    p = TPL / "login.html"
    t = p.read_text(encoding="utf-8")
    t = t.replace('<html lang="en">', '<html lang="en" data-theme="light">', 1)
    t = _inject_head(t)
    if 'id="themeToggle"' not in t:
        t = t.replace("<body>", '<body class="login-body">\n    ' + FLOAT_TOGGLE, 1)
    p.write_text(t, encoding="utf-8")


def patch_index():
    p = TPL / "index.html"
    t = p.read_text(encoding="utf-8")
    t = t.replace('<html lang="en">', '<html lang="en" data-theme="light" class="hub-page">', 1)
    t = _inject_head(t)
    if 'id="themeToggle"' not in t:
        t = t.replace("<body>", "<body>\n    " + FLOAT_TOGGLE, 1)
    p.write_text(t, encoding="utf-8")


def patch_ccs():
    p = TPL / "ccs_layout.html"
    t = p.read_text(encoding="utf-8")
    t = t.replace('<html lang="en">', '<html lang="en" data-theme="light">', 1)
    t = _inject_head(t)
    if 'id="themeToggle"' not in t:
        marker = f'<{D} class="ccs-nav-actions">'
        if marker in t:
            t = t.replace(marker, marker + "\n" + CCS_TOGGLE, 1)
        else:
            idx = t.find('class="ccs-nav"')
            if idx != -1:
                end = t.find(">", idx) + 1
                t = t[:end] + "\n" + CCS_TOGGLE + t[end:]
    p.write_text(t, encoding="utf-8")


if __name__ == "__main__":
    patch_layout()
    patch_login()
    patch_index()
    patch_ccs()
    print("done")
