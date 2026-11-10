"""Turn an AnalysisRequest into a validated AnalysisPlan.

Fiscal periods are established from the ``reportDate`` (period of report) of
original Form 10-K filings filed on or before the cutoff date. The XBRL ``fy``
label is never used to decide which year a value belongs to.
"""

from __future__ import annotations

from datetime import date

from finsight.config import Mappings, Settings
from finsight.contracts import AnalysisPlan, AnalysisRequest, Company, FiscalPeriod, SourceRef
from finsight.errors import FinSightError
from finsight.metrics import METRIC_NAMES
from finsight.resolver import ANNUAL_FORMS
from finsight.sec.sources import DataSource, filing_url


class PlanningError(FinSightError):
    pass


def _rows(block: dict) -> list[dict]:
    keys = ["accessionNumber", "filingDate", "reportDate", "form", "primaryDocument"]
    n = len(block.get("accessionNumber", []))
    out = []
    for i in range(n):
        out.append({k: (block.get(k) or [None] * n)[i] for k in keys})
    return out


def filing_rows(submissions: dict, cik: str, source: DataSource | None, needed: int,
                cutoff: date, max_pages: int) -> list[dict]:
    """All filings from the recent block, plus older pages only if too few annual reports were found."""
    rows = _rows(submissions.get("filings", {}).get("recent", {}))

    def annual_ends(rs: list[dict]) -> set[str]:
        return {r["reportDate"] for r in rs if r["form"] == "10-K" and r["reportDate"]
                and date.fromisoformat(r["filingDate"]) <= cutoff}

    pages = submissions.get("filings", {}).get("files", []) or []
    for page in pages[:max_pages]:
        if len(annual_ends(rows)) >= needed or source is None:
            break
        rows += _rows(source.submissions_page(cik, page["name"]).payload)
    return rows


def select_fiscal_periods(rows: list[dict], cutoff: date, num_years: int,
                          latest_fiscal_year: int | None) -> tuple[list[FiscalPeriod], list[str]]:
    notes: list[str] = []
    originals: dict[date, dict] = {}
    for r in rows:
        if r["form"] != "10-K" or not r.get("reportDate"):
            continue
        filed = date.fromisoformat(r["filingDate"])
        if filed > cutoff:
            continue
        end = date.fromisoformat(r["reportDate"])
        # Keep the earliest original 10-K for each period end; later ones would be duplicates.
        if end not in originals or filed < date.fromisoformat(originals[end]["filingDate"]):
            originals[end] = r
    if any(r["form"] == "10-KT" and date.fromisoformat(r["filingDate"]) <= cutoff for r in rows):
        notes.append("A transition-period report (10-KT) exists; transition periods are not treated as fiscal years.")
    ends = sorted(originals, reverse=True)
    if latest_fiscal_year is not None:
        ends = [e for e in ends if e.year <= latest_fiscal_year]
        if not ends or ends[0].year != latest_fiscal_year:
            raise PlanningError(
                f"No Form 10-K for a fiscal year ending in {latest_fiscal_year} was filed by {cutoff.isoformat()}."
            )
    chosen = sorted(ends[:num_years])
    if not chosen:
        raise PlanningError(f"No Form 10-K filed on or before {cutoff.isoformat()}.")
    if len(chosen) < num_years:
        notes.append(f"Only {len(chosen)} annual report(s) available by the cutoff; {num_years} requested.")
    labels = [f"FY{e.year}" for e in chosen]
    if len(set(labels)) != len(labels):  # e.g. a fiscal-year-end change produced two ends in one year
        labels = [f"FY{e.isoformat()}" for e in chosen]
    periods = [
        FiscalPeriod(label=lbl, end_date=e, defining_accession=originals[e]["accessionNumber"],
                     defining_filing_date=date.fromisoformat(originals[e]["filingDate"]))
        for lbl, e in zip(labels, chosen)
    ]
    return periods, notes


def build_plan(request: AnalysisRequest, company: Company, submissions: dict, source: DataSource | None,
               settings: Settings, mappings: Mappings) -> AnalysisPlan:
    rows = filing_rows(submissions, company.cik, source, request.num_years + 1, request.cutoff_date,
                       settings.sec.max_history_pages)
    periods, notes = select_fiscal_periods(rows, request.cutoff_date, request.num_years,
                                           request.latest_fiscal_year)
    first_end = periods[0].end_date
    docs = []
    for r in rows:
        if r["form"] not in ANNUAL_FORMS or not r.get("reportDate"):
            continue
        filed = date.fromisoformat(r["filingDate"])
        rep = date.fromisoformat(r["reportDate"])
        if filed <= request.cutoff_date and rep >= first_end:
            docs.append(SourceRef(accession=r["accessionNumber"], form=r["form"], filing_date=filed,
                                  report_date=rep, url=filing_url(company.cik, r["accessionNumber"],
                                                                  r.get("primaryDocument"))))
    docs.sort(key=lambda d: (d.filing_date, d.accession))
    if mappings.company_overrides_applied:
        notes.append("Company-specific tag overrides applied for: " + ", ".join(mappings.company_overrides_applied))
    return AnalysisPlan(
        request=request,
        company=company,
        cutoff_date=request.cutoff_date,
        fiscal_periods=periods,
        required_fields=sorted(mappings.fields),
        metrics=list(METRIC_NAMES),
        documents=docs,
        notes=notes,
    )
