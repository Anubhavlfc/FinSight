"""Turn raw XBRL rows into one accepted value per (field, fiscal period).

Decision rules, in order:

1. **Period.** A duration value is annual only if it spans
   ``annual_min_days..annual_max_days`` and ends within ``end_date_tolerance_days``
   of the fiscal year end taken from the 10-K. Instant (balance sheet) values must
   have no start date and the same end date. The XBRL ``fy``/``fp`` labels are
   ignored: a 10-K labels its Q4 three-month figure ``fy=2025, fp=FY`` too.
2. **Eligibility.** Only USD values from Form 10-K or 10-K/A filed on or before the
   cutoff date.
3. **Tag.** The highest-priority mapped tag that has a value for every fiscal year
   (comparability). If none does, the best tag per year, with a warning.
4. **Restatement policy.** For a given tag and period, the value from the latest
   filing filed by the cutoff wins (latest comparable presentation). Earlier values
   are kept as ALTERNATIVE facts; if they differ, a ``restated_value`` warning is raised.
5. **Sign.** Fields declared ``positive_outflow`` (capex) are expressed as positive
   outflows; a negative reported value is flipped and disclosed.
6. **Derivation.** A missing field with a ``derive_from`` rule (gross profit =
   revenue - cost of revenue) is computed from accepted facts and marked DERIVED.

Nothing here invents a value: a field with no eligible candidate becomes a
missing-field issue.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from finsight.collector import RawFactRow
from finsight.config import FieldMapping, Mappings, Settings
from finsight.contracts import (
    AnalysisPlan,
    FactStatus,
    FinancialFact,
    FiscalPeriod,
    PeriodType,
    Severity,
    ValidationIssue,
)

ANNUAL_REPORT_FORMS = {"10-K", "10-K/A"}


def fact_id(field_name: str, period: str | None, row: RawFactRow | None = None, extra: str = "") -> str:
    basis = f"{row.taxonomy}:{row.tag}|{row.unit}|{row.start}|{row.end}|{row.val}|{row.accn}|{row.form}" if row else extra
    return f"fact:{field_name}:{period or 'none'}:{hashlib.sha1(basis.encode()).hexdigest()[:10]}"


def money(v: str | Decimal) -> str:
    return f"{Decimal(v):,}"


def within_tolerance(a: Decimal, b: Decimal, settings: Settings) -> bool:
    tol = max(Decimal(str(settings.validation.abs_tolerance_usd)),
              Decimal(str(settings.validation.rel_tolerance)) * max(abs(a), abs(b)))
    return abs(a - b) <= tol


@dataclass
class NormalizationResult:
    accepted: list[FinancialFact] = field(default_factory=list)
    alternatives: list[FinancialFact] = field(default_factory=list)
    rejected: list[FinancialFact] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)
    period_starts: dict[str, date] = field(default_factory=dict)


class _IssueFactory:
    def __init__(self, sink: list[ValidationIssue]):
        self.sink = sink
        self.n = 0

    def __call__(self, code: str, severity: Severity, message: str, **kw) -> ValidationIssue:
        self.n += 1
        issue = ValidationIssue(id=f"issue:{code}:{self.n:03d}", code=code, severity=severity,
                                message=message, **kw)
        self.sink.append(issue)
        return issue


def _match_period(row: RawFactRow, mapping: FieldMapping, periods: list[FiscalPeriod],
                  settings: Settings) -> tuple[FiscalPeriod | None, str | None]:
    """Return (period, None) when the row is an annual value for a period, else (None, reason).

    Returns (None, None) for rows unrelated to any fiscal year end (silently ignored).
    """
    end = date.fromisoformat(row.end)
    tol = settings.periods.end_date_tolerance_days
    period = next((p for p in periods if abs((end - p.end_date).days) <= tol), None)
    if period is None:
        return None, None
    if mapping.period_type == "duration":
        if not row.start:
            return None, "expected a duration value but the fact is an instant"
        days = (end - date.fromisoformat(row.start)).days + 1
        if not (settings.periods.annual_min_days <= days <= settings.periods.annual_max_days):
            return None, f"not an annual duration ({days} days, fy={row.fy}, fp={row.fp})"
    elif row.start:
        return None, "expected an instant (balance sheet) value but the fact covers a duration"
    return period, None


def _to_fact(row: RawFactRow, field_name: str, period: FiscalPeriod | None, status: FactStatus,
             reason: str, source_doc_id: str | None, value: Decimal | None = None) -> FinancialFact:
    raw = Decimal(row.val)
    return FinancialFact(
        id=fact_id(field_name, period.label if period else None, row),
        field=field_name,
        fiscal_period=period.label if period else None,
        value=raw if value is None else value,
        raw_value=raw,
        currency=row.unit if row.unit.isalpha() and len(row.unit) == 3 else "n/a",
        unit=row.unit,
        period_type=PeriodType.DURATION if row.start else PeriodType.INSTANT,
        period_start=date.fromisoformat(row.start) if row.start else None,
        period_end=date.fromisoformat(row.end),
        taxonomy=row.taxonomy,
        tag=row.tag,
        accession=row.accn,
        form=row.form,
        filed=date.fromisoformat(row.filed) if row.filed else None,
        source_document_id=source_doc_id,
        location=f"XBRL companyfacts {row.taxonomy}:{row.tag} [{row.unit}] {row.start or ''}..{row.end} "
                 f"in accession {row.accn}" + (f" (frame {row.frame})" if row.frame else ""),
        status=status,
        status_reason=reason,
    )


def _recency_key(row: RawFactRow) -> tuple:
    # Latest filing wins; on the same date an amendment supersedes the original.
    return (row.filed or "", 1 if row.form == "10-K/A" else 0, row.accn)


def normalize(plan: AnalysisPlan, rows: list[RawFactRow], mappings: Mappings, settings: Settings,
              source_doc_id: str | None) -> NormalizationResult:
    res = NormalizationResult()
    issue = _IssueFactory(res.issues)
    periods = plan.fiscal_periods
    labels = [p.label for p in periods]
    by_tag: dict[str, list[RawFactRow]] = defaultdict(list)
    for r in rows:
        by_tag[f"{r.taxonomy}:{r.tag}"].append(r)

    for mapping in mappings.fields.values():
        # tag -> period label -> eligible rows
        eligible: dict[str, dict[str, list[RawFactRow]]] = {}
        period_of: dict[int, FiscalPeriod] = {}
        for tag in mapping.tags:
            eligible[tag] = defaultdict(list)
            for r in by_tag.get(tag, []):
                period, reason = _match_period(r, mapping, periods, settings)
                if period is None and reason is None:
                    continue
                if reason is None:
                    if r.unit != plan.currency:
                        reason = f"unit {r.unit} is not the plan currency {plan.currency}"
                    elif r.form not in ANNUAL_REPORT_FORMS:
                        reason = f"reported on form {r.form}, not an annual report"
                    elif r.filed and date.fromisoformat(r.filed) > plan.cutoff_date:
                        reason = f"filed {r.filed}, after the analysis cutoff {plan.cutoff_date}"
                if reason:
                    res.rejected.append(_to_fact(r, mapping.name, period, FactStatus.REJECTED, reason, source_doc_id))
                    continue
                eligible[tag][period.label].append(r)
                period_of[id(r)] = period

        covering = [t for t in mapping.tags if all(eligible[t].get(lbl) for lbl in labels)]
        chosen_tag: dict[str, str] = {}
        if covering:
            chosen_tag = {lbl: covering[0] for lbl in labels}
        else:
            for lbl in labels:
                t = next((t for t in mapping.tags if eligible[t].get(lbl)), None)
                if t:
                    chosen_tag[lbl] = t
            if len(set(chosen_tag.values())) > 1:
                issue("mixed_tags_across_years", Severity.WARNING,
                      f"{mapping.name}: no single XBRL tag covers every year; years use "
                      + ", ".join(f"{k}={v}" for k, v in sorted(chosen_tag.items()))
                      + ". Check that the definitions are comparable.",
                      field=mapping.name, details={"tags": chosen_tag})

        for period in periods:
            lbl = period.label
            tag = chosen_tag.get(lbl)
            if tag is None:
                continue  # handled as missing below (after derivations)
            candidates = eligible[tag][lbl]
            # Collapse exact duplicates (same filing, same value; companyfacts repeats rows per frame).
            unique: dict[tuple[str, str], RawFactRow] = {}
            for r in candidates:
                unique.setdefault((r.accn, r.val), r)
            per_filing: dict[str, set[str]] = defaultdict(set)
            for accn, val in unique:
                per_filing[accn].add(val)
            conflicted = {a for a, vals in per_filing.items() if len(vals) > 1}
            ordered = sorted(unique.values(), key=_recency_key, reverse=True)
            selected = next((r for r in ordered if r.accn not in conflicted), None)
            for a in conflicted:
                issue("conflicting_values_same_filing", Severity.WARNING,
                      f"{mapping.name} {lbl}: filing {a} reports different values for {tag}: "
                      + ", ".join(sorted(per_filing[a])) + ". Values from this filing were not used.",
                      field=mapping.name, fiscal_period=lbl, details={"accession": a})
            if selected is None:
                issue("conflicting_values_unresolved", Severity.BLOCKING,
                      f"{mapping.name} {lbl}: every candidate filing reports conflicting values; "
                      "no value accepted.", field=mapping.name, fiscal_period=lbl,
                      affected_metrics=list(mapping.required_for))
                continue
            value = Decimal(selected.val)
            reason = f"tag {tag} (priority {mapping.tags.index(tag) + 1}); latest filing by cutoff ({selected.accn}, filed {selected.filed})"
            if mapping.sign == "positive_outflow" and value < 0:
                value = -value
                issue("sign_normalized", Severity.WARNING,
                      f"{mapping.name} {lbl}: reported as {money(selected.val)}; expressed as a positive cash outflow.",
                      field=mapping.name, fiscal_period=lbl)
                reason += "; sign flipped to positive outflow"
            accepted = _to_fact(selected, mapping.name, period, FactStatus.ACCEPTED, reason, source_doc_id, value)
            res.accepted.append(accepted)
            if accepted.period_start and mapping.name == "revenue":
                res.period_starts[lbl] = accepted.period_start
            for r in ordered:
                if r is selected:
                    continue
                if r.accn in conflicted:
                    res.rejected.append(_to_fact(r, mapping.name, period, FactStatus.REJECTED,
                                                 "conflicting values within the same filing", source_doc_id))
                    continue
                same = Decimal(r.val) == Decimal(selected.val)
                why = (f"duplicate of accepted value, also reported in {r.accn} (filed {r.filed})" if same else
                       f"originally reported {money(r.val)} in {r.accn} (filed {r.filed}); superseded by {money(selected.val)} "
                       f"in {selected.accn} (filed {selected.filed}) under the latest-filing restatement policy")
                alt = _to_fact(r, mapping.name, period, FactStatus.ALTERNATIVE, why, source_doc_id)
                res.alternatives.append(alt)
                if not same:
                    issue("restated_value", Severity.WARNING,
                          f"{mapping.name} {lbl}: {why}.", field=mapping.name, fiscal_period=lbl,
                          fact_ids=[accepted.id, alt.id],
                          details={"original": r.val, "restated": selected.val,
                                   "original_accession": r.accn, "restated_accession": selected.accn})
            # Other tags that also report this period are rejected but disclosed.
            for other in mapping.tags:
                if other == tag or not eligible[other].get(lbl):
                    continue
                best = sorted(eligible[other][lbl], key=_recency_key, reverse=True)[0]
                differs = not within_tolerance(Decimal(best.val), Decimal(selected.val), settings)
                rej = _to_fact(best, mapping.name, period, FactStatus.REJECTED,
                               f"competing tag {other}; {tag} has priority"
                               + (f" (value differs: {money(best.val)} vs {money(selected.val)})" if differs else ""),
                               source_doc_id)
                res.rejected.append(rej)
                if differs:
                    issue("competing_tag_differs",
                          Severity.INFO if mapping.competing_difference_severity == "info" else Severity.WARNING,
                          f"{mapping.name} {lbl}: {other} reports {money(best.val)} but {tag} reports {money(selected.val)}. "
                          f"Using {tag} per the configured priority.",
                          field=mapping.name, fiscal_period=lbl, fact_ids=[accepted.id, rej.id])

    _derive_and_flag_missing(res, plan, mappings, issue)
    return res


def _derive_and_flag_missing(res: NormalizationResult, plan: AnalysisPlan, mappings: Mappings,
                             issue: _IssueFactory) -> None:
    have = {(f.field, f.fiscal_period): f for f in res.accepted}
    for mapping in mappings.fields.values():
        for period in plan.fiscal_periods:
            lbl = period.label
            if (mapping.name, lbl) in have:
                continue
            if mapping.derive_from:
                left, op, right = mapping.derive_from.split()
                a, b = have.get((left, lbl)), have.get((right, lbl))
                if a and b and op in "+-":
                    value = a.value - b.value if op == "-" else a.value + b.value
                    derived = FinancialFact(
                        id=fact_id(mapping.name, lbl, extra=f"derived|{a.id}|{op}|{b.id}"),
                        field=mapping.name, fiscal_period=lbl, value=value, raw_value=value,
                        currency=a.currency, unit=a.unit, period_type=a.period_type,
                        period_start=a.period_start, period_end=a.period_end, taxonomy=None, tag=None,
                        extraction_method="derived", accession=None, form=None, filed=None,
                        source_document_id=None, location=f"derived: {mapping.derive_from}",
                        status=FactStatus.DERIVED,
                        status_reason=f"{mapping.name} not reported; derived as {mapping.derive_from}",
                        input_fact_ids=[a.id, b.id],
                    )
                    res.accepted.append(derived)
                    have[(mapping.name, lbl)] = derived
                    issue("derived_field", Severity.INFO,
                          f"{mapping.name} {lbl} was not reported as a tagged value; derived as {mapping.derive_from}.",
                          field=mapping.name, fiscal_period=lbl, fact_ids=[derived.id, a.id, b.id])
                    continue
            if mapping.optional:
                issue("optional_field_missing",
                      Severity.INFO if mapping.missing_severity == "info" else Severity.WARNING,
                      f"{mapping.name} {lbl}: not reported under any mapped tag ({', '.join(mapping.tags)}). "
                      "Dependent metrics are marked unavailable or not applicable.",
                      field=mapping.name, fiscal_period=lbl, affected_metrics=list(mapping.required_for))
            else:
                issue("required_field_missing", Severity.BLOCKING,
                      f"{mapping.name} {lbl}: no eligible value under any mapped tag ({', '.join(mapping.tags)}). "
                      "Next step: confirm the tag in the filing and add a company mapping, or extract it from "
                      "the filing text (planned for Stage 2). No value was substituted.",
                      field=mapping.name, fiscal_period=lbl, affected_metrics=list(mapping.required_for))
