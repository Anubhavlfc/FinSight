"""The Streamlit dashboard. Everything runs offline on the SYNTHETIC fixture company."""

from __future__ import annotations

import time

import pytest

pytest.importorskip("streamlit")

import streamlit as st  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

from finsight.contracts import RunState as S  # noqa: E402
from finsight.dashboard.service import DashboardError, DashboardService  # noqa: E402
from finsight.errors import InvalidTransitionError  # noqa: E402
from finsight.orchestrator import Orchestrator  # noqa: E402
from tests.conftest import REPO  # noqa: E402

APP = str(REPO / "finsight" / "dashboard" / "app.py")


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    monkeypatch.delenv("FINSIGHT_SEC_USER_AGENT", raising=False)
    monkeypatch.setenv("FINSIGHT_HOME", str(tmp_path))
    st.cache_resource.clear()
    yield
    st.cache_resource.clear()


@pytest.fixture
def svc(settings):
    service = DashboardService(settings)
    yield service
    service.shutdown()


def wait(svc: DashboardService, run_id: str, timeout: float = 30.0) -> None:
    end = time.time() + timeout
    while svc.jobs.is_active(run_id):
        assert time.time() < end, "run did not finish"
        time.sleep(0.1)


def demo_run(svc: DashboardService, question: str | None = None) -> str:
    profile = svc.profile("DEMO")
    run_id = svc.start_run(profile, None, question, use_claude=True)
    wait(svc, run_id)
    return run_id


# --------------------------------------------------------------------------- service


def test_without_live_access_only_the_sample_is_offered_and_real_tickers_are_refused(svc):
    avail = svc.availability()
    assert not avail.live_data and "FINSIGHT_SEC_USER_AGENT" in avail.live_reason
    hits, note = svc.search("demo")
    assert [h.ticker for h in hits] == ["DEMO"] and hits[0].synthetic
    assert note and "only the sample" in note
    with pytest.raises(DashboardError, match="FINSIGHT_SEC_USER_AGENT"):
        svc.profile("MSFT")


def test_demo_run_goes_through_every_stage_and_the_result_is_traceable(svc):
    run_id = demo_run(svc, "Why did net income change?")
    status = svc.status(run_id)
    assert status.phase == "ready_for_review" and status.review.state == "awaiting_review"
    assert all(stage.state == "done" for stage in status.stages)
    res = svc.result(run_id)
    assert res.overview.data_origin == "synthetic_fixture" and res.status.question == "Why did net income change?"
    assert res.changes and res.charts and res.key_metrics
    metric_ids = {c.metric_id for c in res.calculations}
    passage_ids = {p.id for p in res.passages}
    for change in res.changes:
        assert set(change.metric_ids) <= metric_ids
        if change.finding:
            assert {q.passage_id for q in change.finding.quotes} <= passage_ids
    # The sample never uses Claude, even when asked to.
    assert status.investigator.is_rule_based


def test_identical_request_while_running_reuses_the_run(svc):
    profile = svc.profile("DEMO")
    a = svc.start_run(profile, None, None, use_claude=False)
    b = svc.start_run(profile, None, None, use_claude=False)
    wait(svc, a)
    assert a == b or svc.status(b).phase == "ready_for_review"


def test_unavailable_period_is_refused(svc):
    with pytest.raises(DashboardError, match="No Form 10-K"):
        svc.start_run(svc.profile("DEMO"), 1999, None, use_claude=False)


def full_checks(svc: DashboardService, run_id: str) -> dict:
    ids = [c.id for c in svc.result(run_id).changes if c.finding is not None]
    return {"findings": {i: "Confirmed" for i in ids}, "figures_checked": True, "limitations_read": True}


def test_review_needs_a_name_and_changes_requested_keeps_the_report_unapproved(svc):
    run_id = demo_run(svc)
    first = next(iter(full_checks(svc, run_id)["findings"]))
    with pytest.raises(DashboardError, match="name"):
        svc.record_review(run_id, "approved", "  ", "", {})
    with pytest.raises(DashboardError, match="Unknown change"):
        svc.record_review(run_id, "changes_requested", "Dana", "", {"findings": {"x": "Disputed"}})
    svc.record_review(run_id, "changes_requested", "Dana", "Quote for FY2025 is from the wrong section",
                      {"findings": {first: "Disputed"}})
    run = svc.load_run(run_id)
    assert run.state == S.READY_FOR_REVIEW
    [entry] = svc.human_reviews(run_id)
    assert entry["decision"] == "changes_requested" and entry["reviewer"] == "Dana"
    assert entry["checks"]["findings"][first] == "Disputed"
    assert svc.status(run_id).review.state == "changes_requested"


def test_approval_is_recorded_once_and_is_final(svc):
    run_id = demo_run(svc)
    svc.record_review(run_id, "changes_requested", "Dana", "first pass", {})
    with pytest.raises(DashboardError, match="To approve"):
        svc.record_review(run_id, "approved", "Dana", "", {"figures_checked": True})
    svc.record_review(run_id, "approved", "Dana", "checked against the 10-K", full_checks(svc, run_id))
    run = svc.load_run(run_id)
    assert run.state == S.APPROVED
    assert [h["decision"] for h in svc.human_reviews(run_id)] == ["changes_requested", "approved"]
    assert svc.status(run_id).review.approval_note == "checked against the 10-K"
    with pytest.raises(DashboardError, match="APPROVED"):
        svc.record_review(run_id, "approved", "Dana", "", {})


def test_orchestrator_refuses_unknown_decisions_and_unfinished_runs(settings, store, demo_source):
    from datetime import date

    from finsight.contracts import AnalysisRequest

    orch = Orchestrator(settings, store, lambda req: demo_source)
    run = orch.create(AnalysisRequest(identifier="DEMO", cutoff_date=date(2026, 11, 1), offline=True))
    with pytest.raises(InvalidTransitionError):
        orch.record_review(run.id, "approved", "Dana")
    with pytest.raises(ValueError):
        orch.record_review(run.id, "maybe", "Dana")


def test_downloads_are_limited_to_the_run_files(svc):
    run_id = demo_run(svc)
    _, items = svc.downloads(run_id)
    names = {d.name for d in items}
    assert {"report.html", "metrics.csv", "findings.json"} <= names
    assert svc.read_file(run_id, "metrics.csv").splitlines()[0].startswith(b"metric_id,")
    with pytest.raises(DashboardError):
        svc.read_file(run_id, "../../etc/passwd")


# --------------------------------------------------------------------------- app


def test_home_page_renders_and_explains_live_access():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert any("FINSIGHT_SEC_USER_AGENT" in i.value for i in at.info)
    assert at.title[0].value == "FinSight"


def test_run_from_the_sidebar_then_review_and_approve():
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.text_input(key="company_query").input("DEMO").run()
    next(b for b in at.button if b.label == "Run analysis").click().run()
    assert not at.exception
    run_id = at.query_params["run"]
    run_id = run_id[0] if isinstance(run_id, list) else run_id

    deadline = time.time() + 30
    while time.time() < deadline:
        at.run()
        if at.tabs:
            break
        time.sleep(0.3)
    assert not at.exception
    assert [t.label for t in at.tabs][:2] == ["Summary", "Metrics & charts"]
    assert any("Synthetic sample" in w.value for w in at.warning)

    approve = next(b for b in at.button if b.label == "Approve report")
    assert approve.disabled  # nothing checked yet
    for ctl in at.get("button_group"):
        ctl.set_value("Confirmed")
    at.run()
    for cb in at.checkbox:
        cb.check()
    at.text_input(key=f"rv_{run_id}_name").input("Test Reviewer")
    at.text_area(key=f"rv_{run_id}_note").input("Checked against the filings")
    at.run()
    approve = next(b for b in at.button if b.label == "Approve report")
    assert not approve.disabled
    approve.click().run()
    assert not at.exception
    assert any("approved" in t.label for t in at.tabs)
    assert any("The report is final" in x.value for x in at.success)
