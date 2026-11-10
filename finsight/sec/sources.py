"""Data sources: live SEC EDGAR (with an on-disk cache) and offline fixtures.

Both implement the same interface, so every downstream stage behaves identically
whether a run is live or offline. Every payload comes back with a
``SourceDocument`` describing where it came from, when, and its SHA-256.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from finsight.config import SecSettings
from finsight.contracts import SourceDocument, SourceKind
from finsight.errors import SourceNotFoundError
from finsight.sec.client import SecHttpClient

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
TICKERS_EXCHANGE_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{document}"

TICKERS_TTL_SECONDS = 24 * 3600


def filing_url(cik: str, accession: str, primary_document: str | None) -> str:
    acc = accession.replace("-", "")
    doc = primary_document or f"{accession}-index.htm"
    return ARCHIVE_URL.format(cik_int=int(cik), acc_nodash=acc, document=doc)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class Fetched:
    payload: Any
    document: SourceDocument


class DataSource(Protocol):
    is_offline: bool
    stats: dict[str, int]

    def company_tickers(self) -> Fetched: ...

    def submissions(self, cik: str) -> Fetched: ...

    def submissions_page(self, cik: str, name: str) -> Fetched: ...

    def company_facts(
        self, cik: str, must_include_accession: str | None = None, force_refresh: bool = False
    ) -> Fetched: ...

    def filing_document(self, cik: str, accession: str, document: str) -> Fetched:
        """Raw bytes of a filing's document (payload is ``bytes``)."""
        ...


def _make_doc(kind: SourceKind, cik: str | None, url: str, body: bytes, retrieved_at: datetime,
              from_cache: bool, is_fixture: bool) -> SourceDocument:
    digest = sha256(body)
    return SourceDocument(
        id=f"src:{kind.value}:{cik or 'all'}:{digest[:12]}",
        kind=kind,
        cik=cik,
        url=url,
        retrieved_at=retrieved_at,
        sha256=digest,
        from_cache=from_cache,
        is_fixture=is_fixture,
    )


def _contains_accession(facts: dict, accession: str) -> bool:
    for taxonomy in facts.get("facts", {}).values():
        for concept in taxonomy.values():
            for rows in concept.get("units", {}).values():
                for row in rows:
                    if row.get("accn") == accession:
                        return True
    return False


# --------------------------------------------------------------------------- cache


class HttpCache:
    """Stores raw response bytes plus metadata, keyed by URL."""

    def __init__(self, root: Path):
        self.root = Path(root) / "http"

    def _paths(self, url: str) -> tuple[Path, Path]:
        key = hashlib.sha256(url.encode()).hexdigest()[:40]
        return self.root / f"{key}.body", self.root / f"{key}.meta.json"

    def get(self, url: str) -> tuple[bytes, dict] | None:
        body_p, meta_p = self._paths(url)
        if not (body_p.exists() and meta_p.exists()):
            return None
        body = body_p.read_bytes()
        meta = json.loads(meta_p.read_text())
        if meta.get("sha256") != sha256(body):
            return None  # corrupted entry: treat as a miss
        return body, meta

    def put(self, url: str, body: bytes, retrieved_at: datetime) -> dict:
        body_p, meta_p = self._paths(url)
        body_p.parent.mkdir(parents=True, exist_ok=True)
        meta = {"url": url, "retrieved_at": retrieved_at.isoformat(), "sha256": sha256(body),
                "stored_at_epoch": time.time()}
        tmp = body_p.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(body_p)
        meta_p.write_text(json.dumps(meta))
        return meta


# --------------------------------------------------------------------------- live


@dataclass
class LiveSecSource:
    client: SecHttpClient
    cache: HttpCache
    settings: SecSettings = field(default_factory=SecSettings)
    now: callable = time.time
    is_offline: bool = False
    stats: dict[str, int] = field(default_factory=lambda: {"network": 0, "cache_hits": 0})

    def _fetch(self, url: str, kind: SourceKind, cik: str | None, ttl: float | None,
               reuse_if=None, force_refresh: bool = False, raw: bool = False) -> Fetched:
        cached = None if force_refresh else self.cache.get(url)
        if cached is not None:
            body, meta = cached
            age = self.now() - meta.get("stored_at_epoch", 0)
            payload = body if raw else json.loads(body)
            fresh = (ttl is None or age <= ttl) and (reuse_if is None or reuse_if(payload))
            if fresh:
                self.stats["cache_hits"] += 1
                doc = _make_doc(kind, cik, url, body,
                                datetime.fromisoformat(meta["retrieved_at"]), True, False)
                return Fetched(payload, doc)
        resp = self.client.get(url)
        self.stats["network"] += 1
        retrieved = datetime.now(timezone.utc)
        self.cache.put(url, resp.body, retrieved)
        payload = resp.body if raw else json.loads(resp.body)
        return Fetched(payload, _make_doc(kind, cik, url, resp.body, retrieved, False, False))

    def company_tickers(self) -> Fetched:
        return self._fetch(TICKERS_URL, SourceKind.COMPANY_TICKERS, None, TICKERS_TTL_SECONDS)

    def company_exchanges(self) -> Fetched:
        """Ticker list with the listing exchange. Only used for search display, never for analysis."""
        return self._fetch(TICKERS_EXCHANGE_URL, SourceKind.COMPANY_TICKERS, None, TICKERS_TTL_SECONDS)

    def submissions(self, cik: str) -> Fetched:
        return self._fetch(SUBMISSIONS_URL.format(cik=cik), SourceKind.SUBMISSIONS, cik,
                           self.settings.submissions_ttl_seconds)

    def submissions_page(self, cik: str, name: str) -> Fetched:
        # Older history pages do not change once written.
        return self._fetch(SUBMISSIONS_PAGE_URL.format(name=name), SourceKind.SUBMISSIONS, cik, None)

    def company_facts(self, cik: str, must_include_accession: str | None = None,
                      force_refresh: bool = False) -> Fetched:
        # Reuse cached facts only if they already include the latest annual filing.
        reuse_if = (lambda p: _contains_accession(p, must_include_accession)) if must_include_accession else None
        return self._fetch(COMPANY_FACTS_URL.format(cik=cik), SourceKind.COMPANY_FACTS, cik, None,
                           reuse_if=reuse_if, force_refresh=force_refresh)

    def filing_document(self, cik: str, accession: str, document: str) -> Fetched:
        # Filed documents never change, so a cached copy is always reused.
        url = filing_url(cik, accession, document)
        f = self._fetch(url, SourceKind.FILING_DOCUMENT, cik, None, raw=True)
        f.document = f.document.model_copy(update={"accession": accession})
        return f


# --------------------------------------------------------------------------- offline fixtures


@dataclass
class FixtureSource:
    """Reads SEC-shaped JSON from a fixtures directory.

    Layout::

        <root>/company_tickers.json
        <root>/submissions/CIK##########.json
        <root>/companyfacts/CIK##########.json
        <root>/filings/<accession without dashes>/<primary document>
    """

    root: Path
    is_offline: bool = True
    stats: dict[str, int] = field(default_factory=lambda: {"network": 0, "cache_hits": 0, "fixture_reads": 0})

    def _read(self, rel: str, kind: SourceKind, cik: str | None, raw: bool = False) -> Fetched:
        path = Path(self.root) / rel
        if not path.exists():
            raise SourceNotFoundError(
                f"Offline fixture not found: {path}. Offline mode only covers the bundled fixture companies."
            )
        body = path.read_bytes()
        self.stats["fixture_reads"] += 1
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        doc = _make_doc(kind, cik, f"fixture://{rel}", body, mtime, False, True)
        return Fetched(body if raw else json.loads(body), doc)

    def company_tickers(self) -> Fetched:
        return self._read("company_tickers.json", SourceKind.COMPANY_TICKERS, None)

    def submissions(self, cik: str) -> Fetched:
        return self._read(f"submissions/CIK{cik}.json", SourceKind.SUBMISSIONS, cik)

    def submissions_page(self, cik: str, name: str) -> Fetched:
        return self._read(f"submissions/{name}", SourceKind.SUBMISSIONS, cik)

    def company_facts(self, cik: str, must_include_accession: str | None = None,
                      force_refresh: bool = False) -> Fetched:
        return self._read(f"companyfacts/CIK{cik}.json", SourceKind.COMPANY_FACTS, cik)

    def filing_document(self, cik: str, accession: str, document: str) -> Fetched:
        f = self._read(f"filings/{accession.replace('-', '')}/{document}", SourceKind.FILING_DOCUMENT, cik,
                       raw=True)
        f.document = f.document.model_copy(update={"accession": accession})
        return f
