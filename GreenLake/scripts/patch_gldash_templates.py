"""One-time patcher for bundled dashboard templates (GL_PREFIX / __GL_PREFIX__)."""
from pathlib import Path


def patch_html(s: str) -> str:
    s = s.replace("fetch('/api", "fetch((window.__GL_PREFIX__||'') + '/api")
    s = s.replace("fetch(`/api", "fetch(`${window.__GL_PREFIX__||''}/api")
    s = s.replace('href="/static', 'href="{{ gl_prefix }}/static')
    s = s.replace('src="/static', 'src="{{ gl_prefix }}/static')
    s = s.replace('href="/', 'href="{{ gl_prefix }}/')
    s = s.replace('action="/', 'action="{{ gl_prefix }}/')
    return s


def inject_head_script(html: str) -> str:
    snip = "<script>window.__GL_PREFIX__ = \"{{ gl_prefix }}\";</script>\n"
    if "__GL_PREFIX__" in html:
        return html
    low = html.lower()
    i = low.find("<head>")
    if i == -1:
        return snip + html
    j = i + len("<head>")
    return html[:j] + "\n    " + snip + html[j:]


def main():
    root = Path(__file__).resolve().parents[1] / "gldashboard_bundle" / "app"
    tpl = root / "templates"
    for p in sorted(tpl.glob("**/*.html")):
        t = p.read_text(encoding="utf-8")
        nt = patch_html(t)
        nt = inject_head_script(nt)
        if nt != t:
            p.write_text(nt, encoding="utf-8")
            print("patched", p.relative_to(root))

    js = root / "static" / "js" / "app.js"
    if js.exists():
        t = js.read_text(encoding="utf-8")
        if "__GLP" not in t:
            t = (
                "const __GLP = (typeof window !== 'undefined' && window.__GL_PREFIX__) "
                "? window.__GL_PREFIX__ : '';\n"
            ) + t.replace("fetch('/api", "fetch(__GLP + '/api")
            js.write_text(t, encoding="utf-8")
            print("patched", js.relative_to(root))


if __name__ == "__main__":
    main()
