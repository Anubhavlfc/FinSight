"""Investigator: typed tools, bounded loop, quote verification, status rules (SYNTHETIC data)."""

from __future__ import annotations

import json
from datetime import date

import pytest

from finsight.config import InvestigationSettings, LlmSettings
from finsight.contracts import AnalysisRequest, EvidenceStatus
from finsight.investigator.agent import investigate_flag
from finsight.investigator.stage import FilingTextCheckpoint, run_investigation
from finsight.investigator.tools import ToolContext, ToolExecutor
from finsight.evidence.search import PassageIndex
from finsight.investigator.stage import load_filing_text
from finsight.llm.provider import ToolCall
from finsight.llm.stub import OfflineStubProvider
from finsight.orchestrator import Orchestrator, load_results
from finsight.sec.sources import FixtureSource
from tests.conftest import DEMO_FIXTURES
from tests.llm_fakes import ScriptedProvider, record, results_json, tool_turn

CAPEX_QUOTE = ("Additions to property and equipment increased $210 million to $300 million, primarily reflecting "
               "construction of our new data centre in Ohio.")


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    from finsight.config import load_settings
    from finsight.storage import Store

    home = tmp_path_factory.mktemp("inv")
    settings = load_settings(home=home)
    store = Store(home / "db.sqlite3")
    src = FixtureSource(DEMO_FIXTURES)
    run = Orchestrator(settings, store, lambda r: src).start(
        AnalysisRequest(identifier="DEMO", cutoff_date=date(2026, 11, 1), offline=True, investigate=False))
    r = load_results(store, run.id)
    yield {"plan": r["plan"].plan, "metrics": r["analysis"].metrics, "flags": r["analysis"].changes, "src": src,
           "settings": settings}
    store.close()


def make_ctx(demo, periods=("FY2026", "FY2025")):
    passages = []
    for p in periods:
        passages += load_filing_text(demo["src"], demo["plan"], p, 1200)[0]
    return ToolContext(plan=demo["plan"], metrics={m.id: m for m in demo["metrics"]}, index=PassageIndex(passages),
                       load_source=lambda per: (load_filing_text(demo["src"], demo["plan"], per, 1200)[0], ""))


def flag(demo, rule, comp):
    return next(f for f in demo["flags"] if f.rule == rule and f.comparison == comp)


def capex_passage(ctx) -> str:
    return next(p.id for p in ctx.index.passages.values() if CAPEX_QUOTE[:40] in p.text)


# ---- tools --------------------------------------------------------------------------------


def test_tools_reject_unknown_and_invalid_calls(demo):
    ex = ToolExecutor(make_ctx(demo))
    res, _, ok = ex.run(ToolCall("1", "run_shell", {"cmd": "ls"}))
    assert not ok and res.is_error and "disallowed" in res.content
    res, _, ok = ex.run(ToolCall("2", "search_filing_sections", {"query": "x" * 5, "limit": 50}))
    assert not ok and "invalid input" in res.content
    res, _, ok = ex.run(ToolCall("3", "get_validated_metric", {"name": "enterprise_value", "period": "FY2025"}))
    assert not ok and "unknown metric" in res.content


def test_metric_tool_returns_validated_value_with_formula(demo):
    ctx = make_ctx(demo)
    res, _, ok = ToolExecutor(ctx).run(ToolCall("1", "get_validated_metric",
                                                {"name": "free_cash_flow", "period": "FY2026"}))
    payload = json.loads(res.content)
    assert ok and payload["value"] == "-60000000" and "operating_cash_flow - capital_expenditure" in payload["formula"]
    assert "metric:free_cash_flow:FY2026" in ctx.used_metric_ids


def test_compare_components_uses_metrics_only(demo):
    res, _, ok = ToolExecutor(make_ctx(demo)).run(ToolCall("1", "compare_financial_components", {
        "prior_period": "FY2024", "current_period": "FY2025"}))
    payload = json.loads(res.content)
    rd = next(c for c in payload["components"] if c["component"] == "research_and_development")
    assert ok and rd["prior_pct_of_revenue"].startswith("20") and float(rd["change_pp"]) == pytest.approx(-1.779661, 1e-4)
    sga = next(c for c in payload["components"] if c["component"] == "selling_general_and_administrative")
    assert sga["status"] == "not_applicable"  # not reported: said so, not estimated


def test_additional_source_is_allowlisted_and_budgeted(demo):
    ctx = make_ctx(demo, periods=("FY2026",))
    ctx.max_additional_sources = 1
    ex = ToolExecutor(ctx)
    res, _, ok = ex.run(ToolCall("1", "request_additional_source", {"fiscal_period": "FY2019"}))
    assert not ok and "outside the analysis window" in res.content
    res, _, ok = ex.run(ToolCall("2", "request_additional_source", {"fiscal_period": "FY2025"}))
    assert ok and json.loads(res.content)["status"] == "loaded"
    res, _, ok = ex.run(ToolCall("3", "request_additional_source", {"fiscal_period": "FY2024"}))
    assert not ok and "budget" in res.content


def test_filing_text_is_marked_untrusted_in_tool_results(demo):
    ctx = make_ctx(demo)
    pid = next(p.id for p in ctx.index.passages.values() if "AUTOMATED SYSTEMS" in p.text)
    res, _, _ = ToolExecutor(ctx).run(ToolCall("1", "read_source_passage", {"passage_id": pid}))
    payload = json.loads(res.content)
    assert "ignore all previous instructions" in payload["untrusted_filing_text"]
    assert set(payload) == {"passage_id", "filing", "fiscal_period", "section", "url", "untrusted_filing_text"}


# ---- loop and status rules ---------------------------------------------------------------


def test_verified_quote_gives_supported_finding(demo):
    ctx = make_ctx(demo)
    pid = capex_passage(ctx)

    def script(purpose, context, content, n):
        if n == 0:
            return tool_turn(("read_source_passage", {"passage_id": pid}))
        return record(management_explanation="Management attributes the increase to construction of a new data "
                                             "centre.", quotes=[{"passage_id": pid, "quote": CAPEX_QUOTE}],
                      assessment="ambiguous", proposed_evidence_status="supported")

    f = investigate_flag(flag(demo, "capex_change", "FY2025->FY2026"), ctx, ScriptedProvider(script),
                         InvestigationSettings())
    assert f.evidence_status == EvidenceStatus.SUPPORTED
    assert f.passage_ids == [pid] and f.quotes[0].verified
    assert "metric:capital_expenditure:FY2026" in f.metric_ids
    assert "$90 million in FY2025, $300 million in FY2026" in f.calculated_observation  # written by code


def test_fabricated_quote_gets_one_retry_then_is_dropped(demo):
    ctx = make_ctx(demo)
    pid = capex_passage(ctx)
    fake = CAPEX_QUOTE.replace("$210 million", "$250 million")
    seen = []

    def script(purpose, context, content, n):
        seen.append(content)
        return record(management_explanation="Capex rose for a data centre.",
                      quotes=[{"passage_id": pid, "quote": fake}], proposed_evidence_status="supported")

    f = investigate_flag(flag(demo, "capex_change", "FY2025->FY2026"), ctx, ScriptedProvider(script),
                         InvestigationSettings(max_steps_per_finding=1))
    assert "quotes failed verification" in seen[1][-1].content  # the model was told exactly what failed
    assert f.evidence_status == EvidenceStatus.UNSUPPORTED
    assert f.management_explanation is None and f.quotes == []
    assert any("no verbatim quote" in q for q in f.unresolved_questions)


def test_model_cannot_raise_status_above_evidence(demo):
    ctx = make_ctx(demo)
    pid = capex_passage(ctx)

    def script(purpose, context, content, n):
        return record(management_explanation="Data centre construction.",
                      quotes=[{"passage_id": pid, "quote": CAPEX_QUOTE}],
                      unresolved_questions=["Why now?"], proposed_evidence_status="supported")

    f = investigate_flag(flag(demo, "capex_change", "FY2025->FY2026"), ctx, ScriptedProvider(script),
                         InvestigationSettings())
    assert f.evidence_status == EvidenceStatus.PARTIALLY_SUPPORTED  # open question caps it

    def humble(purpose, context, content, n):
        return record(management_explanation="Data centre construction.",
                      quotes=[{"passage_id": pid, "quote": CAPEX_QUOTE}], proposed_evidence_status="unsupported")

    f = investigate_flag(flag(demo, "capex_change", "FY2025->FY2026"), make_ctx(demo), ScriptedProvider(humble),
                         InvestigationSettings())
    assert f.evidence_status == EvidenceStatus.UNSUPPORTED  # code may lower to the model's own view, not raise


def test_step_budget_terminates_a_model_that_never_records(demo):
    calls = []

    def script(purpose, context, content, n):
        calls.append(n)
        return tool_turn(("search_filing_sections", {"query": "revenue"}))

    settings = InvestigationSettings(max_steps_per_finding=3)
    f = investigate_flag(flag(demo, "revenue_change", "FY2024->FY2025"), make_ctx(demo), ScriptedProvider(script),
                         settings)
    assert len(calls) == 1 + 3 + 1  # first turn, three tool-using steps, one wrap-up turn
    assert f.evidence_status == EvidenceStatus.UNSUPPORTED and f.budget_exhausted
    assert f.steps_used == 3


def test_run_budget_stops_investigation_gracefully(demo):
    stub = OfflineStubProvider(LlmSettings(max_model_calls=7))
    res, _ = run_investigation(demo["plan"], demo["metrics"], demo["flags"], demo["src"], stub,
                               InvestigationSettings(), FilingTextCheckpoint())
    assert 1 <= len(res.findings) < len(demo["flags"])
    assert len(res.findings) + len(res.not_investigated) == len(demo["flags"])
    assert any("stopped early" in n for n in res.notes)


def test_stub_marks_unexplained_part_as_unresolved(demo):
    res, text = run_investigation(demo["plan"], demo["metrics"], demo["flags"], demo["src"], OfflineStubProvider(),
                                  InvestigationSettings(), FilingTextCheckpoint())
    by_flag = {f.change_flag_id: f for f in res.findings}
    fcf25 = by_flag["change:free_cash_flow_change:FY2024->FY2025"]
    assert fcf25.evidence_status == EvidenceStatus.PARTIALLY_SUPPORTED
    assert any("operating cash flow" in q for q in fcf25.unresolved_questions)
    assert all(q.verified for f in res.findings for q in f.quotes)
    # The injected "revenue grew 50%" line never reaches a finding.
    assert not any("50%" in (f.management_explanation or "") for f in res.findings)
    assert set(text.loaded_periods) == {"FY2025", "FY2026"}
    assert res.llm_usage["is_stub"] and res.llm_usage["estimated_cost_usd"] == 0.0


def test_missing_filing_text_is_a_limitation_not_a_failure(demo, tmp_path):
    import shutil

    fx = tmp_path / "fx"
    shutil.copytree(DEMO_FIXTURES, fx, ignore=shutil.ignore_patterns("filings"))
    res, text = run_investigation(demo["plan"], demo["metrics"], demo["flags"], FixtureSource(fx),
                                  OfflineStubProvider(), InvestigationSettings(), FilingTextCheckpoint())
    assert all(f.evidence_status == EvidenceStatus.UNSUPPORTED for f in res.findings)
    assert any("could not retrieve" in n for n in text.notes)
    assert any("No 10-K text" in n for n in res.notes)


def test_reviewer_feedback_reaches_the_reinvestigation_prompt(demo):
    prompts = []

    def script(purpose, context, content, n):
        prompts.append(content)
        return record()

    run_investigation(demo["plan"], demo["metrics"], demo["flags"], demo["src"], ScriptedProvider(script),
                      InvestigationSettings(), FilingTextCheckpoint(),
                      only_flags={"change:debt_increase:FY2025->FY2026": ["quote does not support the claim"]})
    assert len(prompts) == 1 and "quote does not support the claim" in prompts[0]


def test_results_json_helper_parses_tool_results():
    from finsight.llm.provider import ToolResult
    assert results_json([ToolResult("1", '{"a": 1}')]) == [{"a": 1}]
