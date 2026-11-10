"""Command-line interface.

    finsight analyze MSFT                      # live SEC data (needs FINSIGHT_SEC_USER_AGENT)
    finsight analyze DEMO --offline            # bundled SYNTHETIC fixture, no network
    finsight analyze MSFT --cutoff 2025-12-31 --latest-fy 2025
    finsight resume <run_id> [--company CIK]   # continue an interrupted / failed / needs-input run
    finsight show <run_id>
    finsight approve <run_id> --note "checked"
    finsight runs
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from finsight.config import Settings, load_settings, sec_user_agent
from finsight.contracts import AnalysisRequest, RunState, Severity, WorkflowRun
from finsight.export import presentation_tables, write_outputs
from finsight.orchestrator import Orchestrator, load_results
from finsight.sec.client import SecHttpClient
from finsight.sec.sources import FixtureSource, HttpCache, LiveSecSource
from finsight.storage import Store

FIXTURE_BANNER = ("*** OFFLINE DEMO: SYNTHETIC FIXTURE DATA. These figures are made up for testing and "
                  "do not describe any real company. ***")


def make_source_factory(settings: Settings, fixtures_dir: Path | None = None):
    def factory(request: AnalysisRequest):
        if request.offline:
            return FixtureSource(Path(fixtures_dir or settings.fixtures_dir))
        client = SecHttpClient(sec_user_agent(), settings.sec)
        return LiveSecSource(client=client, cache=HttpCache(settings.cache_dir), settings=settings.sec)
    return factory


def _print_run(run: WorkflowRun, store: Store, settings: Settings, out_root: Path | None, write: bool) -> int:
    results = load_results(store, run.id)
    plan_cp, val_cp, analysis = results["plan"], results["validation"], results["analysis"]
    is_fixture = bool(plan_cp and any(d.is_fixture for d in plan_cp.documents)) or run.request.offline
    if is_fixture:
        print(FIXTURE_BANNER)
    print(f"Run {run.id}  state={run.state.value}")
    if run.state == RunState.NEEDS_INPUT and run.needs_input:
        print(run.needs_input["question"])
        for c in run.needs_input.get("candidates", []):
            print(f"  CIK {c['cik']}  {c['title']}  ({', '.join(c['tickers'])})")
        print(f"Resume with: finsight resume {run.id} --company <CIK>")
    if run.error:
        print(f"Error: {run.error}")
    if plan_cp:
        c = plan_cp.plan.company
        print(f"Company: {c.name}  CIK {c.cik}  tickers {', '.join(c.tickers) or '-'}  "
              f"SIC {c.sic or '-'} {c.sic_description or ''}")
        print(f"Cutoff: {plan_cp.plan.cutoff_date}  currency: {plan_cp.plan.currency}")
        periods = val_cp.report.fiscal_periods if val_cp else plan_cp.plan.fiscal_periods
        for p in periods:
            print(f"  {p.label}: {p.start_date or '?'} to {p.end_date}  (10-K {p.defining_accession}, "
                  f"filed {p.defining_filing_date})")
        for n in plan_cp.plan.notes:
            print(f"  note: {n}")
    if analysis and val_cp:
        labels = [p.label for p in val_cp.report.fiscal_periods]
        fin, chg = presentation_tables(analysis.metrics, labels)
        with pd.option_context("display.max_columns", None, "display.width", 160):
            print("\nFinancials (USD millions unless stated; full precision kept in exports)")
            print(fin.to_string())
            print("\nYear-over-year changes")
            print(chg.to_string())
        if analysis.changes:
            print("\nFlagged changes (ranked by investigation settings, not a judgement of importance)")
            for i, f in enumerate(analysis.changes, 1):
                print(f"  {i}. [{f.rule}] {f.description}")
        else:
            print("\nNo changes exceeded the configured investigation thresholds.")
    if val_cp:
        issues = [i for i in val_cp.report.issues if i.severity != Severity.INFO]
        print(f"\nValidation issues: {len(issues)} (info items are in the exports)")
        for i in issues[:25]:
            print(f"  [{i.severity.value}] {i.message}")
        if len(issues) > 25:
            print(f"  ... {len(issues) - 25} more in validation_issues.csv")
    if write:
        out = (out_root or settings.output_dir) / run.id
        files = write_outputs(out, run, results)
        print(f"\nExports written to {out}/ ({', '.join(p.name for p in files)})")
    if run.state == RunState.READY_FOR_REVIEW:
        print("\nREADY FOR REVIEW (not approved). Approve with: finsight approve " + run.id)
    elif run.state == RunState.PARTIAL:
        print("\nPARTIAL: blocking issues above make some metrics unavailable. Nothing was estimated.")
    return 0 if run.state in (RunState.READY_FOR_REVIEW, RunState.PARTIAL, RunState.APPROVED) else (
        3 if run.state == RunState.NEEDS_INPUT else 2)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="finsight", description="Financial Analysis Copilot (Stage 1)")
    p.add_argument("--db", type=Path, help="SQLite database path")
    p.add_argument("--out", type=Path, help="Output directory root")
    p.add_argument("--fixtures", type=Path, help="Offline fixtures directory")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="Start a new analysis")
    a.add_argument("identifier", help="Ticker, CIK or company name")
    a.add_argument("--cutoff", type=date.fromisoformat, default=None,
                   help="Analysis cutoff date YYYY-MM-DD (default: today)")
    a.add_argument("--latest-fy", type=int, default=None,
                   help="Calendar year in which the latest fiscal year of the window ends")
    a.add_argument("--years", type=int, default=3)
    a.add_argument("--offline", action="store_true", help="Use bundled fixtures; no network")
    a.add_argument("--question", default=None, help="Optional analysis question (used from Stage 2)")
    a.add_argument("--no-export", action="store_true")

    r = sub.add_parser("resume", help="Resume an interrupted, failed or needs-input run")
    r.add_argument("run_id")
    r.add_argument("--company", default=None, help="Clarified company (CIK) for a NEEDS_INPUT run")

    s = sub.add_parser("show", help="Show a stored run")
    s.add_argument("run_id")
    s.add_argument("--export", action="store_true")

    ap = sub.add_parser("approve", help="Mark a reviewed run as approved")
    ap.add_argument("run_id")
    ap.add_argument("--note", default="")

    sub.add_parser("runs", help="List recent runs")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    store = Store(args.db or settings.database_path)
    orch = Orchestrator(settings, store, make_source_factory(settings, args.fixtures))
    try:
        if args.cmd == "analyze":
            req = AnalysisRequest(identifier=args.identifier, cutoff_date=args.cutoff or date.today(),
                                  latest_fiscal_year=args.latest_fy, num_years=args.years,
                                  question=args.question, offline=args.offline)
            run = orch.start(req)
            return _print_run(run, store, settings, args.out, write=not args.no_export)
        if args.cmd == "resume":
            run = orch.resume(args.run_id, identifier=args.company)
            return _print_run(run, store, settings, args.out, write=True)
        if args.cmd == "show":
            run = store.load_run(args.run_id)
            if run is None:
                print(f"No run {args.run_id}", file=sys.stderr)
                return 1
            return _print_run(run, store, settings, args.out, write=args.export)
        if args.cmd == "approve":
            run = orch.approve(args.run_id, args.note)
            print(f"Run {run.id} is now {run.state.value}")
            return 0
        if args.cmd == "runs":
            for row in store.list_runs():
                print("  ".join(str(x) for x in row))
            return 0
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        store.close()
    return 1


if __name__ == "__main__":
    sys.exit(main())
