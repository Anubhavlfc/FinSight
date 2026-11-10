"""Background execution of analysis runs.

Each run executes on a worker thread with its own SQLite connection. Run state
and checkpoints are written by the orchestrator as it goes, so the HTTP side only
reads the database: a browser refresh, or a second API worker, sees the same run.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Hashable

from finsight.config import Settings
from finsight.contracts import RunState as S
from finsight.orchestrator import Orchestrator, SourceFactory
from finsight.storage import Store
from finsight.workflow import TERMINAL, transition

log = logging.getLogger(__name__)


class JobManager:
    def __init__(self, settings: Settings, db_path: Path, source_factory: SourceFactory, max_workers: int,
                 max_queued: int, budget_seconds: float, provider_factory: Callable | None = None):
        self.settings = settings
        self.db_path = db_path
        self.source_factory = source_factory
        self.provider_factory = provider_factory
        self.max_workers = max_workers
        self.max_queued = max_queued
        self.budget_seconds = budget_seconds
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="finsight-run")
        self._lock = threading.Lock()
        self._active: dict[str, Hashable | None] = {}

    # ---- bookkeeping --------------------------------------------------------------------
    def is_active(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._active

    def active_count(self) -> int:
        with self._lock:
            return len(self._active)

    def has_capacity(self) -> bool:
        return self.active_count() < self.max_workers + self.max_queued

    def find_duplicate(self, key: Hashable) -> str | None:
        with self._lock:
            return next((rid for rid, k in self._active.items() if k == key), None)

    # ---- execution ----------------------------------------------------------------------
    def submit(self, run_id: str, key: Hashable | None = None, resume: bool = False,
               identifier: str | None = None) -> bool:
        """Queue a stored run. Returns False if it is already running."""
        with self._lock:
            if run_id in self._active:
                return False
            self._active[run_id] = key
        self._pool.submit(self._work, run_id, resume, identifier)
        return True

    def _work(self, run_id: str, resume: bool, identifier: str | None) -> None:
        store = Store(self.db_path)
        try:
            orch = Orchestrator(self.settings, store, self.source_factory, self.provider_factory,
                                budget_seconds=self.budget_seconds)
            if resume:
                orch.resume(run_id, identifier=identifier)
            else:
                orch.drive(run_id)
        except Exception as exc:  # a stage raised something the orchestrator does not route
            log.exception("run %s crashed", run_id)
            try:
                run = store.load_run(run_id)
                if run is not None and run.state not in TERMINAL:
                    run.failed_stage = run.state if run.state != S.CREATED else S.PLANNING
                    run.error = f"Unexpected error at {run.state.value}: {type(exc).__name__} (resume to retry)"
                    transition(run, S.FAILED, run.error)
                    store.save_run(run)
            except Exception:
                log.exception("could not record the failure of run %s", run_id)
        finally:
            store.close()
            with self._lock:
                self._active.pop(run_id, None)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
