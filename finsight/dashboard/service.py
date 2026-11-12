"""Everything the Streamlit dashboard does that is not drawing.

The dashboard is a third front end over the same engine as the CLI and the web app.
It holds no financial logic: runs go through the ``Orchestrator`` on a background
worker (``JobManager``), and results are reshaped by the same view builders the
HTTP API uses (``finsight.api.views``), so every figure, citation and review state
shown here is identical to the report and the downloads.

Kept free of Streamlit imports so it can be tested directly.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from finsight.api import schemas as s
from finsight.api.directory import SAMPLE_CUTOFF, CompanyDirectory, LiveUnavailable
from finsight.api.jobs import JobManager
from finsight.api.views import build_result, build_status, ensure_outputs, list_downloads
from finsight.config import Settings, llm_model, load_settings, sec_user_agent
from finsight.contracts import AnalysisRequest, RunState as S, WorkflowRun
from finsight.errors import ConfigurationError
from finsight.llm.factory import has_anthropic_credentials
from finsight.orchestrator import Orchestrator, load_results
from finsight.sec.client import SecHttpClient
from finsight.sec.sources import DataSource, FixtureSource, HttpCache, LiveSecSource
from finsight.storage import Store

REVIEWABLE = {S.READY_FOR_REVIEW, S.PARTIAL}
QUESTION_MAX = 400


@dataclass
class Availability:
    live_data: bool
    live_reason: str | None
    claude: bool
    model: str | None


class DashboardError(Exception):
    """A problem the person can act on, with the message to show them."""


class DashboardService:
    def __init__(self, settings: Settings | None = None, db_path: Path | None = None,
                 source_factory=None, provider_factory=None, live_source: DataSource | None = None,
                 max_workers: int = 2, budget_seconds: float = 900):
        self.settings = settings or load_settings()
        self.db_path = Path(db_path or self.settings.database_path)
        self._live_override = live_source
        self._client: SecHttpClient | None = None
        self._client_lock = threading.Lock()
        self.fixture = FixtureSource(Path(self.settings.fixtures_dir))
        self.directory = CompanyDirectory(self.settings, self.fixture, self.live_source)
        self.jobs = JobManager(self.settings, self.db_path, source_factory or self._source_for_request,
                               max_workers=max_workers, max_queued=4, budget_seconds=budget_seconds,
                               provider_factory=provider_factory)
        Store(self.db_path).close()  # create the schema up front

    # ---- data sources -------------------------------------------------------------------
    def _sec_client(self) -> SecHttpClient | None:
        ua = sec_user_agent()
        if not ua:
            return None
        with self._client_lock:  # one client per process so SEC request pacing holds across runs
            if self._client is None:
                self._client = SecHttpClient(ua, self.settings.sec)
            return self._client

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
            raise ConfigurationError("Live SEC access needs FINSIGHT_SEC_USER_AGENT to be set.")
        return source

    def availability(self) -> Availability:
        live = self._live_override is not None or sec_user_agent() is not None
        claude = has_anthropic_credentials()
        return Availability(
            live_data=live,
            live_reason=None if live else "Set FINSIGHT_SEC_USER_AGENT (an app name and contact address) "
                                          "to analyse real companies. Only the synthetic sample works until then.",
            claude=claude, model=llm_model(self.settings) if claude else None)

    # ---- companies ----------------------------------------------------------------------
    def search(self, query: str, limit: int = 8) -> tuple[list[s.CompanyHit], str | None]:
        query = query.strip()
        if not query:
            return self.directory.examples(), None
        try:
            hits, live = self.directory.search(query, limit)
        except Exception:
            hits, live = CompanyDirectory(self.settings, self.fixture, lambda: None).search(query, limit)
            return hits, "The SEC company list could not be loaded, so only the sample company was searched."
        note = None if live else "Live SEC search is not configured, so only the sample company is listed."
        return hits, note

    def profile(self, identifier: str) -> s.CompanyProfile:
        try:
            return self.directory.profile(identifier)
        except LiveUnavailable:
            raise DashboardError(self.availability().live_reason) from None
        except Exception as exc:
            raise DashboardError(f"Could not load {identifier} from SEC EDGAR: {exc}") from exc

    # ---- runs ---------------------------------------------------------------------------
    def start_run(self, profile: s.CompanyProfile, latest_fiscal_year: int | None, question: str | None,
                  use_claude: bool) -> str:
        if not profile.supported:
            raise DashboardError(profile.unsupported_reason or "This company is not supported.")
        years = [o.fiscal_year for o in profile.fiscal_years]
        year = latest_fiscal_year or profile.default_fiscal_year
        if year not in years:
            raise DashboardError(f"No Form 10-K for a fiscal year ending in {year} is on file for {profile.name}.")
        question = " ".join((question or "").split())[:QUESTION_MAX] or None
        rule_based = profile.synthetic or not use_claude or not has_anthropic_credentials()
        key = (profile.cik, year, question or "", rule_based)
        existing = self.jobs.find_duplicate(key)
        if existing:
            return existing
        if not self.jobs.has_capacity():
            raise DashboardError("The analysis queue is full. Wait for a running analysis to finish.")
        req = AnalysisRequest(
            identifier=profile.cik, cutoff_date=SAMPLE_CUTOFF if profile.synthetic else date.today(),
            latest_fiscal_year=year, num_years=3, question=question, offline=profile.synthetic,
            llm="stub" if rule_based else "auto")
        with self._store() as st:
            run = Orchestrator(self.settings, st, self.jobs.source_factory).create(req)
        self.jobs.submit(run.id, key=key)
        return run.id

    def resume(self, run_id: str, company_cik: str | None = None) -> None:
        run = self.load_run(run_id)
        if self.jobs.is_active(run_id):
            return
        if run.state in (S.READY_FOR_REVIEW, S.PARTIAL, S.APPROVED):
            raise DashboardError("This analysis has already finished.")
        identifier = None
        if run.state == S.NEEDS_INPUT:
            allowed = {c["cik"] for c in (run.needs_input or {}).get("candidates", [])}
            identifier = company_cik.zfill(10) if company_cik else None
            if identifier not in allowed:
                raise DashboardError("Choose one of the listed companies.")
        self.jobs.submit(run_id, resume=True, identifier=identifier)

    def runs(self, limit: int = 30) -> list[s.RunSummary]:
        from finsight.api.views import build_summary

        out = []
        with self._store() as st:
            for row in st.list_runs(limit):
                active = self.jobs.is_active(row[0])
                run = st.load_run(row[0])
                if run is not None:
                    out.append(build_summary(run, st, active))
        return out

    def load_run(self, run_id: str) -> WorkflowRun:
        with self._store() as st:
            run = st.load_run(run_id)
        if run is None:
            raise DashboardError(f"No analysis with ID {run_id}.")
        return run

    def status(self, run_id: str) -> s.RunStatus:
        active = self.jobs.is_active(run_id)  # read before the run, so a just-finished run is not "interrupted"
        with self._store() as st:
            run = st.load_run(run_id)
            if run is None:
                raise DashboardError(f"No analysis with ID {run_id}.")
            return build_status(run, load_results(st, run.id), active, self.settings)

    def result(self, run_id: str) -> s.RunResult | None:
        active = self.jobs.is_active(run_id)
        with self._store() as st:
            run = st.load_run(run_id)
            if run is None:
                raise DashboardError(f"No analysis with ID {run_id}.")
            return build_result(run, st, self.settings, active)

    def human_reviews(self, run_id: str) -> list[dict]:
        return list(self.load_run(run_id).stats.get("human_reviews") or [])

    def record_review(self, run_id: str, decision: str, reviewer: str, note: str, checks: dict) -> WorkflowRun:
        if self.jobs.is_active(run_id):
            raise DashboardError("This analysis is still running.")
        with self._store() as st:
            try:
                return Orchestrator(self.settings, st, self.jobs.source_factory).record_review(
                    run_id, decision, reviewer, note, checks)
            except (ValueError, KeyError) as exc:
                raise DashboardError(str(exc).strip("'\"")) from exc
            except Exception as exc:  # InvalidTransitionError
                raise DashboardError(str(exc)) from exc

    # ---- research report ----------------------------------------------------------------
    def research(self, run_id: str):
        with self._store() as st:
            return load_results(st, run_id).get("research")

    def build_research(self, run_id: str, price: float | None, price_date: date | None, price_source: str,
                       price_url: str = "", use_claude: bool = True):
        """Build and store the research report; the PDF is rendered with the run's other downloads."""
        from finsight.research.builder import ResearchError
        from finsight.research.service import generate, manual_snapshot

        if self.jobs.is_active(run_id):
            raise DashboardError("This analysis is still running.")
        with self._store() as st:
            run = st.load_run(run_id)
            if run is None:
                raise DashboardError(f"No analysis with ID {run_id}.")
            results = load_results(st, run.id)
            plan = results.get("plan")
            ticker = plan.plan.company.tickers[0] if plan and plan.plan.company.tickers else ""
            market = None
            if price:
                if not price_date or not (price_source or "").strip():
                    raise DashboardError("Give the price's date and source, or leave the price empty.")
                if price_date > date.today():
                    raise DashboardError("The price date cannot be in the future.")
                market = manual_snapshot(ticker, price, price_date, price_source, price_url)
            try:
                source = self._source_for_request(run.request)
            except ConfigurationError:
                source = None
            try:
                return generate(st, self.settings, run, results, market, source,
                                use_llm=use_claude and has_anthropic_credentials())
            except (ResearchError, ValueError) as exc:
                raise DashboardError(str(exc)) from exc

    # ---- files --------------------------------------------------------------------------
    def downloads(self, run_id: str) -> tuple[Path, list[s.DownloadInfo]]:
        with self._store() as st:
            run = st.load_run(run_id)
            if run is None or run.state not in (S.READY_FOR_REVIEW, S.PARTIAL, S.APPROVED):
                raise DashboardError("Downloads are available once the analysis has finished.")
            out = ensure_outputs(self.settings, run, load_results(st, run.id))
        return out, list_downloads(out)

    def read_file(self, run_id: str, name: str) -> bytes:
        out, items = self.downloads(run_id)
        if name not in {d.name for d in items}:
            raise DashboardError("That file is not one of this run's downloads.")
        return (out / name).read_bytes()

    # ---- helpers ------------------------------------------------------------------------
    def _store(self):
        return _StoreCtx(self.db_path)

    def shutdown(self) -> None:
        self.jobs.shutdown()


class _StoreCtx:
    def __init__(self, path: Path):
        self.path = path

    def __enter__(self) -> Store:
        self.store = Store(self.path)
        return self.store

    def __exit__(self, *exc) -> None:
        self.store.close()
