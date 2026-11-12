"""Data contracts for the research report. Values are ``Decimal``; rounding happens on render."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from finsight.contracts import utcnow


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MarketSnapshot(_M):
    """A share price at a stated date, with where it came from. Supplied, never invented."""

    ticker: str
    price: Decimal = Field(gt=0)
    as_of: date
    currency: Literal["USD"] = "USD"
    source: str = Field(description="Who published the price, e.g. 'Consolidated tape (SIP) official close'")
    source_url: str | None = None
    retrieved_at: datetime = Field(default_factory=utcnow)
    note: str = ""


class RateInput(_M):
    """A market or analyst input to the discount rate, e.g. the risk-free rate."""

    value_pct: Decimal
    as_of: date | None = None
    source: str
    source_url: str | None = None


class Citation(_M):
    id: str = Field(description="S1, S2, ... in order of first use")
    kind: Literal["filing_passage", "sec_data", "market_data", "engine"]
    label: str
    url: str | None = None
    detail: str = ""
    passage_id: str | None = None


class Assumption(_M):
    key: str
    label: str
    value: str = Field(description="Display value")
    basis: Literal["sourced", "calculated", "assumption", "rule"]
    explanation: str
    citation_ids: list[str] = Field(default_factory=list)
    metric_ids: list[str] = Field(default_factory=list)


class Paragraph(_M):
    text: str = Field(description="May contain [S#] citation markers")
    kind: Literal["engine", "filing_extract", "ai_summary", "note"] = "engine"
    citation_ids: list[str] = Field(default_factory=list)


class Section(_M):
    key: str
    title: str
    paragraphs: list[Paragraph] = Field(default_factory=list)
    bullets: list[Paragraph] = Field(default_factory=list)
    provenance: str = Field(default="", description="How this section was produced, shown under the heading")


class DcfYear(_M):
    year: int
    growth_pct: Decimal
    fcf: Decimal
    discount_factor: Decimal
    present_value: Decimal


class Valuation(_M):
    available: bool
    reason: str = ""
    method: str = "Free-cash-flow DCF with a Gordon-growth terminal value"
    forecast_years: int | None = None
    base_fcf_method: str | None = None
    base_fcf_margin_pct: Decimal | None = None
    base_fcf: Decimal | None = None
    base_fcf_metric_id: str | None = None
    discount_rate_pct: Decimal | None = None
    terminal_growth_pct: Decimal | None = None
    stage1_growth_pct: Decimal | None = None
    projection: list[DcfYear] = Field(default_factory=list)
    pv_explicit: Decimal | None = None
    terminal_value: Decimal | None = None
    pv_terminal: Decimal | None = None
    enterprise_value: Decimal | None = None
    cash: Decimal | None = None
    short_term_investments: Decimal | None = None
    debt: Decimal | None = None
    equity_value: Decimal | None = None
    diluted_shares: Decimal | None = None
    value_per_share: Decimal | None = None
    target_price: Decimal | None = None
    upside_pct: Decimal | None = None
    implied_stage1_growth_pct: Decimal | None = Field(
        default=None, description="Stage-1 growth at which the DCF equals the current price (reverse DCF)")
    sensitivity: dict[str, dict[str, Decimal | None]] = Field(
        default_factory=dict, description="discount rate % -> terminal growth % -> value per share")
    multiples: list[dict[str, str]] = Field(default_factory=list, description="name, at_price, at_target, basis")


class Recommendation(_M):
    rating: Literal["BUY", "HOLD", "SELL", "NOT RATED"]
    rule: str
    reason: str


class CompanyHeader(_M):
    name: str
    ticker: str
    exchange: str
    sector: str
    industry: str
    cik: str
    fiscal_year_end: str | None = None
    citation_ids: list[str] = Field(default_factory=list)


class ResearchReport(_M):
    run_id: str
    created_at: datetime = Field(default_factory=utcnow)
    title: str
    header: CompanyHeader
    market: MarketSnapshot | None
    valuation: Valuation
    recommendation: Recommendation
    assumptions: list[Assumption]
    sections: list[Section]
    citations: list[Citation]
    periods: list[str]
    narrative_mode: Literal["ai_summary", "filing_extract"]
    narrative_provider: str
    run_state: str
    review_status: str
    is_fixture: bool = False
    limitations: list[str] = Field(default_factory=list)
    llm_usage: dict = Field(default_factory=dict)


def money(v: Decimal) -> str:
    """Price for display: two decimals, half-up (521.125 -> 521.13), thousands separators."""
    from decimal import ROUND_HALF_UP

    return f"{Decimal(v).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"
