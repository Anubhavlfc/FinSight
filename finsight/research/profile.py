"""Company header fields and the few extra XBRL values the valuation needs, all from SEC data.

* Exchange: ``exchanges`` in the SEC submissions file.
* Sector: the SEC filer-review office (``ownerOrg``, e.g. "06 Technology"), else the SIC division.
* Industry: the SIC description.
* Diluted shares and short-term investments: us-gaap facts from the latest 10-K in company facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from finsight.contracts import AnalysisPlan
from finsight.sec.sources import DataSource

# SIC divisions (first two digits), used only when the SEC office is not in the submissions file.
_SIC_DIVISIONS = [
    (1, 9, "Agriculture, Forestry and Fishing"), (10, 14, "Mining"), (15, 17, "Construction"),
    (20, 39, "Manufacturing"), (40, 49, "Transportation, Communications and Utilities"),
    (50, 51, "Wholesale Trade"), (52, 59, "Retail Trade"), (60, 67, "Finance, Insurance and Real Estate"),
    (70, 89, "Services"), (91, 99, "Public Administration"),
]

SHARES_TAGS = ("WeightedAverageNumberOfDilutedSharesOutstanding",)
SHORT_TERM_INVESTMENT_TAGS = ("ShortTermInvestments", "MarketableSecuritiesCurrent",
                              "AvailableForSaleSecuritiesDebtSecuritiesCurrent")


@dataclass
class XbrlValue:
    value: Decimal
    tag: str
    accession: str | None
    end: date
    form: str | None
    filed: str | None


@dataclass
class ProfileData:
    exchange: str
    sector: str
    sector_basis: str
    industry: str
    submissions_url: str | None
    company_facts_url: str | None
    diluted_shares: XbrlValue | None
    short_term_investments: XbrlValue | None
    notes: list[str]


def sic_division(sic: str | None) -> str | None:
    try:
        two = int(str(sic)[:2])
    except (TypeError, ValueError):
        return None
    return next((name for lo, hi, name in _SIC_DIVISIONS if lo <= two <= hi), None)


def find_fact(facts: dict, tags: tuple[str, ...], unit: str, end: date, accession: str | None,
              duration: bool) -> XbrlValue | None:
    """The fact for ``end`` from the given filing (else any 10-K), trying ``tags`` in order."""
    gaap = (facts or {}).get("facts", {}).get("us-gaap", {})
    for tag in tags:
        rows = gaap.get(tag, {}).get("units", {}).get(unit, [])
        hits = [r for r in rows if r.get("end") == end.isoformat() and (("start" in r) == duration)
                and r.get("form") in ("10-K", "10-K/A")]
        if duration:
            hits = [r for r in hits if 350 <= (date.fromisoformat(r["end"]) - date.fromisoformat(r["start"])).days <= 380]
        if not hits:
            continue
        best = next((r for r in hits if r.get("accn") == accession), hits[-1])
        return XbrlValue(Decimal(str(best["val"])), f"us-gaap:{tag}", best.get("accn"), end, best.get("form"),
                         best.get("filed"))
    return None


def load_profile(plan: AnalysisPlan, source: DataSource | None) -> ProfileData:
    c = plan.company
    notes: list[str] = []
    sub: dict = {}
    facts: dict = {}
    sub_url = facts_url = None
    if source is not None:
        try:
            f = source.submissions(c.cik)
            sub, sub_url = f.payload or {}, f.document.url
        except Exception as exc:  # header fields degrade to "not available", the report still renders
            notes.append(f"SEC submissions file could not be loaded ({exc.__class__.__name__}).")
        try:
            f = source.company_facts(c.cik)
            facts, facts_url = f.payload or {}, f.document.url
        except Exception as exc:
            notes.append(f"SEC company facts could not be loaded ({exc.__class__.__name__}).")
    exchanges = [e for e in (sub.get("exchanges") or []) if e]
    exchange = ", ".join(dict.fromkeys(exchanges)) or "Not reported to SEC"
    owner = (sub.get("ownerOrg") or "").strip()
    if owner:
        sector = owner.split(" ", 1)[1] if owner[:2].isdigit() and " " in owner else owner
        basis = "SEC Division of Corporation Finance review office for the filer"
    else:
        sector = sic_division(c.sic) or "Not available"
        basis = "SIC division of the filer's SIC code"
    industry = c.sic_description or sub.get("sicDescription") or "Not available"
    if c.sic:
        industry = f"{industry} (SIC {c.sic})"
    latest = plan.fiscal_periods[-1]
    shares = find_fact(facts, SHARES_TAGS, "shares", latest.end_date, latest.defining_accession, duration=True)
    sti = find_fact(facts, SHORT_TERM_INVESTMENT_TAGS, "USD", latest.end_date, latest.defining_accession,
                    duration=False)
    if facts and shares is None:
        notes.append(f"Diluted weighted-average shares for {latest.label} were not found in company facts.")
    if facts and sti is None:
        notes.append(f"No short-term investments line for {latest.label}; treated as zero in the valuation.")
    return ProfileData(exchange, sector, basis, industry, sub_url, facts_url, shares, sti, notes)
