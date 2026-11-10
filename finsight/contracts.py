"""Typed data contracts shared by every stage of the workflow.

Monetary values are ``Decimal`` so that calculations keep full precision;
rounding happens only when values are presented. Each object carries enough
provenance to trace a number back to the filing it came from.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- request / plan


class AnalysisRequest(_Model):
    identifier: str = Field(description="Ticker, CIK or company name")
    cutoff_date: date = Field(description="Only filings filed on or before this date are used")
    latest_fiscal_year: int | None = Field(
        default=None,
        description="Calendar year in which the latest fiscal year in the window ends. "
        "Default: the latest completed fiscal year with a 10-K filed by the cutoff.",
    )
    num_years: int = Field(default=3, ge=2, le=5)
    question: str | None = None
    offline: bool = False
    investigate: bool = Field(default=True, description="Run Stage 2 investigation, report and review")
    llm: Literal["auto", "anthropic", "stub"] = Field(
        default="auto", description="auto: Anthropic when credentials are set and not offline, else the stub")


class Company(_Model):
    cik: str = Field(description="10-digit zero-padded CIK")
    name: str
    tickers: list[str] = Field(default_factory=list)
    sic: str | None = None
    sic_description: str | None = None
    fiscal_year_end: str | None = Field(default=None, description="MMDD as reported by SEC")
    state_of_incorporation: str | None = None


class FiscalPeriod(_Model):
    label: str = Field(description="Display label, e.g. FY2025 (year in which the period ends)")
    end_date: date
    start_date: date | None = Field(
        default=None, description="Filled from annual duration facts during normalization"
    )
    defining_accession: str = Field(description="10-K whose reportDate defines this period end")
    defining_filing_date: date


class AnalysisPlan(_Model):
    request: AnalysisRequest
    company: Company
    cutoff_date: date
    currency: Literal["USD"] = "USD"
    industry_profile: str = "nonfinancial_operating"
    fiscal_periods: list[FiscalPeriod]
    required_fields: list[str]
    metrics: list[str]
    documents: list["SourceRef"]
    notes: list[str] = Field(default_factory=list)


class SourceRef(_Model):
    """A filing the plan intends to use (not yet downloaded)."""

    accession: str
    form: str
    filing_date: date
    report_date: date | None
    url: str


# --------------------------------------------------------------------------- sources & facts


class SourceKind(str, Enum):
    COMPANY_TICKERS = "company_tickers"
    SUBMISSIONS = "submissions"
    COMPANY_FACTS = "company_facts"
    FILING_DOCUMENT = "filing_document"


class SourceDocument(_Model):
    id: str
    kind: SourceKind
    cik: str | None
    url: str
    retrieved_at: datetime
    sha256: str
    from_cache: bool = False
    is_fixture: bool = False
    accession: str | None = None
    form: str | None = None
    filing_date: date | None = None
    fiscal_period_end: date | None = None


class PeriodType(str, Enum):
    DURATION = "duration"
    INSTANT = "instant"


class FactStatus(str, Enum):
    ACCEPTED = "accepted"
    ALTERNATIVE = "alternative"  # valid but superseded (e.g. originally reported before restatement)
    REJECTED = "rejected"
    DERIVED = "derived"  # computed from accepted facts, e.g. gross profit = revenue - cost


class FinancialFact(_Model):
    id: str
    field: str = Field(description="Standardized field name, e.g. revenue")
    fiscal_period: str | None = Field(default=None, description="FiscalPeriod.label")
    value: Decimal
    raw_value: Decimal
    currency: str
    unit: str
    scale: int = Field(default=1, description="Multiplier already applied by XBRL (always 1)")
    period_type: PeriodType
    period_start: date | None
    period_end: date
    taxonomy: str | None
    tag: str | None
    extraction_method: Literal["xbrl_companyfacts", "derived", "filing_text"] = "xbrl_companyfacts"
    accession: str | None
    form: str | None
    filed: date | None
    source_document_id: str | None
    location: str = Field(description="Where in the source the value was found")
    status: FactStatus
    status_reason: str = ""
    input_fact_ids: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- validation


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    RECOVERABLE = "recoverable"
    BLOCKING = "blocking"


class ValidationIssue(_Model):
    id: str
    code: str
    severity: Severity
    message: str
    field: str | None = None
    fiscal_period: str | None = None
    fact_ids: list[str] = Field(default_factory=list)
    affected_metrics: list[str] = Field(default_factory=list)
    resolved: bool = False
    resolution: str | None = None
    recovery_attempts: int = 0
    details: dict[str, Any] = Field(default_factory=dict)


class ValidationReport(_Model):
    fiscal_periods: list[FiscalPeriod]
    accepted_facts: list[FinancialFact]
    rejected_candidates: list[FinancialFact]
    alternative_facts: list[FinancialFact]
    issues: list[ValidationIssue]
    field_coverage: dict[str, dict[str, bool]] = Field(
        default_factory=dict, description="field -> fiscal period label -> has accepted value"
    )

    def fact(self, field: str, period: str) -> FinancialFact | None:
        for f in self.accepted_facts:
            if f.field == field and f.fiscal_period == period:
                return f
        return None

    @property
    def blocking_issues(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.BLOCKING and not i.resolved]


# --------------------------------------------------------------------------- analysis


class MetricStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"  # an input is missing or blocked
    NOT_APPLICABLE = "not_applicable"  # e.g. no gross profit for this business model
    SUPPRESSED = "suppressed"  # computable but misleading, e.g. % growth from a negative base


class CalculatedMetric(_Model):
    id: str
    name: str
    fiscal_period: str = Field(description="Period label, or 'FY2024->FY2025' for comparisons")
    value: Decimal | None
    unit: Literal["USD", "ratio", "percent", "percentage_points", "x"]
    formula: str
    input_fact_ids: list[str] = Field(default_factory=list)
    input_metric_ids: list[str] = Field(default_factory=list)
    status: MetricStatus = MetricStatus.AVAILABLE
    note: str = ""
    is_calculated_measure: bool = Field(
        default=True, description="False when the value is a reported figure passed through"
    )


class ChangeFlag(_Model):
    id: str
    rule: str
    metric: str
    comparison: str
    observed: Decimal
    observed_unit: str
    threshold: Decimal
    score: Decimal = Field(description="observed / threshold magnitude; used for ranking only")
    direction: Literal["increase", "decrease", "transition"]
    description: str
    metric_ids: list[str]


class AnalysisResult(_Model):
    metrics: list[CalculatedMetric]
    changes: list[ChangeFlag]


# --------------------------------------------------------------------------- Stage 2: evidence


class FilingPassage(_Model):
    """A section-aware chunk of annual-report text, with enough provenance to cite it."""

    id: str = Field(description="psg:<accession>:<section>:<n>")
    accession: str
    form: str
    filing_date: date
    fiscal_period: str | None = Field(default=None, description="Fiscal period the filing reports on")
    section: str = Field(description="e.g. item_7, item_8/note_14")
    section_title: str
    text: str
    char_start: int = Field(description="Offset in the filing's extracted plain text")
    char_end: int
    source_document_id: str
    url: str
    is_fixture: bool = False


class EvidenceStatus(str, Enum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"


class EvidenceQuote(_Model):
    """A verbatim excerpt the investigator relies on. ``verified`` is set by code, never by the LLM."""

    passage_id: str
    quote: str
    verified: bool = False
    verification_note: str = ""


class ToolCallRecord(_Model):
    step: int
    tool: str
    input: dict[str, Any]
    ok: bool
    summary: str


class InvestigationFinding(_Model):
    id: str
    change_flag_id: str
    calculated_observation: str = Field(description="What the numbers show; written by code from metrics")
    management_explanation: str | None = Field(
        default=None, description="What the company attributes the change to; must be backed by quotes")
    analyst_interpretation: str | None = Field(default=None, description="Clearly labelled interpretation")
    unresolved_questions: list[str] = Field(default_factory=list)
    assessment: Literal["favorable", "unfavorable", "ambiguous"] = "ambiguous"
    evidence_status: EvidenceStatus
    status_reason: str = ""
    quotes: list[EvidenceQuote] = Field(default_factory=list)
    metric_ids: list[str] = Field(default_factory=list)
    passage_ids: list[str] = Field(default_factory=list)
    steps_used: int = 0
    budget_exhausted: bool = False
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    produced_by: str = Field(default="", description="Provider name, e.g. anthropic:<model> or offline-stub")
    revision: int = 0


class InvestigationResult(_Model):
    findings: list[InvestigationFinding] = Field(default_factory=list)
    not_investigated: list[str] = Field(default_factory=list, description="Change flag IDs skipped (budget)")
    notes: list[str] = Field(default_factory=list)
    llm_usage: dict[str, Any] = Field(default_factory=dict)


class ReportClaim(_Model):
    id: str
    section: str
    text: str
    kind: Literal["numeric", "management_explanation", "analyst_interpretation", "unresolved", "limitation",
                  "summary"] = "numeric"
    metric_ids: list[str] = Field(default_factory=list)
    passage_ids: list[str] = Field(default_factory=list)
    evidence_status: EvidenceStatus
    finding_id: str | None = None


class ChartSpec(_Model):
    id: str
    title: str
    unit: str
    filename: str
    periods: list[str]
    series: dict[str, list[float | None]] = Field(description="Series name -> values in display units")
    metric_ids: dict[str, list[str | None]] = Field(description="Series name -> metric ID per period")
    scale: float = Field(default=1.0, description="Display value = metric value / scale")
    caption: str = ""


class ReportDraft(_Model):
    title: str
    revision: int = 0
    claims: list[ReportClaim]
    charts: list[ChartSpec]
    markdown: str = ""
    html: str = Field(default="", description="Same content; charts referenced as charts/<file>.png")
    passages: list[FilingPassage] = Field(default_factory=list, description="Every passage the report cites")
    excluded_claims: list[ReportClaim] = Field(
        default_factory=list, description="Claims removed during revision, kept for the audit trail")


class ReviewDefect(_Model):
    code: str
    message: str
    severity: Literal["error", "warning"] = "error"
    route_to: Literal["drafting", "investigation", "none"] = "drafting"
    claim_id: str | None = None
    finding_id: str | None = None
    change_flag_id: str | None = None
    source: Literal["deterministic", "llm"] = "deterministic"
    suggested_status: EvidenceStatus | None = Field(default=None, description="LLM reviewer's lower verdict")


class ReviewResult(_Model):
    passed: bool
    defects: list[ReviewDefect] = Field(default_factory=list)
    revision_cycle: int = 0
    checks_run: list[str] = Field(default_factory=list)
    needs_analyst_attention: bool = False
    stats: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- workflow


class RunState(str, Enum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    COLLECTING = "COLLECTING"
    VALIDATING = "VALIDATING"
    ANALYZING = "ANALYZING"
    INVESTIGATING = "INVESTIGATING"
    DRAFTING = "DRAFTING"
    REVIEWING = "REVIEWING"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    NEEDS_INPUT = "NEEDS_INPUT"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    APPROVED = "APPROVED"


class StateTransition(_Model):
    from_state: RunState
    to_state: RunState
    at: datetime
    reason: str


class WorkflowRun(_Model):
    id: str
    request: AnalysisRequest
    state: RunState = RunState.CREATED
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    history: list[StateTransition] = Field(default_factory=list)
    failed_stage: RunState | None = None
    error: str | None = None
    needs_input: dict[str, Any] | None = None
    stats: dict[str, Any] = Field(default_factory=dict)


AnalysisPlan.model_rebuild()
