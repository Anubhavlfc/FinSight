"""Company lookup for the selection form: search, profile, supported fiscal years.

Search reads the SEC ticker lists (cached by the same HTTP cache as the pipeline).
Scope checks and fiscal-year windows reuse the resolver and planner, so the form
offers exactly what an analysis would accept.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import date
from typing import Callable

from finsight.api import schemas as s
from finsight.config import Settings, load_mappings
from finsight.errors import CompanyNotFoundError, SourceNotFoundError, UnsupportedCompanyError
from finsight.planner import PlanningError, filing_rows, select_fiscal_periods
from finsight.resolver import check_scope, company_from_submissions, find_cik, pad_cik
from finsight.sec.sources import DataSource, FixtureSource

# The synthetic fixture's filings run to mid-2026; a fixed cutoff keeps the sample reproducible.
SAMPLE_CUTOFF = date(2026, 11, 1)
INDEX_TTL_SECONDS = 6 * 3600
WINDOW = 3


@dataclass
class _Entry:
    cik: str
    name: str
    tickers: list[str]
    exchange: str | None
    synthetic: bool
    name_key: str = ""

    def hit(self) -> s.CompanyHit:
        return s.CompanyHit(cik=self.cik, name=self.name, ticker=self.tickers[0] if self.tickers else None,
                            tickers=self.tickers, exchange=self.exchange, synthetic=self.synthetic)


def _group(rows: list[tuple[str, str, str, str | None]], synthetic: bool) -> list[_Entry]:
    by_cik: dict[str, _Entry] = {}
    for cik, name, ticker, exchange in rows:
        e = by_cik.get(cik)
        if e is None:
            e = by_cik[cik] = _Entry(cik, name, [], exchange, synthetic, name.lower())
        if ticker and ticker not in e.tickers:
            e.tickers.append(ticker)
        e.exchange = e.exchange or exchange
    return list(by_cik.values())


class CompanyDirectory:
    def __init__(self, settings: Settings, fixture: FixtureSource, live_source: Callable[[], DataSource | None]):
        self.settings = settings
        self.fixture = fixture
        self._live_source = live_source
        self._lock = threading.Lock()
        self._live: list[_Entry] | None = None
        self._live_at = 0.0
        self._fixture_entries = self._load_fixture()

    # ---- indexes ------------------------------------------------------------------------
    def _load_fixture(self) -> list[_Entry]:
        payload = self.fixture.company_tickers().payload
        rows = [(pad_cik(r["cik_str"]), r["title"], r["ticker"], "Synthetic") for r in payload.values()]
        entries = _group(rows, synthetic=True)
        # Only offer fixture companies that have a filing history to resolve.
        return [e for e in entries if (self.fixture.root / "submissions" / f"CIK{e.cik}.json").exists()]

    def _live_entries(self) -> list[_Entry]:
        source = self._live_source()
        if source is None:
            return []
        with self._lock:
            if self._live is not None and time.monotonic() - self._live_at < INDEX_TTL_SECONDS:
                return self._live
            rows: list[tuple[str, str, str, str | None]] = []
            try:
                payload = source.company_exchanges().payload  # type: ignore[attr-defined]
                f = payload["fields"]
                i_cik, i_name, i_tic, i_ex = f.index("cik"), f.index("name"), f.index("ticker"), f.index("exchange")
                rows = [(pad_cik(r[i_cik]), r[i_name], r[i_tic], r[i_ex]) for r in payload["data"]]
            except Exception:  # the exchange list is a display nicety; fall back to the plain ticker list
                payload = source.company_tickers().payload
                rows = [(pad_cik(r["cik_str"]), r["title"], r["ticker"], None) for r in payload.values()]
            self._live, self._live_at = _group(rows, synthetic=False), time.monotonic()
            return self._live

    def is_fixture(self, identifier: str) -> bool:
        ident = identifier.strip()
        if ident.isdigit():
            return any(e.cik == pad_cik(ident) for e in self._fixture_entries)
        return any(ident.upper() in e.tickers for e in self._fixture_entries)

    # ---- search -------------------------------------------------------------------------
    def search(self, query: str, limit: int = 8) -> tuple[list[s.CompanyHit], bool]:
        q = query.strip().lower()
        live = self._live_entries()
        if not q:
            return [], bool(live)
        scored: list[tuple[int, int, _Entry]] = []
        for n, e in enumerate(live + self._fixture_entries):
            tickers = [t.lower() for t in e.tickers]
            if q in tickers:
                score = 0
            elif any(t.startswith(q) for t in tickers):
                score = 1
            elif e.name_key.startswith(q):
                score = 2
            elif any(w.startswith(q) for w in e.name_key.split()):
                score = 3
            elif len(q) >= 3 and q in e.name_key:
                score = 4
            elif q.isdigit() and e.cik.lstrip("0").startswith(q.lstrip("0") or "0"):
                score = 5
            else:
                continue
            scored.append((score, n, e))
        scored.sort(key=lambda t: (t[0], t[1]))
        return [e.hit() for _, _, e in scored[:limit]], bool(live)

    def examples(self) -> list[s.CompanyHit]:
        return [e.hit() for e in self._fixture_entries[:1]]

    # ---- profile ------------------------------------------------------------------------
    def source_for(self, identifier: str) -> tuple[DataSource | None, bool]:
        if self.is_fixture(identifier):
            return self.fixture, True
        return self._live_source(), False

    def profile(self, identifier: str, today: date | None = None) -> s.CompanyProfile:
        """Raises CompanyNotFoundError / AmbiguousCompanyError / source errors; unsupported is a normal result."""
        source, synthetic = self.source_for(identifier)
        if source is None:
            raise LiveUnavailable()
        cutoff = SAMPLE_CUTOFF if synthetic else (today or date.today())
        cik, _ = find_cik(identifier, source)
        try:
            sub = source.submissions(cik).payload
        except SourceNotFoundError as exc:
            raise CompanyNotFoundError(f"No SEC filing history found for CIK {cik}.") from exc
        company = company_from_submissions(cik, sub)
        exchange = next((e.exchange for e in (self._fixture_entries if synthetic else self._live_entries())
                         if e.cik == cik), None)
        fye = company.fiscal_year_end
        base = dict(cik=cik, name=company.name, tickers=company.tickers, exchange=exchange, sic=company.sic,
                    industry=company.sic_description,
                    fiscal_year_end=f"{fye[:2]}-{fye[2:]}" if fye and len(fye) == 4 else None, synthetic=synthetic)
        try:
            check_scope(company, sub, self.settings)
        except UnsupportedCompanyError as exc:
            return s.CompanyProfile(**base, supported=False, unsupported_reason=str(exc), next_steps=[
                "Choose a US nonfinancial operating company that files Form 10-K.",
                "Open the sample analysis to see the full workflow."])
        rows = filing_rows(sub, cik, source, WINDOW + 3, cutoff, self.settings.sec.max_history_pages)
        years = sorted({date.fromisoformat(r["reportDate"]).year for r in rows
                        if r["form"] == "10-K" and r.get("reportDate")
                        and date.fromisoformat(r["filingDate"]) <= cutoff}, reverse=True)
        options: list[s.FiscalYearOption] = []
        notes: list[str] = []
        for y in years[:6]:
            try:
                periods, period_notes = select_fiscal_periods(rows, cutoff, WINDOW, y)
            except PlanningError:
                continue
            if len(periods) < 2:
                continue
            last = periods[-1]
            options.append(s.FiscalYearOption(
                fiscal_year=y, label=last.label, period_end=last.end_date, filed=last.defining_filing_date,
                accession=last.defining_accession, window=[p.label for p in periods],
                complete=len(periods) == WINDOW))
            if y == years[0]:
                notes += [n for n in period_notes if "transition" in n]
        if not options:
            return s.CompanyProfile(
                **base, supported=False,
                unsupported_reason=f"{company.name} has fewer than two Form 10-K annual reports on file, so a "
                                   "multi-year comparison cannot be built.",
                next_steps=["Choose a company with a longer filing history.",
                            "Open the sample analysis to see the full workflow."])
        mappings = load_mappings(self.settings.config_dir, cik)
        if mappings.company_overrides_applied:
            notes.append("Company-specific XBRL tag overrides are configured for this company.")
        if not options[0].complete:
            notes.append(f"Only {len(options[0].window)} fiscal years are on file; the analysis will cover those.")
        return s.CompanyProfile(**base, supported=True, fiscal_years=options,
                                default_fiscal_year=options[0].fiscal_year, notes=notes)


class LiveUnavailable(Exception):
    """Live SEC access is not configured on this server."""
