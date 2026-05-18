"""Load dark-theme CSS after inline <style> so it wins over HPE elegant overrides."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PLATFORM = [
    "GreenLakeTools.html",
    "DeviceManagement.html",
    "UserManagement.html",
    "Subscriptionmanagement.html",
    "TransferDevices.html",
    "TransferSubscriptions.html",
]

DARK_LINK = '  <link rel="stylesheet" href="/platform-tools-dark.css" />\n'
DASH_LINK = '    <link rel="stylesheet" href="{{ gl_prefix }}/static/css/dashboard-dark.css">\n'


def _strip_early_dark(text: str, link: str) -> str:
    if link.strip() in text:
        text = text.replace(link, "")
        text = text.replace(link.replace("\n", ""), "")
    return text


def patch_platform(path: Path) -> None:
    t = path.read_text(encoding="utf-8")
    t = _strip_early_dark(t, DARK_LINK)
    if DARK_LINK.strip() not in t and "</style>" in t:
        t = t.replace("</style>", "</style>\n" + DARK_LINK, 1)
    path.write_text(t, encoding="utf-8")


def patch_template(path: Path, link: str) -> None:
    t = path.read_text(encoding="utf-8")
    t = _strip_early_dark(t, link)
    if link.strip() not in t and "</style>" in t:
        # last inline block (ccs has style in head)
        idx = t.rfind("</style>")
        t = t[: idx + len("</style>")] + "\n" + link + t[idx + len("</style>") :]
    path.write_text(t, encoding="utf-8")


def main() -> None:
    for name in PLATFORM:
        patch_platform(ROOT / name)
    tpl = ROOT / "gldashboard_bundle" / "app" / "templates"
    for name in ("index.html", "login.html", "ccs_layout.html", "layout.html"):
        p = tpl / name
        if p.exists():
            patch_template(p, DASH_LINK)
    print("CSS order patched.")


if __name__ == "__main__":
    main()
