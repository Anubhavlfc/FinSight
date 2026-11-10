"""Stage 2 through the orchestrator: report files, revision cycles, resume, budgets (SYNTHETIC data)."""

from __future__ import annotations

import json
from datetime import date

from finsight.contracts import AnalysisRequest, EvidenceStatus, ReviewDefect, ReviewResult, RunState
from finsight.llm.provider import LLMTransientError
from finsight.llm.stub import OfflineStubProvider
from finsight.orchestrator import Orchestrator, load_results
from finsight.export import write_outputs
from finsight.sec.sources import FixtureSource
from tests.conftest import DEMO_FIXTURES
from tests.llm_fakes import ScriptedProvider, record, results_json, tool_turn

REQ = dict(identifier="DEMO", cutoff_date=date(2026, 11, 1), offline=True)
S = RunState


def orch(settings, store, provider_factory=None):
    src = FixtureSource(DEMO_FIXTURES)
    return Orchestrator(settings, store, lambda r: src, provider_factory)


def test_offline_demo_writes_traceable_report(settings, store, tmp_path):
    run = orch(settings, store).start(AnalysisRequest(**REQ))
    assert run.state == S.READY_FOR_REVIEW
    r = load_results(store, run.id)
    draft = r["draft"]
    passage_ids = {p.id for p in draft.passages}
    for c in draft.claims:
        if c.kind == "management_explanation":
            assert c.passage_ids and set(c.passage_ids) <= passage_ids
        if c.kind in ("numeric", "summary") and c.metric_ids:
            assert all(m.startswith("metric:") for m in c.metric_ids)
    files = {p.name for p in write_outputs(tmp_path / "out", run, r)}
    assert {"report.md", "report.html", "findings.json", "review.json", "evidence_passages.csv"} <= files
    assert {"revenue_profit.png", "margins.png", "cash_flow.png", "cash_debt.png"} <= files
    html = (tmp_path / "out" / "report.html").read_text()
    assert 'src="charts/' not in html and "data:image/png;base64," in html  # self-contained
    md = (tmp_path / "out" / "report.md").read_text()
    assert "SYNTHETIC FIXTURE DATA" in md and "OFFLINE STUB" in md and "not approved" in md
    assert run.stats["llm_usage"]["is_stub"] and run.stats["review"]["passed"]


def test_no_investigate_keeps_stage1_path(settings, store):
    run = orch(settings, store).start(AnalysisRequest(investigate=False, **REQ))
    assert [t.to_state for t in run.history][-2:] == [S.ANALYZING, S.READY_FOR_REVIEW]


def _llm_script(review_verdict="partially_supported"):
    """A model that quotes the capex sentence for every change and a reviewer that disputes one finding."""
    state = {"reviews": 0}

    def script(purpose, context, content, n):
        if purpose == "review":
            state["reviews"] += 1
            payload = json.loads(content.split("\n", 1)[1])
            target = next(f for f in payload if "Capital expenditure rose 233" in f["change"])
            return tool_turn(("submit_review", {"verdicts": [
                {"finding_id": target["finding_id"], "verdict": review_verdict, "problems": ["only partly"]}]}))
        if n == 0:
            return tool_turn(("search_filing_sections", {"query": "property and equipment increased",
                                                         "fiscal_period": "FY2026"}))
        if n == 1:
            hits = [h for r in results_json(content) for h in r.get("results", [])]
            return tool_turn(("read_source_passage", {"passage_id": hits[0]["passage_id"]}))
        pid = results_json(content)[0]["passage_id"]
        if "capex_change:FY2025->FY2026" not in purpose:
            return record()
        return record(management_explanation="Management attributes the increase to a new data centre.",
                      quotes=[{"passage_id": pid, "quote": "Additions to property and equipment increased $210 "
                                                           "million to $300 million"}],
                      proposed_evidence_status="supported")
    return script, state


def test_llm_review_downgrade_routes_back_then_converges(settings, store):
    script, state = _llm_script()
    provider = ScriptedProvider(script)
    run = orch(settings, store, lambda req: (provider, "scripted")).start(AnalysisRequest(**REQ))
    states = [t.to_state for t in run.history]
    # review 1 -> re-investigate the disputed change -> review 2 -> drafting repair -> review 3 passes
    assert states[4:] == [S.INVESTIGATING, S.DRAFTING, S.REVIEWING, S.INVESTIGATING, S.DRAFTING, S.REVIEWING,
                          S.DRAFTING, S.REVIEWING, S.READY_FOR_REVIEW]
    r = load_results(store, run.id)
    f = next(f for f in r["investigation"].findings if f.change_flag_id == "change:capex_change:FY2025->FY2026")
    assert f.evidence_status == EvidenceStatus.PARTIALLY_SUPPORTED  # lowered, never raised
    assert any(q.startswith("Reviewer:") for q in f.unresolved_questions)
    assert r["review"].latest.passed and len(r["review"].reviews) == 3
    assert run.stats["review"]["revision_cycles"] == 2
    assert run.stats["llm_usage"]["calls_by_purpose"]["review"] == 3


def test_revision_cycles_are_capped(settings, store, monkeypatch):
    import finsight.orchestrator as om

    def always_fail(draft, *a, cycle=0, **kw):
        cycle = a[5] if len(a) > 5 else cycle
        return ReviewResult(passed=False, revision_cycle=cycle, defects=[ReviewDefect(
            code="number_mismatch", message="stubborn", route_to="drafting", claim_id="claim:summary:revenue")])

    monkeypatch.setattr(om, "review", always_fail)
    run = orch(settings, store).start(AnalysisRequest(**REQ))
    states = [t.to_state for t in run.history]
    assert states.count(S.REVIEWING) == settings.investigation.max_revision_cycles + 1
    assert run.state == S.READY_FOR_REVIEW
    assert run.stats["review"]["needs_analyst_attention"] and run.stats["review"]["unresolved_defects"] == 1
    assert "analyst attention needed" in run.history[-1].reason
    draft = load_results(store, run.id)["draft"]
    assert any(c.id == "claim:summary:revenue" for c in draft.excluded_claims)
    assert "Removed during review" in draft.markdown


def test_llm_outage_fails_resumably_and_keeps_findings(settings, store):
    calls = {"n": 0}
    stub = OfflineStubProvider(settings.llm)

    class Flaky:
        name, is_stub, usage = stub.name, True, stub.usage

        def start(self, purpose, system, tools, context=None):
            calls["n"] += 1
            if calls["n"] == 3:
                raise LLMTransientError("overloaded")
            return stub.start(purpose, system, tools, context)

    o = orch(settings, store, lambda req: (Flaky(), "flaky"))
    run = o.start(AnalysisRequest(**REQ))
    assert run.state == S.FAILED and run.failed_stage == S.INVESTIGATING and "resume" in run.error
    kept = load_results(store, run.id)["investigation"]
    assert len(kept.findings) == 2
    run = o.resume(run.id)
    assert run.state == S.READY_FOR_REVIEW
    assert len(load_results(store, run.id)["investigation"].findings) == 8
    assert calls["n"] == 3 + 6  # the two finished findings were not redone


def test_budget_exhaustion_still_produces_a_report(settings, store):
    settings.llm.max_model_calls = 10
    run = orch(settings, store).start(AnalysisRequest(**REQ))
    assert run.state == S.READY_FOR_REVIEW
    r = load_results(store, run.id)
    assert r["investigation"].not_investigated
    assert "Not investigated:" in r["draft"].markdown and "stopped early" in r["draft"].markdown


def test_partial_run_still_investigates_and_ends_partial(settings, store, monkeypatch):
    import finsight.orchestrator as om
    from finsight.contracts import Severity, ValidationIssue

    real = om.validate

    def with_blocking(*a, **kw):
        out = real(*a, **kw)
        out.report.issues.append(ValidationIssue(id="issue:test", code="test", severity=Severity.BLOCKING,
                                                 message="synthetic blocking issue"))
        return out

    monkeypatch.setattr(om, "validate", with_blocking)
    run = orch(settings, store).start(AnalysisRequest(**REQ))
    assert run.state == S.PARTIAL and S.REVIEWING in [t.to_state for t in run.history]
    assert "synthetic blocking issue" in load_results(store, run.id)["draft"].markdown
