"""Run SSO Tools as a standalone Flask dev server (optional; normally use `python main.py`)."""
from __future__ import annotations

import os

from .webapp import build_sso_tools_app


def main() -> None:
    app = build_sso_tools_app()
    port = int(os.environ.get("SSO_TOOLS_PORT", "5051"))
    host = os.environ.get("SSO_TOOLS_HOST", "127.0.0.1")
    debug = os.environ.get("SSO_TOOLS_DEBUG", "1") == "1"
    app.run(debug=debug, host=host, port=port)


if __name__ == "__main__":
    main()
