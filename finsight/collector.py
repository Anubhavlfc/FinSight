"""Collect XBRL company facts for the plan's fields.

Only rows for mapped tags are kept, which keeps checkpoints small. The full
download stays in the HTTP cache with its hash, so it can be re-read.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from finsight.config import Mappings
from finsight.contracts import AnalysisPlan, SourceDocument
from finsight.sec.sources import DataSource


class RawFactRow(BaseModel):
    taxonomy: str
    tag: str
    label: str | None = None
    unit: str
    start: str | None = None
    end: str
    val: str  # kept as text to preserve exact precision
    accn: str
    fy: int | None = None
    fp: str | None = None
    form: str | None = None
    filed: str | None = None
    frame: str | None = None


class CollectionResult(BaseModel):
    rows: list[RawFactRow]
    company_facts_document: SourceDocument
    contains_latest_accession: bool
    latest_accession: str
    attempts: int = 1
    tags_missing_from_source: list[str] = Field(default_factory=list)


def collect(plan: AnalysisPlan, source: DataSource, mappings: Mappings,
            force_refresh: bool = False, attempt: int = 1) -> CollectionResult:
    latest = plan.fiscal_periods[-1].defining_accession
    fetched = source.company_facts(plan.company.cik, must_include_accession=latest,
                                   force_refresh=force_refresh)
    facts = fetched.payload.get("facts", {})
    wanted = {tag for m in mappings.fields.values() for tag in m.tags}
    rows: list[RawFactRow] = []
    seen_latest = False
    missing: list[str] = []
    for qualified in sorted(wanted):
        taxonomy, tag = qualified.split(":", 1)
        concept = facts.get(taxonomy, {}).get(tag)
        if concept is None:
            missing.append(qualified)
            continue
        for unit, unit_rows in concept.get("units", {}).items():
            for r in unit_rows:
                rows.append(RawFactRow(
                    taxonomy=taxonomy, tag=tag, label=concept.get("label"), unit=unit,
                    start=r.get("start"), end=r["end"], val=str(r["val"]), accn=r["accn"],
                    fy=r.get("fy"), fp=r.get("fp"), form=r.get("form"), filed=r.get("filed"),
                    frame=r.get("frame"),
                ))
    for taxonomy in facts.values():
        for concept in taxonomy.values():
            for unit_rows in concept.get("units", {}).values():
                if any(r.get("accn") == latest for r in unit_rows):
                    seen_latest = True
                    break
            if seen_latest:
                break
        if seen_latest:
            break
    return CollectionResult(rows=rows, company_facts_document=fetched.document,
                            contains_latest_accession=seen_latest, latest_accession=latest,
                            attempts=attempt, tags_missing_from_source=missing)
