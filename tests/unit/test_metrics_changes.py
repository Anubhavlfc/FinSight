"""Metric formulas, denominator handling and change detection on SYNTHETIC inputs."""

from __future__ import annotations

from decimal import Decimal

from finsight.changes import detect_changes
from finsight.config import AnalysisSettings, load_mappings, load_settings
from finsight.contracts import MetricStatus, Severity
from finsight.metrics import compute_metrics
from finsight.validator import validate
from tests.conftest import annual, collection, instant, make_plan

REV = "RevenueFromContractWithCustomerExcludingAssessedTax"


def metrics_for(rows):
    report = validate(make_plan(), collection(rows), load_mappings(), load_settings()).report
    return {m.id: m for m in compute_metrics(report)}, report


def rows_with(revenue, op_income, ocf=(30, 30, 30), capex=(10, 10, 10), net=(5, 5, 5), extra=()):
    rows = []
    for i, fy in enumerate((2024, 2025, 2026)):
        rows += [annual(REV, fy, revenue[i]), annual("OperatingIncomeLoss", fy, op_income[i]),
                 annual("NetIncomeLoss", fy, net[i]),
                 annual("NetCashProvidedByUsedInOperatingActivities", fy, ocf[i]),
                 annual("PaymentsToAcquirePropertyPlantAndEquipment", fy, capex[i]),
                 instant("CashAndCashEquivalentsAtCarryingValue", fy, 50), instant("Assets", fy, 500)]
    return rows + list(extra)


def test_growth_suppressed_for_zero_or_negative_base():
    m, _ = metrics_for(rows_with((0, 100, 150), (0, 10, 15), ocf=(5, -20, 30), capex=(10, 10, 10)))
    g = m["metric:revenue_growth:FY2024->FY2025"]
    assert g.status == MetricStatus.SUPPRESSED and g.value is None and "absolute change is 100" in g.note
    assert m["metric:revenue_change:FY2024->FY2025"].value == 100
    # Margin undefined for zero revenue.
    assert m["metric:operating_margin:FY2024"].status == MetricStatus.UNAVAILABLE
    # FCF -5 -> -30 -> 20: growth suppressed (negative prior), absolute change still given.
    assert m["metric:free_cash_flow_growth:FY2025->FY2026"].status == MetricStatus.SUPPRESSED
    assert m["metric:free_cash_flow_change:FY2025->FY2026"].value == 50


def test_margin_change_in_percentage_points_full_precision():
    m, _ = metrics_for(rows_with((300, 300, 700), (30, 40, 70)))
    om = m["metric:operating_margin:FY2025"].value
    assert abs(om - Decimal(40) / Decimal(300) * 100) < Decimal("1e-25")  # 13.333... kept unrounded
    assert len(str(om)) > 30
    ch = m["metric:operating_margin_change:FY2024->FY2025"]
    assert ch.unit == "percentage_points"
    assert abs(ch.value - (Decimal(40) / 3 - 10)) < Decimal("1e-25")


def test_metric_lineage_points_to_facts():
    m, report = metrics_for(rows_with((100, 120, 150), (10, 12, 15)))
    fcf = m["metric:free_cash_flow:FY2026"]
    assert fcf.value == 20 and "operating_cash_flow - capital_expenditure" in fcf.formula
    accepted = {f.id for f in report.accepted_facts}
    assert set(fcf.input_fact_ids) <= accepted and len(fcf.input_fact_ids) == 2


def test_debt_never_uses_total_liabilities():
    extra = [instant("Liabilities", fy, 400) for fy in (2024, 2025, 2026)]
    m, _ = metrics_for(rows_with((100, 120, 150), (10, 12, 15), extra=extra))
    d = m["metric:interest_bearing_debt:FY2026"]
    assert d.value is None and d.status == MetricStatus.UNAVAILABLE and "not assumed" in d.note


def test_debt_components_and_disclosure():
    extra = []
    for fy in (2024, 2025, 2026):
        extra += [instant("LongTermDebtNoncurrent", fy, 100), instant("ShortTermBorrowings", fy, 7)]
    extra += [instant("LongTermDebtCurrent", 2026, 20)]
    m, _ = metrics_for(rows_with((100, 120, 150), (10, 12, 15), extra=extra))
    assert m["metric:interest_bearing_debt:FY2026"].value == 127
    d25 = m["metric:interest_bearing_debt:FY2025"]
    assert d25.value == 107 and "current portion" in d25.note and "commercial paper not reported" in d25.note


def test_long_term_debt_consistent_tags_raise_no_issue():
    extra = []
    for fy in (2024, 2025, 2026):
        extra += [instant("LongTermDebt", fy, 120), instant("LongTermDebtNoncurrent", fy, 100),
                  instant("LongTermDebtCurrent", fy, 20)]
    m, report = metrics_for(rows_with((100, 120, 150), (10, 12, 15), extra=extra))
    assert m["metric:interest_bearing_debt:FY2026"].value == 120
    assert not [i for i in report.issues if i.code == "accounting_check_failed"]


def test_balance_sheet_debt_lines_preferred_over_face_value_total():
    # Amazon-style: LongTermDebt at face value, balance-sheet lines at carrying amount.
    M = 1_000_000
    extra = []
    for fy in (2024, 2025, 2026):
        extra += [instant("LongTermDebt", fy, 6880 * M), instant("LongTermDebtNoncurrent", fy, 6560 * M),
                  instant("LongTermDebtCurrent", fy, 280 * M)]
    rows = rows_with((1000 * M, 1200 * M, 1500 * M), (100 * M, 120 * M, 150 * M), extra=extra)
    m, report = metrics_for(rows)
    d = m["metric:interest_bearing_debt:FY2026"]
    assert d.value == 6840 * M and len(d.input_fact_ids) == 2
    checks = [i for i in report.issues if i.code == "accounting_check_failed"]
    assert len(checks) == 3 and all(i.severity == Severity.INFO for i in checks)
    assert "face value" in checks[0].message


def test_debt_and_finance_lease_tags_used_as_disclosed_fallback():
    # Coca-Cola-style: LongTermDebt tags stop after FY2024; combined debt+lease tags cover every year.
    extra = [instant("LongTermDebtNoncurrent", 2024, 90), instant("LongTermDebtCurrent", 2024, 10)]
    for fy in (2024, 2025, 2026):
        extra += [instant("LongTermDebtAndCapitalLeaseObligations", fy, 95),
                  instant("LongTermDebtAndCapitalLeaseObligationsCurrent", fy, 11)]
    m, _ = metrics_for(rows_with((100, 120, 150), (10, 12, 15), extra=extra))
    for fy in (2024, 2025, 2026):
        d = m[f"metric:interest_bearing_debt:FY{fy}"]
        assert d.value == 106 and "finance lease" in d.note


def test_gross_margin_not_applicable_without_gross_profit():
    m, _ = metrics_for(rows_with((100, 120, 150), (10, 12, 15)))
    assert m["metric:gross_margin:FY2025"].status == MetricStatus.NOT_APPLICABLE
    assert m["metric:gross_margin_change:FY2024->FY2025"].status == MetricStatus.NOT_APPLICABLE
    assert m["metric:current_ratio:FY2025"].status == MetricStatus.NOT_APPLICABLE


def test_change_rules_and_materiality():
    # Revenue +20% but tiny in absolute terms relative to the materiality floor.
    m, _ = metrics_for(rows_with((100, 120, 150_000), (10, 12, 15_000)))
    flags = detect_changes(list(m.values()), ["FY2024", "FY2025", "FY2026"], AnalysisSettings())
    rev_flags = [f for f in flags if f.rule == "revenue_change"]
    assert [f.comparison for f in rev_flags] == ["FY2025->FY2026"]  # +20 is below 0.5% of 150,000


def test_transition_ranked_first():
    m, _ = metrics_for(rows_with((100, 112, 125), (10, 11, 12), net=(-5, 4, 6)))
    flags = detect_changes(list(m.values()), ["FY2024", "FY2025", "FY2026"], AnalysisSettings())
    assert flags[0].rule == "net_income_transition" and flags[0].direction == "transition"


def test_net_income_change_flag_on_positive_base():
    # Net income 100 -> 70 (-30%) with revenue flat: flagged as a decrease; no transition involved.
    m, _ = metrics_for(rows_with((1000, 1000, 1000), (150, 150, 150), net=(100, 70, 72)))
    flags = detect_changes(list(m.values()), ["FY2024", "FY2025", "FY2026"], AnalysisSettings())
    ni = [f for f in flags if f.rule == "net_income_change"]
    assert [(f.comparison, f.direction) for f in ni] == [("FY2024->FY2025", "decrease")]
    assert ni[0].observed == m["metric:net_income_growth:FY2024->FY2025"].value  # -30, full precision


def test_net_income_change_from_loss_base_uses_absolute_change():
    # -10 -> -40: still a loss (no transition), % growth suppressed, absolute change vs materiality.
    m, _ = metrics_for(rows_with((1000, 1000, 1000), (150, 150, 150), net=(-10, -40, -41)))
    flags = detect_changes(list(m.values()), ["FY2024", "FY2025", "FY2026"], AnalysisSettings())
    ni = [f for f in flags if f.rule.startswith("net_income_change")]
    assert [f.rule for f in ni] == ["net_income_change_nonpositive_base"] and ni[0].observed == -30


def test_capex_change_flags_increase_and_decrease():
    m, _ = metrics_for(rows_with((1000, 1000, 1000), (150, 150, 150), capex=(100, 200, 120)))
    flags = detect_changes(list(m.values()), ["FY2024", "FY2025", "FY2026"], AnalysisSettings())
    capex = {(f.comparison, f.direction) for f in flags if f.rule == "capex_change"}
    assert capex == {("FY2024->FY2025", "increase"), ("FY2025->FY2026", "decrease")}
