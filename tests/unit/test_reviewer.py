"""Reviewer: deterministic checks catch tampered numbers, quotes, charts and causal claims (SYNTHETIC)."""

from __future__ import annotations

from datetime import date

import pytest

from finsight.contracts import AnalysisRequest, EvidenceStatus, MetricStatus
from finsight.orchestrator import Orchestrator, load_results
from finsight.review.reviewer import deterministic_checks, llm_review, review
from finsight.sec.sources import FixtureSource
from tests.conftest import DEMO_FIXTURES
from tests.llm_fakes import ScriptedProvider, tool_turn


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    from finsight.config import load_settings
    from finsight.storage import Store

    home = tmp_path_factory.mktemp("rev")
    store = Store(home / "db.sqlite3")
    run = Orchestrator(load_settings(home=home), store, lambda r: FixtureSource(DEMO_FIXTURES)).start(
        AnalysisRequest(identifier="DEMO", cutoff_date=date(2026, 11, 1), offline=True))
    r = load_results(store, run.id)
    yield r
    store.close()


def checks(r, draft=None, inv=None, metrics=None):
    return deterministic_checks(draft or r["draft"], metrics or r["analysis"].metrics,
                                inv or r["investigation"], is_fixture=True, llm_is_stub=True)


def codes(defects):
    return {d.code for d in defects}


def test_clean_demo_draft_passes(demo):
    assert checks(demo) == []
    latest = demo["review"].latest
    assert latest.passed and "llm_narrative_review: skipped (offline stub, no LLM available)" in latest.checks_run


def test_tampered_number_is_caught(demo):
    d = demo["draft"].model_copy(deep=True)
    c = next(c for c in d.claims if c.id == "claim:summary:revenue")
    c.text = c.text.replace("$1,239 million", "$1,293 million")
    assert "number_mismatch" in codes(checks(demo, draft=d))


def test_wrong_unit_is_caught(demo):
    d = demo["draft"].model_copy(deep=True)
    c = next(c for c in d.claims if "operating_margin_change" in c.id or "Operating margin moved" in c.text)
    c.text = c.text.replace("percentage points", "%")  # +4.5 pp written as +4.5%
    assert "number_mismatch" in codes(checks(demo, draft=d))


def test_altered_quote_is_caught_and_routed_to_investigation(demo):
    inv = demo["investigation"].model_copy(deep=True)
    f = next(f for f in inv.findings if f.quotes)
    f.quotes[0].quote = f.quotes[0].quote.replace("million", "billion", 1)
    defects = checks(demo, inv=inv)
    bad = [d for d in defects if d.code == "quote_not_verbatim"]
    assert bad and bad[0].route_to == "investigation" and bad[0].change_flag_id == f.change_flag_id


def test_number_in_explanation_must_come_from_quotes_or_metrics(demo):
    d = demo["draft"].model_copy(deep=True)
    c = next(c for c in d.claims if c.kind == "management_explanation")
    c.text += " Margins will expand by 7.5 percentage points next year."
    assert "explanation_number_unsupported" in codes(checks(demo, draft=d))


def test_unhedged_causal_interpretation_is_flagged(demo):
    from finsight.contracts import ReportClaim

    d = demo["draft"].model_copy(deep=True)
    base = dict(section="findings", kind="analyst_interpretation", evidence_status=EvidenceStatus.UNSUPPORTED)
    d.claims.append(ReportClaim(id="x1", text="Margins rose because of price increases.", **base))
    d.claims.append(ReportClaim(id="x2", text="Margins may have risen because of price increases.", **base))
    flagged = [x.claim_id for x in checks(demo, draft=d) if x.code == "unhedged_causal_claim"]
    assert flagged == ["x1"]


def test_chart_value_must_match_metric(demo):
    d = demo["draft"].model_copy(deep=True)
    d.charts[0].series["Revenue"][1] = 1190.0
    assert "chart_value_mismatch" in codes(checks(demo, draft=d))


def test_unavailable_metric_must_be_disclosed(demo):
    metrics = [m.model_copy() for m in demo["analysis"].metrics]
    for m in metrics:
        if m.id == "metric:current_ratio:FY2026":
            m.status, m.value, m.note = MetricStatus.UNAVAILABLE, None, "missing input"
    assert "unavailable_not_disclosed" in codes(checks(demo, metrics=metrics))


def test_unresolved_citation_marker_is_caught(demo):
    d = demo["draft"].model_copy(deep=True)
    d.markdown += "\nAn extra claim [P99]."
    assert "citation_marker_unresolved" in codes(checks(demo, draft=d))


def test_llm_review_can_only_lower_a_status(demo):
    inv = demo["investigation"]
    target = next(f for f in inv.findings if f.evidence_status == EvidenceStatus.SUPPORTED)
    other = next(f for f in inv.findings if f.evidence_status == EvidenceStatus.PARTIALLY_SUPPORTED)

    def script(purpose, context, content, n):
        return tool_turn(("submit_review", {"verdicts": [
            {"finding_id": target.id, "verdict": "partially_supported", "problems": ["covers only part"]},
            {"finding_id": other.id, "verdict": "supported", "problems": []},  # an upgrade: ignored
        ]}))

    defects, note = llm_review(demo["draft"], inv, ScriptedProvider(script))
    assert "1 verdict" not in note and "2 verdict(s)" in note
    assert len(defects) == 1
    d = defects[0]
    assert d.finding_id == target.id and d.suggested_status == EvidenceStatus.PARTIALLY_SUPPORTED
    assert d.route_to == "investigation" and d.source == "llm"


def test_review_reports_quality_stats(demo):
    r = review(demo["draft"], demo["analysis"].metrics, demo["investigation"], None, True, True, 0)
    assert r.passed and r.stats["numeric_defects"] == 0 and r.stats["citation_defects"] == 0
    assert 0 < r.stats["supported_claim_rate"] <= 1
