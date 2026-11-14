"""Record real SEC EDGAR data for the golden accuracy tests.

    export FINSIGHT_SEC_USER_AGENT="FinSight research you@example.com"
    python scripts/record_golden_fixture.py MSFT AAPL WMT

Writes ``fixtures/golden/`` in the same layout as the offline demo fixture, so
``FixtureSource`` can replay it:

* ``company_tickers.json``: only the recorded companies.
* ``submissions/CIK##########.json``: the filing index, trimmed to the columns the
  planner reads (accession, filing date, report date, form, primary document).
* ``companyfacts/CIK##########.json``: only the us-gaap / dei concepts named in
  ``config/mappings.toml`` (plus company overrides), and only rows for periods ending on
  or after ``MIN_PERIOD_END``. Within that, every row is kept, including restated values,
  prior-year comparatives and quarterly rows, so the normalizer's period and restatement
  rules are exercised on real data.

Filing text is not recorded (it is large); the golden tests run with ``--no-investigate``.
The expected values live in ``tests/golden/expected.toml`` and are typed from the
financial statements in each 10-K, not produced by FinSight.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from finsight.config import load_mappings, load_settings, sec_user_agent  # noqa: E402
from finsight.sec.client import SecHttpClient  # noqa: E402
from finsight.sec.sources import HttpCache, LiveSecSource  # noqa: E402

OUT = REPO / "fixtures" / "golden"
# Rows for periods ending before this date cannot fall in a window ending FY2024 or later.
MIN_PERIOD_END = "2021-01-01"
SUBMISSION_KEYS = ["accessionNumber", "filingDate", "reportDate", "form", "primaryDocument"]
EXTRA_CONCEPTS = {"dei:EntityCommonStockSharesOutstanding",
                  "us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding"}


def wanted_concepts(cik: str) -> set[str]:
    mappings = load_mappings(cik=cik)
    tags: set[str] = set(EXTRA_CONCEPTS)
    for name in mappings.fields:
        tags.update(mappings[name].tags)
    return tags


def trim_submissions(sub: dict) -> dict:
    out = {k: sub[k] for k in ("cik", "entityType", "sic", "sicDescription", "name", "tickers",
                               "exchanges", "fiscalYearEnd", "stateOfIncorporation") if k in sub}
    recent = sub["filings"]["recent"]
    out["filings"] = {"recent": {k: recent[k] for k in SUBMISSION_KEYS}, "files": []}
    return out


def trim_facts(facts: dict, keep: set[str]) -> dict:
    out = {k: v for k, v in facts.items() if k != "facts"}
    out["facts"] = {}
    for taxonomy, concepts in facts["facts"].items():
        kept = {}
        for name, c in concepts.items():
            if f"{taxonomy}:{name}" not in keep:
                continue
            units = {u: [r for r in rows if r.get("end", "") >= MIN_PERIOD_END]
                     for u, rows in c.get("units", {}).items()}
            kept[name] = {**c, "units": {u: rows for u, rows in units.items() if rows}}
        if kept:
            out["facts"][taxonomy] = kept
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("tickers", nargs="+")
    args = ap.parse_args()
    ua = sec_user_agent()
    if not ua:
        print("Set FINSIGHT_SEC_USER_AGENT first (SEC requires a contact in the User-Agent).")
        return 2
    settings = load_settings()
    source = LiveSecSource(client=SecHttpClient(ua, settings.sec), cache=HttpCache(settings.cache_dir),
                           settings=settings.sec)
    tickers = source.company_tickers().payload
    by_ticker = {row["ticker"].upper(): row for row in tickers.values()}
    (OUT / "submissions").mkdir(parents=True, exist_ok=True)
    (OUT / "companyfacts").mkdir(parents=True, exist_ok=True)

    kept_tickers: dict[str, dict] = {}
    recorded = []
    for i, t in enumerate(args.tickers):
        row = by_ticker[t.upper()]
        cik = f"{int(row['cik_str']):010d}"
        kept_tickers[str(i)] = row
        sub = source.submissions(cik)
        facts = source.company_facts(cik, force_refresh=True)
        (OUT / "submissions" / f"CIK{cik}.json").write_text(
            json.dumps(trim_submissions(sub.payload), indent=None, separators=(",", ":")))
        (OUT / "companyfacts" / f"CIK{cik}.json").write_text(
            json.dumps(trim_facts(facts.payload, wanted_concepts(cik)), indent=None, separators=(",", ":")))
        recorded.append({"ticker": t.upper(), "cik": cik,
                         "submissions_sha256": sub.document.sha256,
                         "companyfacts_sha256": facts.document.sha256})
        print(f"{t}: CIK {cik}")
    (OUT / "company_tickers.json").write_text(json.dumps(kept_tickers, indent=1))
    (OUT / "recorded.json").write_text(json.dumps(
        {"recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "note": "Real SEC EDGAR data, trimmed. SHA-256 values are of the untrimmed responses.",
         "companies": recorded}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
