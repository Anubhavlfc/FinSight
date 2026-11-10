"""INVESTIGATING stage: load 10-K text, then investigate flags in rank order.

* Text for the 10-Ks that explain each flagged comparison is loaded up front;
  the investigator may request other 10-Ks in the window (allowlisted).
* Flags are investigated in rank order up to ``max_findings``; the rest are
  listed as not investigated so the report can disclose them.
* If the run's LLM budget runs out, investigation stops gracefully and the
  remaining flags are disclosed. Findings already made are kept.
* Missing filing text is a disclosed limitation, not a failure.
"""

from __future__ import annotations

import logging
from typing import Callable

from pydantic import BaseModel, Field

from finsight.config import InvestigationSettings
from finsight.contracts import (
    AnalysisPlan,
    CalculatedMetric,
    ChangeFlag,
    FilingPassage,
    InvestigationFinding,
    InvestigationResult,
    SourceDocument,
)
from finsight.errors import AccessDeniedError, SourceNotFoundError, TransientSourceError
from finsight.evidence.search import PassageIndex
from finsight.evidence.text import extract_passages
from finsight.investigator.agent import investigate_flag
from finsight.investigator.tools import ToolContext
from finsight.contracts import EvidenceStatus
from finsight.llm.provider import BudgetExceeded, LLMProvider, LLMRefusal
from finsight.sec.sources import DataSource

log = logging.getLogger(__name__)


class FilingTextCheckpoint(BaseModel):
    passages: list[FilingPassage] = Field(default_factory=list)
    documents: list[SourceDocument] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    loaded_periods: list[str] = Field(default_factory=list)


def load_filing_text(source: DataSource, plan: AnalysisPlan, period: str,
                     max_chars: int) -> tuple[list[FilingPassage], SourceDocument | None, str]:
    fp = next((p for p in plan.fiscal_periods if p.label == period), None)
    if fp is None:
        return [], None, f"{period} is not in the analysis window"
    ref = next((d for d in plan.documents if d.accession == fp.defining_accession), None)
    if ref is None:
        return [], None, f"no filing reference for {period} ({fp.defining_accession})"
    document = ref.url.rsplit("/", 1)[-1]
    if document.endswith("-index.htm"):
        return [], None, f"{period}: primary document name not listed in the filing history"
    try:
        fetched = source.filing_document(plan.company.cik, ref.accession, document)
    except (SourceNotFoundError, AccessDeniedError, TransientSourceError) as exc:
        return [], None, f"could not retrieve the {ref.form} for {period}: {exc}"
    doc = fetched.document.model_copy(update={"form": ref.form, "filing_date": ref.filing_date,
                                              "fiscal_period_end": fp.end_date})
    passages, notes = extract_passages(fetched.payload, doc, accession=ref.accession, form=ref.form,
                                       filing_date=ref.filing_date, fiscal_period=period, max_chars=max_chars)
    return passages, doc, "; ".join(notes)


def periods_to_preload(flags: list[ChangeFlag]) -> list[str]:
    return list(dict.fromkeys(f.comparison.split("->")[1] for f in flags))


def run_investigation(
    plan: AnalysisPlan,
    metrics: list[CalculatedMetric],
    flags: list[ChangeFlag],
    source: DataSource,
    provider: LLMProvider,
    settings: InvestigationSettings,
    text: FilingTextCheckpoint,
    prior: InvestigationResult | None = None,
    only_flags: dict[str, list[str]] | None = None,
    revision: int = 0,
    on_progress: Callable[[InvestigationResult, FilingTextCheckpoint], None] | None = None,
) -> tuple[InvestigationResult, FilingTextCheckpoint]:
    """``prior`` lets a resumed or revision run keep findings already made.
    ``only_flags`` (flag id -> reviewer feedback) re-investigates just those flags."""
    selected = flags[: settings.max_findings]
    result = prior.model_copy(deep=True) if prior else InvestigationResult(
        not_investigated=[f.id for f in flags[settings.max_findings:]])
    if not prior and flags[settings.max_findings:]:
        result.notes.append(f"{len(flags) - settings.max_findings} lower-ranked change(s) not investigated "
                            f"(max_findings = {settings.max_findings}).")

    def load(period: str) -> tuple[list[FilingPassage], str]:
        passages, doc, note = load_filing_text(source, plan, period, settings.passage_max_chars)
        if doc:
            text.documents.append(doc)
        if note:
            text.notes.append(note)
        if passages:
            text.passages.extend(passages)
            text.loaded_periods.append(period)
        return passages, note

    for period in periods_to_preload(selected):
        if period not in text.loaded_periods:
            load(period)
    index = PassageIndex(text.passages)
    by_id = {m.id: m for m in metrics}
    done = {f.change_flag_id for f in result.findings}
    budget_hit = False
    for flag in selected:
        if only_flags is not None:
            if flag.id not in only_flags:
                continue
        elif flag.id in done:
            continue  # resumed run: already investigated
        if budget_hit:
            if flag.id not in result.not_investigated:
                result.not_investigated.append(flag.id)
            continue
        ctx = ToolContext(plan=plan, metrics=by_id, index=index, load_source=load,
                          search_results=settings.search_results,
                          max_additional_sources=settings.max_additional_sources)
        try:
            finding = investigate_flag(flag, ctx, provider, settings, plan.request.question,
                                       feedback=(only_flags or {}).get(flag.id), revision=revision)
        except BudgetExceeded as exc:
            budget_hit = True
            result.notes.append(f"Investigation stopped early: {exc}. Remaining changes are listed as not "
                                "investigated.")
            if flag.id not in result.not_investigated and flag.id not in done:
                result.not_investigated.append(flag.id)
            continue
        except LLMRefusal as exc:
            finding = InvestigationFinding(
                id=f"finding:{flag.id}", change_flag_id=flag.id, calculated_observation=flag.description,
                evidence_status=EvidenceStatus.UNSUPPORTED, status_reason=f"the model declined: {exc}",
                metric_ids=list(flag.metric_ids), produced_by=provider.name, revision=revision,
                unresolved_questions=["The investigation could not be completed for this change."])
        result.findings = [f for f in result.findings if f.change_flag_id != flag.id] + [finding]
        if flag.id in result.not_investigated:
            result.not_investigated.remove(flag.id)
        result.llm_usage = provider.usage.summary(provider.name, provider.is_stub)
        if on_progress:
            on_progress(result, text)
    order = {f.id: n for n, f in enumerate(flags)}
    result.findings.sort(key=lambda f: order.get(f.change_flag_id, 999))
    result.llm_usage = provider.usage.summary(provider.name, provider.is_stub)
    if not text.passages:
        result.notes.append("No 10-K text could be loaded, so no change could be explained from disclosures.")
    return result, text
