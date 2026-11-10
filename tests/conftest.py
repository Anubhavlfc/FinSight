"""Shared test helpers. All data here is SYNTHETIC."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from finsight.collector import CollectionResult, RawFactRow
from finsight.config import load_mappings, load_settings
from finsight.contracts import (
    AnalysisPlan,
    AnalysisRequest,
    Company,
    FiscalPeriod,
    SourceDocument,
    SourceKind,
    utcnow,
)
from finsight.sec.sources import FixtureSource
from finsight.storage import Store

REPO = Path(__file__).resolve().parents[1]
DEMO_FIXTURES = REPO / "fixtures" / "demo"
DEMO_CIK = "0009999901"


@pytest.fixture(autouse=True)
def _no_llm_credentials(request, monkeypatch):
    """Tests never call a real LLM (without credentials "auto" picks the offline stub), except
    opt-in tests marked ``llm``."""
    if request.node.get_closest_marker("llm"):
        return
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "FINSIGHT_ANTHROPIC_API_KEY", "FINSIGHT_LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def settings(tmp_path):
    return load_settings(home=tmp_path)


@pytest.fixture
def mappings():
    return load_mappings()


@pytest.fixture
def demo_source():
    return FixtureSource(DEMO_FIXTURES)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.sqlite3")
    yield s
    s.close()


def make_plan(ends=("2024-06-30", "2025-06-30", "2026-06-30"), cutoff="2026-11-01") -> AnalysisPlan:
    periods = [FiscalPeriod(label=f"FY{e[:4]}", end_date=date.fromisoformat(e),
                            defining_accession=f"0000000001-{e[2:4]}-000001",
                            defining_filing_date=date.fromisoformat(e)) for e in ends]
    req = AnalysisRequest(identifier="TEST", cutoff_date=date.fromisoformat(cutoff))
    return AnalysisPlan(request=req, company=Company(cik="0000000001", name="TEST CO (SYNTHETIC)"),
                        cutoff_date=req.cutoff_date, fiscal_periods=periods, required_fields=[],
                        metrics=[], documents=[])


def row(tag, end, val, start=None, accn="0000000001-26-000001", form="10-K", filed="2026-07-29",
        unit="USD", fy=None, fp="FY", taxonomy="us-gaap") -> RawFactRow:
    return RawFactRow(taxonomy=taxonomy, tag=tag, unit=unit, start=start, end=end, val=str(val), accn=accn,
                      fy=fy, fp=fp, form=form, filed=filed)


def annual(tag, fy, val, **kw) -> RawFactRow:
    """An annual duration row for a June fiscal year end."""
    return row(tag, f"{fy}-06-30", val, start=f"{fy - 1}-07-01", **kw)


def instant(tag, fy, val, **kw) -> RawFactRow:
    return row(tag, f"{fy}-06-30", val, **kw)


def collection(rows, latest_accession="0000000001-26-000001", contains_latest=True) -> CollectionResult:
    doc = SourceDocument(id="src:test", kind=SourceKind.COMPANY_FACTS, cik="0000000001", url="test://facts",
                         retrieved_at=utcnow(), sha256="0" * 64, is_fixture=True)
    return CollectionResult(rows=rows, company_facts_document=doc, contains_latest_accession=contains_latest,
                            latest_accession=latest_accession)
