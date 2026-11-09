"""Company resolution, scope checks and fiscal-period selection (SYNTHETIC fixtures)."""

from __future__ import annotations

from datetime import date

import pytest

from finsight.errors import AmbiguousCompanyError, CompanyNotFoundError, UnsupportedCompanyError
from finsight.planner import PlanningError, build_plan, select_fiscal_periods
from finsight.contracts import AnalysisRequest
from finsight.resolver import check_scope, resolve_company
from tests.conftest import DEMO_CIK


@pytest.mark.parametrize("ident", ["DEMO", "demo", "9999901", "0009999901", "Demo Analytics"])
def test_resolves_by_ticker_cik_and_name(ident, demo_source, settings):
    res = resolve_company(ident, demo_source, settings)
    assert res.company.cik == DEMO_CIK
    assert res.company.fiscal_year_end == "0630"
    assert all(d.is_fixture for d in res.documents)


def test_ambiguous_name_asks_for_clarification(demo_source, settings):
    with pytest.raises(AmbiguousCompanyError) as exc:
        resolve_company("Demo Synthetic", demo_source, settings)
    assert {c["cik"] for c in exc.value.candidates} == {"0009999901", "0009999903"}


def test_unknown_company(demo_source, settings):
    with pytest.raises(CompanyNotFoundError):
        resolve_company("Nonexistent Widgets", demo_source, settings)


def test_bank_is_rejected_with_explanation(demo_source, settings):
    with pytest.raises(UnsupportedCompanyError) as exc:
        resolve_company("SBNK", demo_source, settings)
    assert exc.value.profile == "bank_or_credit_institution"
    assert "net interest margin" in str(exc.value)


def test_foreign_private_issuer_rejected(settings):
    from finsight.contracts import Company
    sub = {"filings": {"recent": {"form": ["20-F", "6-K"]}}}
    with pytest.raises(UnsupportedCompanyError) as exc:
        check_scope(Company(cik="0000000002", name="FOREIGN CO (SYNTHETIC)", sic="3674"), sub, settings)
    assert exc.value.profile == "foreign_private_issuer"


def _rows(*specs):
    return [{"accessionNumber": a, "form": f, "filingDate": fd, "reportDate": rd, "primaryDocument": None}
            for a, f, fd, rd in specs]


ROWS = _rows(
    ("A22", "10-K", "2022-08-01", "2022-06-30"),
    ("A23", "10-K", "2023-07-31", "2023-06-30"),
    ("A24", "10-K", "2024-07-30", "2024-06-30"),
    ("A25", "10-K", "2025-07-30", "2025-06-30"),
    ("A25A", "10-K/A", "2025-10-15", "2025-06-30"),
    ("Q1", "10-Q", "2025-10-28", "2025-09-30"),
    ("A26", "10-K", "2026-07-29", "2026-06-30"),
)


def test_periods_come_from_report_dates_and_respect_cutoff():
    periods, _ = select_fiscal_periods(ROWS, date(2026, 11, 1), 3, None)
    assert [p.label for p in periods] == ["FY2024", "FY2025", "FY2026"]
    assert [p.defining_accession for p in periods] == ["A24", "A25", "A26"]
    # Before the FY2026 10-K was filed, the window ends at FY2025.
    periods, _ = select_fiscal_periods(ROWS, date(2026, 7, 28), 3, None)
    assert [p.label for p in periods] == ["FY2023", "FY2024", "FY2025"]


def test_amendment_does_not_create_an_extra_year():
    periods, _ = select_fiscal_periods(ROWS, date(2025, 12, 31), 3, None)
    assert [p.defining_accession for p in periods] == ["A23", "A24", "A25"]


def test_latest_fiscal_year_option():
    periods, _ = select_fiscal_periods(ROWS, date(2026, 11, 1), 3, 2025)
    assert [p.label for p in periods] == ["FY2023", "FY2024", "FY2025"]
    with pytest.raises(PlanningError):
        select_fiscal_periods(ROWS, date(2026, 11, 1), 3, 2030)


def test_short_history_is_noted():
    periods, notes = select_fiscal_periods(ROWS[:2], date(2026, 11, 1), 3, None)
    assert len(periods) == 2 and "Only 2" in notes[0]


def test_build_plan_lists_annual_documents(demo_source, settings, mappings):
    res = resolve_company("DEMO", demo_source, settings)
    req = AnalysisRequest(identifier="DEMO", cutoff_date=date(2026, 11, 1))
    plan = build_plan(req, res.company, res.submissions, demo_source, settings, mappings)
    assert [d.accession for d in plan.documents] == [
        "0009999901-24-000010", "0009999901-25-000010", "0009999901-25-000020", "0009999901-26-000010"]
    assert plan.documents[0].url.startswith("https://www.sec.gov/Archives/edgar/data/9999901/")
    assert "revenue" in plan.required_fields and "free_cash_flow" in plan.metrics
