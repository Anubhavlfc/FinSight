"""Build the sample analyses bundled with the web app (``web/public/sample/``).

The sample is produced by the real pipeline through the real API, then saved as
static files, so the landing page and the sample report work without a server:

    python scripts/build_web_sample.py                 # synthetic DEMO company (offline, no keys)
    python scripts/build_web_sample.py --live NVDA     # also cache a run on real SEC filings

``--live`` needs FINSIGHT_SEC_USER_AGENT (and uses Claude if an API key is set,
otherwise the rule-based investigator). Cached live samples are labelled as such
in the app and are never refreshed automatically.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from finsight.api.app import create_app  # noqa: E402
from finsight.api.settings import ApiSettings  # noqa: E402
from finsight.config import load_settings  # noqa: E402

TARGET = ROOT / "web" / "public" / "sample"
# analysis.json repeats the whole validation report; it is the largest file and the same
# information is in the CSV downloads, so it is left out of the static bundle.
SKIP = {"analysis.json", ".written"}


def build(client: TestClient, home: Path, sample_id: str, identifier: str, title: str, description: str) -> dict:
    created = client.post("/api/runs", json={"identifier": identifier})
    created.raise_for_status()
    run_id = created.json()["run_id"]
    deadline = time.monotonic() + 600
    while True:
        status = client.get(f"/api/runs/{run_id}").json()
        if status["phase"] not in ("queued", "running"):
            break
        if time.monotonic() > deadline:
            raise SystemExit(f"{identifier}: timed out in state {status['state']}")
        time.sleep(0.2)
    if not status["has_results"]:
        raise SystemExit(f"{identifier}: run ended as {status['state']}: {status.get('error')}")
    result = client.get(f"/api/runs/{run_id}/result").json()
    diagnostics = client.get(f"/api/runs/{run_id}/diagnostics").json()
    summary = next(r for r in client.get("/api/runs").json()["runs"] if r["id"] == run_id)

    out = TARGET / sample_id
    shutil.rmtree(out, ignore_errors=True)
    files = out / "files"
    src = home / "out" / run_id
    for p in sorted(src.rglob("*")):
        if p.is_file() and p.name not in SKIP:
            dest = files / p.relative_to(src)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dest)
    result["downloads"] = [d for d in result["downloads"] if d["name"] not in SKIP]
    (out / "result.json").write_text(json.dumps(result, separators=(",", ":")))
    (out / "diagnostics.json").write_text(json.dumps(diagnostics, separators=(",", ":")))
    print(f"{sample_id}: {status['state']}, {len(result['changes'])} changes, "
          f"{sum(f.stat().st_size for f in out.rglob('*') if f.is_file()) // 1024} KB")
    return {"id": sample_id, "title": title, "description": description,
            "generated_at": datetime.now(timezone.utc).isoformat(), "summary": summary}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="append", default=[], metavar="TICKER",
                    help="also cache a run on real SEC filings for this ticker (repeatable)")
    args = ap.parse_args()
    index_path = TARGET / "index.json"
    existing = {e["id"]: e for e in json.loads(index_path.read_text())} if index_path.exists() else {}
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        app = create_app(load_settings(home=home), ApiSettings(web_dist=None, runs_per_hour=1000))
        with TestClient(app) as client:
            existing["demo"] = build(
                client, home, "demo", "DEMO", "Demo Analytics Corp",
                "Synthetic company with a restatement, a loss-to-profit swing and negative free cash flow.")
            for ticker in args.live:
                sid = ticker.lower().replace(".", "-")
                existing[sid] = build(client, home, sid, ticker, ticker.upper(),
                                      "Cached run on real SEC filings. Not refreshed automatically.")
    TARGET.mkdir(parents=True, exist_ok=True)
    ordered = [existing.pop("demo")] + sorted(existing.values(), key=lambda e: e["id"])
    index_path.write_text(json.dumps(ordered, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
