"""Single-prompt baseline and the comparison with the agentic run (SYNTHETIC fixture, scripted model)."""

from __future__ import annotations

from datetime import date

import pytest

from finsight.cli import main
from finsight.contracts import AnalysisRequest, EvidenceStatus
from finsight.evaluation.compare import evaluate, to_markdown, write
from finsight.orchestrator import Orchestrator, load_results
from finsight.sec.sources import FixtureSource
from tests.conftest import DEMO_FIXTURES
from tests.llm_fakes import ScriptedProvider, tool_turn


@pytest.fixture
def results(settings, store):
    orch = Orchestrator(settings, store, lambda req: FixtureSource(DEMO_FIXTURES))
    run = orch.start(AnalysisRequest(identifier="DEMO", cutoff_date=date(2026, 11, 1), offline=True))
    return load_results(store, run.id)


def sentence_from(text: str) -> str:
    """A verbatim span of a passage, long enough to pass verification."""
    s = " ".join(text.split())
    return s[:120]


def test_baseline_is_scored_by_the_same_code_as_the_agentic_run(results):
    findings = results["investigation"].findings
    passages = {p.id: p for p in results["filing_text"].passages}
    seen: dict = {}

    def script(purpose, context, content, n):
        assert purpose == "baseline" and n == 0  # exactly one call
        seen["prompt"] = content
        ex = context["excerpts"]
        ids = list(ex)
        good, fabricated = ids[0], ids[1]
        p = passages[ex[good][0]]
        return tool_turn(("record_findings", {"findings": [
            {"change_id": good, "management_explanation": "Management attributes the change to the quoted factors.",
             "quotes": [{"passage_id": p.id, "quote": sentence_from(p.text)}],
             "proposed_evidence_status": "supported"},
            {"change_id": fabricated, "management_explanation": "Revenue rose 99.9% because of a new product.",
             "quotes": [{"passage_id": ex[fabricated][0], "quote": "This sentence does not appear in the filing at all."}],
             "proposed_evidence_status": "supported"},
            {"change_id": "change:not_a_real_one", "quotes": []},
        ]}))

    provider = ScriptedProvider(script)
    ev = evaluate(results, provider, per_flag=4)
    flag_ids = [f.change_flag_id for f in findings]
    b = ev["baseline"]
    assert b["changes"] == len(flag_ids) and b["model_calls"] == 1
    assert "unknown change IDs" in b["errors"][0]
    good, fabricated = flag_ids[0], flag_ids[1]
    assert b["per_change"][good]["status"] == "supported" and b["per_change"][good]["quotes_verified"] == 1
    # A fabricated quote is dropped, so the explanation is not reported and the status is unsupported.
    fab = b["per_change"][fabricated]
    assert fab["status"] == "unsupported" and fab["quotes_proposed"] == 1 and fab["quotes_verified"] == 0
    assert fab["management_explanation"] is None
    assert b["quote_failure_rate"] == 0.5
    # Changes the model did not answer are unsupported, never silently dropped.
    assert all(b["per_change"][c]["status"] == "unsupported" for c in flag_ids[2:])
    # The prompt holds the calculated observations and the excerpts, nothing invented.
    assert all(cid in seen["prompt"] for cid in flag_ids) and "untrusted filing text" in seen["prompt"]

    a = ev["agentic"]
    assert a["changes"] == len(flag_ids)
    assert a["explained"] == sum(1 for f in findings if f.management_explanation and f.quotes)
    assert a["evidence_status"]["supported"] == sum(1 for f in findings
                                                    if f.evidence_status == EvidenceStatus.SUPPORTED)
    md = to_markdown(ev)
    assert "| Explained with a verified quote |" in md and good in md


def test_baseline_without_a_tool_call_marks_every_change_unsupported(results):
    from finsight.llm.provider import LLMTurn

    provider = ScriptedProvider(lambda *a: LLMTurn(text="I think revenue grew.", tool_calls=[], stop_reason="end_turn"))
    ev = evaluate(results, provider)
    assert ev["baseline"]["explained"] == 0 and ev["baseline"]["errors"] == ["the model did not call record_findings"]


def test_explanation_numbers_outside_quotes_are_counted(results):
    passages = {p.id: p for p in results["filing_text"].passages}

    def script(purpose, context, content, n):
        cid, ids = next(iter(context["excerpts"].items()))
        p = passages[ids[0]]
        return tool_turn(("record_findings", {"findings": [
            {"change_id": cid, "management_explanation": "Management says sales grew by $4,321 million.",
             "quotes": [{"passage_id": p.id, "quote": sentence_from(p.text)}], "proposed_evidence_status": "supported"},
        ]}))

    ev = evaluate(results, ScriptedProvider(script))
    assert ev["baseline"]["unsupported_explanation_numbers"] == 1


def test_evaluation_needs_an_investigated_run(settings, store):
    orch = Orchestrator(settings, store, lambda req: FixtureSource(DEMO_FIXTURES))
    run = orch.start(AnalysisRequest(identifier="DEMO", cutoff_date=date(2026, 11, 1), offline=True,
                                     investigate=False))
    with pytest.raises(ValueError, match="no investigation"):
        evaluate(load_results(store, run.id), ScriptedProvider(lambda *a: None))


def test_cli_refuses_without_claude(tmp_path, capsys, results, store, monkeypatch):
    run_id = next(iter(store.list_runs()))[0]
    assert main(["--db", str(store.path), "evaluate", run_id]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_write_produces_json_and_markdown(results, tmp_path):
    ev = evaluate(results, ScriptedProvider(lambda *a: tool_turn(("record_findings", {"findings": []}))))
    files = write(ev, tmp_path)
    assert [f.name for f in files] == ["evaluation.json", "evaluation.md"]
