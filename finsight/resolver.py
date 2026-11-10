"""Company identity resolution and scope checks.

Resolution order: CIK (digits) -> exact ticker -> company name. A name that
matches more than one company raises ``AmbiguousCompanyError`` so the workflow
can ask the user instead of guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from finsight.config import Settings
from finsight.contracts import Company, SourceDocument
from finsight.errors import AmbiguousCompanyError, CompanyNotFoundError, UnsupportedCompanyError
from finsight.sec.sources import DataSource

ANNUAL_FORMS = {"10-K", "10-K/A"}
FOREIGN_ANNUAL_FORMS = {"20-F", "20-F/A", "40-F", "40-F/A"}

_SUFFIXES = {"INC", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "PLC", "LLC", "HOLDINGS", "THE", "LP"}


def pad_cik(value: str | int) -> str:
    return str(int(value)).zfill(10)


def _norm_name(s: str) -> list[str]:
    tokens = re.sub(r"[^A-Z0-9 ]", " ", s.upper()).split()
    return [t for t in tokens if t not in _SUFFIXES]


@dataclass
class Resolution:
    company: Company
    submissions: dict
    documents: list[SourceDocument]


def _candidates_by_name(query: str, rows: list[dict]) -> list[dict]:
    q = _norm_name(query)
    if not q:
        return []
    by_cik: dict[str, dict] = {}
    exact: dict[str, dict] = {}
    for r in rows:
        title_tokens = _norm_name(r["title"])
        cik = pad_cik(r["cik_str"])
        if title_tokens == q:
            exact.setdefault(cik, {"cik": cik, "title": r["title"], "tickers": []})["tickers"].append(r["ticker"])
        if all(t in title_tokens for t in q):
            by_cik.setdefault(cik, {"cik": cik, "title": r["title"], "tickers": []})["tickers"].append(r["ticker"])
    if len(exact) == 1:
        return list(exact.values())
    return list(by_cik.values())


def find_cik(identifier: str, source: DataSource) -> tuple[str, list[SourceDocument]]:
    ident = identifier.strip()
    if not ident:
        raise CompanyNotFoundError("Empty company identifier")
    if ident.isdigit() and len(ident) <= 10:
        return pad_cik(ident), []
    fetched = source.company_tickers()
    rows = list(fetched.payload.values()) if isinstance(fetched.payload, dict) else list(fetched.payload)
    ticker = ident.upper().replace(".", "-")
    for r in rows:
        if r["ticker"].upper().replace(".", "-") == ticker:
            return pad_cik(r["cik_str"]), [fetched.document]
    matches = _candidates_by_name(ident, rows)
    if len(matches) == 1:
        return matches[0]["cik"], [fetched.document]
    if len(matches) > 1:
        raise AmbiguousCompanyError(ident, sorted(matches, key=lambda m: m["title"])[:15])
    raise CompanyNotFoundError(f"No SEC registrant matches '{ident}' by ticker, CIK or name")


def check_scope(company: Company, submissions: dict, settings: Settings) -> None:
    if company.sic and company.sic.isdigit():
        sic = int(company.sic)
        for rng in settings.scope.unsupported_sic_ranges:
            if rng.low <= sic <= rng.high:
                raise UnsupportedCompanyError(
                    f"{company.name} (SIC {company.sic}, {company.sic_description or 'n/a'}) falls under the "
                    f"'{rng.profile}' profile, which this version does not support. {rng.reason} "
                    "Please choose a nonfinancial operating company that files Form 10-K.",
                    profile=rng.profile,
                )
    forms = set(submissions.get("filings", {}).get("recent", {}).get("form", []))
    if not forms & ANNUAL_FORMS:
        if forms & FOREIGN_ANNUAL_FORMS:
            raise UnsupportedCompanyError(
                f"{company.name} files annual reports on Form 20-F/40-F (foreign private issuer). "
                "This version supports US domestic filers that file Form 10-K.",
                profile="foreign_private_issuer",
            )


def company_from_submissions(cik: str, sub: dict) -> Company:
    return Company(
        cik=cik,
        name=sub.get("name", ""),
        tickers=list(sub.get("tickers", []) or []),
        sic=str(sub.get("sic") or "") or None,
        sic_description=sub.get("sicDescription") or None,
        fiscal_year_end=sub.get("fiscalYearEnd") or None,
        state_of_incorporation=sub.get("stateOfIncorporation") or None,
    )


def resolve_company(identifier: str, source: DataSource, settings: Settings) -> Resolution:
    cik, docs = find_cik(identifier, source)
    fetched = source.submissions(cik)
    sub = fetched.payload
    company = company_from_submissions(cik, sub)
    check_scope(company, sub, settings)
    return Resolution(company=company, submissions=sub, documents=docs + [fetched.document])
