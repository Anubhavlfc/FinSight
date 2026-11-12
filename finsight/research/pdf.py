"""Render a ``ResearchReport`` to an A4 PDF in the CFA Research Challenge written-report layout.

Layout: a placeholder page for the CFA Institute front cover (never reproduced here), then the report
body (at most ten A4 pages, checked after rendering), then an appendix (at most ten pages) with the
calculations, valuation detail, sources and limitations.
"""

from __future__ import annotations

import html
import re
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Flowable,
    Frame,
    Image,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from finsight.contracts import CalculatedMetric, ChartSpec, MetricStatus, ValidationReport
from finsight.export import FINANCIAL_ROWS, presentation_tables
from finsight.research.builder import MODEL_LABEL
from finsight.research.models import ResearchReport, Section, money

MAX_BODY_PAGES = 10
MAX_APPENDIX_PAGES = 10

NAVY = colors.HexColor("#14213d")
TEAL = colors.HexColor("#0f766e")
INK = colors.HexColor("#1b1b1b")
MUTED = colors.HexColor("#5b5b57")
RULE = colors.HexColor("#d9d8d3")
SHADE = colors.HexColor("#f2f4f7")
RATING_COLORS = {"BUY": colors.HexColor("#1f7a4d"), "HOLD": colors.HexColor("#8a6d00"),
                 "SELL": colors.HexColor("#b42318"), "NOT RATED": MUTED}

_FONTS_READY = False


def _fonts() -> tuple[str, str]:
    """DejaVu Sans (ships with matplotlib) covers the symbols the report uses; Helvetica does not."""
    global _FONTS_READY
    try:
        if not _FONTS_READY:
            import matplotlib
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont

            base = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
            pdfmetrics.registerFont(TTFont("FS", str(base / "DejaVuSans.ttf")))
            pdfmetrics.registerFont(TTFont("FS-Bold", str(base / "DejaVuSans-Bold.ttf")))
            pdfmetrics.registerFont(TTFont("FS-Italic", str(base / "DejaVuSans-Oblique.ttf")))
            pdfmetrics.registerFont(TTFont("FS-BoldItalic", str(base / "DejaVuSans-BoldOblique.ttf")))
            pdfmetrics.registerFontFamily("FS", normal="FS", bold="FS-Bold", italic="FS-Italic",
                                          boldItalic="FS-BoldItalic")
            _FONTS_READY = True
        return "FS", "FS-Bold"
    except Exception:  # pragma: no cover - fall back to the built-in fonts
        return "Helvetica", "Helvetica-Bold"


@dataclass
class Styles:
    body: ParagraphStyle
    small: ParagraphStyle
    tiny: ParagraphStyle
    h1: ParagraphStyle
    h2: ParagraphStyle
    h3: ParagraphStyle
    bullet: ParagraphStyle
    prov: ParagraphStyle
    cell: ParagraphStyle
    cell_b: ParagraphStyle


def _styles(scale: float = 1.0) -> Styles:
    f, fb = _fonts()
    sz = 8.6 * scale
    return Styles(
        body=ParagraphStyle("body", fontName=f, fontSize=sz, leading=sz * 1.38, textColor=INK, spaceAfter=4,
                            alignment=TA_LEFT),
        small=ParagraphStyle("small", fontName=f, fontSize=7.4, leading=9.6, textColor=MUTED, spaceAfter=3),
        tiny=ParagraphStyle("tiny", fontName=f, fontSize=6.6, leading=8.4, textColor=INK),
        h1=ParagraphStyle("h1", fontName=fb, fontSize=17, leading=21, textColor=NAVY, spaceAfter=2),
        h2=ParagraphStyle("h2", fontName=fb, fontSize=11.5, leading=15, textColor=NAVY, spaceBefore=8, spaceAfter=2),
        h3=ParagraphStyle("h3", fontName=fb, fontSize=9, leading=12, textColor=NAVY, spaceBefore=5, spaceAfter=2),
        bullet=ParagraphStyle("bullet", fontName=f, fontSize=sz, leading=sz * 1.38, textColor=INK, leftIndent=10,
                              bulletIndent=1, spaceAfter=3),
        prov=ParagraphStyle("prov", fontName="FS-Italic" if f == "FS" else "Helvetica-Oblique", fontSize=7,
                            leading=9, textColor=TEAL, spaceAfter=4),
        cell=ParagraphStyle("cell", fontName=f, fontSize=7.2, leading=9, textColor=INK),
        cell_b=ParagraphStyle("cell_b", fontName=fb, fontSize=7.2, leading=9, textColor=INK),
    )


def md(text: str) -> str:
    """The report's tiny markup (**bold**, [S#] citations) to ReportLab paragraph markup."""
    t = html.escape(text, quote=False)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"\[((?:S\d+)(?:,\s*S\d+)*)\]",
               lambda m: "<font size='6.5' color='#0f766e'>[" + ", ".join(
                   f"<a href='#{c.strip()}'>{c.strip()}</a>" for c in m.group(1).split(",")) + "]</font>", t)
    return t


class Marker(Flowable):
    """Zero-size flowable that records the page it lands on."""

    def __init__(self, name: str, sink: dict):
        super().__init__()
        self.name, self.sink = name, sink
        self.width = self.height = 0

    def draw(self):
        self.sink[self.name] = self.canv.getPageNumber()


@dataclass
class RenderStats:
    total_pages: int
    body_pages: int
    appendix_pages: int
    scale: float
    trimmed_paragraphs: int


def render_pdf(report: ResearchReport, metrics: list[CalculatedMetric], validation: ValidationReport,
               charts: list[ChartSpec], out_path: Path, flagged_changes: list | None = None) -> RenderStats:
    """Render, then shrink the narrative (bounded) until the body fits in ten A4 pages."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        images = _render_charts(charts, Path(tmp))
        trims, scale = 0, 1.0
        for attempt in range(6):
            marks: dict = {}
            total = _build(report, metrics, validation, images, out_path, marks, scale, trims, flagged_changes or [])
            body = marks.get("appendix", total + 1) - marks.get("body", 2)
            if body <= MAX_BODY_PAGES:
                return RenderStats(total, body, total - marks.get("appendix", total + 1) + 1, scale, trims)
            if attempt % 2 == 0:
                trims += 1   # drop the last narrative paragraph of each narrative section
            else:
                scale = max(0.85, scale - 0.05)
        raise ValueError(f"The report body needs {body} pages; the limit is {MAX_BODY_PAGES}.")


def _render_charts(charts: list[ChartSpec], tmp: Path) -> dict[str, Path]:
    from finsight.report.charts import render_chart

    out = {}
    for spec in charts:
        p = tmp / spec.filename
        render_chart(spec, p)
        out[spec.id] = p
    return out


def _build(report, metrics, validation, images, out_path, marks, scale, trims, flagged) -> int:
    st = _styles(scale)
    f, fb = _fonts()
    approved = report.run_state == "APPROVED"
    pages = {"n": 0}

    def footer(canv, doc):
        pages["n"] = canv.getPageNumber()
        canv.saveState()
        w, h = A4
        canv.setStrokeColor(RULE)
        canv.line(15 * mm, 13 * mm, w - 15 * mm, 13 * mm)
        canv.setFont(f, 6.6)
        canv.setFillColor(MUTED)
        canv.drawString(15 * mm, 9 * mm, f"{report.header.name} ({report.header.ticker})  ·  FinSight model-generated "
                                         f"research draft  ·  {report.review_status}  ·  Not investment advice")
        canv.drawRightString(w - 15 * mm, 9 * mm, f"Page {canv.getPageNumber()}")
        if not approved:
            canv.setFont(fb, 46)
            canv.setFillColor(colors.Color(0.75, 0.1, 0.1, alpha=0.07))
            canv.translate(w / 2, h / 2)
            canv.rotate(40)
            canv.drawCentredString(0, 0, "DRAFT · NOT APPROVED")
        canv.restoreState()

    doc = BaseDocTemplate(str(out_path), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm,
                          bottomMargin=17 * mm, title=report.title, author="FinSight (model-generated)",
                          subject="Equity research report draft", creator="FinSight")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")
    doc.addPageTemplates([PageTemplate(id="cover", frames=[frame]), PageTemplate(id="page", frames=[frame],
                                                                                 onPage=footer)])
    fl: list = []
    fl += _cover(report, st)
    fl += [NextPageTemplate("page"), PageBreak(), Marker("body", marks)]
    fl += _first_page_header(report, st, doc.width)
    for sec in report.sections:
        fl += _section(sec, report, st, doc.width, metrics, images, trims)
    fl += [PageBreak(), Marker("appendix", marks)]
    fl += _appendix(report, metrics, validation, flagged, st, doc.width)
    doc.build(fl)
    return pages["n"]


# ---- pieces ----------------------------------------------------------------------------------------


def _cover(r: ResearchReport, st: Styles) -> list:
    box = Table([[Paragraph(
        "<b>CFA Institute Research Challenge front cover goes here.</b><br/><br/>"
        "This page is a placeholder. Insert the official front cover provided by CFA Institute and fill in its "
        "highlighted fields; do not alter any other part of it, including the CFA Institute logo. FinSight does "
        "not reproduce the cover or the logo.", st.body)]], colWidths=[150 * mm], rowHeights=[60 * mm])
    box.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 1, NAVY), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                             ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                             ("BACKGROUND", (0, 0), (-1, -1), SHADE)]))
    fields = [["Field", "Value for the cover"]] + [[k, v] for k, v in _header_rows(r)]
    t = Table([[Paragraph(md(a), st.cell_b if i == 0 else st.cell), Paragraph(md(b), st.cell_b if i == 0 else st.cell)]
               for i, (a, b) in enumerate(fields)], colWidths=[50 * mm, 100 * mm])
    t.setStyle(_grid())
    return [Spacer(1, 30 * mm), box, Spacer(1, 10 * mm), Paragraph("Values for the cover's highlighted fields", st.h3),
            t, Spacer(1, 6 * mm), Paragraph(MODEL_LABEL, st.small)]


def _header_rows(r: ResearchReport) -> list[tuple[str, str]]:
    h, m, v, rec = r.header, r.market, r.valuation, r.recommendation
    price = f"${money(m.price)} (as of {m.as_of:%d %B %Y})" if m else "Not supplied"
    target = (f"${v.target_price:,.2f} ({v.upside_pct:+.1f}% {'increase' if v.upside_pct >= 0 else 'decrease'})"
              if v.available and v.upside_pct is not None else "Not set")
    return [("Company name", h.name), ("Exchange", h.exchange), ("Ticker symbol", h.ticker), ("Sector", h.sector),
            ("Industry", h.industry), ("Recommendation", rec.rating), ("Current price", price),
            ("Target price", target)]


def _grid(header: bool = True) -> TableStyle:
    cmds = [("GRID", (0, 0), (-1, -1), 0.4, RULE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
    if header:
        cmds.append(("BACKGROUND", (0, 0), (-1, 0), SHADE))
    return TableStyle(cmds)


def _first_page_header(r: ResearchReport, st: Styles, width: float) -> list:
    h, rec = r.header, r.recommendation
    title = Paragraph(f"{html.escape(h.name)}", st.h1)
    sub = Paragraph(f"{html.escape(h.exchange)}: {html.escape(h.ticker)}  ·  Fiscal years {r.periods[0]} to "
                    f"{r.periods[-1]}  ·  Report date {r.created_at:%d %B %Y}", st.small)
    rows = _header_rows(r)
    left = [[Paragraph(f"<b>{k}</b>", st.cell), Paragraph(md(v), st.cell)] for k, v in rows if k != "Recommendation"]
    lt = Table(left, colWidths=[30 * mm, 88 * mm])
    lt.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.3, RULE), ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5), ("LEFTPADDING", (0, 0), (-1, -1), 2)]))
    rating = Table([[Paragraph(f"<font color='white'><b>{rec.rating}</b></font>",
                               ParagraphStyle("r", parent=st.h1, alignment=1, fontSize=20, leading=24))],
                    [Paragraph("<font color='white'>Recommendation (model)</font>",
                               ParagraphStyle("r2", parent=st.small, alignment=1, textColor=colors.white))]],
                   colWidths=[width - 122 * mm], rowHeights=[16 * mm, 8 * mm])
    rating.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), RATING_COLORS[rec.rating]),
                                ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    top = Table([[lt, rating]], colWidths=[122 * mm, width - 122 * mm])
    top.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    banner_text = (f"<b>{'Approved analysis' if r.run_state == 'APPROVED' else 'Draft for human review'}.</b> "
                   f"{html.escape(MODEL_LABEL)} Review status: {html.escape(r.review_status)}. Narrative: "
                   + ("AI-drafted from cited 10-K passages, quotes verified verbatim."
                      if r.narrative_mode == "ai_summary" else "verbatim 10-K extracts (no AI text)."))
    if r.is_fixture:
        banner_text = "<b>SYNTHETIC DEMO DATA. Not a real company.</b> " + banner_text
    banner = Table([[Paragraph(banner_text, st.small)]], colWidths=[width])
    banner.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fff7e6")),
                                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e0b54a"))]))
    return [title, sub, Spacer(1, 3 * mm), top, Spacer(1, 2 * mm), banner]


def _section(sec: Section, r: ResearchReport, st: Styles, width, metrics, images, trims) -> list:
    out: list = [CondPageBreak(30 * mm), Paragraph(sec.title, st.h2)]
    if sec.provenance:
        out.append(Paragraph(html.escape(sec.provenance), st.prov))
    paras = sec.paragraphs
    bullets = sec.bullets
    if trims and sec.key in ("business", "industry", "esg"):
        keep = max(1, len([p for p in paras if p.kind != "note"]) - trims)
        paras = [p for p in paras if p.kind != "note"][:keep] + [p for p in paras if p.kind == "note"]
    if trims and sec.key == "risks":
        narrative = [b for b in bullets if b.kind != "engine"]
        bullets = narrative[:max(2, len(narrative) - trims)] + [b for b in bullets if b.kind == "engine"]
    for p in paras:
        out.append(Paragraph(md(p.text), st.small if p.kind == "note" else st.body))
    for b in bullets:
        out.append(Paragraph(md(b.text), st.bullet, bulletText="•"))
    if sec.key == "valuation":
        out += _valuation_tables(r, st, width)
    if sec.key == "financials":
        out.insert(3, _fin_table(metrics, r.periods, st, width))
        out.insert(4, Spacer(1, 2 * mm))
        out += _chart_grid(images, width, st)
    return out


def _fin_table(metrics, labels, st, width) -> Table:
    fin, chg = presentation_tables(metrics, labels)
    rows = [["USD millions unless stated"] + labels]
    for idx, row in fin.iterrows():
        rows.append([idx] + list(row.values))
    for idx, row in chg.iterrows():
        rows.append([idx] + [""] + list(row.values))
    data = [[Paragraph(md(str(c)), st.cell_b if i == 0 or j == 0 else st.cell) for j, c in enumerate(r)]
            for i, r in enumerate(rows)]
    first = 62 * mm
    t = Table(data, colWidths=[first] + [(width - first) / len(labels)] * len(labels), repeatRows=1)
    style = _grid()
    style.add("ALIGN", (1, 0), (-1, -1), "RIGHT")
    style.add("LINEABOVE", (0, len(fin) + 1), (-1, len(fin) + 1), 0.9, NAVY)
    t.setStyle(style)
    return t


def _chart_grid(images: dict, width, st) -> list:
    order = ["chart:revenue_profit", "chart:margins", "chart:cash_flow", "chart:cash_debt"]
    cells = [Image(str(images[c]), width=width / 2 - 3 * mm, height=(width / 2 - 3 * mm) * 3.8 / 7.2)
             for c in order if c in images]
    if not cells:
        return []
    rows = [cells[i:i + 2] + ([""] if len(cells[i:i + 2]) == 1 else []) for i in range(0, len(cells), 2)]
    t = Table(rows, colWidths=[width / 2] * 2)
    t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return [Spacer(1, 2 * mm), KeepTogether([t, Paragraph(
        "Charts plot FinSight's validated metrics; the plotted values and their metric IDs are in Appendix A and "
        "in the run's chart CSV downloads.", st.small)])]


def _valuation_tables(r: ResearchReport, st, width) -> list:
    v = r.valuation
    out: list = []
    rows = [["Input", "Value", "Basis", "Source / explanation"]]
    for a in r.assumptions:
        src = a.explanation + (" " + " ".join(f"[{c}]" for c in a.citation_ids) if a.citation_ids else "")
        rows.append([a.label, a.value, a.basis, src])
    t = Table([[Paragraph(md(str(c)), st.cell_b if i == 0 else st.cell) for c in row] for i, row in enumerate(rows)],
              colWidths=[38 * mm, 26 * mm, 18 * mm, width - 82 * mm], repeatRows=1)
    t.setStyle(_grid())
    out += [Paragraph("Valuation inputs and assumptions", st.h3), t]
    if v.available and v.sensitivity:
        gs = list(next(iter(v.sensitivity.values())).keys())
        srows = [["Discount rate \\ terminal growth"] + [f"{g}%" for g in gs]]
        for dr, row in v.sensitivity.items():
            srows.append([f"{Decimal(dr):.2f}%"] + [f"${x:,.2f}" if x is not None else "n/a" for x in row.values()])
        s = Table([[Paragraph(md(c), st.cell_b if i == 0 or j == 0 else st.cell) for j, c in enumerate(rw)]
                   for i, rw in enumerate(srows)], colWidths=[50 * mm] + [30 * mm] * len(gs))
        sty = _grid()
        mid = len(srows) // 2
        sty.add("BACKGROUND", (2, mid), (2, mid), colors.HexColor("#dff3ef"))
        s.setStyle(sty)
        out += [Paragraph("Sensitivity of value per share", st.h3), s]
    if v.multiples:
        mrows = [["Multiple", "At current price", "At target price", "Basis"]] + \
                [[m["name"], m["at_price"], m["at_target"], m["basis"]] for m in v.multiples]
        m = Table([[Paragraph(md(c), st.cell_b if i == 0 else st.cell) for c in rw] for i, rw in enumerate(mrows)],
                  colWidths=[38 * mm, 26 * mm, 26 * mm, width - 90 * mm])
        m.setStyle(_grid())
        out += [Paragraph("Implied multiples (cross-check, not used to set the target)", st.h3), m]
    return out


def _appendix(r: ResearchReport, metrics, validation: ValidationReport, flagged, st: Styles, width) -> list:
    out: list = [Paragraph("Appendix", st.h1),
                 Paragraph("The appendix is not part of the ten-page body.", st.small)]
    # A. calculations
    out.append(Paragraph("A. Calculations behind every figure", st.h2))
    names = [n for n, _ in FINANCIAL_ROWS]
    rows = [["Metric ID", "Value", "Formula", "Inputs (fact IDs)"]]
    for m in metrics:
        if m.name not in names or m.status != MetricStatus.AVAILABLE or m.value is None:
            continue
        val = f"{m.value / Decimal(1_000_000):,.0f}m" if m.unit == "USD" else f"{m.value:.2f}"
        rows.append([m.id.replace("metric:", ""), val, m.formula, f"{len(m.input_fact_ids)} fact(s)"
                     + (f", {len(m.input_metric_ids)} metric(s)" if m.input_metric_ids else "")])
    t = Table([[Paragraph(md(str(c)), st.cell_b if i == 0 else st.tiny) for c in row] for i, row in enumerate(rows)],
              colWidths=[48 * mm, 20 * mm, width - 98 * mm, 30 * mm], repeatRows=1)
    t.setStyle(_grid())
    out += [Paragraph("Each input fact (XBRL tag, value, accession) is listed in the run's facts_audit_trail.csv "
                      "download, under the same IDs.", st.small), t]
    if flagged:
        out.append(Paragraph("Flagged changes (ranked)", st.h3))
        out += [Paragraph(md(f"{c.description}"), st.bullet, bulletText="•") for c in flagged]
    issues = [i for i in validation.issues if i.severity.value != "info"]
    if issues:
        out.append(Paragraph("Validation warnings", st.h3))
        out += [Paragraph(md(f"[{i.severity.value}] {i.message}"), st.small) for i in issues[:15]]
    # B. valuation detail
    v = r.valuation
    out.append(CondPageBreak(60 * mm))
    out.append(Paragraph("B. Valuation detail", st.h2))
    if v.available:
        prow = [["Year", "FCF growth", "Free cash flow", "Discount factor", "Present value"]]
        for y in v.projection:
            prow.append([f"Year {y.year}", f"{y.growth_pct:.2f}%", f"${y.fcf / Decimal(1e9):,.2f}bn",
                         f"{y.discount_factor:.4f}", f"${y.present_value / Decimal(1e9):,.2f}bn"])
        bn = lambda x: f"${x / Decimal(1e9):,.2f}bn"  # noqa: E731
        prow += [["Terminal value", "", bn(v.terminal_value), "", bn(v.pv_terminal)],
                 ["Enterprise value", "", "", "", bn(v.enterprise_value)],
                 ["+ Cash and equivalents", "", "", "", bn(v.cash)],
                 ["+ Short-term investments", "", "", "", bn(v.short_term_investments)],
                 ["− Interest-bearing debt", "", "", "", bn(v.debt)],
                 ["Equity value", "", "", "", bn(v.equity_value)],
                 ["÷ Diluted shares", "", "", "", f"{v.diluted_shares / Decimal(1e6):,.0f}m"],
                 ["Value per share", "", "", "", f"${v.value_per_share:,.2f}"]]
        p = Table([[Paragraph(md(c), st.cell_b if i == 0 else st.cell) for c in rw] for i, rw in enumerate(prow)],
                  colWidths=[45 * mm] + [(width - 45 * mm) / 4] * 4)
        sty = _grid()
        sty.add("ALIGN", (1, 0), (-1, -1), "RIGHT")
        p.setStyle(sty)
        out.append(p)
    else:
        out.append(Paragraph(md(v.reason), st.body))
    # C. sources
    out.append(CondPageBreak(40 * mm))
    out.append(Paragraph("C. Sources", st.h2))
    out.append(Paragraph("All sources are public. Filing passages can be checked at the character offsets given, "
                         "in the text FinSight extracted from the filing.", st.small))
    for c in r.citations:
        url = f" <a href='{html.escape(c.url)}' color='#0f766e'>{html.escape(c.url)}</a>" if c.url else ""
        out.append(Paragraph(f"<a name='{c.id}'/><b>[{c.id}]</b> {html.escape(c.label)}. "
                             f"{html.escape(c.detail)}{url}", st.tiny))
        out.append(Spacer(1, 1.2))
    # D. limitations and review
    out.append(CondPageBreak(40 * mm))
    out.append(Paragraph("D. Method, limitations and review", st.h2))
    out.append(Paragraph(md(
        f"Produced by FinSight from analysis run {r.run_id} (state {r.run_state}). Review status: {r.review_status}. "
        f"Narrative sections: {r.narrative_provider}. Rating rule: {r.recommendation.rule}"), st.small))
    out += [Paragraph(md(x), st.small, bulletText="•") for x in r.limitations]
    out.append(Paragraph(md(MODEL_LABEL), st.small))
    return out
