"""``finsight-dashboard``: start the Streamlit dashboard with FinSight's theme.

Extra arguments are passed to ``streamlit run`` (e.g. ``--server.port 8600``).
"""

from __future__ import annotations

import sys
from pathlib import Path

THEME = {
    "theme.base": "dark",
    "theme.primaryColor": "#35c9c0",
    "theme.backgroundColor": "#08101c",
    "theme.secondaryBackgroundColor": "#0f1929",
    "theme.textColor": "#f2f4f8",
    "browser.gatherUsageStats": "false",
    "client.toolbarMode": "minimal",
}


def main(argv: list[str] | None = None) -> int:
    try:
        from streamlit.web import cli as stcli
    except ImportError:
        print("The dashboard needs Streamlit: pip install -e '.[dashboard]'", file=sys.stderr)
        return 1
    app = Path(__file__).with_name("app.py")
    args = list(sys.argv[1:] if argv is None else argv)
    flags = [f"--{k}={v}" for k, v in THEME.items() if not any(a.startswith(f"--{k}") for a in args)]
    sys.argv = ["streamlit", "run", str(app), *flags, *args]
    return stcli.main()


if __name__ == "__main__":
    sys.exit(main())
