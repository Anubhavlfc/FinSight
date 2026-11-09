"""SQLite persistence for runs, checkpoints and results.

Each stage's output is saved as a JSON checkpoint once the stage finishes, so an
interrupted run resumes from the last completed stage. Facts, issues, metrics
and change flags are also written to tables so they can be queried directly.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TypeVar

from pydantic import BaseModel

from finsight.contracts import (
    AnalysisResult,
    SourceDocument,
    ValidationReport,
    WorkflowRun,
    utcnow,
)

T = TypeVar("T", bound=BaseModel)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, state TEXT NOT NULL, identifier TEXT NOT NULL, cik TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, run_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transitions (
    run_id TEXT NOT NULL, seq INTEGER NOT NULL, from_state TEXT, to_state TEXT, at TEXT, reason TEXT,
    PRIMARY KEY (run_id, seq)
);
CREATE TABLE IF NOT EXISTS checkpoints (
    run_id TEXT NOT NULL, stage TEXT NOT NULL, created_at TEXT NOT NULL, payload_json TEXT NOT NULL,
    PRIMARY KEY (run_id, stage)
);
CREATE TABLE IF NOT EXISTS sources (
    run_id TEXT NOT NULL, id TEXT NOT NULL, kind TEXT, url TEXT, sha256 TEXT, retrieved_at TEXT,
    from_cache INTEGER, is_fixture INTEGER, json TEXT, PRIMARY KEY (run_id, id)
);
CREATE TABLE IF NOT EXISTS facts (
    run_id TEXT NOT NULL, id TEXT NOT NULL, field TEXT, fiscal_period TEXT, value TEXT, status TEXT,
    tag TEXT, accession TEXT, json TEXT, PRIMARY KEY (run_id, id)
);
CREATE TABLE IF NOT EXISTS issues (
    run_id TEXT NOT NULL, id TEXT NOT NULL, severity TEXT, code TEXT, field TEXT, fiscal_period TEXT,
    message TEXT, json TEXT, PRIMARY KEY (run_id, id)
);
CREATE TABLE IF NOT EXISTS metrics (
    run_id TEXT NOT NULL, id TEXT NOT NULL, name TEXT, fiscal_period TEXT, value TEXT, unit TEXT,
    status TEXT, json TEXT, PRIMARY KEY (run_id, id)
);
CREATE TABLE IF NOT EXISTS changes (
    run_id TEXT NOT NULL, id TEXT NOT NULL, rule TEXT, metric TEXT, comparison TEXT, score TEXT, json TEXT,
    PRIMARY KEY (run_id, id)
);
"""


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.execute("PRAGMA journal_mode=WAL" if str(path) != ":memory:" else "PRAGMA journal_mode=MEMORY")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._conn:
            yield self._conn

    # ---- runs ---------------------------------------------------------------------------
    def save_run(self, run: WorkflowRun, cik: str | None = None) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO runs (id, state, identifier, cik, created_at, updated_at, run_json) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state, "
                "cik=COALESCE(excluded.cik, runs.cik), updated_at=excluded.updated_at, run_json=excluded.run_json",
                (run.id, run.state.value, run.request.identifier, cik, run.created_at.isoformat(),
                 run.updated_at.isoformat(), run.model_dump_json()),
            )
            c.execute("DELETE FROM transitions WHERE run_id=?", (run.id,))
            c.executemany(
                "INSERT INTO transitions VALUES (?,?,?,?,?,?)",
                [(run.id, i, t.from_state.value, t.to_state.value, t.at.isoformat(), t.reason)
                 for i, t in enumerate(run.history)],
            )

    def load_run(self, run_id: str) -> WorkflowRun | None:
        row = self._conn.execute("SELECT run_json FROM runs WHERE id=?", (run_id,)).fetchone()
        return WorkflowRun.model_validate_json(row[0]) if row else None

    def list_runs(self, limit: int = 20) -> list[tuple]:
        return self._conn.execute(
            "SELECT id, state, identifier, cik, created_at, updated_at FROM runs ORDER BY created_at DESC LIMIT ?",
            (limit,)).fetchall()

    # ---- checkpoints --------------------------------------------------------------------
    def save_checkpoint(self, run_id: str, stage: str, payload: BaseModel) -> None:
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO checkpoints VALUES (?,?,?,?)",
                      (run_id, stage, utcnow().isoformat(), payload.model_dump_json()))

    def load_checkpoint(self, run_id: str, stage: str, model: type[T]) -> T | None:
        row = self._conn.execute("SELECT payload_json FROM checkpoints WHERE run_id=? AND stage=?",
                                 (run_id, stage)).fetchone()
        return model.model_validate_json(row[0]) if row else None

    def delete_checkpoint(self, run_id: str, stage: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM checkpoints WHERE run_id=? AND stage=?", (run_id, stage))

    def checkpoint_stages(self, run_id: str) -> list[str]:
        return [r[0] for r in self._conn.execute(
            "SELECT stage FROM checkpoints WHERE run_id=? ORDER BY created_at", (run_id,))]

    # ---- results ------------------------------------------------------------------------
    def save_sources(self, run_id: str, docs: list[SourceDocument]) -> None:
        with self.tx() as c:
            c.executemany("INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?,?,?,?,?)", [
                (run_id, d.id, d.kind.value, d.url, d.sha256, d.retrieved_at.isoformat(), int(d.from_cache),
                 int(d.is_fixture), d.model_dump_json()) for d in docs])

    def save_validation(self, run_id: str, report: ValidationReport) -> None:
        facts = report.accepted_facts + report.alternative_facts + report.rejected_candidates
        with self.tx() as c:
            c.execute("DELETE FROM facts WHERE run_id=?", (run_id,))
            c.execute("DELETE FROM issues WHERE run_id=?", (run_id,))
            c.executemany("INSERT OR REPLACE INTO facts VALUES (?,?,?,?,?,?,?,?,?)", [
                (run_id, f.id, f.field, f.fiscal_period, str(f.value), f.status.value, f.tag, f.accession,
                 f.model_dump_json()) for f in facts])
            c.executemany("INSERT OR REPLACE INTO issues VALUES (?,?,?,?,?,?,?,?)", [
                (run_id, i.id, i.severity.value, i.code, i.field, i.fiscal_period, i.message, i.model_dump_json())
                for i in report.issues])

    def save_analysis(self, run_id: str, result: AnalysisResult) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM metrics WHERE run_id=?", (run_id,))
            c.execute("DELETE FROM changes WHERE run_id=?", (run_id,))
            c.executemany("INSERT OR REPLACE INTO metrics VALUES (?,?,?,?,?,?,?,?)", [
                (run_id, m.id, m.name, m.fiscal_period, None if m.value is None else str(m.value), m.unit,
                 m.status.value, m.model_dump_json()) for m in result.metrics])
            c.executemany("INSERT OR REPLACE INTO changes VALUES (?,?,?,?,?,?,?)", [
                (run_id, f.id, f.rule, f.metric, f.comparison, str(f.score), f.model_dump_json())
                for f in result.changes])

    def query(self, sql: str, params: tuple = ()) -> list[tuple]:
        return self._conn.execute(sql, params).fetchall()


def dumps(obj) -> str:
    return json.dumps(obj, default=str, indent=2)
