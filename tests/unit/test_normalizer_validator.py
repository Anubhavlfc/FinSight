"""Normalization and validation rules on small SYNTHETIC inputs."""

from __future__ import annotations

from decimal import Decimal

from finsight.contracts import FactStatus, Severity
from finsight.validator import validate
from tests.conftest import annual, collection, instant, make_plan, row

REV = "RevenueFromContractWithCustomerExcludingAssessedTax"


def run(rows, plan=None, settings=None, **kw):
    from finsight.config import load_mappings, load_settings
    return validate(plan or make_plan(), collection(rows, **kw), load_mappings(), settings or load_settings())


def codes(report, severity=None):
    return [i.code for i in report.issues if severity is None or i.severity == severity]


def base_rows():
    rows = []
    for fy, rev in [(2024, 100), (2025, 120), (2026, 150)]:
        rows += [annual(REV, fy, rev), annual("OperatingIncomeLoss", fy, rev // 10),
                 annual("NetIncomeLoss", fy, rev // 20),
                 annual("NetCashProvidedByUsedInOperatingActivities", fy, 30),
                 annual("PaymentsToAcquirePropertyPlantAndEquipment", fy, 10),
                 instant("CashAndCashEquivalentsAtCarryingValue", fy, 50),
                 instant("Assets", fy, 500)]
    return rows


def test_quarterly_fact_labelled_fy_is_not_annual():
    rows = base_rows() + [row(REV, "2026-06-30", 40, start="2026-04-01", fy=2026, fp="FY")]
    out = run(rows).report
    assert out.fact("revenue", "FY2026").value == Decimal(150)
    rej = [f for f in out.rejected_candidates if f.raw_value == 40]
    assert rej and "not an annual duration" in rej[0].status_reason


def test_latest_filing_wins_and_original_is_kept():
    rows = base_rows()
    rows = [r for r in rows if not (r.tag == REV and r.end == "2024-06-30")]
    rows += [annual(REV, 2024, 105, accn="A-24", filed="2024-07-30"),
             annual(REV, 2024, 100, accn="A-25", filed="2025-07-30")]
    out = run(rows).report
    assert out.fact("revenue", "FY2024").value == 100
    alt = [f for f in out.alternative_facts if f.field == "revenue"]
    assert alt[0].raw_value == 105 and alt[0].status == FactStatus.ALTERNATIVE
    assert "restated_value" in codes(out, Severity.WARNING)


def test_filings_after_cutoff_are_ignored():
    rows = base_rows() + [annual(REV, 2026, 999, accn="LATE", filed="2027-01-15")]
    out = run(rows).report
    assert out.fact("revenue", "FY2026").value == 150
    assert any("after the analysis cutoff" in f.status_reason for f in out.rejected_candidates)


def test_amendment_beats_original_on_same_day():
    rows = base_rows()
    rows += [annual(REV, 2026, 151, accn="ORIG", form="10-K", filed="2026-08-01"),
             annual(REV, 2026, 152, accn="AMEND", form="10-K/A", filed="2026-08-01")]
    out = run(rows).report
    assert out.fact("revenue", "FY2026").value == 152


def test_exact_duplicates_collapse_without_warning():
    rows = base_rows() + [annual(REV, 2026, 150)]  # same accession, same value
    out = run(rows).report
    assert "restated_value" not in codes(out)
    assert out.fact("revenue", "FY2026").value == 150


def test_tag_covering_all_years_is_preferred_for_comparability():
    rows = [r for r in base_rows() if r.tag != REV]
    rows += [annual("Revenues", fy, v) for fy, v in [(2024, 100), (2025, 120), (2026, 150)]]
    rows += [annual(REV, 2026, 150)]  # higher priority but only one year
    out = run(rows).report
    assert {out.fact("revenue", p).tag for p in ("FY2024", "FY2025", "FY2026")} == {"Revenues"}
    assert "mixed_tags_across_years" not in codes(out)


def test_mixed_tags_warn():
    rows = [r for r in base_rows() if r.tag != REV]
    rows += [annual("Revenues", 2024, 100), annual(REV, 2025, 120), annual(REV, 2026, 150)]
    out = run(rows).report
    assert "mixed_tags_across_years" in codes(out, Severity.WARNING)


def test_non_usd_is_rejected():
    rows = [r for r in base_rows() if not (r.tag == REV and r.end == "2025-06-30")]
    rows += [annual(REV, 2025, 120, unit="EUR")]
    out = run(rows).report
    assert out.fact("revenue", "FY2025") is None
    assert "required_field_missing" in codes(out, Severity.BLOCKING)


def test_capex_sign_normalized_and_disclosed():
    rows = [r for r in base_rows() if not (r.tag.startswith("PaymentsToAcquire") and r.end == "2026-06-30")]
    rows += [annual("PaymentsToAcquirePropertyPlantAndEquipment", 2026, -12)]
    out = run(rows).report
    f = out.fact("capital_expenditure", "FY2026")
    assert f.value == 12 and f.raw_value == -12
    assert "sign_normalized" in codes(out, Severity.WARNING)


def test_missing_required_field_is_blocking_not_invented():
    rows = [r for r in base_rows() if not (r.tag == "OperatingIncomeLoss" and r.end == "2025-06-30")]
    out = run(rows).report
    assert out.fact("operating_income", "FY2025") is None
    blocking = [i for i in out.issues if i.code == "required_field_missing"]
    assert blocking[0].fiscal_period == "FY2025" and "operating_margin" in blocking[0].affected_metrics


def test_gross_profit_derived_when_not_tagged():
    rows = base_rows() + [annual("CostOfRevenue", fy, 40) for fy in (2024, 2025, 2026)]
    out = run(rows).report
    gp = out.fact("gross_profit", "FY2025")
    assert gp.status == FactStatus.DERIVED and gp.value == 80 and len(gp.input_fact_ids) == 2
    assert "derived_field" in codes(out, Severity.INFO)


def test_failed_balance_check_creates_issue_without_balancing_entry():
    rows = base_rows() + [instant("LiabilitiesAndStockholdersEquity", fy, 500) for fy in (2024, 2025)]
    rows += [instant("LiabilitiesAndStockholdersEquity", 2026, 470_000_000)]
    rows = [r for r in rows if not (r.tag == "Assets" and r.end == "2026-06-30")]
    rows += [instant("Assets", 2026, 500_000_000)]
    out = run(rows).report
    failed = [i for i in out.issues if i.code == "accounting_check_failed"]
    assert len(failed) == 1 and failed[0].fiscal_period == "FY2026"
    assert out.fact("total_assets", "FY2026").value == 500_000_000  # untouched


def test_rounding_tolerance_accepts_small_differences():
    rows = base_rows()
    rows = [r for r in rows if r.tag != "Assets"]
    rows += [instant("Assets", fy, 1_000_000_000) for fy in (2024, 2025, 2026)]
    rows += [instant("LiabilitiesAndStockholdersEquity", fy, 1_000_000_000 + 2_000_000) for fy in (2024, 2025, 2026)]
    assert "accounting_check_failed" not in codes(run(rows).report)


def test_stale_company_facts_recover_once_then_block(settings):
    out = run(base_rows(), contains_latest=False)
    assert out.needs_refetch and "company_facts_stale" in codes(out.report, Severity.RECOVERABLE)
    from finsight.config import load_mappings
    exhausted = validate(make_plan(), collection(base_rows(), contains_latest=False), load_mappings(), settings,
                         recovery_attempts=settings.validation.max_recovery_attempts)
    assert not exhausted.needs_refetch
    assert "company_facts_stale" in codes(exhausted.report, Severity.BLOCKING)


def test_non_consecutive_years_are_blocking():
    plan = make_plan(ends=("2022-06-30", "2024-06-30", "2025-06-30"))
    out = run(base_rows(), plan=plan).report
    assert "non_consecutive_periods" in codes(out, Severity.BLOCKING)
