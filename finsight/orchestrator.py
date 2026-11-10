"""Central orchestrator for the Stage 1 workflow.

The orchestrator owns the run state. Each stage is a plain function; the
orchestrator decides what runs next, saves a checkpoint after each stage, and
routes failures:

* ambiguous company -> NEEDS_INPUT (resume with a chosen CIK)
* unsupported company / no filings -> FAILED with an explanation
* transient retrieval failure after bounded retries -> FAILED, resumable at COLLECTING
* stale company facts -> bounded VALIDATING -> COLLECTING recovery, then disclosed
* blocking validation issues -> PARTIAL (results kept, affected metrics unavailable)
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Callable

from pydantic import BaseModel, Field

from finsight.changes import detect_changes
from finsight.collector import CollectionResult, collect
from finsight.config import Settings, load_mappings
from finsight.contracts import (
    AnalysisPlan,
    AnalysisRequest,
    AnalysisResult,
    RunState as S,
    Severity,
    SourceDocument,
    ValidationReport,
    WorkflowRun,
)
from finsight.errors import (
    AccessDeniedError,
    AmbiguousCompanyError,
    CompanyNotFoundError,
    ConfigurationError,
    SourceNotFoundError,
    TransientSourceError,
    UnsupportedCompanyError,
)
from finsight.metrics import compute_metrics
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


SourceFactory = Callable[[AnalysisRequest], DataSource]


def new_run_id() -> str:
    return f"run-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"


class Orchestrator:
    def __init__(self, settings: Settings, store: Store, source_factory: SourceFactory):
        self.settings = settings
        self.store = store
        self.source_factory = source_factory
        self._source: DataSource | None = None

    # ---- public API ---------------------------------------------------------------------
    def start(self, request: AnalysisRequest) -> WorkflowRun:
        run = WorkflowRun(id=new_run_id(), request=request)
        self.store.save_run(run)
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

    def approve(self, run_id: str, note: str = "") -> WorkflowRun:
        run = self.store.load_run(run_id)
        if run is None:
            raise KeyError(f"No run {run_id}")
        transition(run, S.APPROVED, f"approved by analyst{': ' + note if note else ''}")
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
        started = time.monotonic()
        steps = {S.CREATED: self._created, S.PLANNING: self._plan, S.COLLECTING: self._collect,
                 S.VALIDATING: self._validate, S.ANALYZING: self._analyze}
        guard = 0
        while run.state not in TERMINAL:
            guard += 1
            if guard > 20:  # hard stop: no loop in this workflow should get near this
                self._fail(run, run.state, "workflow exceeded its step limit")
                break
            step = steps.get(run.state)
            if step is None:
                self._fail(run, run.state, f"state {run.state.value} is not implemented in Stage 1")
                break
            step(run)
            self.store.save_run(run, cik=self._cik(run))
        stats = run.stats
        stats["last_drive_seconds"] = round(time.monotonic() - started, 3)
        for k, v in getattr(self._source, "stats", {}).items():
            stats[f"source_{k}"] = stats.get(f"source_{k}", 0) + v
        self.store.save_run(run, cik=self._cik(run))
        return run

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
        if blocking:
            transition(run, S.PARTIAL, f"{len(blocking)} blocking issue(s); affected metrics unavailable")
        else:
            transition(run, S.READY_FOR_REVIEW,
                       "data pipeline complete; investigation and report drafting arrive in Stage 2")


def load_results(store: Store, run_id: str) -> dict:
    return {
        "plan": store.load_checkpoint(run_id, "plan", PlanCheckpoint),
        "collection": store.load_checkpoint(run_id, "collection", CollectionResult),
        "validation": store.load_checkpoint(run_id, "validation", ValidationCheckpoint),
        "analysis": store.load_checkpoint(run_id, "analysis", AnalysisResult),
    }
