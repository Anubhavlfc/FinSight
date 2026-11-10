"""Real SEC EDGAR integration tests. Opt-in only.

    export FINSIGHT_SEC_USER_AGENT="FinSight research you@example.com"
    FINSIGHT_RUN_LIVE=1 pytest -m live

These assert structure and invariants, not specific figures, so they keep passing
as new filings arrive.
"""

from __future__ import annotations

import os
from datetime import date

import pytest

from finsight.cli import make_source_factory
from finsight.config import load_settings
from finsight.contracts import AnalysisRequest, MetricStatus, RunState
from finsight.orchestrator import Orchestrator, load_results
from finsight.storage import Store

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("FINSIGHT_RUN_LIVE") != "1" or not os.environ.get("FINSIGHT_SEC_USER_AGENT"),
                       reason="set FINSIGHT_RUN_LIVE=1 and FINSIGHT_SEC_USER_AGENT to run live SEC tests"),
]


@pytest.mark.parametrize("ticker", ["MSFT"])
def test_live_three_year_dataset(ticker, tmp_path):
    settings = load_settings(home=tmp_path)
    store = Store(tmp_path / "live.sqlite3")
    orch = Orchestrator(settings, store, make_source_factory(settings))
    run = orch.start(AnalysisRequest(identifier=ticker, cutoff_date=date.today()))
    assert run.state in (RunState.READY_FOR_REVIEW, RunState.PARTIAL), run.error
    r = load_results(store, run.id)
    periods = r["validation"].report.fiscal_periods
    assert len(periods) == 3
    for a, b in zip(periods, periods[1:]):
        assert 350 <= (b.end_date - a.end_date).days <= 380
    rev = [m for m in r["analysis"].metrics if m.name == "revenue"]
    assert all(m.status == MetricStatus.AVAILABLE and m.value > 0 for m in rev)
    assert not r["collection"].company_facts_document.is_fixture

    # Second run should reuse the cache for company facts.
    again = orch.start(AnalysisRequest(identifier=ticker, cutoff_date=date.today()))
    assert again.stats.get("source_cache_hits", 0) >= 1


@pytest.mark.parametrize("ticker", ["MSFT"])
def test_live_filing_text_investigation_and_report(ticker, tmp_path):
    """Stage 2 against real 10-Ks with the offline stub investigator (no LLM credentials needed)."""
    from finsight.contracts import SourceKind

    settings = load_settings(home=tmp_path)
    store = Store(tmp_path / "live.sqlite3")
    run = Orchestrator(settings, store, make_source_factory(settings)).start(
        AnalysisRequest(identifier=ticker, cutoff_date=date.today(), llm="stub"))
    assert run.state in (RunState.READY_FOR_REVIEW, RunState.PARTIAL), run.error
    r = load_results(store, run.id)
    text = r["filing_text"]
    sections = {p.section for p in text.passages}
    assert "item_7" in sections and any(s.startswith("item_8/note_") for s in sections), sections
    assert all(d.kind == SourceKind.FILING_DOCUMENT and d.accession and len(d.sha256) == 64
               for d in text.documents)
    assert all(q.verified for f in r["investigation"].findings for q in f.quotes)
    assert r["draft"] is not None and r["review"].latest.passed, r["review"].latest.defects


LLM_ENABLED = os.environ.get("FINSIGHT_RUN_LLM") == "1" and bool(os.environ.get("ANTHROPIC_API_KEY"))


@pytest.mark.llm
@pytest.mark.skipif(not LLM_ENABLED, reason="set FINSIGHT_RUN_LLM=1 and ANTHROPIC_API_KEY (makes paid API calls)")
def test_live_claude_investigation(tmp_path):
    """Real Claude investigation and review on two flagged changes. Costs a few cents to a dollar."""
    settings = load_settings(home=tmp_path)
    settings.investigation.max_findings = 2
    store = Store(tmp_path / "live.sqlite3")
    run = Orchestrator(settings, store, make_source_factory(settings)).start(
        AnalysisRequest(identifier="MSFT", cutoff_date=date.today(), llm="anthropic"))
    assert run.state in (RunState.READY_FOR_REVIEW, RunState.PARTIAL), run.error
    r = load_results(store, run.id)
    findings = r["investigation"].findings
    assert findings and all(f.produced_by.startswith("anthropic:") for f in findings)
    assert all(q.verified for f in findings for q in f.quotes)
    assert any(c.startswith("llm_narrative_review: ") and "verdict" in c for c in r["review"].latest.checks_run)
    assert run.stats["llm_usage"]["input_tokens"] > 0
