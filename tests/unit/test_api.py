"""The web API over the engine. Everything runs offline on the SYNTHETIC fixture company."""

from __future__ import annotations

import json
import threading
import time
from datetime import date

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from finsight.api import schemas as s  # noqa: E402
from finsight.api.app import create_app  # noqa: E402
from finsight.api.settings import ApiSettings  # noqa: E402
from finsight.contracts import AnalysisRequest  # noqa: E402
from finsight.errors import TransientSourceError  # noqa: E402
from finsight.orchestrator import Orchestrator  # noqa: E402
from finsight.sec.sources import FixtureSource  # noqa: E402
from finsight.storage import Store  # noqa: E402
from tests.conftest import DEMO_CIK, DEMO_FIXTURES, REPO  # noqa: E402

FINISHED = ("ready_for_review", "partial", "approved", "failed", "needs_input", "interrupted")


@pytest.fixture(autouse=True)
def _no_live_sec(monkeypatch):
    monkeypatch.delenv("FINSIGHT_SEC_USER_AGENT", raising=False)


def make_client(settings, **kw) -> TestClient:
    api = kw.pop("api", None) or ApiSettings(web_dist=None)
    return TestClient(create_app(settings, api, **kw), raise_server_exceptions=False)


@pytest.fixture
def client(settings):
    with make_client(settings) as c:
        yield c


def wait_for(client: TestClient, run_id: str, phases=FINISHED, timeout=30.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/runs/{run_id}").json()
        if body["phase"] in phases and not (body["phase"] in FINISHED and body["is_active"]):
            return body
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not reach {phases}: {body['phase']}")


def wait_until(client: TestClient, run_id: str, predicate, timeout=30.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/runs/{run_id}").json()
        if predicate(body):
            return body
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} never matched: {body['state']}")


def start_demo(client: TestClient, **extra) -> str:
    r = client.post("/api/runs", json={"identifier": "DEMO", **extra})
    assert r.status_code == 202, r.text
    return r.json()["run_id"]


class GatedSource(FixtureSource):
    """A fixture source whose facts download waits for the test, to hold a run in COLLECTING."""

    def __init__(self, gate: threading.Event, fail: threading.Event | None = None):
        super().__init__(DEMO_FIXTURES)
        self._gate, self._fail = gate, fail

    def company_facts(self, cik, must_include_accession=None, force_refresh=False):
        assert self._gate.wait(timeout=20), "test never released the gate"
        if self._fail is not None and self._fail.is_set():
            raise TransientSourceError("simulated outage")
        return super().company_facts(cik, must_include_accession, force_refresh)


# ---- service and lookups -----------------------------------------------------------------

def test_health_reports_what_this_server_can_do(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["live_data"]["available"] is False and "not configured" in body["live_data"]["reason"]
    assert body["investigator"] == {**body["investigator"], "claude_available": False, "default": "rule_based",
                                    "model": None}
    assert body["approval"] == "open"
    assert body["limits"]["years"] == 3


def test_api_responses_are_not_cached_and_carry_security_headers(client):
    r = client.get("/api/health")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_search_without_live_access_lists_only_sample_companies_and_says_so(client):
    body = client.get("/api/companies/search", params={"q": "demo"}).json()
    assert body["live_search"] is False and "sample companies" in body["note"]
    assert [h["ticker"] for h in body["results"]] == ["DEMO"]
    assert body["results"][0]["synthetic"] is True and body["results"][0]["cik"] == DEMO_CIK


def test_search_rejects_empty_and_oversized_queries(client):
    assert client.get("/api/companies/search", params={"q": ""}).status_code == 422
    assert client.get("/api/companies/search", params={"q": "x" * 81}).status_code == 422


def test_profile_offers_three_year_windows_defaulting_to_latest_completed_year(client):
    body = client.get("/api/companies/DEMO").json()
    assert body["supported"] is True and body["synthetic"] is True
    assert body["default_fiscal_year"] == 2026
    latest = body["fiscal_years"][0]
    assert latest["fiscal_year"] == 2026 and latest["window"] == ["FY2024", "FY2025", "FY2026"]
    assert latest["complete"] is True
    # Older years with a shorter history are offered honestly, marked as incomplete windows.
    assert all(o["complete"] == (len(o["window"]) == 3) for o in body["fiscal_years"])


def test_unsupported_company_is_explained_with_next_steps(client):
    body = client.get("/api/companies/SBNK").json()
    assert body["supported"] is False
    assert "net interest margin" in body["unsupported_reason"]
    assert body["next_steps"]
    r = client.post("/api/runs", json={"identifier": "SBNK"})
    assert r.status_code == 422
    err = r.json()
    assert err["code"] == "unsupported_company" and err["next_steps"] and err["fields"] == {"identifier": "unsupported company"}


def test_real_ticker_without_live_access_is_refused_not_substituted(client):
    """No silent fallback to demo figures when live data is unavailable."""
    for r in (client.get("/api/companies/MSFT"), client.post("/api/runs", json={"identifier": "MSFT"})):
        assert r.status_code == 503
        assert r.json()["code"] == "live_unavailable"
    assert client.get("/api/runs").json()["runs"] == []


# ---- request validation ------------------------------------------------------------------

@pytest.mark.parametrize("payload, field", [
    ({"identifier": ""}, "identifier"),
    ({"identifier": "<script>alert(1)</script>"}, "identifier"),
    ({"identifier": "DEMO", "question": "x" * 401}, "question"),
    ({"identifier": "DEMO", "investigator": "gpt"}, "investigator"),
    ({"identifier": "DEMO", "num_years": 5}, "num_years"),  # unknown fields are refused, not ignored
])
def test_invalid_requests_name_the_field(client, payload, field):
    r = client.post("/api/runs", json=payload)
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "invalid_input" and field in body["fields"]


def test_unavailable_period_lists_the_years_that_exist(client):
    r = client.post("/api/runs", json={"identifier": "DEMO", "latest_fiscal_year": 2031})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "period_unavailable" and "FY2026" in body["next_steps"][0]


def test_unknown_run_and_unknown_api_path_return_json_404(client):
    for path in ("/api/runs/run-20260101-000000-abcdef", "/api/runs/..%2F..%2Fetc", "/api/nope"):
        r = client.get(path)
        assert r.status_code == 404 and r.json()["code"] in ("run_not_found", "not_found", "http_error")


# ---- the run lifecycle -------------------------------------------------------------------

def test_demo_run_reports_real_stages_and_produces_the_dashboard(client):
    run_id = start_demo(client, question="Why did free cash flow fall?")
    status = wait_for(client, run_id)
    assert status["phase"] == "ready_for_review" and status["has_results"] and status["has_report"]
    assert [st["key"] for st in status["stages"]] == [
        "planning", "collecting", "validating", "calculating", "investigating", "writing", "reviewing", "ready"]
    assert all(st["state"] == "done" for st in status["stages"])
    assert status["synthetic"] is True and status["investigator"]["is_rule_based"] is True
    assert status["review"]["state"] == "awaiting_review"
    assert status["activity"], "the activity feed comes from recorded state transitions"

    result = client.get(f"/api/runs/{run_id}/result").json()
    s.RunResult.model_validate(result)  # the payload matches the published contract
    ov = result["overview"]
    assert ov["company"]["ticker"] == "DEMO" and ov["currency"] == "USD"
    assert [p["label"] for p in ov["periods"]] == ["FY2024", "FY2025", "FY2026"]
    assert ov["question"] == "Why did free cash flow fall?"

    metrics = {m["name"]: m for m in result["key_metrics"]}
    revenue, fcf = metrics["revenue"], metrics["free_cash_flow"]
    assert revenue["display"] == "$1,239 million" and revenue["period"] == "FY2026"
    assert revenue["is_calculated"] is False and fcf["is_calculated"] is True
    assert revenue["comparison"] == {**revenue["comparison"], "label": "vs FY2025", "display": "+5.0%", "direction": "up"}
    assert fcf["display"] == "-$60 million"
    assert [p["period"] for p in revenue["series"]] == ["FY2024", "FY2025", "FY2026"]
    assert [p["value"] for p in revenue["series"]] == pytest.approx([1000e6, 1180e6, 1239e6])

    assert {c["id"] for c in result["charts"]} == {
        "chart:revenue_profit", "chart:margins", "chart:cash_flow", "chart:cash_debt"}
    for chart in result["charts"]:
        assert chart["periods"] == ["FY2024", "FY2025", "FY2026"] and chart["unit"]
        assert all(len(sr["values"]) == 3 for sr in chart["series"])

    changes = result["changes"]
    assert changes and [c["rank"] for c in changes] == list(range(1, len(changes) + 1))
    for c in changes:
        assert c["evidence"] in ("supported", "partially_supported", "unsupported", "not_investigated")
        assert c["description"] and c["magnitude"] and c["evidence_label"]
    quoted = [c for c in changes if c["finding"] and c["finding"]["quotes"]]
    assert quoted, "at least one change carries a verified quote"
    passages = {p["id"]: p for p in result["passages"]}
    for c in quoted:
        f = c["finding"]
        # The three kinds of statement stay in separate fields all the way to the client.
        assert f["calculated_observation"] and f["management_explanation"]
        for q in f["quotes"]:
            assert q["verified"] is True and q["text"] in passages[q["passage_id"]]["text"]

    assert {a["key"] for a in result["interpretation"]} == {"profitability", "liquidity", "cash_generation", "leverage"}
    assert result["calculations"] and result["sources"]


def test_downloads_are_real_files_and_cannot_escape_the_run_folder(client):
    run_id = start_demo(client)
    wait_for(client, run_id)
    downloads = client.get(f"/api/runs/{run_id}/result").json()["downloads"]
    assert {d["format"] for d in downloads} == {"html", "md", "csv", "json", "png"}
    assert not any(d["format"] == "pdf" for d in downloads)
    for d in downloads:
        r = client.get(f"/api/runs/{run_id}/files/{d['name']}")
        assert r.status_code == 200 and len(r.content) == d["size_bytes"], d["name"]
        assert "attachment" in r.headers["content-disposition"]
        assert "sandbox" in r.headers["content-security-policy"]
    inline = client.get(f"/api/runs/{run_id}/files/report.html", params={"inline": "true"})
    assert inline.status_code == 200 and inline.headers["content-type"].startswith("text/html")
    assert "attachment" not in inline.headers.get("content-disposition", "")
    assert "sandbox" in inline.headers["content-security-policy"]
    for bad in ("../../etc/passwd", "..%2F..%2Fpyproject.toml", ".written", "analysis.sqlite3", "nope.csv"):
        assert client.get(f"/api/runs/{run_id}/files/{bad}").status_code == 404, bad


def test_results_are_refused_until_the_run_has_them(settings):
    gate = threading.Event()
    with make_client(settings, source_factory=lambda req: GatedSource(gate)) as client:
        run_id = start_demo(client)
        status = wait_until(client, run_id, lambda b: b["state"] == "COLLECTING")
        states = {st["key"]: st["state"] for st in status["stages"]}
        assert states["planning"] == "done" and states["collecting"] == "active" and states["validating"] == "pending"
        assert status["is_active"] is True and status["current_activity"]
        assert client.get(f"/api/runs/{run_id}/result").status_code == 409
        assert client.get(f"/api/runs/{run_id}/files/report.html").status_code == 409
        assert client.post(f"/api/runs/{run_id}/review",
                           json={"decision": "approved", "reviewer": "Dana"}).status_code == 409
        gate.set()
        assert wait_for(client, run_id)["phase"] == "ready_for_review"


def test_identical_request_while_running_reuses_the_run(settings):
    gate = threading.Event()
    with make_client(settings, source_factory=lambda req: GatedSource(gate)) as client:
        first = start_demo(client)
        again = client.post("/api/runs", json={"identifier": "demo"})
        assert again.status_code == 202 and again.json() == {"run_id": first, "reused": True}
        other = client.post("/api/runs", json={"identifier": "DEMO", "question": "A different question"})
        assert other.json()["reused"] is False and other.json()["run_id"] != first
        gate.set()
        wait_for(client, first)
        wait_for(client, other.json()["run_id"])
        assert len(client.get("/api/runs").json()["runs"]) == 2
        # Once finished, the same request starts a fresh analysis instead of reusing the old one.
        assert client.post("/api/runs", json={"identifier": "DEMO"}).json()["reused"] is False


def test_queue_limit_and_hourly_limit_are_enforced(settings):
    gate = threading.Event()
    api = ApiSettings(web_dist=None, max_concurrent_runs=1, max_queued_runs=0, runs_per_hour=2)
    with make_client(settings, api=api, source_factory=lambda req: GatedSource(gate)) as client:
        first = start_demo(client, question="one")
        busy = client.post("/api/runs", json={"identifier": "DEMO", "question": "two"})
        assert busy.status_code == 429 and busy.json()["code"] == "busy" and busy.headers["retry-after"] == "30"
        gate.set()
        wait_for(client, first)
        second = start_demo(client, question="two")
        wait_for(client, second)
        limited = client.post("/api/runs", json={"identifier": "DEMO", "question": "three"})
        assert limited.status_code == 429
        body = limited.json()
        assert body["code"] == "rate_limited" and body["retry_after_seconds"] > 0 and body["next_steps"]


def test_failed_run_explains_itself_and_resumes_from_the_failed_stage(settings):
    gate, fail = threading.Event(), threading.Event()
    gate.set()
    fail.set()
    with make_client(settings, source_factory=lambda req: GatedSource(gate, fail)) as client:
        run_id = start_demo(client)
        status = wait_for(client, run_id)
        assert status["phase"] == "failed"
        assert status["error"]["stage"] == "collecting" and status["error"]["retryable"] is True
        assert status["error"]["next_steps"]
        states = {st["key"]: st["state"] for st in status["stages"]}
        assert states["planning"] == "done" and states["collecting"] == "failed"
        assert client.get(f"/api/runs/{run_id}/result").status_code == 409

        fail.clear()
        r = client.post(f"/api/runs/{run_id}/resume", json={})
        assert r.status_code == 202 and r.json()["run_id"] == run_id
        assert wait_for(client, run_id)["phase"] == "ready_for_review"
        diag = client.get(f"/api/runs/{run_id}/diagnostics").json()
        assert any(t["to_state"] == "FAILED" for t in diag["transitions"])
        assert client.post(f"/api/runs/{run_id}/resume", json={}).json()["code"] == "already_finished"


def test_run_left_unfinished_by_a_restart_is_reported_and_can_be_resumed(settings):
    with make_client(settings) as client:
        # A run that exists in storage but that no worker in this process owns, as after a restart.
        st = Store(settings.database_path)
        run = Orchestrator(settings, st, lambda req: FixtureSource(DEMO_FIXTURES)).create(
            AnalysisRequest(identifier=DEMO_CIK, cutoff_date=date(2026, 11, 1), offline=True, llm="stub"))
        st.close()
        status = client.get(f"/api/runs/{run.id}").json()
        assert status["phase"] == "interrupted" and status["is_active"] is False
        assert client.get("/api/runs").json()["runs"][0]["phase"] == "interrupted"
        assert client.post(f"/api/runs/{run.id}/resume", json={}).status_code == 202
        assert wait_for(client, run.id)["phase"] == "ready_for_review"


def test_approval_is_an_explicit_action_and_can_require_a_token(settings):
    api = ApiSettings(web_dist=None, reviewer_token="s3cret-review")
    token = {"X-Reviewer-Token": "s3cret-review"}
    with make_client(settings, api=api) as client:
        assert client.get("/api/health").json()["approval"] == "token_required"
        run_id = start_demo(client)
        assert wait_for(client, run_id)["review"]["state"] == "awaiting_review"
        change_ids = [c["id"] for c in client.get(f"/api/runs/{run_id}/result").json()["changes"] if c["finding"]]
        assert change_ids
        approve = {"decision": "approved", "reviewer": "Dana", "note": "Checked the cash flow figures.",
                   "findings": {c: "confirmed" for c in change_ids},
                   "figures_checked": True, "limitations_read": True}
        denied = client.post(f"/api/runs/{run_id}/review", json=approve)
        assert denied.status_code == 401 and denied.json()["code"] == "reviewer_token_required"
        wrong = client.post(f"/api/runs/{run_id}/review", json=approve, headers={"X-Reviewer-Token": "nope"})
        assert wrong.status_code == 401
        ok = client.post(f"/api/runs/{run_id}/review", json=approve, headers=token)
        assert ok.status_code == 200
        body = ok.json()
        assert body["phase"] == "approved" and body["review"]["state"] == "approved"
        assert body["review"]["approval_note"] == "Checked the cash flow figures." and body["review"]["approved_at"]
        [h] = body["review"]["history"]
        assert h["decision"] == "approved" and h["reviewer"] == "Dana" and sorted(h["confirmed"]) == sorted(change_ids)
        summary = client.get("/api/runs").json()["runs"][0]
        assert summary["review"]["state"] == "approved"
        # An approved report stays readable and downloadable without running anything again.
        assert client.get(f"/api/runs/{run_id}/result").status_code == 200
        assert client.get(f"/api/runs/{run_id}/files/report.md").status_code == 200
        # ...and cannot be reviewed again.
        assert client.post(f"/api/runs/{run_id}/review", json=approve, headers=token).status_code == 409


def test_review_rules_match_the_cli_and_dashboard(client):
    run_id = start_demo(client)
    wait_for(client, run_id)
    change_ids = [c["id"] for c in client.get(f"/api/runs/{run_id}/result").json()["changes"] if c["finding"]]
    url = f"/api/runs/{run_id}/review"

    # Approval without saying what was checked is refused, with what is missing.
    r = client.post(url, json={"decision": "approved", "reviewer": "Dana"})
    assert r.status_code == 422 and r.json()["code"] == "review_incomplete"
    assert "10-K financial statements" in r.json()["message"]
    # A disputed change blocks approval.
    findings = {c: "confirmed" for c in change_ids} | {change_ids[0]: "disputed"}
    r = client.post(url, json={"decision": "approved", "reviewer": "Dana", "findings": findings,
                               "figures_checked": True, "limitations_read": True})
    assert r.status_code == 422 and "disputed" in r.json()["message"]
    # Unknown change IDs are refused rather than stored.
    r = client.post(url, json={"decision": "changes_requested", "reviewer": "Dana", "findings": {"x": "disputed"}})
    assert r.status_code == 422 and "Unknown change" in r.json()["message"]
    # A change request needs a note or a disputed change.
    assert client.post(url, json={"decision": "changes_requested", "reviewer": "Dana"}).status_code == 422
    r = client.post(url, json={"decision": "changes_requested", "reviewer": "Dana",
                               "findings": {change_ids[0]: "disputed"}, "note": "Quote is from the wrong year"})
    assert r.status_code == 200
    review = r.json()["review"]
    assert r.json()["phase"] == "ready_for_review"
    assert review["state"] == "changes_requested" and review["label"] == "Changes requested by Dana"
    assert review["history"][-1]["disputed"] == [change_ids[0]]
    assert client.get("/api/runs").json()["runs"][0]["review"]["state"] == "changes_requested"


def test_archive_lists_runs_newest_first_with_review_state(client):
    a = start_demo(client, question="first")
    wait_for(client, a)
    b = start_demo(client, question="second")
    wait_for(client, b)
    runs = client.get("/api/runs").json()["runs"]
    assert [r["id"] for r in runs] == [b, a]
    for r in runs:
        assert r["company"]["ticker"] == "DEMO" and r["periods"] == ["FY2024", "FY2025", "FY2026"]
        assert r["phase"] == "ready_for_review" and r["review"]["state"] == "awaiting_review"
        assert r["formats"] == ["HTML", "Markdown", "CSV", "JSON", "PNG"]


# ---- static app, contracts and the bundled sample ----------------------------------------

def test_single_page_app_is_served_with_deep_link_fallback(settings, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>FinSight AI</title>")
    (dist / "assets" / "app.js").write_text("console.log('hi')")
    with make_client(settings, api=ApiSettings(web_dist=dist)) as client:
        for path in ("/", "/app", "/app/runs/run-20260101-000000-abcdef", "/app/reports"):
            r = client.get(path)
            assert r.status_code == 200 and "FinSight AI" in r.text, path
        assert client.get("/assets/app.js").text == "console.log('hi')"
        assert client.get("/assets/missing.js").status_code == 404
        assert client.get("/api/nope").headers["content-type"].startswith("application/json")


def test_cors_is_off_by_default_and_limited_to_configured_origins(settings):
    with make_client(settings) as client:
        r = client.get("/api/health", headers={"Origin": "https://example.org"})
        assert "access-control-allow-origin" not in r.headers
    api = ApiSettings(web_dist=None, cors_origins=["https://app.example.org"])
    with make_client(settings, api=api) as client:
        ok = client.get("/api/health", headers={"Origin": "https://app.example.org"})
        assert ok.headers["access-control-allow-origin"] == "https://app.example.org"
        other = client.get("/api/health", headers={"Origin": "https://evil.example.org"})
        assert "access-control-allow-origin" not in other.headers


def test_openapi_file_matches_the_code():
    assert (REPO / "docs" / "openapi.json").read_text() == _script("export_openapi").render(), \
        "docs/openapi.json is stale: run python scripts/export_openapi.py && (cd web && npm run gen:api)"


def test_bundled_sample_matches_the_current_contract_and_is_labelled_synthetic():
    sample = REPO / "web" / "public" / "sample"
    index = json.loads((sample / "index.json").read_text())
    assert any(e["id"] == "demo" for e in index)
    for entry in index:
        result = s.RunResult.model_validate(json.loads((sample / entry["id"] / "result.json").read_text()))
        if entry["id"] == "demo":
            assert result.status.synthetic is True
            assert "synthetic" in result.overview.data_origin_label.lower()
        for d in result.downloads:
            assert (sample / entry["id"] / "files" / d.name).is_file(), d.name


# ---- limits ------------------------------------------------------------------------------

def test_rate_limiter_counts_per_key_inside_a_sliding_window():
    from finsight.api.limits import RateLimiter

    now = [1000.0]
    limiter = RateLimiter(clock=lambda: now[0])
    assert limiter.check("runs", "a", 2, 60) is None
    assert limiter.check("runs", "a", 2, 60) is None
    wait = limiter.check("runs", "a", 2, 60)
    assert wait is not None and 0 < wait <= 60
    assert limiter.check("runs", "b", 2, 60) is None, "another address has its own allowance"
    assert limiter.check("lookup", "a", 2, 60) is None, "another bucket has its own allowance"
    now[0] += 61
    assert limiter.check("runs", "a", 2, 60) is None, "the window slides"


def test_execution_budget_stops_a_run_between_stages_and_resume_continues(settings, store):
    source = lambda req: FixtureSource(DEMO_FIXTURES)  # noqa: E731
    request = AnalysisRequest(identifier=DEMO_CIK, cutoff_date=date(2026, 11, 1), offline=True, llm="stub")
    run = Orchestrator(settings, store, source, budget_seconds=0).start(request)
    assert run.state.value == "FAILED" and "execution budget" in run.error and "resume" in run.error
    done = Orchestrator(settings, store, source).resume(run.id)
    assert done.state.value == "READY_FOR_REVIEW"


# ---- missing data ------------------------------------------------------------------------

def _script(name: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_missing_data_ends_as_partial_results_not_an_error():
    """Facts for the latest year are missing: the run still finishes, says what is missing and estimates nothing."""
    status, result = _script("build_e2e_fixtures").build_partial()
    assert status["phase"] == "partial" and status["has_results"] and status["has_report"]
    assert any("blocking data issue" in w for w in status["warnings"])
    s.RunResult.model_validate(result)

    revenue = next(m for m in result["key_metrics"] if m["name"] == "revenue")
    assert revenue["status"] == "unavailable" and "missing validated input" in revenue["note"]
    assert revenue["comparison"]["display"] is None and revenue["comparison"]["note"]
    assert [p["value"] for p in revenue["series"]][:2] == pytest.approx([1010e6, 1180e6]), "earlier years still shown"
    assert revenue["series"][2]["value"] is None

    chart = next(c for c in result["charts"] if c["id"] == "chart:revenue_profit")
    assert chart["series"][0]["values"][2] is None, "a gap, not a zero"
    assert any(m["label"] == "Revenue" and m["period"] == "FY2026" for m in result["missing"])
    assert any(i["severity"] == "blocking" for i in result["issues"])
    assert result["downloads"], "the partial report can still be downloaded"


def test_browser_test_fixtures_match_the_current_contract():
    fixtures = REPO / "web" / "e2e" / "fixtures"
    s.RunStatus.model_validate(json.loads((fixtures / "partial-status.json").read_text()))
    result = s.RunResult.model_validate(json.loads((fixtures / "partial-result.json").read_text()))
    assert result.status.phase == "partial"
