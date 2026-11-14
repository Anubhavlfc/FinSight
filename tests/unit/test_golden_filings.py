"""Golden accuracy tests on REAL SEC data.

The pipeline runs offline on XBRL recorded from SEC EDGAR (``fixtures/golden/``, see
``scripts/record_golden_fixture.py``). Its figures are compared with numbers typed by hand from
the audited statements in each 10-K (``tests/golden/expected.toml``), and its ratios with values
worked out here from those printed numbers, not from FinSight's metrics code.

A failure means FinSight disagrees with what the company printed in its 10-K.
"""

from __future__ import annotations

import tomllib
from datetime import date
from decimal import Decimal

import pytest

from finsight.contracts import AnalysisRequest, MetricStatus, RunState, Severity
from finsight.orchestrator import Orchestrator, load_results
from finsight.sec.sources import FixtureSource
from tests.conftest import REPO

GOLDEN = REPO / "fixtures" / "golden"
EXPECTED = tomllib.loads((REPO / "tests" / "golden" / "expected.toml").read_text())
CUTOFF = date(2026, 11, 1)
M = Decimal(1_000_000)
H = Decimal(100)

# Figures printed in millions are exact; percentages are compared to 1e-9 percentage points.
PCT_TOL = Decimal("1e-9")

REPORTED = ["revenue", "operating_income", "net_income", "operating_cash_flow", "capital_expenditure", "cash_and_equivalents"]


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    from finsight.config import load_settings
    from finsight.storage import Store

    home = tmp_path_factory.mktemp("golden")
    settings = load_settings(home=home)
    store = Store(home / "golden.sqlite3")
    orch = Orchestrator(settings, store, lambda req: FixtureSource(GOLDEN))
    out = {}
    for ticker in EXPECTED:
        run = orch.start(AnalysisRequest(identifier=ticker, cutoff_date=CUTOFF, offline=True,
                                         investigate=False))
        out[ticker] = (run, load_results(store, run.id))
    yield out
    store.close()


def metric(results, name, period):
    return next(m for m in results["analysis"].metrics if m.name == name and m.fiscal_period == period)


def cases(field):
    return list(_cases(field))


def _cases(field):
    for ticker, exp in EXPECTED.items():
        if field in exp:
            for i, period in enumerate(exp["periods"]):
                yield pytest.param(ticker, period, i, id=f"{ticker}-{period}")


def test_runs_complete_with_expected_periods(runs):
    for ticker, (run, r) in runs.items():
        assert run.state == RunState.READY_FOR_REVIEW, (ticker, run.error)
        periods = [p.label for p in r["validation"].report.fiscal_periods]
        assert periods == EXPECTED[ticker]["periods"], ticker
        assert r["plan"].plan.company.cik == EXPECTED[ticker]["cik"]


@pytest.mark.parametrize("field", REPORTED)
def test_reported_figures_match_the_10k(runs, field):
    for ticker, exp in EXPECTED.items():
        _, r = runs[ticker]
        for i, period in enumerate(exp["periods"]):
            m = metric(r, field, period)
            assert m.status == MetricStatus.AVAILABLE, (ticker, field, period, m.note)
            assert m.value == exp[field][i] * M, (ticker, field, period, m.value)


@pytest.mark.parametrize("ticker,period,i", cases("gross_profit"))
def test_gross_profit_matches_the_10k(runs, ticker, period, i):
    _, r = runs[ticker]
    assert metric(r, "gross_profit", period).value == EXPECTED[ticker]["gross_profit"][i] * M


@pytest.mark.parametrize("ticker,period,i", cases("debt"))
def test_interest_bearing_debt_is_the_sum_of_the_balance_sheet_debt_lines(runs, ticker, period, i):
    _, r = runs[ticker]
    expected = sum(EXPECTED[ticker]["debt"][i]) * M
    m = metric(r, "interest_bearing_debt", period)
    assert m.value == expected, (m.value, expected, m.note)


@pytest.mark.parametrize("ticker,period,i", cases("revenue"))
def test_margins_and_ratios_from_printed_figures(runs, ticker, period, i):
    exp = EXPECTED[ticker]
    _, r = runs[ticker]
    rev = Decimal(exp["revenue"][i])
    gp = Decimal(exp["gross_profit"][i]) if "gross_profit" in exp else rev - exp["cost_of_revenue"][i]
    checks = {
        "gross_margin": gp / rev * H,
        "operating_margin": Decimal(exp["operating_income"][i]) / rev * H,
        "net_margin": Decimal(exp["net_income"][i]) / rev * H,
        "current_ratio": Decimal(exp["current_assets"][i]) / Decimal(exp["current_liabilities"][i]),
    }
    for name, want in checks.items():
        got = metric(r, name, period).value
        assert abs(got - want) < PCT_TOL, (ticker, name, period, got, want)
    fcf = (exp["operating_cash_flow"][i] - exp["capital_expenditure"][i]) * M
    assert metric(r, "free_cash_flow", period).value == fcf


@pytest.mark.parametrize("ticker", list(EXPECTED))
def test_year_over_year_changes_from_printed_figures(runs, ticker):
    exp = EXPECTED[ticker]
    _, r = runs[ticker]
    p = exp["periods"]
    for a, b in ((0, 1), (1, 2)):
        lbl = f"{p[a]}->{p[b]}"
        want = (Decimal(exp["revenue"][b]) - exp["revenue"][a]) / Decimal(exp["revenue"][a]) * H
        assert abs(metric(r, "revenue_growth", lbl).value - want) < PCT_TOL
        om = [Decimal(exp["operating_income"][k]) / Decimal(exp["revenue"][k]) * H for k in (a, b)]
        assert abs(metric(r, "operating_margin_change", lbl).value - (om[1] - om[0])) < PCT_TOL
        fcf = [Decimal(exp["operating_cash_flow"][k] - exp["capital_expenditure"][k]) for k in (a, b)]
        assert abs(metric(r, "free_cash_flow_growth", lbl).value - (fcf[1] - fcf[0]) / fcf[0] * H) < PCT_TOL


def test_apple_window_respects_the_cutoff(runs):
    """Apple filed its FY2026 10-K on 2026-11-03, two days after the cutoff."""
    _, r = runs["AAPL"]
    assert r["validation"].report.fiscal_periods[-1].end_date == date(2025, 9, 27)


def test_walmart_gross_profit_basis_is_disclosed(runs):
    """Walmart's gross profit is on net sales; FinSight derives it from total revenues.

    The derived value is total revenues - cost of sales, and a warning states how far it is from
    the net-sales basis, which is exactly Walmart's own gross profit.
    """
    exp = EXPECTED["WMT"]
    _, r = runs["WMT"]
    issues = {i.fiscal_period: i for i in r["validation"].report.issues if i.code == "derived_on_total_revenue"}
    for i, period in enumerate(exp["periods"]):
        derived = metric(r, "gross_profit", period).value
        assert derived == (exp["revenue"][i] - exp["cost_of_revenue"][i]) * M
        issue = issues[period]
        assert issue.severity == Severity.WARNING
        gap = Decimal(issue.details["difference"])
        assert gap == (exp["revenue"][i] - exp["net_sales"][i]) * M
        assert derived - gap == exp["company_gross_profit"][i] * M


def test_coca_cola_debt_includes_other_short_term_borrowings(runs):
    """Coca-Cola's "loans and notes payable" is commercial paper plus other short-term borrowings."""
    _, r = runs["KO"]
    m = metric(r, "interest_bearing_debt", "FY2024")
    assert any(fid.startswith("fact:other_short_term_borrowings:FY2024") for fid in m.input_fact_ids)
    assert "OtherShortTermBorrowings" in m.note


def test_no_unexpected_warnings(runs):
    """Real filings should not produce warnings beyond the known, explained ones."""
    allowed = {"competing_tag_differs", "derived_on_total_revenue"}
    for ticker, (_, r) in runs.items():
        unexpected = [i for i in r["validation"].report.issues
                      if i.severity in (Severity.WARNING, Severity.BLOCKING) and i.code not in allowed]
        assert not unexpected, (ticker, [i.message for i in unexpected])
