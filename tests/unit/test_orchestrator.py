"""Workflow states, recovery loops, caching across runs, and resume (SYNTHETIC data)."""

from __future__ import annotations

import json
import shutil
from datetime import date

import pytest

from finsight.contracts import AnalysisRequest, RunState, WorkflowRun
from finsight.errors import InvalidTransitionError, TransientSourceError
from finsight.orchestrator import Orchestrator, load_results
from finsight.sec.client import HttpResponse, SecHttpClient
from finsight.sec.sources import COMPANY_FACTS_URL, SUBMISSIONS_URL, TICKERS_URL, FixtureSource, HttpCache, LiveSecSource
from finsight.workflow import transition
from tests.conftest import DEMO_CIK, DEMO_FIXTURES

REQ = dict(cutoff_date=date(2026, 11, 1))


def test_illegal_transition_rejected():
    run = WorkflowRun(id="r", request=AnalysisRequest(identifier="X", **REQ))
    with pytest.raises(InvalidTransitionError):
        transition(run, RunState.APPROVED, "skip everything")


def test_ambiguous_company_needs_input_then_resumes(settings, store):
    orch = Orchestrator(settings, store, lambda r: FixtureSource(DEMO_FIXTURES))
    run = orch.start(AnalysisRequest(identifier="Demo Synthetic", offline=True, **REQ))
    assert run.state == RunState.NEEDS_INPUT and len(run.needs_input["candidates"]) == 2
    assert load_results(store, run.id)["collection"] is None  # nothing collected before clarification
    run = orch.resume(run.id, identifier="9999901")
    assert run.state == RunState.READY_FOR_REVIEW


def test_unsupported_company_fails_with_reason(settings, store):
    orch = Orchestrator(settings, store, lambda r: FixtureSource(DEMO_FIXTURES))
    run = orch.start(AnalysisRequest(identifier="SBNK", offline=True, **REQ))
    assert run.state == RunState.FAILED and "bank" in run.error


def test_approval_is_a_separate_step(settings, store):
    orch = Orchestrator(settings, store, lambda r: FixtureSource(DEMO_FIXTURES))
    run = orch.start(AnalysisRequest(identifier="DEMO", offline=True, **REQ))
    assert run.state == RunState.READY_FOR_REVIEW
    assert orch.approve(run.id, "checked").state == RunState.APPROVED


class FixtureBackedTransport:
    """Serves the SYNTHETIC fixture over a fake HTTP transport, counting requests."""

    def __init__(self, facts_override=None, fail_facts=0):
        self.calls: list[str] = []
        self.facts_override = facts_override
        self.fail_facts = fail_facts

    def __call__(self, url, headers, timeout):
        self.calls.append(url)
        if url == TICKERS_URL:
            return HttpResponse(200, (DEMO_FIXTURES / "company_tickers.json").read_bytes())
        if url == SUBMISSIONS_URL.format(cik=DEMO_CIK):
            return HttpResponse(200, (DEMO_FIXTURES / "submissions" / f"CIK{DEMO_CIK}.json").read_bytes())
        if url == COMPANY_FACTS_URL.format(cik=DEMO_CIK):
            if self.fail_facts:
                self.fail_facts -= 1
                return HttpResponse(503, b"")
            if self.facts_override:
                return HttpResponse(200, self.facts_override.pop(0))
            return HttpResponse(200, (DEMO_FIXTURES / "companyfacts" / f"CIK{DEMO_CIK}.json").read_bytes())
        return HttpResponse(404, b"")


def live_factory(settings, transport):
    def factory(req):
        client = SecHttpClient("FinSight tests test@example.com", settings.sec.model_copy(
            update={"max_requests_per_second": 1000, "backoff_base_seconds": 0.0}), transport=transport,
            sleep=lambda s: None)
        return LiveSecSource(client=client, cache=HttpCache(settings.cache_dir), settings=settings.sec)
    return factory


def test_repeat_run_reuses_cache(settings, store):
    transport = FixtureBackedTransport()
    orch = Orchestrator(settings, store, live_factory(settings, transport))
    first = orch.start(AnalysisRequest(identifier="DEMO", **REQ))
    n_first = len(transport.calls)
    second = orch.start(AnalysisRequest(identifier="DEMO", **REQ))
    assert first.state == second.state == RunState.READY_FOR_REVIEW
    assert n_first == 3  # tickers, submissions, company facts
    assert len(transport.calls) == n_first  # everything served from cache the second time
    assert second.stats["source_cache_hits"] == 3


def test_transient_failure_fails_then_resume_completes(settings, store):
    transport = FixtureBackedTransport(fail_facts=settings.sec.max_attempts)
    orch = Orchestrator(settings, store, live_factory(settings, transport))
    run = orch.start(AnalysisRequest(identifier="DEMO", **REQ))
    assert run.state == RunState.FAILED and run.failed_stage == RunState.COLLECTING
    assert "after 3 attempts" in run.error
    run = orch.resume(run.id)
    assert run.state == RunState.READY_FOR_REVIEW
    # Planning was not repeated on resume: the plan checkpoint was reused.
    assert transport.calls.count(TICKERS_URL) == 1


def test_stale_company_facts_trigger_one_bounded_refetch(settings, store):
    stale = json.loads((DEMO_FIXTURES / "companyfacts" / f"CIK{DEMO_CIK}.json").read_text())
    for concept in stale["facts"]["us-gaap"].values():
        concept["units"]["USD"] = [r for r in concept["units"]["USD"] if r["accn"] != "0009999901-26-000010"]
    body = json.dumps(stale).encode()
    transport = FixtureBackedTransport(facts_override=[body, body, body])
    orch = Orchestrator(settings, store, live_factory(settings, transport))
    run = orch.start(AnalysisRequest(identifier="DEMO", **REQ))
    facts_calls = transport.calls.count(COMPANY_FACTS_URL.format(cik=DEMO_CIK))
    assert facts_calls == 1 + settings.validation.max_recovery_attempts  # bounded
    assert run.state == RunState.PARTIAL
    issues = load_results(store, run.id)["validation"].report.issues
    assert any(i.code == "company_facts_stale" and i.severity.value == "blocking" for i in issues)
    states = [t.to_state for t in run.history]
    assert states.count(RunState.COLLECTING) == 2


def test_interrupted_run_resumes_from_checkpoint(settings, store, monkeypatch, tmp_path):
    fixtures = tmp_path / "fx"
    shutil.copytree(DEMO_FIXTURES, fixtures)
    source = FixtureSource(fixtures)
    orch = Orchestrator(settings, store, lambda r: source)

    import finsight.orchestrator as orch_mod
    real_validate = orch_mod.validate

    def crash(*a, **kw):
        raise KeyboardInterrupt("simulated crash during validation")

    monkeypatch.setattr(orch_mod, "validate", crash)
    with pytest.raises(KeyboardInterrupt):
        orch.start(AnalysisRequest(identifier="DEMO", offline=True, **REQ))
    run_id = store.list_runs()[0][0]
    interrupted = store.load_run(run_id)
    assert interrupted.state == RunState.VALIDATING
    assert set(store.checkpoint_stages(run_id)) == {"plan", "collection"}

    # Remove the facts fixture: resuming must not need to collect again.
    (fixtures / "companyfacts" / f"CIK{DEMO_CIK}.json").unlink()
    monkeypatch.setattr(orch_mod, "validate", real_validate)
    reads_before = source.stats["fixture_reads"]
    run = orch.resume(run_id)
    assert run.state == RunState.READY_FOR_REVIEW
    assert source.stats["fixture_reads"] == reads_before


def test_failed_run_records_stage(settings, store):
    def factory(req):
        class Broken(FixtureSource):
            def company_facts(self, *a, **kw):
                raise TransientSourceError("network down")
        return Broken(DEMO_FIXTURES)
    run = Orchestrator(settings, store, factory).start(AnalysisRequest(identifier="DEMO", offline=True, **REQ))
    assert run.state == RunState.FAILED and run.failed_stage == RunState.COLLECTING
