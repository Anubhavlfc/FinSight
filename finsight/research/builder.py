"""Assemble a ``ResearchReport`` from a finished run, a market snapshot and the valuation settings."""

from __future__ import annotations

from decimal import Decimal

from finsight.contracts import CalculatedMetric, FilingPassage, MetricStatus, RunState, WorkflowRun
from finsight.llm.provider import LLMProvider
from finsight.presentation import fmt_metric, fmt_usd
from finsight.research.models import (
    Assumption,
    Citation,
    CompanyHeader,
    MarketSnapshot,
    Paragraph,
    ResearchReport,
    Section,
    money,
)
from finsight.research.narrative import TOPICS, draft_topic, select_passages
from finsight.research.profile import ProfileData, load_profile
from finsight.research.valuation import ValuationSettings, rate, value
from finsight.sec.sources import DataSource

FINISHED = {RunState.READY_FOR_REVIEW, RunState.PARTIAL, RunState.APPROVED}
MODEL_LABEL = ("Model-generated research draft. Financial figures are calculated by FinSight from SEC XBRL data; "
               "the price target and rating follow mechanically from the stated assumptions. Not investment advice.")


class ResearchError(Exception):
    """The run cannot produce a research report; the message says why."""


class Citations:
    def __init__(self):
        self.items: list[Citation] = []
        self._by_key: dict[str, str] = {}

    def _add(self, key: str, **kw) -> str:
        if key not in self._by_key:
            cid = f"S{len(self.items) + 1}"
            self.items.append(Citation(id=cid, **kw))
            self._by_key[key] = cid
        return self._by_key[key]

    def passage(self, p: FilingPassage) -> str:
        return self._add(p.id, kind="filing_passage", passage_id=p.id, url=p.url,
                         label=f"{p.form} filed {p.filing_date} ({p.accession}), {p.section_title}",
                         detail=f"characters {p.char_start}-{p.char_end} of the filing text")

    def sec(self, key: str, label: str, url: str | None, detail: str = "") -> str:
        return self._add(key, kind="sec_data", label=label, url=url, detail=detail)

    def market(self, m: MarketSnapshot) -> str:
        return self._add("market", kind="market_data", url=m.source_url,
                         label=f"{m.ticker} share price {money(m.price)} {m.currency}, {m.as_of}: {m.source}",
                         detail=(m.note + " " if m.note else "") + f"Retrieved {m.retrieved_at:%Y-%m-%d %H:%M} UTC.")

    def engine(self) -> str:
        return self._add("engine", kind="engine", label="FinSight calculation from validated SEC XBRL facts",
                         detail="Formulas, input fact IDs and filing accessions are listed in Appendix A.")


def _cite(text: str, ids: list[str]) -> str:
    return f"{text} [{', '.join(dict.fromkeys(ids))}]" if ids else text


def _metric(by_id: dict[str, CalculatedMetric], name: str, period: str) -> CalculatedMetric | None:
    m = by_id.get(f"metric:{name}:{period}")
    return m if m is not None and m.status == MetricStatus.AVAILABLE and m.value is not None else None


def review_status(run: WorkflowRun) -> str:
    reviews = run.stats.get("human_reviews") or []
    if run.state == RunState.APPROVED:
        last = next((r for r in reversed(reviews) if r.get("decision") == "approved"), {})
        return f"Underlying analysis approved by {last.get('reviewer', 'an analyst')} on {str(last.get('at', ''))[:10]}"
    if reviews and reviews[-1].get("decision") == "changes_requested":
        return f"Changes requested by {reviews[-1].get('reviewer')}; not approved"
    return "Awaiting human review; not approved"


def build_research(run: WorkflowRun, results: dict, market: MarketSnapshot | None, vs: ValuationSettings,
                   source: DataSource | None, provider: LLMProvider | None,
                   profile: ProfileData | None = None) -> ResearchReport:
    if run.state not in FINISHED:
        raise ResearchError(f"The analysis must be finished first (it is {run.state.value}).")
    plan_cp, val_cp, analysis = results.get("plan"), results.get("validation"), results.get("analysis")
    if plan_cp is None or val_cp is None or analysis is None:
        raise ResearchError("The analysis has no validated metrics to report on.")
    plan, report = plan_cp.plan, val_cp.report
    labels = [p.label for p in report.fiscal_periods]
    latest, first = labels[-1], labels[0]
    by_id = {m.id: m for m in analysis.metrics}
    c = Citations()
    profile = profile or load_profile(plan, source)
    limitations: list[str] = list(profile.notes)
    ticker = (market.ticker if market else None) or (plan.company.tickers[0] if plan.company.tickers else plan.company.cik)
    if market and plan.company.tickers and market.ticker.upper() not in [t.upper() for t in plan.company.tickers]:
        raise ResearchError(f"The price is for {market.ticker}, but {plan.company.name} trades as "
                            f"{', '.join(plan.company.tickers)}.")

    sub_cite = c.sec("submissions", f"SEC EDGAR submissions file for CIK {plan.company.cik}", profile.submissions_url,
                     "Exchange, SEC review office (sector) and SIC industry code")
    header = CompanyHeader(name=plan.company.name, ticker=ticker, exchange=profile.exchange, sector=profile.sector,
                           industry=profile.industry, cik=plan.company.cik,
                           fiscal_year_end=plan.company.fiscal_year_end, citation_ids=[sub_cite])

    # ---- valuation -------------------------------------------------------------------------
    shares = profile.diluted_shares.value if profile.diluted_shares else None
    sti = profile.short_term_investments.value if profile.short_term_investments else None
    valuation, input_ids = value(analysis.metrics, labels, shares, sti, market, vs)
    rec = rate(valuation, market, vs)
    eng = c.engine()
    mkt = c.market(market) if market else None
    facts_cite = c.sec("companyfacts", f"SEC XBRL company facts for CIK {plan.company.cik}", profile.company_facts_url,
                       "us-gaap facts from the 10-K filings listed in Appendix C")
    assumptions = _assumptions(valuation, profile, market, vs, input_ids, labels, c, eng, mkt, facts_cite)

    # ---- sections --------------------------------------------------------------------------
    passages = list((results.get("filing_text").passages if results.get("filing_text") else []))
    use_ai = provider is not None and not provider.is_stub
    sections: list[Section] = []
    sections.append(_investment_summary(header, market, valuation, rec, by_id, labels, results, c, eng, mkt))
    narrative_notes: list[str] = []
    for key in ("business", "industry"):
        sections.append(_narrative(TOPICS[key], passages, latest, plan.company.name, provider, c, narrative_notes))
    sections.append(_valuation_section(valuation, rec, market, vs, c, eng, mkt))
    sections.append(_financial_analysis(by_id, labels, results, c, eng))
    risks = _narrative(TOPICS["risks"], passages, latest, plan.company.name, provider, c, narrative_notes)
    risks.bullets += _model_risks(valuation, vs, report, eng)
    sections.append(risks)
    esg = _narrative(TOPICS["esg"], passages, latest, plan.company.name, provider, c, narrative_notes)
    esg.paragraphs.append(Paragraph(kind="note", text=(
        "Scope note: governance data such as board composition and executive pay is published in the proxy "
        "statement (DEF 14A), which FinSight does not collect. No third-party ESG rating is used.")))
    sections.append(esg)
    limitations += narrative_notes

    limitations += [
        "Sector is the SEC review office assigned to the filer, not a GICS or other commercial classification.",
        "The valuation is a single-scenario model; it does not forecast revenue or margins line by line.",
        "Net cash counts cash, cash equivalents and short-term investments against interest-bearing debt; leases, "
        "long-term investments and other claims are excluded.",
    ]
    if run.request.offline:
        limitations.insert(0, "SYNTHETIC DEMO DATA: the company, figures and filing text are made up for testing.")
    usage = provider.usage.summary(provider.name, provider.is_stub) if use_ai else {}
    return ResearchReport(
        run_id=run.id, title=f"{plan.company.name} ({ticker}) equity research report", header=header, market=market,
        valuation=valuation, recommendation=rec, assumptions=assumptions, sections=sections, citations=c.items,
        periods=labels, narrative_mode="ai_summary" if use_ai else "filing_extract",
        narrative_provider=provider.name if use_ai else "none (verbatim filing extracts)",
        run_state=run.state.value, review_status=review_status(run), is_fixture=run.request.offline,
        limitations=limitations, llm_usage=usage)


# ---- sections ---------------------------------------------------------------------------------


def _narrative(topic, passages, latest, company, provider, c: Citations, notes: list[str]) -> Section:
    chosen = select_passages(passages, topic, latest) or select_passages(passages, topic, None)
    paras, n = draft_topic(chosen, topic, company, provider)
    notes += n
    by_id = {p.id: p for p in passages}
    out = []
    for d in paras:
        ids = [c.passage(by_id[pid]) for pid in d.passage_ids if pid in by_id]
        out.append(Paragraph(text=_cite(d.text, ids), kind=d.kind, citation_ids=ids))
    if paras and paras[0].kind == "ai_summary":
        prov = ("AI-drafted summary of the cited 10-K passages. Each paragraph was kept only if its quotes were "
                "found verbatim in the filing.")
    else:
        prov = "Verbatim extracts from the company's latest 10-K, chosen by keyword relevance."
    sec = Section(key=topic.key, title=topic.title, provenance=prov)
    if topic.key == "risks":
        sec.bullets = out
    else:
        sec.paragraphs = out
    if not out:
        sec.paragraphs = [Paragraph(kind="note", text="No 10-K text was available for this section.")]
    return sec


def _investment_summary(header, market, v, rec, by_id, labels, results, c, eng, mkt) -> Section:
    latest, first = labels[-1], labels[0]
    s = Section(key="summary", title="Investment Summary",
                provenance="Written by FinSight from the valuation and validated metrics; no AI text.")
    if market and v.available:
        s.paragraphs.append(Paragraph(text=_cite(
            f"FinSight's model rates {header.name} **{rec.rating}** with a target price of "
            f"**${v.target_price:,.2f}**, {v.upside_pct:+.1f}% against the {market.as_of:%d %B %Y} price of "
            f"${money(market.price)}. The target is a {v.forecast_years}-year free-cash-flow DCF at a "
            f"{v.discount_rate_pct:.2f}% discount rate and {v.terminal_growth_pct:.1f}% terminal growth; the "
            f"current price implies year-one free-cash-flow growth of "
            + (f"{v.implied_stage1_growth_pct:.1f}%" if v.implied_stage1_growth_pct is not None else "a rate outside "
               "the -30% to +60% search range")
            + f", against the {v.stage1_growth_pct:.1f}% the model assumes.", [x for x in (mkt, eng) if x])))
    else:
        s.paragraphs.append(Paragraph(kind="note", text=f"Not rated: {rec.reason}"))
    bullets = []
    rev, g = _metric(by_id, "revenue", latest), [m for m in (_metric(by_id, "revenue_growth", f"{a}->{b}")
                                                            for a, b in zip(labels, labels[1:])) if m]
    if rev:
        bullets.append(f"**Growth.** Revenue was {fmt_metric(rev)} in {latest}"
                       + (", with growth of " + " and ".join(f"{fmt_metric(m)} ({m.fiscal_period})" for m in g) if g else "")
                       + ".")
    om0, om1 = _metric(by_id, "operating_margin", first), _metric(by_id, "operating_margin", latest)
    if om0 and om1:
        bullets.append(f"**Profitability.** Operating margin moved from {fmt_metric(om0)} in {first} to "
                       f"{fmt_metric(om1)} in {latest}.")
    fcf0, fcf1 = _metric(by_id, "free_cash_flow", first), _metric(by_id, "free_cash_flow", latest)
    capex = _metric(by_id, "capital_expenditure", latest)
    if fcf0 and fcf1:
        bullets.append(f"**Cash generation.** Free cash flow (operating cash flow less capital expenditure) was "
                       f"{fmt_metric(fcf1)} in {latest} versus {fmt_metric(fcf0)} in {first}"
                       + (f"; capital expenditure was {fmt_metric(capex)} in {latest}." if capex else "."))
    cash, debt = _metric(by_id, "cash_and_equivalents", latest), _metric(by_id, "interest_bearing_debt", latest)
    if cash and debt:
        bullets.append(f"**Balance sheet.** Year-end cash and cash equivalents of {fmt_metric(cash)} against "
                       f"interest-bearing debt of {fmt_metric(debt)}.")
    s.bullets = [Paragraph(text=_cite(b, [eng])) for b in bullets]
    inv = results.get("investigation")
    if inv:
        fav = [f for f in inv.findings if f.assessment == "favorable"][:1]
        unf = [f for f in inv.findings if f.assessment == "unfavorable"][:1]
        for f, lbl in [(x, "What went well") for x in fav] + [(x, "What to watch") for x in unf]:
            s.bullets.append(Paragraph(text=_cite(f"**{lbl}.** {f.calculated_observation}", [eng])))
    return s


def _valuation_section(v, rec, market, vs, c, eng, mkt) -> Section:
    s = Section(key="valuation", title="Valuation",
                provenance="Deterministic model; every input is listed in the assumptions table and Appendix B.")
    if not v.available:
        s.paragraphs.append(Paragraph(kind="note", text=v.reason))
        return s
    s.paragraphs.append(Paragraph(text=_cite(
        f"We value the equity with a {v.forecast_years}-year free-cash-flow DCF. "
        + (f"Base free cash flow of {fmt_usd(v.base_fcf)} is latest-year revenue times the average free-cash-flow "
           f"margin over the analysed years ({v.base_fcf_margin_pct:.1f}%), which smooths one-year swings in capital "
           f"spending. It grows" if v.base_fcf_method == "average_margin" else
           f"Free cash flow of {fmt_usd(v.base_fcf)} in the latest fiscal year grows")
        + f" at {v.stage1_growth_pct:.1f}% in year one (the revenue CAGR over the analysed "
        f"window, bounded to {vs.stage1_growth_floor_pct:.0f}-{vs.stage1_growth_cap_pct:.0f}%) and fades in a "
        f"straight line to {v.terminal_growth_pct:.1f}% by year {vs.forecast_years}. Cash flows are discounted at "
        f"{v.discount_rate_pct:.2f}% (end-of-year convention). Enterprise value of {fmt_usd(v.enterprise_value)} "
        f"plus net cash of {fmt_usd(v.cash + v.short_term_investments - v.debt, signed=True)} gives equity value of "
        f"{fmt_usd(v.equity_value)}, or ${v.value_per_share:,.2f} per diluted share.", [eng])))
    if v.pv_terminal and v.enterprise_value:
        share = v.pv_terminal / v.enterprise_value * 100
        s.paragraphs.append(Paragraph(text=(
            f"The terminal value is {share:.0f}% of enterprise value, so the result is most sensitive to the discount "
            "rate and terminal growth; the sensitivity table shows the range.")))
    if market:
        s.paragraphs.append(Paragraph(text=_cite(
            f"Rating rule: {rec.rule} {rec.reason} Rating: **{rec.rating}**.", [mkt] if mkt else [])))
    return s


def _financial_analysis(by_id, labels, results, c: Citations, eng) -> Section:
    s = Section(key="financials", title="Financial Analysis",
                provenance="Figures from FinSight's validated metrics. Explanations are management's own, with the "
                           "10-K passages they come from; each was checked against the filing text.")
    s.paragraphs.append(Paragraph(text=_cite(
        f"The table and charts cover fiscal years {labels[0]} to {labels[-1]}. The changes below are those FinSight "
        "flagged as significant, in rank order, with what management says caused them.", [eng])))
    inv, draft = results.get("investigation"), results.get("draft")
    passages = {p.id: p for p in (draft.passages if draft else [])}
    if results.get("filing_text"):
        passages.update({p.id: p for p in results["filing_text"].passages})
    kept = {cl.id for cl in (draft.claims if draft else [])}
    for f in (inv.findings if inv else [])[:6]:
        text = f"**{f.calculated_observation}**"
        ids = [eng]
        if f.management_explanation and f"claim:{f.id}:management" in kept:
            pids = [q.passage_id for q in f.quotes if q.verified and q.passage_id in passages]
            ids += [c.passage(passages[p]) for p in dict.fromkeys(pids)]
            text += f" Management's explanation ({f.evidence_status.value.replace('_', ' ')}): {f.management_explanation}"
        else:
            text += " No management explanation that passed review was found in the filings."
        s.bullets.append(Paragraph(text=_cite(text, ids)))
    if inv and inv.not_investigated:
        s.paragraphs.append(Paragraph(kind="note", text=f"{len(inv.not_investigated)} further flagged change(s) were "
                                                        "not investigated (budget); they appear in Appendix A."))
    return s


def _model_risks(v, vs, report, eng) -> list[Paragraph]:
    out = []
    if v.available and v.sensitivity:
        vals = [x for row in v.sensitivity.values() for x in row.values() if x is not None]
        if vals:
            out.append(Paragraph(kind="engine", text=_cite(
                f"**Valuation risk.** Across discount rates of ±1 percentage point and terminal growth of ±0.5 "
                f"percentage points, value per share ranges from ${min(vals):,.2f} to ${max(vals):,.2f}.", [eng])))
    warn = [i for i in report.issues if i.severity.value in ("warning", "error") and not i.resolved]
    if warn:
        out.append(Paragraph(kind="engine", text=(
            f"**Data risk.** Validation raised {len(warn)} unresolved warning(s) on the reported figures; they are "
            "listed in Appendix A.")))
    return out


def _assumptions(v, profile: ProfileData, market, vs: ValuationSettings, ids, labels, c: Citations, eng, mkt,
                 facts_cite) -> list[Assumption]:
    out: list[Assumption] = []
    if market:
        out.append(Assumption(key="price", label=f"Current price ({market.as_of})", value=f"${money(market.price)}",
                              basis="sourced", explanation=market.source, citation_ids=[mkt]))
    if vs.risk_free is not None and vs.discount_rate_override_pct is None:
        rf = vs.risk_free
        out.append(Assumption(key="risk_free", label="Risk-free rate", value=f"{rf.value_pct:.2f}%", basis="sourced",
                              explanation=f"{rf.source}" + (f", as of {rf.as_of}" if rf.as_of else ""),
                              citation_ids=[c.sec("risk_free", f"{rf.source}" + (f", {rf.as_of}" if rf.as_of else ""),
                                                  rf.source_url, "Market input to the discount rate")]))
        out.append(Assumption(key="beta", label="Beta", value=f"{vs.beta:.2f}", basis="assumption",
                              explanation="Not estimated; market-average risk assumed."))
        out.append(Assumption(key="erp", label="Equity risk premium", value=f"{vs.equity_risk_premium_pct:.2f}%",
                              basis="assumption", explanation="Analyst assumption (config/research.toml)."))
    if v.discount_rate_pct is not None:
        out.append(Assumption(key="discount_rate", label="Discount rate (cost of equity)",
                              value=f"{v.discount_rate_pct:.2f}%", basis="calculated" if vs.discount_rate_override_pct
                              is None else "assumption",
                              explanation="Risk-free rate + beta x equity risk premium" if vs.discount_rate_override_pct
                              is None else "Set for this report by the analyst."))
    if v.stage1_growth_pct is not None:
        out.append(Assumption(key="stage1_growth", label="Year-1 FCF growth", value=f"{v.stage1_growth_pct:.1f}%",
                              basis="calculated", metric_ids=ids.get("stage1_growth", []), citation_ids=[eng],
                              explanation=f"Revenue CAGR {labels[0]}-{labels[-1]}, bounded to "
                                          f"{vs.stage1_growth_floor_pct:.0f}-{vs.stage1_growth_cap_pct:.0f}%; fades "
                                          f"linearly to terminal growth."))
    out.append(Assumption(key="terminal_growth", label="Terminal growth", value=f"{vs.terminal_growth_pct:.1f}%",
                          basis="assumption", explanation="Analyst assumption (config/research.toml)."))
    out.append(Assumption(key="years", label="Forecast horizon", value=f"{vs.forecast_years} years", basis="assumption",
                          explanation="Explicit forecast period before the terminal value."))
    if v.base_fcf is not None:
        out.append(Assumption(key="base_fcf", label="Base free cash flow", value=fmt_usd(v.base_fcf),
                              basis="calculated", metric_ids=ids.get("base_fcf", []), citation_ids=[eng],
                              explanation=(f"{labels[-1]} revenue x average FCF margin {labels[0]}-{labels[-1]} "
                                           f"({v.base_fcf_margin_pct:.1f}%); FCF = operating cash flow less capex."
                                           if v.base_fcf_method == "average_margin" else
                                           f"{labels[-1]} operating cash flow less capital expenditure.")))
    if v.cash is not None:
        out.append(Assumption(key="cash", label="Cash and cash equivalents", value=fmt_usd(v.cash), basis="calculated",
                              metric_ids=ids.get("cash", []), citation_ids=[eng], explanation="Latest year end."))
    sti = profile.short_term_investments
    out.append(Assumption(key="sti", label="Short-term investments", value=fmt_usd(sti.value) if sti else "$0",
                          basis="sourced" if sti else "assumption", citation_ids=[facts_cite] if sti else [],
                          explanation=f"{sti.tag}, {sti.accession}" if sti else "Not reported; treated as zero."))
    if v.debt is not None:
        out.append(Assumption(key="debt", label="Interest-bearing debt", value=fmt_usd(v.debt), basis="calculated",
                              metric_ids=ids.get("debt", []), citation_ids=[eng], explanation="Latest year end."))
    sh = profile.diluted_shares
    if sh:
        out.append(Assumption(key="shares", label="Diluted shares", value=f"{sh.value / Decimal(1_000_000):,.0f} million",
                              basis="sourced", citation_ids=[facts_cite],
                              explanation=f"Weighted-average diluted shares, {labels[-1]} ({sh.tag}, {sh.accession})"))
    out.append(Assumption(key="rating_rule", label="Rating rule",
                          value=f"BUY ≥ {vs.buy_at_or_above_pct:+.0f}%, SELL ≤ {vs.sell_at_or_below_pct:+.0f}%",
                          basis="rule", explanation="Upside of target price over current price."))
    return out
