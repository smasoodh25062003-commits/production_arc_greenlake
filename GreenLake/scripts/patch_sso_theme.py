"""Unify SSO Tools with global greenlake-theme.js."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "sso_tools" / "templates"

HEAD = '  <script src="/greenlake-theme.js"></script>\n'

INLINE_THEME = re.compile(
    r"\s*<script>\s*\(function \(\) \{[\s\S]*?okta-role-string-theme[\s\S]*?\}\)\(\);\s*</script>\s*",
    re.MULTILINE,
)


def patch_template(path: Path) -> None:
    t = path.read_text(encoding="utf-8")
    if "/greenlake-theme.js" not in t:
        t = t.replace("<head>", "<head>\n" + HEAD, 1)
    t = INLINE_THEME.sub("\n", t)
    path.write_text(t, encoding="utf-8")


def main() -> None:
    for p in TPL.glob("*.html"):
        patch_template(p)
    print("SSO templates patched:", len(list(TPL.glob("*.html"))))


if __name__ == "__main__":
    main()
