"""Reshape stored run state and checkpoints into the API's response models.

Nothing here computes a financial figure. Values come from validated metrics and
are formatted with the engine's own formatters (``finsight.presentation``), so the
dashboard, the report and the downloads always show the same numbers.
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from finsight.api import schemas as s
from finsight.config import Settings
from finsight.contracts import (
    CalculatedMetric,
    ChangeFlag,
    EvidenceStatus,
    MetricStatus,
    RunState as S,
    Severity,
    WorkflowRun,
)
from finsight.export import CHANGE_ROWS, FINANCIAL_ROWS, presentation_tables, write_outputs
from finsight.orchestrator import PlanCheckpoint, load_results
from finsight.presentation import fmt_metric, fmt_usd
from finsight.report.charts import CHARTS, build_chart_specs
from finsight.storage import Store

STAGES: list[tuple[str, str, S | None]] = [
    ("planning", "Planning", S.PLANNING),
    ("collecting", "Collecting", S.COLLECTING),
    ("validating", "Validating", S.VALIDATING),
    ("calculating", "Calculating", S.ANALYZING),
    ("investigating", "Investigating", S.INVESTIGATING),
    ("writing", "Writing", S.DRAFTING),
    ("reviewing", "Reviewing", S.REVIEWING),
    ("ready", "Ready for review", None),
]
STAGE_KEY = {state: key for key, _, state in STAGES if state is not None}
DONE_STATES = {S.READY_FOR_REVIEW, S.PARTIAL, S.APPROVED}
RESULT_STATES = DONE_STATES
ORDER = [S.CREATED, S.PLANNING, S.COLLECTING, S.VALIDATING, S.ANALYZING, S.INVESTIGATING, S.DRAFTING, S.REVIEWING,
         S.READY_FOR_REVIEW]

LABELS = {name: label for name, label in FINANCIAL_ROWS + CHANGE_ROWS}
LABELS.update({
    "revenue_change": "Revenue change", "net_income_change": "Net income change",
    "net_income_growth": "Net income growth %", "operating_cash_flow_change": "Operating cash flow change",
    "capital_expenditure_change": "Capital expenditure change",
    "capital_expenditure_growth": "Capital expenditure growth %",
    "interest_bearing_debt_growth": "Debt growth %", "current_ratio_change": "Current ratio change",
    "free_cash_flow": "Free cash flow", "interest_bearing_debt": "Interest-bearing debt",
})

EVIDENCE_LABEL = {
    "supported": "Supported by filing quotes",
    "partially_supported": "Partly supported",
    "unsupported": "No supporting evidence found",
    "not_investigated": "Not investigated",
}

DOWNLOADS: list[tuple[str, str, str]] = [
    ("research_report.pdf", "Research report (PDF)",
     "Company header, market valuation, cited narrative sections and an appendix of calculations and sources"),
    ("report.html", "Report (HTML)", "Self-contained report with charts, citations and calculation notes"),
    ("report.md", "Report (Markdown)", "The same report as Markdown text"),
    ("financial_dataset.csv", "Financial dataset", "Accepted values per field and fiscal year, full precision"),
    ("metrics.csv", "Metrics", "Every metric with formula, status and input fact IDs"),
    ("facts_audit_trail.csv", "Facts audit trail", "Accepted, alternative and rejected XBRL facts with reasons"),
    ("validation_issues.csv", "Validation issues", "Every validation issue by severity"),
    ("evidence_passages.csv", "Evidence passages", "Every cited filing passage with accession and position"),
    ("findings.json", "Findings", "Findings with quotes, status and the investigation tool trace"),
    ("review.json", "Review record", "Checks run and defects found in each review cycle"),
    ("analysis.json", "Full analysis", "Plan, sources, validation report, metrics and change flags"),
    ("run_summary.json", "Run summary", "State history and run statistics"),
]
DOWNLOAD_META = {name: (label, desc) for name, label, desc in DOWNLOADS}

_outputs_lock = threading.Lock()


def label_for(name: str) -> str:
    return LABELS.get(name) or name.replace("_", " ").capitalize()


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _clean_error(message: str) -> str:
    message = re.sub(r"\s*\((?:resume to[^)]*)\)", "", message)
    message = re.sub(r"\s*Or run with --offline[^.]*\.", "", message)
    message = re.sub(r",? or rerun with --llm stub\.?", ".", message)
    return message.strip()


def error_next_steps(message: str, retryable: bool) -> list[str]:
    m = message.lower()
    if "falls under" in m or "20-f" in m:
        return ["Choose a US nonfinancial company that files Form 10-K, for example MSFT, COST or NVDA.",
                "Open the sample analysis to see what a finished report looks like."]
    if "no sec registrant" in m:
        return ["Check the ticker, or search by company name and pick a result from the list."]
    if "no form 10-k" in m:
        return ["Choose an earlier fiscal year, or a company with at least two annual reports on file."]
    if "user-agent" in m or "finsight_sec_user_agent" in m:
        return ["Live SEC access is not configured on this server. The sample analysis still works.",
                "If you run this server, set FINSIGHT_SEC_USER_AGENT and restart it."]
    if "llm" in m or "anthropic" in m:
        return ["Retry: completed stages are reused.",
                "If it keeps failing, start a new analysis with the rule-based investigator."]
    if retryable:
        return ["Retry: the run continues from the stage that stopped, and completed stages are reused."]
    return []


def review_status(run: WorkflowRun) -> s.ReviewStatus:
    rv = run.stats.get("review") or {}
    base = dict(passed=rv.get("passed"), revision_cycles=int(rv.get("revision_cycles") or 0),
                unresolved_defects=int(rv.get("unresolved_defects") or 0))
    if run.state == S.APPROVED:
        t = next((t for t in reversed(run.history) if t.to_state == S.APPROVED), None)
        note = t.reason.split(":", 1)[1].strip() if t and ":" in t.reason else None
        return s.ReviewStatus(state="approved", label="Approved by analyst", approved_at=t.at if t else None,
                              approval_note=note, **base)
    if run.state in (S.READY_FOR_REVIEW, S.PARTIAL):
        if rv.get("needs_analyst_attention"):
            return s.ReviewStatus(state="needs_attention", label="Needs analyst attention", **base)
        return s.ReviewStatus(state="awaiting_review", label="Awaiting analyst review", **base)
    return s.ReviewStatus(state="not_reviewed", label="Not ready for review", **base)


def investigator_used(run: WorkflowRun) -> s.InvestigatorUsed | None:
    name = run.stats.get("llm_provider")
    if not name:
        return None
    stub = "stub" in name
    shown = "Rule-based investigator (no LLM)" if stub else (
        f"Claude ({name.split(':', 1)[1]})" if name.startswith("anthropic:") else name)
    return s.InvestigatorUsed(name=shown, is_rule_based=stub, note=str(run.stats.get("llm_provider_note") or ""))


def company_ref(plan_cp: PlanCheckpoint | None) -> s.CompanyRef | None:
    if plan_cp is None:
        return None
    c = plan_cp.plan.company
    return s.CompanyRef(cik=c.cik, name=c.name, ticker=c.tickers[0] if c.tickers else None, tickers=c.tickers)


def phase_of(run: WorkflowRun, active: bool) -> str:
    if run.state == S.APPROVED:
        return "approved"
    if run.state == S.READY_FOR_REVIEW:
        return "ready_for_review"
    if run.state == S.PARTIAL:
        return "partial"
    if run.state == S.FAILED:
        return "failed"
    if run.state == S.NEEDS_INPUT:
        return "needs_input"
    if not active:
        return "interrupted"
    return "queued" if run.state == S.CREATED else "running"


def _stages(run: WorkflowRun) -> list[s.StageStatus]:
    entered: dict[S, datetime] = {}
    left: dict[S, tuple[datetime, str]] = {}
    for t in run.history:
        entered.setdefault(t.to_state, t.at)
        left[t.from_state] = (t.at, t.reason)
    out = []
    finished = run.state in DONE_STATES
    current = {S.FAILED: run.failed_stage or S.PLANNING, S.NEEDS_INPUT: S.PLANNING}.get(run.state, run.state)
    for key, label, state in STAGES:
        if state is None:
            done_at = next((t.at for t in reversed(run.history) if t.to_state in (S.READY_FOR_REVIEW, S.PARTIAL)),
                           None)
            out.append(s.StageStatus(key=key, label=label if run.state != S.PARTIAL else "Partial results",
                                     state="done" if finished else "pending", started_at=done_at,
                                     finished_at=done_at))
            continue
        if finished:
            st = "done" if state in entered else "skipped"
        elif state == current:
            st = "failed" if run.state == S.FAILED else "active"
        elif state in entered and ORDER.index(state) < ORDER.index(current):
            st = "done"
        else:
            st = "pending"  # includes stages a revision loop will run again
        detail = left[state][1] if st == "done" and state in left else None
        out.append(s.StageStatus(key=key, label=label, state=st, started_at=entered.get(state),
                                 finished_at=left[state][0] if st in ("done", "failed") and state in left else None,
                                 detail=_sentence(detail) if detail else None))
    return out


def _sentence(text: str) -> str:
    text = _clean_error(text).replace("->", "→")
    return text[:1].upper() + text[1:] if text else text


def _activity(run: WorkflowRun) -> list[s.ActivityItem]:
    items = []
    for t in run.history:
        if t.to_state == S.FAILED:
            kind, stage = "error", STAGE_KEY.get(run.failed_stage or t.from_state)
        elif t.to_state in (S.NEEDS_INPUT, S.PARTIAL):
            kind, stage = "warning", STAGE_KEY.get(t.from_state)
        elif t.to_state in (S.READY_FOR_REVIEW, S.APPROVED):
            kind, stage = "success", "ready"
        elif t.from_state in ORDER and t.to_state in ORDER and ORDER.index(t.to_state) < ORDER.index(t.from_state):
            kind, stage = "warning", STAGE_KEY.get(t.from_state)
        else:
            kind, stage = "info", STAGE_KEY.get(t.from_state)
        message = "Analysis started" if t.from_state == S.CREATED and t.to_state == S.PLANNING else _sentence(t.reason)
        items.append(s.ActivityItem(at=t.at, stage=stage, kind=kind, message=message))
    return items[-60:]


def _current_activity(run: WorkflowRun, phase: str, synthetic: bool, inv: s.InvestigationProgress | None) -> str:
    if phase == "interrupted":
        where = STAGE_KEY.get(run.state, "the start")
        return f"The server stopped while this analysis was at {where}. Resume to continue; completed stages are reused."
    text = {
        S.CREATED: "Queued. Waiting for a free worker.",
        S.PLANNING: "Identifying the company and selecting fiscal years from its 10-K filing history.",
        S.COLLECTING: ("Reading reported figures from the bundled synthetic fixture." if synthetic else
                       "Collecting reported figures from SEC EDGAR."),
        S.VALIDATING: "Checking reporting periods, units, restatements and accounting relationships.",
        S.ANALYZING: "Calculating metrics and ranking year-over-year changes.",
        S.INVESTIGATING: "Searching the 10-K text for the company's explanation of each flagged change.",
        S.DRAFTING: "Writing the report from validated metrics and findings.",
        S.REVIEWING: "Checking every number, citation and quote in the draft.",
        S.READY_FOR_REVIEW: "Ready for analyst review.",
        S.PARTIAL: "Finished with partial results. Some metrics are unavailable and nothing was estimated.",
        S.APPROVED: "Approved by an analyst.",
        S.NEEDS_INPUT: "Waiting for you to choose which company you meant.",
        S.FAILED: f"Stopped at {STAGE_KEY.get(run.failed_stage, 'planning')}.",
    }[run.state]
    if run.state == S.INVESTIGATING and inv and inv.investigated:
        text += f" {inv.investigated} of {min(inv.flagged, inv.limit)} done."
    return text


def _warnings(run: WorkflowRun, results: dict) -> list[str]:
    out: list[str] = []
    issues = run.stats.get("issues") or {}
    if issues.get("blocking"):
        out.append(f"{issues['blocking']} blocking data issue(s): the affected metrics are unavailable.")
    if issues.get("warning"):
        out.append(f"{issues['warning']} data validation warning(s), for example restated or competing values. "
                   "They are listed under Sources and limitations.")
    plan_cp = results.get("plan")
    if plan_cp:
        out += list(plan_cp.plan.notes)
    inv = results.get("investigation")
    if inv:
        out += list(inv.notes)
        if inv.not_investigated and not any("not investigated" in n for n in inv.notes):
            out.append(f"{len(inv.not_investigated)} flagged change(s) were not investigated (budget or limit).")
    rv = run.stats.get("review") or {}
    if rv.get("needs_analyst_attention"):
        out.append(f"{rv.get('unresolved_defects', 0)} review defect(s) remain after the revision limit.")
    used = investigator_used(run)
    if used and used.is_rule_based:
        out.append("Explanations come from the rule-based investigator, not an AI model. It only quotes sentences "
                   "that state a cause, so some changes stay unexplained.")
    return out


def build_status(run: WorkflowRun, results: dict, active: bool, settings: Settings) -> s.RunStatus:
    plan_cp, val_cp, inv = results.get("plan"), results.get("validation"), results.get("investigation")
    synthetic = run.request.offline
    phase = phase_of(run, active)
    progress = None
    if run.stats.get("change_flags") is not None and run.request.investigate:
        progress = s.InvestigationProgress(investigated=len(inv.findings) if inv else 0,
                                           flagged=int(run.stats["change_flags"]),
                                           limit=settings.investigation.max_findings)
    terminal = run.state in DONE_STATES | {S.FAILED, S.NEEDS_INPUT}
    now = datetime.now(timezone.utc)
    done_at = next((t.at for t in reversed(run.history) if t.to_state in (S.READY_FOR_REVIEW, S.PARTIAL, S.FAILED,
                                                                         S.NEEDS_INPUT)), None)
    end = (done_at or run.updated_at) if terminal or phase == "interrupted" else now
    error = None
    if run.state == S.FAILED and run.error:
        raw = run.error
        retryable = "resume to" in raw or run.failed_stage not in (S.PLANNING, None)
        msg = _clean_error(raw)
        error = s.RunError(message=msg, stage=STAGE_KEY.get(run.failed_stage), retryable=retryable,
                           next_steps=error_next_steps(raw, retryable))
    needs = None
    if run.state == S.NEEDS_INPUT and run.needs_input:
        needs = s.NeedsInput(question=run.needs_input.get("question", "Which company did you mean?"),
                             candidates=[s.Candidate(cik=c["cik"], name=c["title"], tickers=c.get("tickers", []))
                                         for c in run.needs_input.get("candidates", [])])
    periods = [p.label for p in ((val_cp.report.fiscal_periods if val_cp else None)
                                 or (plan_cp.plan.fiscal_periods if plan_cp else []))]
    return s.RunStatus(
        id=run.id, state=run.state.value, phase=phase, is_active=active, identifier=run.request.identifier,
        question=run.request.question, company=company_ref(plan_cp), periods=periods, synthetic=synthetic,
        created_at=run.created_at, updated_at=run.updated_at,
        finished_at=done_at if run.state in DONE_STATES | {S.FAILED} else None,
        elapsed_seconds=round(max(0.0, (_utc(end) - _utc(run.created_at)).total_seconds()), 1),
        stages=_stages(run), current_activity=_current_activity(run, phase, synthetic, progress),
        activity=_activity(run), warnings=_warnings(run, results), error=error, needs_input=needs,
        review=review_status(run), investigator=investigator_used(run), investigation=progress,
        has_results=results.get("analysis") is not None and run.state in RESULT_STATES,
        has_report=results.get("draft") is not None and run.state in RESULT_STATES,
    )


def build_summary(run: WorkflowRun, store: Store, active: bool) -> s.RunSummary:
    plan_cp = store.load_checkpoint(run.id, "plan", PlanCheckpoint)
    stages = set(store.checkpoint_stages(run.id))
    has_results = "analysis" in stages and run.state in RESULT_STATES
    has_report = "draft" in stages and run.state in RESULT_STATES
    formats = (["HTML", "Markdown"] if has_report else []) + (["CSV", "JSON"] if has_results else []) + (
        ["PNG"] if has_report else [])
    return s.RunSummary(
        id=run.id, state=run.state.value, phase=phase_of(run, active), is_active=active,
        identifier=run.request.identifier, company=company_ref(plan_cp),
        periods=[p.label for p in plan_cp.plan.fiscal_periods] if plan_cp else [], synthetic=run.request.offline,
        created_at=run.created_at, updated_at=run.updated_at, review=review_status(run),
        investigator=investigator_used(run), has_results=has_results, has_report=has_report, formats=formats)


# --------------------------------------------------------------------------- results


def _parts(m: CalculatedMetric) -> tuple[str, str]:
    """Split the engine's display string into a figure and its unit, e.g. ('$1,239', 'million')."""
    text = fmt_metric(m)
    if m.value is None:
        return text, ""
    if m.unit == "USD":
        head, _, tail = text.partition(" ")
        return head, tail
    if m.unit == "percentage_points":
        return f"{m.value:+.1f}", "pp"
    if m.unit == "percent":
        return text[:-1], "%"
    return text[:-1], "x"


def _point(m: CalculatedMetric | None, period: str) -> s.SeriesPoint:
    if m is None:
        return s.SeriesPoint(period=period, display="not reported", value=None)
    ok = m.status == MetricStatus.AVAILABLE and m.value is not None
    return s.SeriesPoint(period=period, display=fmt_metric(m), value=float(m.value) if ok else None)


KEY_METRICS = [
    ("revenue", ["revenue_growth"]),
    ("operating_margin", ["operating_margin_change"]),
    ("net_income", ["net_income_growth", "net_income_change"]),
    ("operating_cash_flow", ["operating_cash_flow_change"]),
    ("free_cash_flow", ["free_cash_flow_growth", "free_cash_flow_change"]),
    ("cash_and_equivalents", ["cash_and_equivalents_change"]),
    ("interest_bearing_debt", ["interest_bearing_debt_growth", "interest_bearing_debt_change"]),
]


def _comparison(by_id: dict[str, CalculatedMetric], names: list[str], prev: str, cur: str) -> s.Comparison | None:
    note = None
    for name in names:
        m = by_id.get(f"metric:{name}:{prev}->{cur}")
        if m is None:
            continue
        if m.status == MetricStatus.AVAILABLE and m.value is not None:
            direction = "up" if m.value > 0 else "down" if m.value < 0 else "flat"
            text = fmt_metric(m, signed=True).replace(" percentage points", " pp")
            return s.Comparison(label=f"vs {prev}", display=text, direction=direction, metric_id=m.id, note=note)
        note = note or (f"Percentage change not shown: {m.note}" if m.note else None)
    return s.Comparison(label=f"vs {prev}", display=None, direction="none", metric_id=None,
                        note=note or "No comparison available") if names else None


def _key_metrics(by_id: dict[str, CalculatedMetric], labels: list[str]) -> list[s.KeyMetric]:
    out = []
    last = labels[-1]
    for name, comps in KEY_METRICS:
        m = by_id.get(f"metric:{name}:{last}")
        if m is None:
            continue
        value_text, unit_text = _parts(m)
        out.append(s.KeyMetric(
            name=name, label=label_for(name).replace(" %", "").replace(" (calculated)", ""), period=last,
            metric_id=m.id, value_text=value_text, unit_text=unit_text, display=fmt_metric(m),
            status=m.status.value, note=m.note or None, is_calculated=m.is_calculated_measure, formula=m.formula,
            comparison=_comparison(by_id, comps, labels[-2], last) if len(labels) > 1 else None,
            series=[_point(by_id.get(f"metric:{name}:{p}"), p) for p in labels]))
    return out


def _table(caption: str, frame, rows: list[tuple[str, str]], by_id: dict[str, CalculatedMetric]) -> s.DataTable:
    out = []
    for (name, label), values in zip(rows, frame.values):
        cells = []
        for col, text in zip(frame.columns, values):
            m = by_id.get(f"metric:{name}:{col}")
            cells.append(s.TableCell(display=text or "not reported", status=m.status.value if m else "missing",
                                     metric_id=m.id if m else None))
        out.append(s.TableRow(name=name, label=label, cells=cells))
    return s.DataTable(caption=caption, columns=[c.replace("->", " → ") for c in frame.columns], rows=out)


def _magnitude(flag: ChangeFlag) -> str:
    v = flag.observed
    if flag.observed_unit == "USD":
        return fmt_usd(v, signed=True)
    if flag.observed_unit == "percentage_points":
        return f"{v:+.1f} pp"
    if flag.observed_unit == "percent":
        return f"{v:+.1f}%"
    return f"{v:+.2f}"


def _threshold(flag: ChangeFlag) -> str:
    if flag.direction == "transition":
        return "Flagged because the sign changed"
    t = flag.threshold.normalize() if isinstance(flag.threshold, Decimal) else flag.threshold
    unit = {"percent": "%", "percentage_points": " pp"}.get(flag.observed_unit, "")
    return f"Investigation setting: {t:f}{unit} or more"


def _base_metric(flag: ChangeFlag) -> str:
    return re.sub(r"_(growth|change)$", "", flag.metric)


AREAS = [
    ("profitability", "Profitability",
     [("gross_margin", "gross_margin_change"), ("operating_margin", "operating_margin_change"),
      ("net_margin", "net_margin_change"), ("net_income", "net_income_growth")]),
    ("liquidity", "Liquidity",
     [("current_ratio", "current_ratio_change"), ("cash_and_equivalents", "cash_and_equivalents_change")]),
    ("cash_generation", "Cash generation",
     [("operating_cash_flow", "operating_cash_flow_change"),
      ("capital_expenditure", "capital_expenditure_growth"), ("free_cash_flow", "free_cash_flow_change")]),
    ("leverage", "Leverage",
     [("interest_bearing_debt", "interest_bearing_debt_growth")]),
]


def _join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _interpretation(by_id: dict[str, CalculatedMetric], labels: list[str],
                    flags: list[ChangeFlag]) -> list[s.InterpretationArea]:
    out = []
    for key, title, specs in AREAS:
        observations, ids, measures = [], [], []
        for name, change in specs:
            ms = [by_id.get(f"metric:{name}:{p}") for p in labels]
            if all(m is None for m in ms):
                continue
            label = label_for(name).replace(" %", "").replace(" (x)", "")
            have = [(p, m) for p, m in zip(labels, ms)
                    if m is not None and m.status == MetricStatus.AVAILABLE and m.value is not None]
            first = next((m for m in ms if m is not None), None)
            measures.append(s.Measure(name=name, label=label, points=[_point(m, p) for p, m in zip(labels, ms)],
                                      is_calculated=bool(first and first.is_calculated_measure),
                                      note=(first.note or None) if first else None))
            if not have:
                reason = next((m.note for m in ms if m is not None and m.note), "not reported")
                observations.append(f"{label} is not available for these years: {reason}.")
                continue
            text = f"{label} was " + _join([f"{fmt_metric(m)} in {p}" for p, m in have]) + "."
            ids += [m.id for _, m in have]
            if len(labels) > 1:
                c = by_id.get(f"metric:{change}:{labels[-2]}->{labels[-1]}")
                if c is not None and c.status == MetricStatus.AVAILABLE and c.value is not None:
                    text += f" Change in {labels[-1]}: {fmt_metric(c, signed=True)}."
                    ids.append(c.id)
            missing = [p for p, m in zip(labels, ms) if (p, m) not in have]
            if missing:
                text += f" Not available for {_join(missing)}."
            observations.append(text)
        names = {n for n, _ in specs}
        related = [f.id for f in flags if _base_metric(f) in names]
        out.append(s.InterpretationArea(
            key=key, title=title, available=bool(ids), observations=observations, metric_ids=ids, measures=measures,
            related_change_ids=related,
            note=None if ids else "The filings did not provide the figures needed for this area."))
    return out


def _limitations(markdown: str) -> list[str]:
    m = re.search(r"^## Risks, uncertainties and missing information\n(.*?)(?=^## )", markdown, re.S | re.M)
    if not m:
        return []
    items = [re.sub(r"\s*\[(?:M|P)\d+\]", "", line[2:]).strip() for line in m.group(1).splitlines()
             if line.startswith("- ")]
    return [i.replace("**", "").replace("`", "") for i in items if i]


SOURCE_LABEL = {"company_tickers": "SEC company ticker list", "submissions": "SEC filing history",
                "company_facts": "SEC XBRL company facts", "filing_document": "10-K filing document"}


def _public_url(url: str | None) -> str | None:
    return url if url and url.startswith("https://") else None


def ensure_outputs(settings: Settings, run: WorkflowRun, results: dict) -> Path:
    """Write the run's export files once per run state; later calls reuse them."""
    out = settings.output_dir / run.id
    marker = out / ".written"
    research = results.get("research")
    stamp = f"{run.state.value}|{run.updated_at.isoformat()}|{research.created_at.isoformat() if research else ''}"
    with _outputs_lock:
        if not marker.exists() or marker.read_text() != stamp:
            write_outputs(out, run, results)
            marker.write_text(stamp)
    return out


def list_downloads(out_dir: Path) -> list[s.DownloadInfo]:
    items = []
    for name, label, desc in DOWNLOADS:
        p = out_dir / name
        if p.is_file():
            items.append(s.DownloadInfo(name=name, label=label, format=p.suffix[1:], description=desc,
                                        size_bytes=p.stat().st_size))
    charts = out_dir / "charts"
    if charts.is_dir():
        titles = {cid: title for cid, title, *_ in CHARTS}
        for p in sorted(charts.iterdir()):
            if p.suffix in (".png", ".csv"):
                title = titles.get(p.stem, p.stem)
                kind = "Chart image" if p.suffix == ".png" else "Chart data"
                items.append(s.DownloadInfo(name=f"charts/{p.name}", label=f"{kind}: {title}", format=p.suffix[1:],
                                            description="Chart as PNG" if p.suffix == ".png" else
                                            "Exact values plotted, with metric IDs", size_bytes=p.stat().st_size))
    return items


def build_result(run: WorkflowRun, store: Store, settings: Settings, active: bool) -> s.RunResult | None:
    results = load_results(store, run.id)
    plan_cp, val_cp, analysis = results["plan"], results["validation"], results["analysis"]
    if plan_cp is None or val_cp is None or analysis is None or run.state not in RESULT_STATES:
        return None
    inv, draft, reviews, text = results["investigation"], results["draft"], results["review"], results["filing_text"]
    plan, report = plan_cp.plan, val_cp.report
    labels = [p.label for p in report.fiscal_periods]
    by_id = {m.id: m for m in analysis.metrics}
    synthetic = run.request.offline
    status = build_status(run, results, active, settings)

    urls = {d.accession: d.url for d in plan.documents}
    periods = [s.PeriodInfo(label=p.label, start_date=p.start_date, end_date=p.end_date,
                            accession=p.defining_accession, filed=p.defining_filing_date,
                            filing_url=None if synthetic else _public_url(urls.get(p.defining_accession)))
               for p in report.fiscal_periods]
    headline = [c.text for c in (draft.claims if draft else []) if c.section == "summary"]
    c = plan.company
    fye = f"{c.fiscal_year_end[:2]}-{c.fiscal_year_end[2:]}" if c.fiscal_year_end and len(c.fiscal_year_end) == 4 \
        else None
    overview = s.Overview(
        company=company_ref(plan_cp), industry=c.sic_description, sic=c.sic, fiscal_year_end=fye,
        currency=plan.currency, cutoff_date=plan.cutoff_date, periods=periods,
        analysis_completed_at=status.finished_at, data_origin="synthetic_fixture" if synthetic else "sec_edgar",
        data_origin_label=("Synthetic fixture: made-up company, figures and filing text" if synthetic else
                           "SEC EDGAR: XBRL company facts and 10-K filings"),
        question=run.request.question, headline=[h.replace("->", "→") for h in headline])

    fin, chg = presentation_tables(analysis.metrics, labels)
    financials = _table("USD millions unless stated. Rounded for display; full precision is in the downloads.",
                        fin, FINANCIAL_ROWS, by_id)
    yoy = _table("Year-over-year changes. Money in USD millions; margins in percentage points.", chg, CHANGE_ROWS,
                 by_id)

    kinds = {f"chart:{cid}": kind for cid, *_rest, kind in CHARTS}
    specs = draft.charts if draft else build_chart_specs(analysis.metrics, labels)
    charts = [s.Chart(id=sp.id, kind=kinds.get(sp.id, "bar"), title=sp.title, unit=sp.unit, caption=sp.caption,
                      periods=sp.periods,
                      series=[s.ChartSeries(name=n, values=v, metric_ids=sp.metric_ids.get(n, []))
                              for n, v in sp.series.items()]) for sp in specs]

    findings = {f.change_flag_id: f for f in (inv.findings if inv else [])}
    changes, unresolved = [], []
    for rank, flag in enumerate(analysis.changes, 1):
        f = findings.get(flag.id)
        evidence = f.evidence_status.value if f else "not_investigated"
        desc = flag.description.replace("->", "→")
        finding = None
        if f:
            finding = s.Finding(
                id=f.id, calculated_observation=f.calculated_observation.replace("->", "→"),
                management_explanation=f.management_explanation, analyst_interpretation=f.analyst_interpretation,
                unresolved_questions=f.unresolved_questions, assessment=f.assessment, status_reason=f.status_reason,
                quotes=[s.Quote(passage_id=q.passage_id, text=q.quote, verified=q.verified,
                                note=q.verification_note) for q in f.quotes],
                metric_ids=f.metric_ids, passage_ids=f.passage_ids, produced_by=f.produced_by,
                steps_used=f.steps_used, budget_exhausted=f.budget_exhausted)
            unresolved += [f"{desc} {q}" for q in f.unresolved_questions]
        changes.append(s.Change(
            rank=rank, id=flag.id, rule=flag.rule, metric_label=label_for(_base_metric(flag)).replace(" %", ""),
            comparison=flag.comparison.replace("->", " → "), direction=flag.direction, description=desc,
            magnitude=_magnitude(flag), threshold=_threshold(flag), evidence=evidence,
            evidence_label=EVIDENCE_LABEL[evidence], metric_ids=flag.metric_ids, finding=finding))

    passages = [s.Passage(id=p.id, number=n, accession=p.accession, form=p.form, filing_date=p.filing_date,
                          fiscal_period=p.fiscal_period, section=p.section_title, text=p.text,
                          char_start=p.char_start, char_end=p.char_end,
                          url=None if p.is_fixture else _public_url(p.url), is_fixture=p.is_fixture)
                for n, p in enumerate(draft.passages if draft else [], 1)]

    docs, seen = [], set()
    collection = results.get("collection")
    for d in list(plan_cp.documents) + ([collection.company_facts_document] if collection else []) + (
            list(text.documents) if text else []):
        if d.id in seen:
            continue
        seen.add(d.id)
        docs.append(s.SourceDoc(kind=d.kind.value, label=SOURCE_LABEL.get(d.kind.value, d.kind.value),
                                url=_public_url(d.url), retrieved_at=d.retrieved_at, sha256=d.sha256,
                                from_cache=d.from_cache, is_fixture=d.is_fixture, accession=d.accession, form=d.form,
                                filing_date=d.filing_date))

    calculations = [s.Calculation(
        metric_id=m.id, name=m.name, label=label_for(m.name), period=m.fiscal_period.replace("->", " → "),
        display=fmt_metric(m), unit=m.unit, status=m.status.value, formula=m.formula,
        inputs=m.input_fact_ids or m.input_metric_ids, note=m.note or None, is_calculated=m.is_calculated_measure)
        for m in analysis.metrics]

    shown = {n for n, _ in FINANCIAL_ROWS} | {n for n, _ in CHANGE_ROWS}
    missing = [s.MissingFigure(label=label_for(m.name), period=m.fiscal_period.replace("->", " → "),
                               status=m.status.value, reason=m.note or "no detail recorded")
               for m in analysis.metrics if m.name in shown and m.status != MetricStatus.AVAILABLE]
    order = {Severity.BLOCKING: 0, Severity.RECOVERABLE: 1, Severity.WARNING: 2, Severity.INFO: 3}
    issues = [s.Issue(severity=i.severity.value, code=i.code, message=i.message, field=i.field,
                      period=i.fiscal_period) for i in sorted(report.issues, key=lambda i: order[i.severity])]

    latest = reviews.latest if reviews else None
    review = s.ReviewDetail(
        passed=latest.passed if latest else None, revision_cycles=latest.revision_cycle if latest else 0,
        checks_run=latest.checks_run if latest else [],
        defects=[f"{d.code}: {d.message}" for d in latest.defects] if latest else [],
        claims=(latest.stats or {}).get("claims") if latest else None,
        excluded_claims=(latest.stats or {}).get("excluded_claims") if latest else None)

    limitations = _limitations(draft.markdown) if draft else [
        f"Data validation ({i.severity.value}): {i.message}" for i in report.issues
        if i.severity in (Severity.WARNING, Severity.BLOCKING)]
    out_dir = ensure_outputs(settings, run, results)
    return s.RunResult(
        status=status, overview=overview, key_metrics=_key_metrics(by_id, labels), financials=financials,
        year_over_year=yoy, charts=charts, changes=changes,
        interpretation=_interpretation(by_id, labels, analysis.changes), passages=passages, sources=docs,
        calculations=calculations, missing=missing, issues=issues, limitations=limitations,
        unresolved_questions=unresolved, review=review, downloads=list_downloads(out_dir))


def build_diagnostics(run: WorkflowRun, store: Store) -> s.Diagnostics:
    results = load_results(store, run.id)
    inv, reviews, text, plan_cp = (results["investigation"], results["review"], results["filing_text"],
                                   results["plan"])
    traces = [{"finding": f.id, "produced_by": f.produced_by, "steps_used": f.steps_used,
               "budget_exhausted": f.budget_exhausted, "status": f.evidence_status.value,
               "status_reason": f.status_reason,
               "tool_calls": [{"step": t.step, "tool": t.tool, "ok": t.ok, "summary": t.summary}
                              for t in f.tool_calls]}
              for f in (inv.findings if inv else [])]
    docs = list(plan_cp.documents) if plan_cp else []
    if results.get("collection"):
        docs.append(results["collection"].company_facts_document)
    docs += list(text.documents) if text else []
    return s.Diagnostics(
        run_id=run.id, request=run.request.model_dump(mode="json"), stats=run.stats,
        transitions=[t.model_dump(mode="json") for t in run.history], checkpoints=store.checkpoint_stages(run.id),
        tool_traces=traces, review_cycles=[r.model_dump(mode="json") for r in (reviews.reviews if reviews else [])],
        sources=[{"id": d.id, "kind": d.kind.value, "url": d.url, "sha256": d.sha256,
                  "retrieved_at": d.retrieved_at.isoformat(), "from_cache": d.from_cache,
                  "is_fixture": d.is_fixture} for d in docs])


__all__ = ["build_status", "build_summary", "build_result", "build_diagnostics", "ensure_outputs",
           "list_downloads", "EvidenceStatus"]
