"""Write the API's OpenAPI schema to docs/openapi.json.

The frontend's TypeScript types are generated from this file (``npm run gen:api`` in
``web/``). CI runs this script with ``--check`` so the schema, and therefore the types,
cannot drift from the Python models unnoticed.

    python scripts/export_openapi.py            # rewrite docs/openapi.json
    python scripts/export_openapi.py --check    # exit 1 if it is out of date
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from finsight.api.app import create_app  # noqa: E402
from finsight.api.settings import ApiSettings  # noqa: E402
from finsight.config import load_settings  # noqa: E402

TARGET = ROOT / "docs" / "openapi.json"


def render() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(load_settings(home=Path(tmp)), ApiSettings(web_dist=None))
        return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    text = render()
    if "--check" in sys.argv:
        if not TARGET.exists() or TARGET.read_text() != text:
            print("docs/openapi.json is out of date. Run: python scripts/export_openapi.py && (cd web && npm run gen:api)")
            return 1
        print("docs/openapi.json is up to date")
        return 0
    TARGET.write_text(text)
    print(f"wrote {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
