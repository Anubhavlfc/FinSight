"""Request and response contracts for the HTTP API.

The TypeScript types in ``web/src/api/schema.d.ts`` are generated from these
models (``npm run gen:api``), so the frontend and backend cannot drift apart
silently. Money and ratios travel as display strings formatted by the engine
plus a plain number for plotting; full-precision values stay in the downloads.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

StageKey = Literal["planning", "collecting", "validating", "calculating", "investigating", "writing",
                   "reviewing", "ready"]
StageState = Literal["pending", "active", "done", "failed", "skipped"]
Phase = Literal["queued", "running", "ready_for_review", "partial", "approved", "failed", "needs_input",
                "interrupted"]
ReviewState = Literal["not_reviewed", "awaiting_review", "needs_attention", "approved"]
EvidenceLabel = Literal["supported", "partially_supported", "unsupported", "not_investigated"]


class _Out(BaseModel):
    # Responses always carry every field, so the generated TypeScript types mark them as present.
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)


# --------------------------------------------------------------------------- service


class Availability(_Out):
    available: bool
    reason: str | None = None


class InvestigatorInfo(_Out):
    claude_available: bool
    default: Literal["claude", "rule_based"]
    model: str | None = None
    note: str


class Limits(_Out):
    max_concurrent_runs: int
    runs_per_hour: int
    run_budget_seconds: int
    question_max_chars: int
    years: int


class Health(_Out):
    status: Literal["ok"] = "ok"
    version: str
    time: datetime
    live_data: Availability
    investigator: InvestigatorInfo
    approval: Literal["open", "token_required"]
    limits: Limits
    active_runs: int
    data_source: str
    supported_companies: str


# --------------------------------------------------------------------------- companies


class CompanyHit(_Out):
    cik: str
    name: str
    ticker: str | None
    tickers: list[str]
    exchange: str | None = None
    synthetic: bool = False


class SearchResponse(_Out):
    query: str
    results: list[CompanyHit]
    live_search: bool = Field(description="False when only the bundled synthetic companies were searched")
    note: str | None = None


class FiscalYearOption(_Out):
    fiscal_year: int = Field(description="Calendar year in which the fiscal year ends")
    label: str
    period_end: date
    filed: date
    accession: str
    window: list[str] = Field(description="Fiscal year labels an analysis ending here would cover")
    complete: bool = Field(description="True when the full three-year window is available")


class CompanyProfile(_Out):
    cik: str
    name: str
    tickers: list[str]
    exchange: str | None = None
    sic: str | None = None
    industry: str | None = None
    fiscal_year_end: str | None = Field(default=None, description="Month and day, e.g. '06-30'")
    synthetic: bool = False
    supported: bool
    unsupported_reason: str | None = None
    next_steps: list[str] = Field(default_factory=list)
    fiscal_years: list[FiscalYearOption] = Field(default_factory=list)
    default_fiscal_year: int | None = None
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- runs


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    identifier: str = Field(min_length=1, max_length=80, description="Ticker, CIK or company name")
    latest_fiscal_year: int | None = Field(default=None, ge=1994, le=2100)
    question: str | None = Field(default=None, max_length=400)
    investigator: Literal["auto", "rule_based"] = "auto"

    @field_validator("identifier")
    @classmethod
    def _identifier(cls, v: str) -> str:
        import re

        if not re.fullmatch(r"[A-Za-z0-9 .,&'()/\-]+", v):
            raise ValueError("use a ticker, a CIK number or a company name (letters, digits and basic punctuation)")
        return v

    @field_validator("question")
    @classmethod
    def _question(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = " ".join(v.split())
        return v or None


class RunCreated(_Out):
    run_id: str
    reused: bool = Field(description="True when an identical analysis was already running and was returned instead")


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    company: str | None = Field(default=None, pattern=r"^\d{1,10}$", description="CIK chosen for a needs-input run")


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    note: str = Field(default="", max_length=300)


class CompanyRef(_Out):
    cik: str
    name: str
    ticker: str | None
    tickers: list[str]


class StageStatus(_Out):
    key: StageKey
    label: str
    state: StageState
    started_at: datetime | None = None
    finished_at: datetime | None = None
    detail: str | None = Field(default=None, description="Outcome reported by the backend when the stage finished")


class ActivityItem(_Out):
    at: datetime
    stage: StageKey | None
    kind: Literal["info", "success", "warning", "error"]
    message: str


class RunError(_Out):
    message: str
    stage: StageKey | None
    retryable: bool
    next_steps: list[str] = Field(default_factory=list)


class Candidate(_Out):
    cik: str
    name: str
    tickers: list[str]


class NeedsInput(_Out):
    question: str
    candidates: list[Candidate]


class ReviewStatus(_Out):
    state: ReviewState
    label: str
    passed: bool | None = None
    revision_cycles: int = 0
    unresolved_defects: int = 0
    approved_at: datetime | None = None
    approval_note: str | None = None


class InvestigatorUsed(_Out):
    name: str
    is_rule_based: bool
    note: str


class InvestigationProgress(_Out):
    investigated: int
    flagged: int
    limit: int


class RunStatus(_Out):
    id: str
    state: str = Field(description="Raw workflow state, e.g. COLLECTING")
    phase: Phase
    is_active: bool = Field(description="True while a worker in this server process owns the run")
    identifier: str
    question: str | None
    company: CompanyRef | None
    periods: list[str]
    synthetic: bool
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    elapsed_seconds: float
    stages: list[StageStatus]
    current_activity: str
    activity: list[ActivityItem]
    warnings: list[str]
    error: RunError | None = None
    needs_input: NeedsInput | None = None
    review: ReviewStatus
    investigator: InvestigatorUsed | None = None
    investigation: InvestigationProgress | None = None
    has_results: bool
    has_report: bool


class DownloadInfo(_Out):
    name: str = Field(description="File name relative to the run's download folder")
    label: str
    format: Literal["csv", "json", "md", "html", "png"]
    description: str
    size_bytes: int


class RunSummary(_Out):
    id: str
    state: str
    phase: Phase
    is_active: bool
    identifier: str
    company: CompanyRef | None
    periods: list[str]
    synthetic: bool
    created_at: datetime
    updated_at: datetime
    review: ReviewStatus
    investigator: InvestigatorUsed | None
    has_results: bool
    has_report: bool
    formats: list[str] = Field(description="Download formats available once the run has results")


class RunList(_Out):
    runs: list[RunSummary]


# --------------------------------------------------------------------------- results


class PeriodInfo(_Out):
    label: str
    start_date: date | None
    end_date: date
    accession: str
    filed: date
    filing_url: str | None


class Overview(_Out):
    company: CompanyRef
    industry: str | None
    sic: str | None
    fiscal_year_end: str | None
    currency: str
    cutoff_date: date
    periods: list[PeriodInfo]
    analysis_completed_at: datetime | None
    data_origin: Literal["sec_edgar", "synthetic_fixture"]
    data_origin_label: str
    question: str | None
    headline: list[str] = Field(description="Calculated summary statements written by the report writer")


class Comparison(_Out):
    label: str = Field(description="e.g. 'vs FY2025'")
    display: str | None
    direction: Literal["up", "down", "flat", "none"]
    metric_id: str | None
    note: str | None = None


class SeriesPoint(_Out):
    period: str
    display: str
    value: float | None = Field(description="Value in the metric's own unit; null when not available")


class KeyMetric(_Out):
    name: str
    label: str
    period: str
    metric_id: str
    value_text: str
    unit_text: str
    display: str
    status: Literal["available", "unavailable", "not_applicable", "suppressed"]
    note: str | None
    is_calculated: bool
    formula: str
    comparison: Comparison | None
    series: list[SeriesPoint]


class TableCell(_Out):
    display: str
    status: Literal["available", "unavailable", "not_applicable", "suppressed", "missing"]
    metric_id: str | None


class TableRow(_Out):
    name: str
    label: str
    cells: list[TableCell]


class DataTable(_Out):
    caption: str
    columns: list[str]
    rows: list[TableRow]


class ChartSeries(_Out):
    name: str
    values: list[float | None]
    metric_ids: list[str | None]


class Chart(_Out):
    id: str
    kind: Literal["bar", "line"]
    title: str
    unit: str
    caption: str
    periods: list[str]
    series: list[ChartSeries]


class Quote(_Out):
    passage_id: str
    text: str
    verified: bool
    note: str


class Finding(_Out):
    id: str
    calculated_observation: str
    management_explanation: str | None
    analyst_interpretation: str | None
    unresolved_questions: list[str]
    assessment: Literal["favorable", "unfavorable", "ambiguous"]
    status_reason: str
    quotes: list[Quote]
    metric_ids: list[str]
    passage_ids: list[str]
    produced_by: str
    steps_used: int
    budget_exhausted: bool


class Change(_Out):
    rank: int
    id: str
    rule: str
    metric_label: str
    comparison: str
    direction: Literal["increase", "decrease", "transition"]
    description: str
    magnitude: str
    threshold: str
    evidence: EvidenceLabel
    evidence_label: str
    metric_ids: list[str]
    finding: Finding | None


class Measure(_Out):
    name: str
    label: str
    points: list[SeriesPoint]
    is_calculated: bool
    note: str | None = None


class InterpretationArea(_Out):
    key: Literal["profitability", "liquidity", "cash_generation", "leverage"]
    title: str
    available: bool
    observations: list[str] = Field(description="Calculated statements; no judgement beyond the numbers")
    metric_ids: list[str]
    measures: list[Measure]
    related_change_ids: list[str]
    note: str | None = None


class Passage(_Out):
    id: str
    number: int = Field(description="Citation number used in the report, e.g. 3 for [P3]")
    accession: str
    form: str
    filing_date: date
    fiscal_period: str | None
    section: str
    text: str
    char_start: int
    char_end: int
    url: str | None
    is_fixture: bool


class SourceDoc(_Out):
    kind: str
    label: str
    url: str | None
    retrieved_at: datetime
    sha256: str
    from_cache: bool
    is_fixture: bool
    accession: str | None
    form: str | None
    filing_date: date | None


class Calculation(_Out):
    metric_id: str
    name: str
    label: str
    period: str
    display: str
    unit: str
    status: str
    formula: str
    inputs: list[str]
    note: str | None
    is_calculated: bool


class MissingFigure(_Out):
    label: str
    period: str
    status: str
    reason: str


class Issue(_Out):
    severity: Literal["info", "warning", "recoverable", "blocking"]
    code: str
    message: str
    field: str | None
    period: str | None


class ReviewDetail(_Out):
    passed: bool | None
    revision_cycles: int
    checks_run: list[str]
    defects: list[str]
    claims: int | None
    excluded_claims: int | None


class RunResult(_Out):
    status: RunStatus
    overview: Overview
    key_metrics: list[KeyMetric]
    financials: DataTable
    year_over_year: DataTable
    charts: list[Chart]
    changes: list[Change]
    interpretation: list[InterpretationArea]
    passages: list[Passage]
    sources: list[SourceDoc]
    calculations: list[Calculation]
    missing: list[MissingFigure]
    issues: list[Issue]
    limitations: list[str]
    unresolved_questions: list[str]
    review: ReviewDetail
    downloads: list[DownloadInfo]


class Diagnostics(_Out):
    run_id: str
    request: dict[str, Any]
    stats: dict[str, Any]
    transitions: list[dict[str, Any]]
    checkpoints: list[str]
    tool_traces: list[dict[str, Any]]
    review_cycles: list[dict[str, Any]]
    sources: list[dict[str, Any]]


class ErrorBody(_Out):
    """Shape of every non-2xx JSON response."""

    code: str
    message: str
    next_steps: list[str] = Field(default_factory=list)
    retry_after_seconds: int | None = None
    fields: dict[str, str] = Field(default_factory=dict)
