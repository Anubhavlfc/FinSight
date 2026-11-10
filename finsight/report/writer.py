"""DRAFTING stage: assemble the report from validated metrics and findings.

The writer adds no new facts. Every number comes from a ``CalculatedMetric``
through ``fmt_metric``; every management statement comes from a finding whose
quotes were verified against the filing. Each statement in the report is a
``ReportClaim`` that cites metric IDs and/or passage IDs, so the reviewer can
check it and a reader can trace it.

Revision: the reviewer may exclude claims (``exclusions``: claim ID -> reason).
Excluded claims are left out of the text, kept in ``excluded_claims`` for the
audit trail, and disclosed in the limitations section.

The Markdown and HTML are rendered from one block list so they cannot drift.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from finsight.contracts import (
    AnalysisPlan,
    AnalysisResult,
    CalculatedMetric,
    ChangeFlag,
    EvidenceStatus,
    FilingPassage,
    InvestigationFinding,
    InvestigationResult,
    MetricStatus,
    ReportClaim,
    ReportDraft,
    Severity,
    SourceDocument,
    ValidationReport,
)
from finsight.export import CHANGE_ROWS, FINANCIAL_ROWS, presentation_tables
from finsight.presentation import fmt_metric
from finsight.report.charts import build_chart_specs

STATUS_LABEL = {EvidenceStatus.SUPPORTED: "Supported", EvidenceStatus.PARTIALLY_SUPPORTED: "Partially supported",
                EvidenceStatus.UNSUPPORTED: "Unsupported / unexplained"}
FIXTURE_NOTE = ("OFFLINE DEMO: SYNTHETIC FIXTURE DATA. The company, figures and filing text are made up for "
                "testing and do not describe any real company.")
STUB_NOTE = ("Investigation by the OFFLINE STUB, a rule-based stand-in that is not an AI model: it quotes "
             "filing sentences that contain explicit attribution language and writes no interpretation. "
             "Narrative review by an LLM was not performed.")


@dataclass
class ReportInputs:
    plan: AnalysisPlan
    validation: ValidationReport
    analysis: AnalysisResult
    investigation: InvestigationResult | None
    passages: list[FilingPassage]
    data_sources: list[SourceDocument]
    is_fixture: bool
    llm_is_stub: bool
    llm_name: str
    exclusions: dict[str, str] = field(default_factory=dict)
    revision: int = 0
    extra_limitations: list[str] = field(default_factory=list)


class _Doc:
    """Blocks rendered to both Markdown and HTML."""

    def __init__(self):
        self.blocks: list[tuple] = []

    def h(self, level: int, text: str):
        self.blocks.append(("h", level, text))

    def p(self, text: str, cls: str = ""):
        self.blocks.append(("p", text, cls))

    def quote(self, text: str):
        self.blocks.append(("quote", text))

    def ul(self, items: list[str]):
        if items:
            self.blocks.append(("ul", items))

    def table(self, header: list[str], rows: list[list[str]]):
        self.blocks.append(("table", header, rows))

    def img(self, src: str, alt: str, caption: str):
        self.blocks.append(("img", src, alt, caption))

    def markdown(self) -> str:
        out = []
        for b in self.blocks:
            if b[0] == "h":
                out.append("#" * b[1] + " " + b[2])
            elif b[0] == "p":
                out.append(b[1])
            elif b[0] == "quote":
                out.append("> " + b[1])
            elif b[0] == "ul":
                out.append("\n".join(f"- {i}" for i in b[1]))
            elif b[0] == "table":
                esc = lambda c: str(c).replace("|", "\\|")  # noqa: E731
                lines = ["| " + " | ".join(map(esc, b[1])) + " |", "|" + "---|" * len(b[1])]
                lines += ["| " + " | ".join(map(esc, r)) + " |" for r in b[2]]
                out.append("\n".join(lines))
            elif b[0] == "img":
                out.append(f"![{b[2]}]({b[1]})\n\n*{b[3]}*")
        return "\n\n".join(out) + "\n"

    @staticmethod
    def _inline(text: str) -> str:
        t = html.escape(text, quote=False)
        t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
        t = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<em>\1</em>", t)
        t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
        t = re.sub(r"\[(P|M)(\d+)\]", lambda m: f'<a href="#{m.group(1).lower()}{m.group(2)}" class="cite">'
                                                f'[{m.group(1)}{m.group(2)}]</a>', t)
        return t

    def html(self, title: str) -> str:
        out = []
        for b in self.blocks:
            if b[0] == "h":
                hid = re.sub(r"[^a-z0-9]+", "-", b[2].lower()).strip("-")
                out.append(f'<h{b[1]} id="{hid}">{self._inline(b[2])}</h{b[1]}>')
            elif b[0] == "p":
                anchor = re.match(r"\*\*\[(P|M)(\d+)\]\*\*", b[1])
                aid = f' id="{anchor.group(1).lower()}{anchor.group(2)}"' if anchor else ""
                cls = f' class="{b[2]}"' if b[2] else ""
                out.append(f"<p{aid}{cls}>{self._inline(b[1])}</p>")
            elif b[0] == "quote":
                out.append(f"<blockquote>{self._inline(b[1])}</blockquote>")
            elif b[0] == "ul":
                out.append("<ul>" + "".join(f"<li>{self._inline(i)}</li>" for i in b[1]) + "</ul>")
            elif b[0] == "table":
                head = "".join(f"<th>{self._inline(str(c))}</th>" for c in b[1])
                rows = "".join("<tr>" + "".join(f"<td>{self._inline(str(c))}</td>" for c in r) + "</tr>"
                               for r in b[2])
                out.append(f'<div class="tw"><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>')
            elif b[0] == "img":
                out.append(f'<figure><img src="{html.escape(b[1])}" alt="{html.escape(b[2])}">'
                           f"<figcaption>{self._inline(b[3])}</figcaption></figure>")
        return HTML_TEMPLATE.format(title=html.escape(title), body="\n".join(out))


HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{ --bg:#fcfcfb; --ink:#0b0b0b; --muted:#52514e; --rule:#e4e3df; --accent:#2a78d6; --warn-bg:#fff4dc; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#1a1a19; --ink:#f0efec; --muted:#c3c2b7; --rule:#383835;
  --accent:#3987e5; --warn-bg:#3a2f12; }} img {{ background:#fcfcfb; }} }}
body {{ background:var(--bg); color:var(--ink); font:15px/1.55 system-ui,-apple-system,Segoe UI,sans-serif;
  max-width:960px; margin:0 auto; padding:24px 16px 64px; }}
h1 {{ font-size:1.6rem; }} h2 {{ margin-top:2.2rem; border-bottom:1px solid var(--rule); padding-bottom:.3rem; }}
h3 {{ margin-top:1.6rem; }}
p.banner {{ background:var(--warn-bg); padding:.6rem .8rem; border-radius:6px; }}
blockquote {{ margin:.6rem 0; padding:.2rem .9rem; border-left:3px solid var(--accent); color:var(--muted); }}
.tw {{ overflow-x:auto; }} table {{ border-collapse:collapse; font-size:.88rem; margin:.6rem 0; }}
th,td {{ border-bottom:1px solid var(--rule); padding:.3rem .6rem; text-align:right; white-space:nowrap; }}
th:first-child,td:first-child {{ text-align:left; white-space:normal; }}
figure {{ margin:1rem 0; }} figure img {{ max-width:100%; height:auto; border-radius:6px; }}
figcaption {{ color:var(--muted); font-size:.85rem; }} a.cite {{ color:var(--accent); text-decoration:none;
  font-size:.85em; }} code {{ font-size:.85em; word-break:break-all; }}
</style></head><body>
{body}
</body></html>
"""


class _Writer:
    def __init__(self, inp: ReportInputs):
        self.inp = inp
        self.metrics = {m.id: m for m in inp.analysis.metrics}
        self.passages = {p.id: p for p in inp.passages}
        self.labels = [p.label for p in inp.validation.fiscal_periods]
        self.doc = _Doc()
        self.claims: list[ReportClaim] = []
        self.excluded: list[ReportClaim] = []
        self.p_num: dict[str, int] = {}
        self.m_num: dict[str, int] = {}

    # ---- citations ----------------------------------------------------------------------
    def cite_p(self, pid: str) -> str:
        self.p_num.setdefault(pid, len(self.p_num) + 1)
        return f"[P{self.p_num[pid]}]"

    def cite_m(self, ids: list[str]) -> str:
        out = []
        for mid in ids:
            if mid in self.metrics:
                self.m_num.setdefault(mid, len(self.m_num) + 1)
                out.append(f"[M{self.m_num[mid]}]")
        return "".join(out)

    def m(self, name: str, period: str) -> CalculatedMetric | None:
        m = self.metrics.get(f"metric:{name}:{period}")
        return m if m and m.status == MetricStatus.AVAILABLE and m.value is not None else None

    def claim(self, cid: str, section: str, text: str, kind: str, metric_ids=(), passage_ids=(),
              status=EvidenceStatus.SUPPORTED, finding_id=None, as_item=False) -> str | None:
        c = ReportClaim(id=cid, section=section, text=text, kind=kind, metric_ids=list(dict.fromkeys(metric_ids)),
                        passage_ids=list(dict.fromkeys(passage_ids)), evidence_status=status, finding_id=finding_id)
        if cid in self.inp.exclusions:
            self.excluded.append(c)
            return None
        self.claims.append(c)
        rendered = text + (" " + self.cite_m(c.metric_ids) if c.metric_ids else "") + \
            "".join(" " + self.cite_p(p) for p in c.passage_ids if p in self.passages)
        if not as_item:
            self.doc.p(rendered)
        return rendered

    # ---- sections -----------------------------------------------------------------------
    def build(self) -> ReportDraft:
        inp, d = self.inp, self.doc
        c = inp.plan.company
        title = f"{c.name}: three-year performance review ({self.labels[0]} to {self.labels[-1]})"
        d.h(1, title)
        banners = []
        if inp.is_fixture:
            banners.append(FIXTURE_NOTE)
        if inp.llm_is_stub:
            banners.append(STUB_NOTE)
        banners.append("Status: READY FOR ANALYST REVIEW, not approved. This is an analytical report, "
                       "not an investment recommendation.")
        for b in banners:
            d.p("**" + b + "**", "banner")
        d.p(f"CIK {c.cik} · tickers {', '.join(c.tickers) or '-'} · SIC {c.sic or '-'} "
            f"{c.sic_description or ''} · currency USD · analysis cutoff {inp.plan.cutoff_date} · "
            f"report revision {inp.revision}")
        if inp.plan.request.question:
            d.p(f"Analyst question: *{inp.plan.request.question}*")
        self.executive_summary()
        self.financial_tables()
        self.charts_section()
        self.ranked_changes()
        self.findings_section()
        self.limitations()
        self.sources()
        self.calculation_notes()
        self.method_notes()
        return ReportDraft(title=title, revision=inp.revision, claims=self.claims, charts=self.charts,
                           markdown=d.markdown(), passages=[self.passages[p] for p in self.p_num],
                           excluded_claims=self.excluded)

    def executive_summary(self):
        d = self.doc
        first, last = self.labels[0], self.labels[-1]
        d.h(2, "Executive summary")
        items = []
        for name, label in [("revenue", "Revenue"), ("operating_margin", "Operating margin"),
                            ("net_income", "Net income"), ("free_cash_flow", "Free cash flow (calculated)")]:
            a, b = self.m(name, first), self.m(name, last)
            if a and b:
                r = self.claim(f"claim:summary:{name}", "summary",
                               f"{label} was {fmt_metric(a)} in {first} and {fmt_metric(b)} in {last}.",
                               "numeric", [a.id, b.id], as_item=True)
                if r:
                    items.append(r)
        d.ul(items)
        flags = self.inp.analysis.changes
        inv = self.inp.investigation
        if not flags:
            self.claim("claim:summary:no_flags", "summary",
                       "No year-over-year change exceeded the configured investigation settings.", "summary")
            return
        counts = {s: 0 for s in EvidenceStatus}
        for f in (inv.findings if inv else []):
            counts[f.evidence_status] += 1
        n_inv = sum(counts.values())
        self.claim("claim:summary:flag_counts", "summary",
                   f"{len(flags)} change(s) met the investigation settings and {n_inv} were investigated in the "
                   f"10-K text: {counts[EvidenceStatus.SUPPORTED]} explained with supporting quotes, "
                   f"{counts[EvidenceStatus.PARTIALLY_SUPPORTED]} partly explained, "
                   f"{counts[EvidenceStatus.UNSUPPORTED]} not explained by the available evidence.", "summary")
        top = []
        findings = {f.change_flag_id: f for f in (inv.findings if inv else [])}
        for flag in flags[:3]:
            f = findings.get(flag.id)
            tail = (f" Evidence: {STATUS_LABEL[f.evidence_status].lower()}; investigator's assessment: "
                    f"{f.assessment}." if f else " Not investigated.")
            r = self.claim(f"claim:summary:top:{flag.id}", "summary", flag.description + tail, "numeric",
                           flag.metric_ids, as_item=True)
            if r:
                top.append(r)
        if top:
            d.p("Largest changes by investigation priority:")
            d.ul(top)

    def financial_tables(self):
        d = self.doc
        d.h(2, "Three-year financial table")
        fin, chg = presentation_tables(self.inp.analysis.metrics, self.labels)
        d.p("USD millions unless stated. Values are rounded for display; full precision is in "
            "`financial_dataset.csv` and `metrics.csv`.")
        d.table(["Line item"] + self.labels, [[idx] + list(row) for idx, row in zip(fin.index, fin.values)])
        d.h(3, "Year-over-year changes")
        comps = list(chg.columns)
        d.table(["Measure"] + comps, [[idx] + list(row) for idx, row in zip(chg.index, chg.values)])
        self.table_metric_ids = [f"metric:{n}:{lbl}" for n, _ in FINANCIAL_ROWS for lbl in self.labels] + \
                                [f"metric:{n}:{cp}" for n, _ in CHANGE_ROWS for cp in comps]

    def charts_section(self):
        d = self.doc
        self.charts = build_chart_specs(self.inp.analysis.metrics, self.labels)
        d.h(2, "Charts")
        for spec in self.charts:
            d.img(f"charts/{spec.filename}", spec.title, spec.caption)
            rows = []
            for name, vals in spec.series.items():
                rows.append([name] + ["n/a" if v is None else (f"{v:,.1f}" if spec.unit.startswith("%")
                                                              or "billions" in spec.unit else f"{v:,.0f}")
                                      for v in vals])
            d.table([f"{spec.title} ({spec.unit})"] + spec.periods, rows)

    def ranked_changes(self):
        d = self.doc
        d.h(2, "Ranked performance changes")
        flags = self.inp.analysis.changes
        if not flags:
            d.p("No change exceeded the configured investigation settings.")
            return
        d.p("Ranked by how far each change exceeds its investigation setting (profit/loss transitions first). "
            "The settings decide what to investigate; they are not financial standards.")
        findings = {f.change_flag_id: f for f in (self.inp.investigation.findings if self.inp.investigation else [])}
        rows = []
        for n, f in enumerate(flags, 1):
            fd = findings.get(f.id)
            obs = (f"{f.observed:+.1f} pp" if f.observed_unit == "percentage_points" else
                   f"{f.observed:+.1f}%" if f.observed_unit == "percent" else f"{f.observed / 1_000_000:+,.0f}M USD")
            rows.append([str(n), f.description, f.comparison, obs, f.rule,
                         STATUS_LABEL[fd.evidence_status] if fd else "not investigated"])
        d.table(["#", "Change", "Years", "Observed", "Rule", "Evidence"], rows)

    def findings_section(self):
        d = self.doc
        d.h(2, "Evidence-backed explanations")
        inv = self.inp.investigation
        if not inv or not inv.findings:
            d.p("No changes were investigated.")
            return
        flags = {f.id: f for f in self.inp.analysis.changes}
        for n, f in enumerate(inv.findings, 1):
            flag = flags.get(f.change_flag_id)
            if flag is None:
                continue
            self.finding(n, flag, f)

    def finding(self, n: int, flag: ChangeFlag, f: InvestigationFinding):
        d = self.doc
        d.h(3, f"{n}. {flag.description}")
        d.p(f"**Evidence status: {STATUS_LABEL[f.evidence_status]}.** {f.status_reason}. "
            f"Investigator's assessment in the company's context: **{f.assessment}**.")
        r = self.claim(f"claim:{f.id}:observation", "findings", f.calculated_observation, "numeric", f.metric_ids,
                       as_item=True)
        if r:
            d.p("**Calculated observation.** " + r)
        if f.management_explanation:
            pids = [q.passage_id for q in f.quotes]
            fy = ", ".join(sorted({self.passages[p].fiscal_period or "" for p in pids if p in self.passages}))
            r = self.claim(f"claim:{f.id}:management", "findings", f.management_explanation,
                           "management_explanation", passage_ids=pids, status=f.evidence_status,
                           finding_id=f.id, as_item=True)
            if r is not None:
                d.p(f"**Management explanation** (attributed to the company, 10-K {fy}): {r}")
                for q in f.quotes:
                    d.quote(f"“{q.quote}” {self.cite_p(q.passage_id)}")
        else:
            d.p("**Management explanation.** None found in the retrieved filing text.")
        if f.analyst_interpretation:
            r = self.claim(f"claim:{f.id}:interpretation", "findings", f.analyst_interpretation,
                           "analyst_interpretation", status=EvidenceStatus.UNSUPPORTED, finding_id=f.id, as_item=True)
            if r:
                d.p(f"**Analyst interpretation** (an interpretation, not a reported fact; produced by "
                    f"{f.produced_by}): {r}")
        unresolved = []
        for i, q in enumerate(f.unresolved_questions):
            r = self.claim(f"claim:{f.id}:unresolved:{i}", "findings", q, "unresolved",
                           status=EvidenceStatus.UNSUPPORTED, finding_id=f.id, as_item=True)
            if r:
                unresolved.append(r)
        if unresolved:
            d.p("**Unresolved questions.**")
            d.ul(unresolved)
        tools = ", ".join(dict.fromkeys(t.tool for t in f.tool_calls)) or "none"
        d.p(f"*Investigation trace: {f.steps_used} step(s); tools used: {tools}; by {f.produced_by}"
            f"{'; step budget exhausted' if f.budget_exhausted else ''}.*")

    def limitations(self):
        d = self.doc
        d.h(2, "Risks, uncertainties and missing information")
        items = []

        def add(cid, text, metric_ids=()):
            r = self.claim(cid, "limitations", text, "limitation", metric_ids, status=EvidenceStatus.SUPPORTED,
                           as_item=True)
            if r:
                items.append(r)

        shown = {n for n, _ in FINANCIAL_ROWS} | {n for n, _ in CHANGE_ROWS}
        for m in self.inp.analysis.metrics:
            if m.name in shown and m.status != MetricStatus.AVAILABLE:
                add(f"claim:limit:{m.id}", f"{m.name.replace('_', ' ').capitalize()} ({m.fiscal_period}) is "
                                           f"{m.status.value.replace('_', ' ')}: {m.note or 'no detail'}.")
        for i in self.inp.validation.issues:
            if i.severity in (Severity.WARNING, Severity.BLOCKING) and not i.resolved:
                add(f"claim:limit:{i.id}", f"Data validation ({i.severity.value}): {i.message}")
        inv = self.inp.investigation
        if inv:
            flags = {f.id: f for f in self.inp.analysis.changes}
            for fid in inv.not_investigated:
                if fid in flags:
                    add(f"claim:limit:notinv:{fid}", f"Not investigated: {flags[fid].description}")
            for k, note in enumerate(inv.notes):
                add(f"claim:limit:inv:{k}", note)
        for k, note in enumerate(self.inp.extra_limitations):
            add(f"claim:limit:extra:{k}", note)
        for ex in self.excluded:
            items.append(f"Removed during review: a {ex.kind.replace('_', ' ')} statement could not be "
                         f"verified ({self.inp.exclusions.get(ex.id, 'reviewer defect')}).")
        if self.inp.llm_is_stub:
            items.append(STUB_NOTE)
        items.append("Management explanations are the company's own account. They are quoted, not verified "
                     "independently, and timing alone is not treated as cause.")
        d.ul(items)

    def sources(self):
        d = self.doc
        d.h(2, "Sources")
        d.p("Filing passages cited in this report. Each one can be found again in the source document at the "
            "given character range of its extracted text.")
        for pid, n in sorted(self.p_num.items(), key=lambda kv: kv[1]):
            p = self.passages.get(pid)
            if p is None:
                continue
            d.p(f"**[P{n}]** {p.form} {p.accession}, filed {p.filing_date}, {p.fiscal_period or ''}, "
                f"{p.section_title}. {p.url} (characters {p.char_start}-{p.char_end}; passage `{p.id}`)"
                + (" SYNTHETIC FIXTURE." if p.is_fixture else ""))
        docs = []
        seen = set()
        for s in self.inp.data_sources:
            if s.id in seen:
                continue
            seen.add(s.id)
            docs.append(f"{s.kind.value}: {s.url} (retrieved {s.retrieved_at:%Y-%m-%d %H:%M} UTC, "
                        f"SHA-256 `{s.sha256[:16]}…`{', fixture' if s.is_fixture else ''})")
        d.p("Data sources:")
        d.ul(docs)

    def calculation_notes(self):
        d = self.doc
        d.h(2, "Calculation notes")
        d.p("Every number in the text cites one of these validated metrics. Values are full precision; formulas "
            "and input fact IDs trace back to XBRL facts in `facts_audit_trail.csv`.")
        for mid, n in sorted(self.m_num.items(), key=lambda kv: kv[1]):
            m = self.metrics[mid]
            val = "n/a" if m.value is None else format(m.value, "f")
            inputs = ", ".join(f"`{i}`" for i in (m.input_fact_ids or m.input_metric_ids)) or "-"
            d.p(f"**[M{n}]** `{m.id}` = {val} {m.unit}. Formula: {m.formula}. Inputs: {inputs}."
                + (f" Note: {m.note}." if m.note else ""))

    def method_notes(self):
        d = self.doc
        d.h(2, "Method")
        s = self.inp.plan
        d.ul([
            "Fiscal years are identified from the period-of-report of original 10-K filings filed by the cutoff, "
            "never from XBRL fiscal-year labels. Restated values from later 10-Ks are preferred; originals are "
            "kept in the audit trail.",
            "Free cash flow = operating cash flow minus capital expenditure (as a positive outflow). It is a "
            "calculated, non-GAAP measure.",
            "Interest-bearing debt = long-term debt including current maturities + commercial paper + short-term "
            "borrowings. Total liabilities is never used as a substitute.",
            "Margin changes are in percentage points. Percentage growth from a zero or negative base is "
            "suppressed and the absolute change is given instead.",
            f"Investigation used {self.inp.llm_name}. Quotes are checked verbatim against the filing by code; "
            "an evidence status can be lowered by checks but never raised by the model.",
            f"Filings considered: {', '.join(f'{r.form} {r.accession} ({r.filing_date})' for r in s.documents)}.",
        ])


def write_report(inp: ReportInputs) -> ReportDraft:
    """The draft: claims, chart specs, Markdown and HTML (charts referenced as relative files)."""
    w = _Writer(inp)
    draft = w.build()
    draft.html = w.doc.html(draft.title)
    return draft
