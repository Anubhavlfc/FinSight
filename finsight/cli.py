"""Command-line interface.

    finsight analyze MSFT                      # live SEC data (needs FINSIGHT_SEC_USER_AGENT)
    finsight analyze DEMO --offline            # bundled SYNTHETIC fixture, no network
    finsight analyze MSFT --cutoff 2025-12-31 --latest-fy 2025
    finsight analyze MSFT --llm stub           # rule-based offline stub instead of Claude
    finsight analyze MSFT --no-investigate     # Stage 1 only: data pipeline, no report
    finsight resume <run_id> [--company CIK]   # continue an interrupted / failed / needs-input run
    finsight show <run_id>
    finsight review <run_id>                   # the changes to check and the review history
    finsight approve <run_id> --reviewer "Dana Lee" --confirm-all --figures-checked --limitations-read
    finsight request-changes <run_id> --reviewer "Dana Lee" --dispute <change id> --note "wrong quote"
    finsight evaluate <run_id>                 # agentic findings vs a single-prompt baseline (needs Claude)
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
from finsight.errors import InvalidTransitionError
from finsight.orchestrator import Orchestrator, load_results
from finsight.review.human import CONFIRMED, DISPUTED, ReviewDecisionError
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
    inv, reviews = results.get("investigation"), results.get("review")
    if inv is not None:
        print(f"\nInvestigation ({run.stats.get('llm_provider', '?')}; {run.stats.get('llm_provider_note', '')})")
        for f in inv.findings:
            print(f"  [{f.evidence_status.value}] {f.calculated_observation}")
            if f.management_explanation:
                print(f"      management: {f.management_explanation[:200]}")
            for q in f.unresolved_questions[:2]:
                print(f"      unresolved: {q}")
        for n in inv.notes:
            print(f"  note: {n}")
        u = inv.llm_usage
        if u:
            print(f"  LLM usage: {u.get('model_calls')} call(s), {u.get('input_tokens')} in / "
                  f"{u.get('output_tokens')} out tokens, est. ${u.get('estimated_cost_usd')}")
    if reviews is not None and reviews.latest:
        r = reviews.latest
        print(f"\nReview: {'passed' if r.passed else 'NOT passed'} after {r.revision_cycle} revision cycle(s); "
              f"{len([d for d in r.defects if d.severity == 'error'])} unresolved defect(s)")
        for d in r.defects[:10]:
            print(f"  [{d.severity}/{d.source}] {d.code}: {d.message}")
        for c in r.checks_run:
            if c.startswith("llm_"):
                print(f"  {c}")
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
        print(f"\nREADY FOR REVIEW (not approved). See what to check with: finsight review {run.id}")
    elif run.state == RunState.PARTIAL:
        print("\nPARTIAL: blocking issues above make some metrics unavailable. Nothing was estimated.")
    return 0 if run.state in (RunState.READY_FOR_REVIEW, RunState.PARTIAL, RunState.APPROVED) else (
        3 if run.state == RunState.NEEDS_INPUT else 2)


def _investigated_changes(store: Store, run_id: str) -> list[tuple[str, str]]:
    """(change flag id, one-line description) for every change with a finding, in rank order."""
    results = load_results(store, run_id)
    inv, analysis = results.get("investigation"), results.get("analysis")
    if not inv:
        return []
    flags = {f.id: f for f in (analysis.changes if analysis else [])}
    out = []
    for f in inv.findings:
        flag = flags.get(f.change_flag_id)
        out.append((f.change_flag_id, (flag.description if flag else f.calculated_observation)
                    + f"  [evidence: {f.evidence_status.value}]"))
    return out


def _print_review(store: Store, run_id: str) -> int:
    run = store.load_run(run_id)
    if run is None:
        print(f"No run {run_id}", file=sys.stderr)
        return 1
    print(f"Run {run.id}  state={run.state.value}")
    changes = _investigated_changes(store, run_id)
    if changes:
        print("\nInvestigated changes. Check each explanation against its quoted 10-K passages:")
        for cid, text in changes:
            print(f"  {cid}\n      {text}")
    history = run.stats.get("human_reviews") or []
    if history:
        print("\nReview history:")
        for h in history:
            disputed = [k for k, v in (h.get("checks") or {}).get("findings", {}).items() if v == DISPUTED]
            print(f"  {h['at'][:16].replace('T', ' ')} UTC  {h['decision']} by {h['reviewer']}"
                  + (f": {h['note']}" if h.get("note") else "")
                  + (f"  (disputed: {', '.join(disputed)})" if disputed else ""))
    if run.state in (RunState.READY_FOR_REVIEW, RunState.PARTIAL):
        print("\nTo approve:  finsight approve " + run.id + ' --reviewer "<your name>" --confirm-all '
              "--figures-checked --limitations-read [--note ...]")
        print("To object:   finsight request-changes " + run.id + ' --reviewer "<your name>" '
              '[--dispute <change id> ...] --note "<what is wrong>"')
    return 0


def _record_decision(orch: Orchestrator, store: Store, args: argparse.Namespace) -> int:
    decision = "approved" if args.cmd == "approve" else "changes_requested"
    ids = [cid for cid, _ in _investigated_changes(store, args.run_id)]
    confirmed = ids if getattr(args, "confirm_all", False) else args.confirm
    findings = {cid: CONFIRMED for cid in confirmed}
    findings.update({cid: DISPUTED for cid in args.dispute})
    checks = {"findings": findings, "figures_checked": args.figures_checked,
              "limitations_read": args.limitations_read}
    try:
        run = orch.record_review(args.run_id, decision, args.reviewer, args.note, checks)
    except (ReviewDecisionError, InvalidTransitionError) as exc:
        print(f"Not recorded. {exc}", file=sys.stderr)
        return 2
    label = "approved" if decision == "approved" else "changes requested; the report stays unapproved"
    print(f"Run {run.id}: {label} (state={run.state.value})")
    return 0


def _evaluate(settings: Settings, store: Store, args: argparse.Namespace) -> int:
    from finsight.config import llm_model
    from finsight.evaluation.compare import evaluate, to_markdown, write
    from finsight.llm.factory import has_anthropic_credentials
    from finsight.llm.provider import AnthropicProvider, LLMError

    if store.load_run(args.run_id) is None:
        print(f"No run {args.run_id}", file=sys.stderr)
        return 1
    if not has_anthropic_credentials():
        print("The baseline is a single Claude call, so it needs ANTHROPIC_API_KEY. Comparing against the "
              "rule-based stub would measure nothing.", file=sys.stderr)
        return 2
    provider = AnthropicProvider(settings.llm, llm_model(settings))
    try:
        ev = evaluate(load_results(store, args.run_id), provider, per_flag=args.excerpts,
                      min_quote_chars=settings.investigation.min_quote_chars)
    except (ValueError, LLMError) as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        return 2
    print(to_markdown(ev))
    files = write(ev, (args.out or settings.output_dir) / args.run_id)
    print(f"Written: {', '.join(str(f) for f in files)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="finsight", description="Financial Analysis Copilot")
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
    a.add_argument("--question", default=None, help="Optional analysis question, given to the investigator")
    a.add_argument("--llm", choices=["auto", "anthropic", "stub"], default="auto",
                   help="auto: Claude when ANTHROPIC_API_KEY is set and not --offline, else the offline stub")
    a.add_argument("--no-investigate", action="store_true",
                   help="Stop after the data pipeline (no investigation, report or review)")
    a.add_argument("--no-export", action="store_true")

    r = sub.add_parser("resume", help="Resume an interrupted, failed or needs-input run")
    r.add_argument("run_id")
    r.add_argument("--company", default=None, help="Clarified company (CIK) for a NEEDS_INPUT run")

    s = sub.add_parser("show", help="Show a stored run")
    s.add_argument("run_id")
    s.add_argument("--export", action="store_true")

    rv = sub.add_parser("review", help="Show the investigated changes to check and the review history")
    rv.add_argument("run_id")

    def decision_parser(name: str, help_: str) -> argparse.ArgumentParser:
        d = sub.add_parser(name, help=help_)
        d.add_argument("run_id")
        d.add_argument("--reviewer", required=True, help="Your name; recorded with the decision")
        d.add_argument("--note", default="", help="What you checked, or what needs to change")
        d.add_argument("--confirm", nargs="+", default=[], metavar="CHANGE_ID",
                       help="Changes whose explanation you checked against the quoted passages")
        d.add_argument("--dispute", nargs="+", default=[], metavar="CHANGE_ID", help="Changes that are wrong")
        d.add_argument("--figures-checked", action="store_true",
                       help="You compared the key figures with the 10-K financial statements")
        d.add_argument("--limitations-read", action="store_true",
                       help="You read the data-quality issues and limitations")
        return d

    ap = decision_parser("approve", "Approve a reviewed report (needs every change confirmed and both checks)")
    ap.add_argument("--confirm-all", action="store_true", help="Confirm every investigated change")
    decision_parser("request-changes", "Record what is wrong; the report stays unapproved")

    ev = sub.add_parser("evaluate", help="Compare a run's agentic findings with a single-prompt baseline "
                                         "(one Claude call; needs ANTHROPIC_API_KEY)")
    ev.add_argument("run_id")
    ev.add_argument("--excerpts", type=int, default=6, help="Retrieved 10-K excerpts per change (default 6)")

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
                                  question=args.question, offline=args.offline, llm=args.llm,
                                  investigate=not args.no_investigate)
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
        if args.cmd == "review":
            return _print_review(store, args.run_id)
        if args.cmd in ("approve", "request-changes"):
            return _record_decision(orch, store, args)
        if args.cmd == "evaluate":
            return _evaluate(settings, store, args)
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
