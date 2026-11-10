"""Build the API payloads the browser tests replay for states the offline server cannot reach on demand.

The only one today is a run that ends with partial results. It is produced by the real
pipeline: the synthetic company's XBRL facts are served without the latest 10-K, so the
validator raises a blocking issue and the run finishes as PARTIAL with unavailable metrics.

    python scripts/build_e2e_fixtures.py

Writes ``web/e2e/fixtures/partial-status.json`` and ``partial-result.json``.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from finsight.api.app import create_app  # noqa: E402
from finsight.api.settings import ApiSettings  # noqa: E402
from finsight.config import load_settings  # noqa: E402
from finsight.sec.sources import Fetched, FixtureSource  # noqa: E402

TARGET = ROOT / "web" / "e2e" / "fixtures"
LATEST_ACCESSION = "0009999901-26-000010"


class StaleFactsSource(FixtureSource):
    """The synthetic fixture, with the latest fiscal year's facts missing from the XBRL data."""

    def company_facts(self, cik, must_include_accession=None, force_refresh=False) -> Fetched:
        fetched = super().company_facts(cik, must_include_accession, force_refresh)
        payload = json.loads(json.dumps(fetched.payload))
        for concept in payload["facts"]["us-gaap"].values():
            for unit, rows in concept["units"].items():
                concept["units"][unit] = [r for r in rows if r["accn"] != LATEST_ACCESSION]
        return Fetched(payload, fetched.document)


def build_partial() -> tuple[dict, dict]:
    """Run the synthetic company to a PARTIAL end state and return (status, result)."""
    with tempfile.TemporaryDirectory() as tmp:
        settings = load_settings(home=Path(tmp))
        app = create_app(settings, ApiSettings(web_dist=None),
                         source_factory=lambda request: StaleFactsSource(Path(settings.fixtures_dir)))
        with TestClient(app) as client:
            run_id = client.post("/api/runs", json={"identifier": "DEMO"}).json()["run_id"]
            deadline = time.monotonic() + 120
            while True:
                status = client.get(f"/api/runs/{run_id}").json()
                if status["phase"] not in ("queued", "running"):
                    break
                if time.monotonic() > deadline:
                    raise SystemExit(f"timed out in state {status['state']}")
                time.sleep(0.05)
            if status["phase"] != "partial":
                raise SystemExit(f"expected a partial run, got {status['state']}: {status.get('error')}")
            return status, client.get(f"/api/runs/{run_id}/result").json()


def main() -> int:
    status, result = build_partial()
    TARGET.mkdir(parents=True, exist_ok=True)
    (TARGET / "partial-status.json").write_text(json.dumps(status, indent=1) + "\n")
    (TARGET / "partial-result.json").write_text(json.dumps(result, separators=(",", ":")) + "\n")
    unavailable = [m["label"] for m in result["key_metrics"] if m["status"] != "available"]
    print(f"partial run: {len(unavailable)} key metrics unavailable, {len(result['missing'])} missing figures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
