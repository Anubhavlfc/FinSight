"""End-to-end offline run on the SYNTHETIC demo fixture.

Expected values below were worked out by hand from the fixture definition in
scripts/build_demo_fixture.py, independently of the metrics code.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from finsight.contracts import AnalysisRequest, MetricStatus, RunState
from finsight.orchestrator import Orchestrator, load_results
from finsight.sec.sources import FixtureSource
from tests.conftest import DEMO_FIXTURES

M = Decimal(1_000_000)
TOL = Decimal("1e-9")  # percent units; Decimal keeps far more precision than this


@pytest.fixture
def demo_run(settings, store):
    orch = Orchestrator(settings, store, lambda req: FixtureSource(DEMO_FIXTURES))
    run = orch.start(AnalysisRequest(identifier="DEMO", cutoff_date=date(2026, 11, 1), offline=True))
    return run, load_results(store, run.id)


def metric(results, name, period):
    return next(m for m in results["analysis"].metrics if m.name == name and m.fiscal_period == period)


def test_run_reaches_ready_for_review_not_approved(demo_run):
    run, _ = demo_run
    assert run.state == RunState.READY_FOR_REVIEW
    assert [t.to_state for t in run.history] == [
        RunState.PLANNING, RunState.COLLECTING, RunState.VALIDATING, RunState.ANALYZING, RunState.READY_FOR_REVIEW]


def test_three_comparable_years_selected(demo_run):
    _, r = demo_run
    periods = r["validation"].report.fiscal_periods
    assert [(p.label, p.start_date, p.end_date) for p in periods] == [
        ("FY2024", date(2023, 7, 1), date(2024, 6, 30)),
        ("FY2025", date(2024, 7, 1), date(2025, 6, 30)),
        ("FY2026", date(2025, 7, 1), date(2026, 6, 30)),
    ]


EXPECTED_USD = {  # USD millions, hand-computed
    ("revenue", "FY2024"): 1000, ("revenue", "FY2025"): 1180, ("revenue", "FY2026"): 1239,
    ("free_cash_flow", "FY2024"): 120, ("free_cash_flow", "FY2025"): 170, ("free_cash_flow", "FY2026"): -60,
    ("interest_bearing_debt", "FY2024"): 550, ("interest_bearing_debt", "FY2025"): 630,
    ("interest_bearing_debt", "FY2026"): 960,
    ("capital_expenditure", "FY2025"): 90,  # the 10-K/A / later 10-K figure, not the original 85
}

EXPECTED_PCT = {
    ("revenue_growth", "FY2024->FY2025"): Decimal(18),
    ("revenue_growth", "FY2025->FY2026"): Decimal(5),
    ("gross_margin", "FY2024"): Decimal(60),
    ("gross_margin", "FY2025"): Decimal(720) / Decimal(1180) * 100,          # 61.0169...
    ("operating_margin", "FY2025"): Decimal(230) / Decimal(1180) * 100,      # 19.4915...
    ("operating_margin", "FY2026"): Decimal(205) / Decimal(1239) * 100,      # 16.5456...
    ("operating_margin_change", "FY2024->FY2025"): Decimal(230) / Decimal(1180) * 100 - 15,  # +4.4915 pp
    ("net_margin", "FY2024"): Decimal(-2),
    ("free_cash_flow_growth", "FY2024->FY2025"): Decimal(50) / Decimal(120) * 100,   # 41.666...
    ("free_cash_flow_growth", "FY2025->FY2026"): Decimal(-230) / Decimal(170) * 100,  # -135.29...
    ("interest_bearing_debt_growth", "FY2025->FY2026"): Decimal(330) / Decimal(630) * 100,
    ("current_ratio", "FY2025"): Decimal(800) / Decimal(380),
}


@pytest.mark.parametrize("key,expected", EXPECTED_USD.items())
def test_usd_values_match_hand_computed(demo_run, key, expected):
    _, r = demo_run
    assert metric(r, *key).value == expected * M


@pytest.mark.parametrize("key,expected", EXPECTED_PCT.items())
def test_ratios_match_hand_computed(demo_run, key, expected):
    _, r = demo_run
    assert abs(metric(r, *key).value - expected) < TOL


def test_restatement_handled_transparently(demo_run):
    _, r = demo_run
    rep = r["validation"].report
    assert rep.fact("revenue", "FY2024").value == 1000 * M
    assert rep.fact("revenue", "FY2024").accession == "0009999901-26-000010"
    originals = [f for f in rep.alternative_facts if f.field == "revenue" and f.fiscal_period == "FY2024"
                 and f.raw_value == 1010 * M]
    assert originals and "superseded" in originals[0].status_reason
    restated = {(i.field, i.fiscal_period) for i in rep.issues if i.code == "restated_value"}
    assert restated == {("revenue", "FY2024"), ("gross_profit", "FY2024"), ("capital_expenditure", "FY2025")}


def test_quarter_inside_10k_rejected(demo_run):
    _, r = demo_run
    q4 = [f for f in r["validation"].report.rejected_candidates if f.raw_value == 320 * M]
    assert q4 and "not an annual duration" in q4[0].status_reason


def test_cutoff_before_amendment_uses_original_capex(settings, store):
    orch = Orchestrator(settings, store, lambda req: FixtureSource(DEMO_FIXTURES))
    run = orch.start(AnalysisRequest(identifier="DEMO", cutoff_date=date(2025, 9, 1), offline=True))
    r = load_results(store, run.id)
    assert [p.label for p in r["validation"].report.fiscal_periods] == ["FY2023", "FY2024", "FY2025"]
    assert metric(r, "capital_expenditure", "FY2025").value == 85 * M


def test_every_available_metric_has_lineage(demo_run):
    _, r = demo_run
    accepted = {f.id for f in r["validation"].report.accepted_facts}
    ids = {m.id for m in r["analysis"].metrics}
    for m in r["analysis"].metrics:
        if m.status != MetricStatus.AVAILABLE:
            continue
        assert m.formula
        assert m.input_fact_ids or m.input_metric_ids
        assert set(m.input_fact_ids) <= accepted
        assert set(m.input_metric_ids) <= ids


def test_flags_ranked(demo_run):
    _, r = demo_run
    assert [(f.rule, f.comparison) for f in r["analysis"].changes] == [
        ("net_income_transition", "FY2024->FY2025"),
        ("free_cash_flow_change", "FY2025->FY2026"),
        ("debt_increase", "FY2025->FY2026"),
        ("free_cash_flow_change", "FY2024->FY2025"),
        ("revenue_change", "FY2024->FY2025"),
        ("operating_margin_change", "FY2024->FY2025"),
    ]


def test_sources_marked_as_fixtures(demo_run, store):
    run, r = demo_run
    assert r["collection"].company_facts_document.is_fixture
    assert all(row[0] == 1 for row in store.query("SELECT is_fixture FROM sources WHERE run_id=?", (run.id,)))
