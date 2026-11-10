"""FastAPI application: a thin HTTP layer over the orchestrator.

    uvicorn finsight.api.app:app --port 8000        # or: finsight-api

Endpoints live under ``/api``. If a built frontend exists (``web/dist`` or
``FINSIGHT_WEB_DIST``) it is served from ``/`` so one process can host both.
"""

from __future__ import annotations

import hmac
import logging
import threading
from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from finsight import __version__
from finsight.api import schemas as s
from finsight.api.directory import SAMPLE_CUTOFF, CompanyDirectory, LiveUnavailable
from finsight.api.jobs import JobManager
from finsight.api.limits import RateLimiter
from finsight.api.settings import ApiSettings
from finsight.api.views import (
    build_diagnostics,
    build_result,
    build_status,
    build_summary,
    ensure_outputs,
    error_next_steps,
)
from finsight.config import Settings, llm_model, load_settings, sec_user_agent
from finsight.contracts import AnalysisRequest, RunState as S, WorkflowRun
from finsight.errors import (
    AccessDeniedError,
    AmbiguousCompanyError,
    CompanyNotFoundError,
    ConfigurationError,
    InvalidTransitionError,
    SourceNotFoundError,
    TransientSourceError,
)
from finsight.llm.factory import has_anthropic_credentials
from finsight.orchestrator import Orchestrator, load_results
from finsight.sec.client import SecHttpClient
from finsight.sec.sources import DataSource, FixtureSource, HttpCache, LiveSecSource
from finsight.storage import Store

log = logging.getLogger(__name__)

QUESTION_MAX = 400
SUPPORTED = ("US domestic, nonfinancial operating companies that file Form 10-K in USD. Banks, broker-dealers, "
             "insurers, REITs (SIC 6000-6799) and foreign private issuers (20-F/40-F) are not supported.")
MEDIA_TYPES = {".csv": "text/csv; charset=utf-8", ".json": "application/json", ".md": "text/markdown; charset=utf-8",
               ".html": "text/html; charset=utf-8", ".png": "image/png"}
# Generated reports quote filing text. They are escaped when written; this policy also stops any
# script from running if one is opened in the browser from the API's origin.
REPORT_CSP = "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, next_steps: list[str] | None = None,
                 retry_after: int | None = None, fields: dict[str, str] | None = None):
        self.status = status
        self.body = s.ErrorBody(code=code, message=message, next_steps=next_steps or [],
                                retry_after_seconds=retry_after, fields=fields or {})


def _error_response(err: ApiError) -> JSONResponse:
    headers = {"Retry-After": str(err.body.retry_after_seconds)} if err.body.retry_after_seconds else None
    return JSONResponse(status_code=err.status, content=err.body.model_dump(mode="json"), headers=headers)


class Services:
    """Everything the handlers share. Built once per app."""

    def __init__(self, settings: Settings, api: ApiSettings, db_path: Path | None = None,
                 source_factory=None, provider_factory=None, live_source=None):
        self.settings = settings
        self.api = api
        self.db_path = Path(db_path or settings.database_path)
        self.limiter = RateLimiter()
        self._client: SecHttpClient | None = None
        self._client_lock = threading.Lock()
        self._live_override = live_source
        self.fixture = FixtureSource(Path(settings.fixtures_dir))
        self.directory = CompanyDirectory(settings, self.fixture, self.live_source)
        self.jobs = JobManager(settings, self.db_path, source_factory or self._source_for_request,
                               api.max_concurrent_runs, api.max_queued_runs, api.run_budget_seconds,
                               provider_factory)
        Store(self.db_path).close()  # create the schema before the first request

    # One SEC client for the whole process, so request pacing holds across concurrent runs.
    def _sec_client(self) -> SecHttpClient | None:
        ua = sec_user_agent()
        if not ua:
            return None
        with self._client_lock:
            if self._client is None:
                self._client = SecHttpClient(ua, self.settings.sec)
            return self._client

    def live_available(self) -> bool:
        return self._live_override is not None or sec_user_agent() is not None

    def live_source(self) -> DataSource | None:
        if self._live_override is not None:
            return self._live_override
        client = self._sec_client()
        if client is None:
            return None
        return LiveSecSource(client=client, cache=HttpCache(self.settings.cache_dir), settings=self.settings.sec)

    def _source_for_request(self, request: AnalysisRequest) -> DataSource:
        if request.offline:
            return FixtureSource(Path(self.settings.fixtures_dir))
        source = self.live_source()
        if source is None:
            raise ConfigurationError("Live SEC access needs FINSIGHT_SEC_USER_AGENT to be set on the server.")
        return source

    def claude_available(self) -> bool:
        return self.api.public_investigator == "auto" and has_anthropic_credentials()


def create_app(settings: Settings | None = None, api: ApiSettings | None = None, *, db_path: Path | None = None,
               source_factory=None, provider_factory=None, live_source=None) -> FastAPI:
    settings = settings or load_settings()
    api = api or ApiSettings.from_env()
    svc = Services(settings, api, db_path, source_factory, provider_factory, live_source)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        svc.jobs.shutdown()

    app = FastAPI(title="FinSight AI API", version=__version__, docs_url="/api/docs", redoc_url=None,
                  openapi_url="/api/openapi.json", lifespan=lifespan,
                  description="Three-year financial analysis of SEC 10-K filers with traceable evidence.")
    app.state.services = svc

    if api.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=api.cors_origins, allow_credentials=False,
                           allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-Reviewer-Token"],
                           max_age=600)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    # ---- errors -------------------------------------------------------------------------
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return _error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        fields = {}
        for e in exc.errors():
            name = str(e["loc"][-1]) if e.get("loc") else "request"
            fields[name] = str(e.get("msg", "invalid value")).removeprefix("Value error, ")
        first = next(iter(fields.items()), ("request", "invalid request"))
        return _error_response(ApiError(422, "invalid_input", f"{first[0].replace('_', ' ').capitalize()}: {first[1]}.",
                                        fields=fields))

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        return _error_response(ApiError(exc.status_code, "http_error", str(exc.detail)))

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception):
        log.exception("unhandled error", exc_info=exc)
        return _error_response(ApiError(500, "internal_error", "The server hit an unexpected error.",
                                        ["Try again in a moment."]))

    # ---- dependencies -------------------------------------------------------------------
    @contextmanager
    def open_store() -> Iterator[Store]:
        # Opened and closed inside the handler: SQLite connections must stay on one thread, and
        # FastAPI may run a generator dependency's setup and teardown on different worker threads.
        st = Store(svc.db_path)
        try:
            yield st
        finally:
            st.close()

    def client_key(request: Request) -> str:
        hops = api.trusted_proxy_hops
        if hops:
            chain = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
            if len(chain) >= hops:
                return chain[-hops]
        return request.client.host if request.client else "unknown"

    def limit_lookups(request: Request) -> None:
        wait = svc.limiter.check("lookup", client_key(request), api.lookups_per_minute, 60)
        if wait:
            raise ApiError(429, "rate_limited", "Too many lookups from this address.",
                           ["Wait a moment and try again."], retry_after=int(wait))

    def get_run(run_id: str, st: Store) -> WorkflowRun:
        run = st.load_run(run_id) if _valid_run_id(run_id) else None
        if run is None:
            raise ApiError(404, "run_not_found", f"No analysis with ID {run_id[:60]} exists on this server.",
                           ["Check the link, or open the report archive to find it.",
                            "Runs are stored on the server; a redeploy without persistent storage removes them."])
        return run

    def source_errors(exc: Exception) -> ApiError:
        if isinstance(exc, LiveUnavailable):
            return ApiError(503, "live_unavailable",
                            "Live SEC data is not configured on this server, so only the synthetic sample "
                            "companies can be analysed here.",
                            ["Run the DEMO sample company, or open the cached sample analysis.",
                             "If you run this server, set FINSIGHT_SEC_USER_AGENT and restart it."])
        if isinstance(exc, CompanyNotFoundError):
            return ApiError(404, "company_not_found", str(exc),
                            ["Check the ticker, or search by company name and pick a result."])
        if isinstance(exc, AmbiguousCompanyError):
            names = "; ".join(f"{c['title']} (CIK {int(c['cik'])})" for c in exc.candidates[:5])
            return ApiError(409, "ambiguous_company", f"{exc}. Matches include: {names}.",
                            ["Search for the company and select one result so the CIK is exact."])
        if isinstance(exc, AccessDeniedError):
            return ApiError(502, "source_refused", "SEC EDGAR refused the request.",
                            ["Try again later.", "If you run this server, check FINSIGHT_SEC_USER_AGENT."])
        if isinstance(exc, (TransientSourceError, SourceNotFoundError)):
            return ApiError(502, "source_unavailable", "SEC EDGAR could not be reached after several attempts.",
                            ["Try again in a minute. Nothing was started."], retry_after=30)
        if isinstance(exc, ConfigurationError):
            return ApiError(503, "live_unavailable", str(exc))
        raise exc

    # ---- service ------------------------------------------------------------------------
    @app.get("/api/health", response_model=s.Health, tags=["service"])
    def health() -> s.Health:
        live = svc.live_available()
        claude = svc.claude_available()
        return s.Health(
            version=__version__, time=datetime.now(timezone.utc),
            live_data=s.Availability(available=live, reason=None if live else
                                     "Live SEC access is not configured on this server. Sample companies still work."),
            investigator=s.InvestigatorInfo(
                claude_available=claude, default="claude" if claude else "rule_based",
                model=llm_model(settings) if claude else None,
                note=("Flagged changes are investigated by Claude with typed tools; quotes are verified by code."
                      if claude else
                      "No LLM is configured on this server. A rule-based investigator quotes filing sentences "
                      "that state a cause; it is conservative and writes no interpretation.")),
            approval="token_required" if api.reviewer_token else "open",
            limits=s.Limits(max_concurrent_runs=api.max_concurrent_runs, runs_per_hour=api.runs_per_hour,
                            run_budget_seconds=api.run_budget_seconds, question_max_chars=QUESTION_MAX, years=3),
            active_runs=svc.jobs.active_count(),
            data_source="SEC EDGAR (data.sec.gov XBRL company facts and www.sec.gov 10-K archives)",
            supported_companies=SUPPORTED)

    # ---- companies ----------------------------------------------------------------------
    @app.get("/api/companies/search", response_model=s.SearchResponse, tags=["companies"],
             dependencies=[Depends(limit_lookups)])
    def search(q: str = Query(min_length=1, max_length=80), limit: int = Query(default=8, ge=1, le=20)):
        note = None
        try:
            hits, live = svc.directory.search(q, limit)
        except (TransientSourceError, AccessDeniedError, SourceNotFoundError, ConfigurationError):
            # The live ticker list is unreachable; say so instead of pretending the search was complete.
            svc_fixture = CompanyDirectory(settings, svc.fixture, lambda: None)
            hits, live = svc_fixture.search(q, limit)
            note = "The SEC company list could not be loaded, so only sample companies were searched. Try again."
        if not live and note is None:
            note = "Live SEC search is not configured on this server. Only sample companies are listed."
        return s.SearchResponse(query=q, results=hits, live_search=live, note=note)

    @app.get("/api/companies/{identifier}", response_model=s.CompanyProfile, tags=["companies"],
             dependencies=[Depends(limit_lookups)], responses={404: {"model": s.ErrorBody}})
    def company(identifier: str):
        if not identifier.strip() or len(identifier) > 80:
            raise ApiError(422, "invalid_input", "Give a ticker, CIK or company name.")
        try:
            return svc.directory.profile(identifier)
        except Exception as exc:
            raise source_errors(exc) from exc

    # ---- runs ---------------------------------------------------------------------------
    @app.post("/api/runs", response_model=s.RunCreated, status_code=202, tags=["runs"],
              responses={code: {"model": s.ErrorBody} for code in (404, 409, 422, 429, 502, 503)})
    def create_run(body: s.RunCreate, request: Request):
        try:
            profile = svc.directory.profile(body.identifier)
        except Exception as exc:
            raise source_errors(exc) from exc
        if not profile.supported:
            raise ApiError(422, "unsupported_company", profile.unsupported_reason or "This company is not supported.",
                           profile.next_steps, fields={"identifier": "unsupported company"})
        years = [o.fiscal_year for o in profile.fiscal_years]
        year = body.latest_fiscal_year or profile.default_fiscal_year
        if year not in years:
            raise ApiError(422, "period_unavailable",
                           f"No Form 10-K for a fiscal year ending in {year} is on file for {profile.name}.",
                           [f"Available fiscal years: {', '.join(f'FY{y}' for y in years)}."],
                           fields={"latest_fiscal_year": "not available"})
        rule_based = profile.synthetic or body.investigator == "rule_based" or not svc.claude_available()
        key = (profile.cik, year, body.question or "", rule_based)
        existing = svc.jobs.find_duplicate(key)
        if existing:
            return s.RunCreated(run_id=existing, reused=True)
        if not svc.jobs.has_capacity():
            raise ApiError(429, "busy", "The analysis queue is full right now.",
                           ["Wait for a running analysis to finish, then try again.",
                            "Open a finished report from the archive in the meantime."], retry_after=30)
        wait = svc.limiter.check("runs", client_key(request), api.runs_per_hour, 3600)
        if wait:
            raise ApiError(429, "rate_limited",
                           f"This address has started {api.runs_per_hour} analyses in the last hour, which is the "
                           "limit for this server.",
                           ["Open an existing report from the archive.", "Try again later."], retry_after=int(wait))
        req = AnalysisRequest(
            identifier=profile.cik, cutoff_date=SAMPLE_CUTOFF if profile.synthetic else date.today(),
            latest_fiscal_year=year, num_years=3, question=body.question, offline=profile.synthetic,
            llm="stub" if rule_based else "auto")
        with open_store() as st:
            run = Orchestrator(settings, st, svc.jobs.source_factory).create(req)
        svc.jobs.submit(run.id, key=key)
        return s.RunCreated(run_id=run.id, reused=False)

    @app.get("/api/runs", response_model=s.RunList, tags=["runs"])
    def list_runs(limit: int = Query(default=30, ge=1, le=100)):
        out = []
        with open_store() as st:
            for row in st.list_runs(limit):
                run = st.load_run(row[0])
                if run is not None:
                    out.append(build_summary(run, st, svc.jobs.is_active(run.id)))
        return s.RunList(runs=out)

    @app.get("/api/runs/{run_id}", response_model=s.RunStatus, tags=["runs"],
             responses={404: {"model": s.ErrorBody}})
    def run_status(run_id: str):
        with open_store() as st:
            run = get_run(run_id, st)
            return build_status(run, load_results(st, run.id), svc.jobs.is_active(run.id), settings)

    @app.get("/api/runs/{run_id}/result", response_model=s.RunResult, tags=["runs"],
             responses={404: {"model": s.ErrorBody}, 409: {"model": s.ErrorBody}})
    def run_result(run_id: str):
        with open_store() as st:
            run = get_run(run_id, st)
            result = build_result(run, st, settings, svc.jobs.is_active(run.id))
        if result is None:
            raise ApiError(409, "not_ready", f"This analysis has no results yet (state {run.state.value}).",
                           ["Follow its progress on the run page."])
        return result

    @app.get("/api/runs/{run_id}/diagnostics", response_model=s.Diagnostics, tags=["runs"],
             responses={404: {"model": s.ErrorBody}})
    def run_diagnostics(run_id: str):
        with open_store() as st:
            return build_diagnostics(get_run(run_id, st), st)

    @app.get("/api/runs/{run_id}/files/{name:path}", tags=["runs"], response_class=FileResponse,
             responses={404: {"model": s.ErrorBody}})
    def run_file(run_id: str, name: str, inline: bool = False):
        with open_store() as st:
            run = get_run(run_id, st)
            if run.state not in (S.READY_FOR_REVIEW, S.PARTIAL, S.APPROVED):
                raise ApiError(409, "not_ready", "Downloads are available once the analysis has finished.")
            out_dir = ensure_outputs(settings, run, load_results(st, run.id)).resolve()
        path = (out_dir / name).resolve()
        if out_dir not in path.parents or not path.is_file() or path.suffix not in MEDIA_TYPES \
                or path.name.startswith("."):
            raise ApiError(404, "file_not_found", "That file is not one of this run's downloads.")
        headers = {"Content-Security-Policy": REPORT_CSP}
        filename = f"finsight-{run.id}-{path.name}"
        return FileResponse(path, media_type=MEDIA_TYPES[path.suffix], headers=headers,
                            filename=None if inline and path.suffix in (".html", ".png") else filename,
                            content_disposition_type="inline" if inline else "attachment")

    def _require_idle(run: WorkflowRun) -> None:
        if svc.jobs.is_active(run.id):
            raise ApiError(409, "already_running", "This analysis is already running.",
                           ["Follow its progress on the run page."])

    @app.post("/api/runs/{run_id}/resume", response_model=s.RunCreated, status_code=202, tags=["runs"],
              responses={code: {"model": s.ErrorBody} for code in (404, 409, 422, 429)})
    def resume_run(run_id: str, body: s.ResumeRequest):
        with open_store() as st:
            run = get_run(run_id, st)
        _require_idle(run)
        if run.state in (S.READY_FOR_REVIEW, S.PARTIAL, S.APPROVED):
            raise ApiError(409, "already_finished", "This analysis has already finished.")
        if run.state == S.NEEDS_INPUT:
            allowed = {c["cik"] for c in (run.needs_input or {}).get("candidates", [])}
            cik = body.company.zfill(10) if body.company else None
            if cik is None or cik not in allowed:
                raise ApiError(422, "invalid_input", "Choose one of the listed companies.",
                               fields={"company": "not one of the candidates"})
            identifier = cik
        else:
            identifier = None
        if not svc.jobs.has_capacity():
            raise ApiError(429, "busy", "The analysis queue is full right now.",
                           ["Wait for a running analysis to finish, then retry."], retry_after=30)
        svc.jobs.submit(run.id, resume=True, identifier=identifier)
        return s.RunCreated(run_id=run.id, reused=True)

    @app.post("/api/runs/{run_id}/approve", response_model=s.RunStatus, tags=["runs"],
              responses={code: {"model": s.ErrorBody} for code in (401, 404, 409)})
    def approve_run(run_id: str, body: s.ApproveRequest, x_reviewer_token: str | None = Header(default=None)):
        if api.reviewer_token and not hmac.compare_digest(x_reviewer_token or "", api.reviewer_token):
            raise ApiError(401, "reviewer_token_required", "Approving a report needs the reviewer token.",
                           ["Enter the token set as FINSIGHT_REVIEWER_TOKEN on the server."])
        with open_store() as st:
            run = get_run(run_id, st)
            _require_idle(run)
            try:
                run = Orchestrator(settings, st, svc.jobs.source_factory).approve(run.id, body.note)
            except InvalidTransitionError:
                raise ApiError(409, "not_reviewable",
                               f"Only a finished analysis can be approved (this one is {run.state.value}).") from None
            return build_status(run, load_results(st, run.id), False, settings)

    # ---- unknown API paths, then the built frontend --------------------------------------
    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"], include_in_schema=False)
    def api_not_found(rest: str):
        raise ApiError(404, "not_found", f"No API endpoint at /api/{rest[:80]}.")

    if api.web_dist is not None:
        dist = api.web_dist.resolve()
        index = dist / "index.html"

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            target = (dist / path).resolve()
            if path and dist in target.parents and target.is_file():
                immutable = path.startswith("assets/")
                return FileResponse(target, headers={"Cache-Control": "public, max-age=31536000, immutable"
                                                     if immutable else "no-cache"})
            return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app


def _valid_run_id(run_id: str) -> bool:
    import re

    return bool(re.fullmatch(r"run-\d{8}-\d{6}-[0-9a-f]{6}", run_id))


def _lazy_app() -> FastAPI:
    return create_app()


def main() -> None:
    """``finsight-api``: run the API (and the built frontend, if present) with uvicorn."""
    import os

    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    uvicorn.run("finsight.api.app:_lazy_app", factory=True, host=os.environ.get("HOST", "127.0.0.1"),
                port=int(os.environ.get("PORT", "8000")), proxy_headers=False)


def __getattr__(name: str):
    # ``uvicorn finsight.api.app:app`` works without building the app at import time (tests import this module).
    if name == "app":
        return create_app()
    raise AttributeError(name)


__all__ = ["create_app", "error_next_steps", "main"]
