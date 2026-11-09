"""Validation: period checks, accounting relationships, coverage, and the report.

A failed accounting check becomes an issue to investigate. It never produces a
balancing entry or an adjusted value.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from finsight.collector import CollectionResult
from finsight.config import Mappings, Settings
from finsight.contracts import AnalysisPlan, FactStatus, Severity, ValidationIssue, ValidationReport
from finsight.normalizer import NormalizationResult, _IssueFactory, normalize, within_tolerance


@dataclass
class ValidationOutcome:
    report: ValidationReport
    needs_refetch: bool  # recoverable: company facts predate the latest 10-K


def _check(issue, settings, lbl, name, expected_desc, actual_desc, expected: Decimal, actual: Decimal,
           fact_ids: list[str], fields: list[str], severity: Severity = Severity.WARNING,
           explanation: str = "") -> None:
    if within_tolerance(expected, actual, settings):
        return
    issue("accounting_check_failed", severity,
          f"{lbl}: {name} does not hold: {expected_desc} = {expected:,} vs {actual_desc} = {actual:,} "
          f"(difference {expected - actual:,}). {explanation}Recorded for investigation; no balancing entry was made.",
          fiscal_period=lbl, fact_ids=fact_ids, details={"check": name, "fields": fields,
                                                         "expected": str(expected), "actual": str(actual)})


def validate(plan: AnalysisPlan, collection: CollectionResult, mappings: Mappings, settings: Settings,
             recovery_attempts: int = 0) -> ValidationOutcome:
    norm: NormalizationResult = normalize(plan, collection.rows, mappings, settings,
                                          collection.company_facts_document.id)
    issue = _IssueFactory(norm.issues)
    issue.n = len(norm.issues)
    periods = [p.model_copy() for p in plan.fiscal_periods]

    # ---- source freshness (recoverable) --------------------------------------------------
    needs_refetch = False
    if not collection.contains_latest_accession:
        can_retry = recovery_attempts < settings.validation.max_recovery_attempts
        needs_refetch = can_retry
        issue("company_facts_stale",
              Severity.RECOVERABLE if can_retry else Severity.BLOCKING,
              f"Company facts contain no values from the latest 10-K ({collection.latest_accession}). "
              + ("Refetching from SEC." if can_retry else
                 f"Still missing after {recovery_attempts} refetch attempt(s); SEC may not have processed "
                 "the filing yet. Latest-year values are unavailable."),
              details={"accession": collection.latest_accession}, recovery_attempts=recovery_attempts)

    # ---- periods ------------------------------------------------------------------------
    if len(periods) < plan.request.num_years:
        issue("insufficient_history", Severity.BLOCKING,
              f"Only {len(periods)} fiscal year(s) available by the cutoff; {plan.request.num_years} requested.",
              affected_metrics=["revenue_growth"])
    for prev, cur in zip(periods, periods[1:]):
        gap = (cur.end_date - prev.end_date).days
        if not settings.periods.min_gap_days <= gap <= settings.periods.max_gap_days:
            issue("non_consecutive_periods", Severity.BLOCKING,
                  f"{prev.label} and {cur.label} end {gap} days apart; growth comparisons between them are not "
                  "like-for-like (fiscal year change or missing year).", fiscal_period=cur.label,
                  affected_metrics=["revenue_growth", "free_cash_flow_growth"])
    for p in periods:
        p.start_date = norm.period_starts.get(p.label)
        if p.start_date is None:
            continue
        mismatched = [f for f in norm.accepted if f.fiscal_period == p.label and f.period_start
                      and f.status == FactStatus.ACCEPTED and f.period_start != p.start_date]
        for f in mismatched:
            issue("period_start_mismatch", Severity.WARNING,
                  f"{f.field} {p.label} starts {f.period_start}, revenue starts {p.start_date}.",
                  field=f.field, fiscal_period=p.label, fact_ids=[f.id])

    # ---- accounting relationships -----------------------------------------------------------
    acc = {(f.field, f.fiscal_period): f for f in norm.accepted}

    def v(field, lbl):
        f = acc.get((field, lbl))
        return f

    for p in periods:
        lbl = p.label
        assets, le = v("total_assets", lbl), v("liabilities_and_equity", lbl)
        if assets and le:
            _check(issue, settings, lbl, "balance sheet balances", "total assets", "liabilities and equity",
                   assets.value, le.value, [assets.id, le.id], ["total_assets", "liabilities_and_equity"])
        liab, eq = v("total_liabilities", lbl), v("stockholders_equity", lbl)
        # A derived total liabilities (L&E - equity) would make this check circular.
        if assets and liab and eq and liab.status != FactStatus.DERIVED:
            _check(issue, settings, lbl, "assets = liabilities + equity", "total assets",
                   "liabilities + equity", assets.value, liab.value + eq.value,
                   [assets.id, liab.id, eq.id], ["total_assets", "total_liabilities", "stockholders_equity"])
        rev, cost, gp = v("revenue", lbl), v("cost_of_revenue", lbl), v("gross_profit", lbl)
        if rev and cost and gp and gp.status == FactStatus.ACCEPTED:
            _check(issue, settings, lbl, "gross profit = revenue - cost of revenue", "gross profit",
                   "revenue - cost of revenue", gp.value, rev.value - cost.value,
                   [gp.id, rev.id, cost.id], ["gross_profit", "revenue", "cost_of_revenue"])
        ltd, ltn, ltc = v("long_term_debt_total", lbl), v("long_term_debt_noncurrent", lbl), v("long_term_debt_current", lbl)
        if ltd and ltn and ltc:
            _check(issue, settings, lbl, "long-term debt = noncurrent + current", "long-term debt",
                   "noncurrent + current", ltd.value, ltn.value + ltc.value,
                   [ltd.id, ltn.id, ltc.id], ["long_term_debt_total", "long_term_debt_noncurrent", "long_term_debt_current"],
                   severity=Severity.INFO,
                   explanation="Filers often tag LongTermDebt at face value and the balance-sheet lines at "
                               "carrying amount (net of unamortized discount and issuance costs); the debt "
                               "metric uses the balance-sheet lines. ")
        for part, whole, name in [("current_assets", "total_assets", "current assets <= total assets"),
                                  ("current_liabilities", "total_liabilities", "current liabilities <= total liabilities"),
                                  ("cash_and_equivalents", "current_assets", "cash <= current assets")]:
            a, b = v(part, lbl), v(whole, lbl)
            if a and b and a.value > b.value and not within_tolerance(a.value, b.value, settings):
                issue("accounting_check_failed", Severity.WARNING,
                      f"{lbl}: {name} does not hold ({a.value:,} > {b.value:,}). Recorded for investigation.",
                      fiscal_period=lbl, fact_ids=[a.id, b.id], details={"check": name})

    # ---- coverage -------------------------------------------------------------------------
    coverage = {m: {p.label: (m, p.label) in acc for p in periods} for m in mappings.fields}

    report = ValidationReport(
        fiscal_periods=periods,
        accepted_facts=norm.accepted,
        rejected_candidates=norm.rejected,
        alternative_facts=norm.alternatives,
        issues=_ordered(norm.issues),
        field_coverage=coverage,
    )
    return ValidationOutcome(report=report, needs_refetch=needs_refetch)


_SEVERITY_ORDER = {Severity.BLOCKING: 0, Severity.RECOVERABLE: 1, Severity.WARNING: 2, Severity.INFO: 3}


def _ordered(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    return sorted(issues, key=lambda i: (_SEVERITY_ORDER[i.severity], i.fiscal_period or "", i.code))
