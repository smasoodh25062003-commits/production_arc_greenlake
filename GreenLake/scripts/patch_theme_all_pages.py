"""Inject global theme assets + toggle into Platform Tools HTML pages."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

HEAD_INJECT = """  <script src="/greenlake-theme.js"></script>
  <link rel="stylesheet" href="/greenlake-theme.css" />
  <link rel="stylesheet" href="/platform-tools-dark.css" />
"""

TOGGLE = """    <button type="button" id="themeToggle" class="gl-theme-toggle" aria-label="Toggle theme" title="Toggle light/dark mode">
      <span class="theme-icon theme-icon-light" aria-hidden="true">&#9728;</span>
      <span class="theme-icon theme-icon-dark" aria-hidden="true">&#9790;</span>
    </button>
"""

PLATFORM_PAGES = [
    "GreenLakeTools.html",
    "DeviceManagement.html",
    "UserManagement.html",
    "Subscriptionmanagement.html",
    "TransferDevices.html",
    "TransferSubscriptions.html",
]


def patch_platform_html(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    orig = text

    text = re.sub(
        r"<html\s+lang=\"en\">",
        '<html lang="en" data-theme="light">',
        text,
        count=1,
    )

    if "/greenlake-theme.js" not in text:
        text = text.replace("<head>", "<head>\n" + HEAD_INJECT, 1)

    if 'id="themeToggle"' not in text:
        text = text.replace("  </nav>\n</header>", TOGGLE + "  </nav>\n</header>", 1)

    if text != orig:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def main() -> None:
    changed = []
    for name in PLATFORM_PAGES:
        p = ROOT / name
        if p.exists() and patch_platform_html(p):
            changed.append(name)
    print("Patched:", ", ".join(changed) if changed else "(none)")


if __name__ == "__main__":
    main()
