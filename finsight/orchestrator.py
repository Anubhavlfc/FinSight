"""Central orchestrator for the workflow.

The orchestrator owns the run state. Each stage is a plain function; the
orchestrator decides what runs next, saves a checkpoint after each stage, and
routes failures:

* ambiguous company -> NEEDS_INPUT (resume with a chosen CIK)
* unsupported company / no filings -> FAILED with an explanation
* transient retrieval failure after bounded retries -> FAILED, resumable at COLLECTING
* stale company facts -> bounded VALIDATING -> COLLECTING recovery, then disclosed
* blocking validation issues -> PARTIAL (results kept, affected metrics unavailable)
* LLM transient failure after SDK retries -> FAILED, resumable at INVESTIGATING (findings kept)
* LLM budget used up -> investigation stops, remaining changes disclosed, report still drafted
* reviewer defects -> back to INVESTIGATING (once, for the affected changes) or DRAFTING
  (claims removed or downgraded), at most ``max_revision_cycles`` times; after that the
  remaining defects are kept and the run is flagged for analyst attention
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import BaseModel, Field

from finsight.changes import detect_changes
from finsight.collector import CollectionResult, collect
from finsight.config import Settings, load_mappings
from finsight.contracts import (
    AnalysisPlan,
    EvidenceStatus,
    InvestigationResult,
    ReportDraft,
    ReviewResult,
    AnalysisRequest,
    AnalysisResult,
    RunState as S,
    Severity,
    SourceDocument,
    ValidationReport,
    WorkflowRun,
    utcnow,
)
from finsight.errors import (
    InvalidTransitionError,
    AccessDeniedError,
    AmbiguousCompanyError,
    CompanyNotFoundError,
    ConfigurationError,
    SourceNotFoundError,
    TransientSourceError,
    UnsupportedCompanyError,
)
from finsight.investigator.stage import FilingTextCheckpoint, run_investigation
from finsight.llm.factory import make_provider
from finsight.llm.provider import LLMConfigurationError, LLMProvider, LLMTransientError
from finsight.metrics import compute_metrics
from finsight.review.human import DECISIONS, ReviewDecisionError, check_decision, normalize_checks
from finsight.report.writer import ReportInputs, write_report
from finsight.review.reviewer import review
from finsight.planner import PlanningError, build_plan
from finsight.resolver import resolve_company
from finsight.sec.sources import DataSource
from finsight.storage import Store
from finsight.validator import validate
from finsight.workflow import TERMINAL, transition

log = logging.getLogger(__name__)


class PlanCheckpoint(BaseModel):
    plan: AnalysisPlan
    documents: list[SourceDocument] = Field(default_factory=list)


class ValidationCheckpoint(BaseModel):
    report: ValidationReport
    recovery_attempts: int = 0


class ReviewHistory(BaseModel):
    reviews: list[ReviewResult] = Field(default_factory=list)

    @property
    def latest(self) -> ReviewResult | None:
        return self.reviews[-1] if self.reviews else None


SourceFactory = Callable[[AnalysisRequest], DataSource]
ProviderFactory = Callable[[AnalysisRequest], "tuple[LLMProvider, str]"]


def new_run_id() -> str:
    return f"run-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"


class Orchestrator:
    def __init__(self, settings: Settings, store: Store, source_factory: SourceFactory,
                 provider_factory: ProviderFactory | None = None, budget_seconds: float | None = None):
        # ``budget_seconds`` is a wall-clock budget for one drive of a run. It is checked between
        # stages, so a stage that is already running finishes (stages have their own timeouts and
        # LLM budgets); the run then stops as FAILED and can be resumed.
        self.budget_seconds = budget_seconds
        self.settings = settings
        self.store = store
        self.source_factory = source_factory
        self.provider_factory = provider_factory or (lambda req: make_provider(settings, req))
        self._source: DataSource | None = None
        self._provider: LLMProvider | None = None

    # ---- public API ---------------------------------------------------------------------
    def create(self, request: AnalysisRequest) -> WorkflowRun:
        """Store a new run without executing it, so a caller can hand its ID out before the work starts."""
        run = WorkflowRun(id=new_run_id(), request=request)
        self.store.save_run(run)
        return run

    def start(self, request: AnalysisRequest) -> WorkflowRun:
        return self._drive(self.create(request))

    def drive(self, run_id: str) -> WorkflowRun:
        """Run a stored run until it reaches a terminal state (used by background workers)."""
        run = self.store.load_run(run_id)
        if run is None:
            raise KeyError(f"No run {run_id}")
        return self._drive(run)

    def resume(self, run_id: str, identifier: str | None = None) -> WorkflowRun:
        run = self.store.load_run(run_id)
        if run is None:
            raise KeyError(f"No run {run_id}")
        if run.state == S.NEEDS_INPUT:
            if not identifier:
                return run
            run.request = run.request.model_copy(update={"identifier": identifier})
            run.needs_input = None
            transition(run, S.PLANNING, f"user clarified company: {identifier}")
        elif run.state == S.FAILED:
            target = run.failed_stage or S.PLANNING
            run.error = None
            transition(run, target, f"resume after failure at {target.value}")
        self.store.save_run(run)
        return self._drive(run)

    def record_review(self, run_id: str, decision: str, reviewer: str, note: str = "",
                      checks: dict[str, Any] | None = None) -> WorkflowRun:
        """Record a person's review decision on a finished run.

        The rules in ``finsight/review/human.py`` apply to every front end: approval needs every
        investigated change confirmed and both checks ticked; a change request needs a note or a
        disputed change. ``approved`` moves the run to APPROVED. ``changes_requested``
        leaves the state alone and keeps the reviewer's objections on the run, so the report stays
        unapproved and the record of why is not lost. Every decision is appended to
        ``run.stats["human_reviews"]``; nothing is overwritten.
        """
        if decision not in DECISIONS:
            raise ReviewDecisionError(f"Unknown review decision {decision!r}")
        reviewer = " ".join((reviewer or "").split())
        if not reviewer:
            raise ValueError("A review decision needs the reviewer's name")
        run = self.store.load_run(run_id)
        if run is None:
            raise KeyError(f"No run {run_id}")
        if run.state not in (S.READY_FOR_REVIEW, S.PARTIAL):
            raise InvalidTransitionError(
                f"Only a finished, unapproved analysis can be reviewed (this one is {run.state.value})")
        note = " ".join((note or "").split())
        inv = self.store.load_checkpoint(run_id, "investigation", InvestigationResult)
        finding_ids = [f.change_flag_id for f in inv.findings] if inv else []
        checks = normalize_checks(checks, finding_ids)
        check_decision(decision, note, checks)
        entry = {"decision": decision, "reviewer": reviewer, "note": note, "at": utcnow().isoformat(),
                 "checks": checks}
        run.stats.setdefault("human_reviews", []).append(entry)
        if decision == "approved":
            transition(run, S.APPROVED, f"approved by analyst {reviewer}{': ' + note if note else ''}")
        else:
            run.updated_at = utcnow()
        self.store.save_run(run)
        return run

    # ---- driver -------------------------------------------------------------------------
    @property
    def source(self) -> DataSource:
        return self._source  # set in _drive

    def _drive(self, run: WorkflowRun) -> WorkflowRun:
        try:
            self._source = self.source_factory(run.request)
        except ConfigurationError as exc:
            if run.state not in TERMINAL:
                self._fail(run, run.state, str(exc))
                self.store.save_run(run)
            return run
        self._provider = None
        started = time.monotonic()
        steps = {S.CREATED: self._created, S.PLANNING: self._plan, S.COLLECTING: self._collect,
                 S.VALIDATING: self._validate, S.ANALYZING: self._analyze, S.INVESTIGATING: self._investigate,
                 S.DRAFTING: self._draft, S.REVIEWING: self._review}
        guard = 0
        while run.state not in TERMINAL:
            guard += 1
            if guard > 40:  # hard stop: no loop in this workflow should get near this
                self._fail(run, run.state, "workflow exceeded its step limit")
                break
            if self.budget_seconds is not None and time.monotonic() - started > self.budget_seconds:
                self._fail(run, run.state, f"execution budget of {self.budget_seconds:.0f} seconds used up at "
                                           f"{run.state.value} (resume to continue from this stage)")
                break
            step = steps.get(run.state)
            if step is None:
                self._fail(run, run.state, f"state {run.state.value} has no handler")
                break
            step(run)
            self.store.save_run(run, cik=self._cik(run))
        stats = run.stats
        stats["last_drive_seconds"] = round(time.monotonic() - started, 3)
        for k, v in getattr(self._source, "stats", {}).items():
            stats[f"source_{k}"] = stats.get(f"source_{k}", 0) + v
        self.store.save_run(run, cik=self._cik(run))
        return run

    def provider(self, run: WorkflowRun) -> LLMProvider:
        if self._provider is None:
            self._provider, note = self.provider_factory(run.request)
            run.stats["llm_provider"] = self._provider.name
            run.stats["llm_provider_note"] = note
        return self._provider

    def _cik(self, run: WorkflowRun) -> str | None:
        cp = self.store.load_checkpoint(run.id, "plan", PlanCheckpoint)
        return cp.plan.company.cik if cp else None

    def _fail(self, run: WorkflowRun, stage: S, message: str) -> None:
        run.failed_stage = stage if stage not in (S.CREATED, S.FAILED) else S.PLANNING
        run.error = message
        transition(run, S.FAILED, message)

    # ---- stages -------------------------------------------------------------------------
    def _created(self, run: WorkflowRun) -> None:
        transition(run, S.PLANNING, "run started")

    def _plan(self, run: WorkflowRun) -> None:
        if self.store.load_checkpoint(run.id, "plan", PlanCheckpoint):
            transition(run, S.COLLECTING, "plan restored from checkpoint")
            return
        try:
            resolution = resolve_company(run.request.identifier, self.source, self.settings)
            mappings = load_mappings(self.settings.config_dir, resolution.company.cik)
            plan = build_plan(run.request, resolution.company, resolution.submissions, self.source,
                              self.settings, mappings)
        except AmbiguousCompanyError as exc:
            run.needs_input = {"question": f"Which company did you mean by '{exc.query}'?",
                               "candidates": exc.candidates}
            transition(run, S.NEEDS_INPUT, str(exc))
            return
        except (UnsupportedCompanyError, CompanyNotFoundError, PlanningError, ConfigurationError,
                AccessDeniedError, SourceNotFoundError) as exc:
            self._fail(run, S.PLANNING, str(exc))
            return
        except TransientSourceError as exc:
            self._fail(run, S.PLANNING, f"{exc} (resume to retry)")
            return
        self.store.save_checkpoint(run.id, "plan", PlanCheckpoint(plan=plan, documents=resolution.documents))
        self.store.save_sources(run.id, resolution.documents)
        transition(run, S.COLLECTING,
                   f"planned {plan.company.name} ({plan.company.cik}): "
                   + ", ".join(f"{p.label} ending {p.end_date}" for p in plan.fiscal_periods))

    def _collect(self, run: WorkflowRun) -> None:
        if self.store.load_checkpoint(run.id, "collection", CollectionResult):
            transition(run, S.VALIDATING, "collection restored from checkpoint")
            return
        plan = self.store.load_checkpoint(run.id, "plan", PlanCheckpoint).plan
        attempts = run.stats.get("recovery_attempts", 0)
        try:
            result = collect(plan, self.source, load_mappings(self.settings.config_dir, plan.company.cik),
                             force_refresh=attempts > 0, attempt=attempts + 1)
        except (TransientSourceError, AccessDeniedError, SourceNotFoundError, ConfigurationError) as exc:
            self._fail(run, S.COLLECTING, f"{exc} (resume to retry)")
            return
        self.store.save_checkpoint(run.id, "collection", result)
        self.store.save_sources(run.id, [result.company_facts_document])
        transition(run, S.VALIDATING, f"collected {len(result.rows)} candidate XBRL values")

    def _validate(self, run: WorkflowRun) -> None:
        cp = self.store.load_checkpoint(run.id, "validation", ValidationCheckpoint)
        if cp is None:
            plan = self.store.load_checkpoint(run.id, "plan", PlanCheckpoint).plan
            collection = self.store.load_checkpoint(run.id, "collection", CollectionResult)
            attempts = run.stats.get("recovery_attempts", 0)
            outcome = validate(plan, collection, load_mappings(self.settings.config_dir, plan.company.cik),
                               self.settings, recovery_attempts=attempts)
            if outcome.needs_refetch:
                run.stats["recovery_attempts"] = attempts + 1
                self.store.delete_checkpoint(run.id, "collection")
                transition(run, S.COLLECTING, "recoverable: company facts predate the latest 10-K; refetching")
                return
            cp = ValidationCheckpoint(report=outcome.report, recovery_attempts=attempts)
            self.store.save_checkpoint(run.id, "validation", cp)
            self.store.save_validation(run.id, cp.report)
        counts: dict[str, int] = {}
        for i in cp.report.issues:
            counts[i.severity.value] = counts.get(i.severity.value, 0) + 1
        run.stats["issues"] = counts
        cov = cp.report.field_coverage
        cells = [v for per in cov.values() for v in per.values()]
        run.stats["field_coverage_pct"] = round(100 * sum(cells) / len(cells), 1) if cells else 0.0
        transition(run, S.ANALYZING, f"validated: {len(cp.report.accepted_facts)} accepted facts, "
                                     f"{len(cp.report.blocking_issues)} blocking issue(s)")

    def _analyze(self, run: WorkflowRun) -> None:
        report = self.store.load_checkpoint(run.id, "validation", ValidationCheckpoint).report
        metrics = compute_metrics(report)
        labels = [p.label for p in report.fiscal_periods]
        result = AnalysisResult(metrics=metrics, changes=detect_changes(metrics, labels, self.settings.analysis))
        self.store.save_checkpoint(run.id, "analysis", result)
        self.store.save_analysis(run.id, result)
        blocking = [i for i in report.issues if i.severity == Severity.BLOCKING and not i.resolved]
        run.stats["blocking_issues"] = len(blocking)
        run.stats["change_flags"] = len(result.changes)
        if run.request.investigate and self.settings.investigation.enabled:
            transition(run, S.INVESTIGATING, f"{len(result.changes)} change(s) flagged for investigation")
        elif blocking:
            transition(run, S.PARTIAL, f"{len(blocking)} blocking issue(s); affected metrics unavailable")
        else:
            transition(run, S.READY_FOR_REVIEW, "data pipeline complete; investigation switched off")

    # ---- Stage 2 ------------------------------------------------------------------------
    def _inputs(self, run: WorkflowRun):
        plan = self.store.load_checkpoint(run.id, "plan", PlanCheckpoint)
        val = self.store.load_checkpoint(run.id, "validation", ValidationCheckpoint).report
        analysis = self.store.load_checkpoint(run.id, "analysis", AnalysisResult)
        return plan, val, analysis

    def _investigate(self, run: WorkflowRun) -> None:
        plan_cp, _val, analysis = self._inputs(run)
        pending = run.stats.get("reinvestigate")
        prior = self.store.load_checkpoint(run.id, "investigation", InvestigationResult)
        if prior is not None and not pending and run.stats.get("investigation_complete"):
            transition(run, S.DRAFTING, "investigation restored from checkpoint")
            return
        text = self.store.load_checkpoint(run.id, "filing_text", FilingTextCheckpoint) or FilingTextCheckpoint()
        try:
            provider = self.provider(run)
        except Exception as exc:  # e.g. the anthropic package or credentials are missing
            self._fail(run, S.INVESTIGATING, f"could not start the LLM provider: {exc}")
            return
        if prior is not None and prior.llm_usage and provider.usage.calls == 0:
            # Fresh provider after a restart: carry the run's usage forward so budgets stay per run.
            saved = run.stats.get("llm_usage") or prior.llm_usage
            provider.usage.restore(saved if saved.get("model_calls", 0) >= prior.llm_usage.get("model_calls", 0)
                                   else prior.llm_usage)

        def save(res: InvestigationResult, txt: FilingTextCheckpoint) -> None:
            self.store.save_checkpoint(run.id, "filing_text", txt)
            self.store.save_checkpoint(run.id, "investigation", res)

        try:
            result, text = run_investigation(
                plan_cp.plan, analysis.metrics, analysis.changes, self.source, provider, self.settings.investigation,
                text, prior=prior, only_flags=pending, revision=run.stats.get("revision_cycle", 0), on_progress=save)
        except LLMTransientError as exc:
            self._fail(run, S.INVESTIGATING, f"{exc} (resume to continue; findings so far are kept)")
            return
        except LLMConfigurationError as exc:
            self._fail(run, S.INVESTIGATING, f"{exc}. Fix the LLM configuration, or rerun with --llm stub.")
            return
        save(result, text)
        self.store.save_sources(run.id, text.documents)
        run.stats["reinvestigate"] = None
        run.stats["investigation_complete"] = True
        run.stats["llm_usage"] = result.llm_usage
        run.stats["findings"] = {s.value: sum(f.evidence_status == s for f in result.findings) for s in EvidenceStatus}
        transition(run, S.DRAFTING, f"investigated {len(result.findings)} change(s)"
                   + (f" ({len(pending)} re-investigated after review)" if pending else ""))

    def _report_inputs(self, run: WorkflowRun) -> ReportInputs:
        plan_cp, val, analysis = self._inputs(run)
        inv = self.store.load_checkpoint(run.id, "investigation", InvestigationResult)
        text = self.store.load_checkpoint(run.id, "filing_text", FilingTextCheckpoint) or FilingTextCheckpoint()
        collection = self.store.load_checkpoint(run.id, "collection", CollectionResult)
        data_sources = list(plan_cp.documents) + ([collection.company_facts_document] if collection else [])
        is_fixture = run.request.offline or any(d.is_fixture for d in data_sources)
        provider_name = run.stats.get("llm_provider", "")
        return ReportInputs(
            plan=plan_cp.plan, validation=val, analysis=analysis, investigation=inv, passages=text.passages,
            data_sources=data_sources, is_fixture=is_fixture, llm_is_stub="stub" in provider_name,
            llm_name=provider_name or "no LLM", exclusions=dict(run.stats.get("exclusions") or {}),
            revision=run.stats.get("revision_cycle", 0),
            extra_limitations=list(plan_cp.plan.notes) + list(text.notes))

    def _draft(self, run: WorkflowRun) -> None:
        inputs = self._report_inputs(run)
        draft = write_report(inputs)
        self.store.save_checkpoint(run.id, "draft", draft)
        transition(run, S.REVIEWING, f"report revision {draft.revision} drafted with {len(draft.claims)} claims")

    def _review(self, run: WorkflowRun) -> None:
        inputs = self._report_inputs(run)
        draft = self.store.load_checkpoint(run.id, "draft", ReportDraft)
        cycle = run.stats.get("revision_cycle", 0)
        try:
            provider = self.provider(run)
        except Exception:
            provider = None
        result = review(draft, inputs.analysis.metrics, inputs.investigation, provider, inputs.is_fixture,
                        inputs.llm_is_stub, cycle, self.settings.investigation.min_quote_chars)
        history = self.store.load_checkpoint(run.id, "review", ReviewHistory) or ReviewHistory()
        history.reviews.append(result)
        self.store.save_checkpoint(run.id, "review", history)
        if provider is not None:
            run.stats["llm_usage"] = provider.usage.summary(provider.name, provider.is_stub)
        actionable = [d for d in result.defects if d.route_to != "none" and d.severity == "error"]
        if result.passed or not actionable or cycle >= self.settings.investigation.max_revision_cycles:
            self._finalize(run, result, history)
            return
        run.stats["revision_cycle"] = cycle + 1
        to_investigate: dict[str, list[str]] = {}
        for d in actionable:
            if d.route_to == "investigation" and d.change_flag_id:
                to_investigate.setdefault(d.change_flag_id, []).append(d.message)
        if to_investigate and not run.stats.get("reinvestigated"):
            run.stats["reinvestigated"] = True
            run.stats["reinvestigate"] = to_investigate
            transition(run, S.INVESTIGATING, f"review cycle {cycle + 1}: re-investigating "
                                             f"{len(to_investigate)} change(s)")
            return
        # Repair in drafting: downgrade findings the LLM reviewer did not accept; drop unverifiable claims.
        inv = inputs.investigation
        exclusions = dict(run.stats.get("exclusions") or {})
        for d in actionable:
            if d.source == "llm" and d.suggested_status and inv is not None:
                for f in inv.findings:
                    if f.id == d.finding_id:
                        f.evidence_status = d.suggested_status
                        f.status_reason += f"; lowered by review: {d.message}"
                        f.unresolved_questions.append(f"Reviewer: {d.message}")
            elif d.claim_id:
                exclusions[d.claim_id] = f"{d.code}: {d.message}"
        if inv is not None:
            self.store.save_checkpoint(run.id, "investigation", inv)
        run.stats["exclusions"] = exclusions
        transition(run, S.DRAFTING, f"review cycle {cycle + 1}: {len(actionable)} defect(s) sent back to drafting")

    def _finalize(self, run: WorkflowRun, result: ReviewResult, history: ReviewHistory) -> None:
        remaining = [d for d in result.defects if d.severity == "error"]
        result.needs_analyst_attention = bool(remaining)
        history.reviews[-1] = result
        self.store.save_checkpoint(run.id, "review", history)
        run.stats["review"] = {"passed": result.passed, "revision_cycles": result.revision_cycle,
                               "unresolved_defects": len(remaining), "needs_analyst_attention": bool(remaining),
                               **result.stats}
        note = ("review passed" if result.passed else
                f"{len(remaining)} review defect(s) remain after {result.revision_cycle} revision cycle(s); "
                "analyst attention needed")
        if run.stats.get("blocking_issues"):
            transition(run, S.PARTIAL, f"{note}; {run.stats['blocking_issues']} blocking data issue(s)")
        else:
            transition(run, S.READY_FOR_REVIEW, note)


def load_results(store: Store, run_id: str) -> dict:
    return {
        "plan": store.load_checkpoint(run_id, "plan", PlanCheckpoint),
        "collection": store.load_checkpoint(run_id, "collection", CollectionResult),
        "validation": store.load_checkpoint(run_id, "validation", ValidationCheckpoint),
        "analysis": store.load_checkpoint(run_id, "analysis", AnalysisResult),
        "filing_text": store.load_checkpoint(run_id, "filing_text", FilingTextCheckpoint),
        "investigation": store.load_checkpoint(run_id, "investigation", InvestigationResult),
        "draft": store.load_checkpoint(run_id, "draft", ReportDraft),
        "review": store.load_checkpoint(run_id, "review", ReviewHistory),
    }
