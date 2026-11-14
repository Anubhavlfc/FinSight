"""FinSight Streamlit dashboard.

    finsight-dashboard                       # or: streamlit run finsight/dashboard/app.py

Pick a company, run the orchestrated pipeline (plan, collect, validate, calculate,
investigate, draft, review), then read the metrics, charts and ranked changes with
their cited explanations, and record a human review decision. Approval is always a
person's explicit action; the system never approves its own work.

Every number on screen comes from the engine's validated metrics through the same
view builders as the web app, so it matches the report and the downloads exactly.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone

import altair as alt
import pandas as pd
import streamlit as st

from finsight.api import schemas as s
from finsight.dashboard.service import REVIEWABLE, DashboardError, DashboardService
from finsight.contracts import RunState as S

SERIES_COLORS = ["#3987e5", "#d95926", "#199e70"]  # fixed order, same as the web app's validated palette
EVIDENCE_BADGE = {
    "supported": ("green", "Supported by filing quotes"),
    "partially_supported": ("orange", "Partly supported"),
    "unsupported": ("red", "No supporting evidence found"),
    "not_investigated": ("gray", "Not investigated"),
}
PHASE_BADGE = {
    "queued": ("gray", "Queued"), "running": ("blue", "Running"),
    "ready_for_review": ("orange", "Ready for review"), "partial": ("orange", "Partial result"),
    "approved": ("green", "Approved"), "failed": ("red", "Failed"), "needs_input": ("orange", "Needs input"),
    "interrupted": ("gray", "Interrupted"),
}
STAGE_ICON = {"done": ":green[:material/check_circle:]", "active": ":blue[:material/progress_activity:]",
              "failed": ":red[:material/cancel:]", "skipped": ":gray[:material/remove:]",
              "pending": ":gray[:material/radio_button_unchecked:]"}
REVIEW_CHOICES = ["Not checked", "Confirmed", "Disputed"]


# --------------------------------------------------------------------------- setup


@st.cache_resource
def get_service() -> DashboardService:
    return DashboardService()


def badge(color: str, text: str) -> str:
    return f":{color}-badge[{text.replace(']', ')')}]"


def esc(text: str | None) -> str:
    """Escape text for Streamlit markdown: filing text can contain $, *, _ and brackets."""
    if not text:
        return ""
    out = text.replace("\\", "\\\\")
    for ch in "$*_`[]<>#|~":
        out = out.replace(ch, "\\" + ch)
    return out


def fmt_time(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def select_run(run_id: str | None) -> None:
    if run_id:
        st.query_params["run"] = run_id
    else:
        st.query_params.pop("run", None)


# --------------------------------------------------------------------------- sidebar


def sidebar(svc: DashboardService) -> None:
    avail = svc.availability()
    with st.sidebar:
        st.markdown("## FinSight")
        st.caption("Three-year analysis of a US-listed company from its SEC filings, "
                   "with every figure and explanation traceable to a filing.")
        st.markdown("#### New analysis")
        if not avail.live_data:
            st.info(avail.live_reason, icon=":material/info:")
        query = st.text_input("Company", key="company_query", placeholder="Ticker, CIK or name, e.g. MSFT",
                              help="Search SEC EDGAR's company list. DEMO is a synthetic sample that runs offline.")
        if not (query or "").strip():
            st.caption("Type a ticker, CIK or company name. DEMO runs a synthetic sample offline.")
            new_analysis_form(svc, None)
            hits = None
        else:
            hits, note = svc.search(query)
            if note:
                st.caption(note)
        if hits is None:
            pass
        elif not hits:
            st.caption("No matching company.")
            new_analysis_form(svc, None)
        else:
            labels = {f"{h.ticker or h.cik} · {h.name}": h for h in hits}
            choice = st.selectbox("Match", list(labels), key="company_choice", label_visibility="collapsed")
            new_analysis_form(svc, labels[choice] if choice else None)

        st.divider()
        st.markdown("#### Analyses")
        runs = svc.runs(15)
        if not runs:
            st.caption("No analyses yet.")
        current = st.query_params.get("run")
        for r in runs:
            name = r.company.ticker or r.company.name if r.company else r.identifier
            color, label = PHASE_BADGE.get(r.phase, ("gray", r.phase))
            if r.review.state == "approved":
                color, label = "green", "Approved"
            periods = f"{r.periods[0]}–{r.periods[-1]}" if r.periods else ""
            title = f"{'▸ ' if r.id == current else ''}{name} {periods}"
            if st.button(title, key=f"open_{r.id}", width="stretch",
                         help=f"{label} · started {fmt_time(r.created_at)} · run {r.id}"):
                select_run(r.id)
                st.rerun()


def new_analysis_form(svc: DashboardService, hit: s.CompanyHit | None) -> None:
    if hit is None:
        st.button("Run analysis", disabled=True, width="stretch", type="primary")
        return
    ident = hit.ticker or hit.cik
    cache = st.session_state.setdefault("profiles", {})
    if ident not in cache:
        try:
            with st.spinner(f"Loading {ident} filing history"):
                cache[ident] = svc.profile(ident)
        except DashboardError as exc:
            st.error(str(exc))
            return
    profile: s.CompanyProfile = cache[ident]
    meta = [profile.industry or "", f"FY ends {profile.fiscal_year_end}" if profile.fiscal_year_end else ""]
    st.caption(" · ".join(m for m in meta if m) or " ")
    if profile.synthetic:
        st.warning("Synthetic sample company. Its figures are made up for testing.", icon=":material/warning:")
    if not profile.supported:
        st.error(profile.unsupported_reason or "This company is not supported.")
        for step in profile.next_steps:
            st.caption(f"• {step}")
        return
    options = {o.fiscal_year: o for o in profile.fiscal_years}
    year = st.selectbox(
        "Period", list(options), key=f"fy_{profile.cik}",
        format_func=lambda y: f"{' → '.join(options[y].window)}" + ("" if options[y].complete else " (partial)"),
        help="Three completed fiscal years ending with the selected year's Form 10-K.")
    for n in profile.notes:
        st.caption(n)
    question = st.text_area("Question (optional)", key="question", max_chars=400, height=80,
                            placeholder="e.g. Why did operating margin change?")
    avail = svc.availability()
    use_claude = False
    if not profile.synthetic:
        use_claude = st.toggle("Investigate with Claude", value=avail.claude, disabled=not avail.claude,
                               help=(f"Model: {avail.model}. Explanations are kept only when quotes are verified "
                                     "against the filing text." if avail.claude else
                                     "Set ANTHROPIC_API_KEY to use Claude. The rule-based investigator "
                                     "is used instead."))
    if st.button("Run analysis", type="primary", width="stretch"):
        try:
            run_id = svc.start_run(profile, year, question, use_claude)
        except DashboardError as exc:
            st.error(str(exc))
            return
        select_run(run_id)
        st.rerun()


# --------------------------------------------------------------------------- run page


def header(status: s.RunStatus) -> None:
    c = status.company
    title = f"{c.name} ({c.ticker})" if c and c.ticker else (c.name if c else status.identifier)
    st.title(title)
    color, label = PHASE_BADGE.get(status.phase, ("gray", status.phase))
    review_color = {"approved": "green", "needs_attention": "red", "awaiting_review": "orange",
                    "changes_requested": "orange"}.get(
        status.review.state, "gray")
    bits = [badge(color, label)]
    if status.phase in ("ready_for_review", "partial"):
        bits.append(badge(review_color, status.review.label))
    if status.periods:
        bits.append(f"Fiscal years {', '.join(status.periods)}")
    if status.investigator:
        bits.append(f"Investigator: {esc(status.investigator.name)}")
    st.markdown(" &nbsp; ".join(bits))
    if status.question:
        st.caption(f"Question: {status.question}")
    if status.synthetic:
        st.warning("**Synthetic sample.** These figures are made up for testing and do not describe any real "
                   "company.", icon=":material/warning:")


@st.fragment(run_every=2)
def progress_panel(svc: DashboardService, run_id: str) -> None:
    status = svc.status(run_id)
    if status.phase not in ("queued", "running"):
        st.rerun(scope="app")
    header(status)
    st.markdown(f"**{esc(status.current_activity)}**  ·  {status.elapsed_seconds:.0f}s elapsed")
    with st.container(border=True):
        for stage in status.stages:
            line = f"{STAGE_ICON.get(stage.state, '')} &nbsp; **{stage.label}**"
            if stage.detail:
                line += f" &nbsp; <span style='opacity:.7'>{esc(stage.detail)}</span>"
            st.markdown(line, unsafe_allow_html=True)
    if status.investigation:
        inv = status.investigation
        st.progress(min(1.0, inv.investigated / max(1, min(inv.flagged, inv.limit))),
                    text=f"Investigated {inv.investigated} of {min(inv.flagged, inv.limit)} flagged changes")
    for w in status.warnings:
        st.warning(w.replace("under Sources and limitations", "under Data quality"))
    with st.expander("Activity", expanded=False):
        for a in reversed(status.activity[-30:]):
            st.markdown(f"`{a.at.strftime('%H:%M:%S')}` {esc(a.message)}")


def not_finished(svc: DashboardService, status: s.RunStatus) -> None:
    if status.phase in ("queued", "running"):
        progress_panel(svc, status.id)
        return
    header(status)
    if status.phase == "needs_input" and status.needs_input:
        st.warning(status.needs_input.question)
        opts = {c.cik: f"{c.name} ({', '.join(c.tickers) or 'no ticker'}) · CIK {c.cik}"
                for c in status.needs_input.candidates}
        cik = st.radio("Which company did you mean?", list(opts), format_func=opts.get)
        if st.button("Continue with this company", type="primary"):
            try:
                svc.resume(status.id, cik)
            except DashboardError as exc:
                st.error(str(exc))
            st.rerun()
        return
    if status.error:
        st.error(status.error.message)
        for step in status.error.next_steps:
            st.caption(f"• {step}")
    elif status.phase == "interrupted":
        st.info("This analysis stopped before finishing (the dashboard was restarted while it ran).")
    st.markdown(" &nbsp; ".join(f"{STAGE_ICON.get(x.state, '')} {x.label}" for x in status.stages))
    if st.button("Resume from the last completed stage", type="primary"):
        try:
            svc.resume(status.id)
        except DashboardError as exc:
            st.error(str(exc))
        st.rerun()


# --------------------------------------------------------------------------- result tabs


def table_frame(t: s.DataTable) -> pd.DataFrame:
    return pd.DataFrame([[c.display for c in r.cells] for r in t.rows], index=[r.label for r in t.rows],
                        columns=t.columns)


def summary_tab(res: s.RunResult) -> None:
    ov = res.overview
    left, right = st.columns([3, 2])
    with left:
        st.subheader("Summary")
        for line in ov.headline:
            st.markdown(f"- {esc(line)}")
        if not ov.headline:
            st.caption("No summary statements were drafted for this run.")
    with right:
        st.subheader("Company")
        st.markdown(
            f"**CIK** {ov.company.cik}  \n**Industry** {esc(ov.industry) or '-'} (SIC {ov.sic or '-'})  \n"
            f"**Fiscal year end** {ov.fiscal_year_end or '-'}  \n**Currency** {ov.currency}  \n"
            f"**Filings up to** {ov.cutoff_date}  \n**Data** {esc(ov.data_origin_label)}")
    st.subheader("Key metrics")
    st.caption(f"Latest fiscal year, compared with the year before. Formulas are in the help icons and in "
               f"Data quality → Calculations.")
    km = res.key_metrics
    for i in range(0, len(km), 4):
        cols = st.columns(4)
        for col, m in zip(cols, km[i:i + 4]):
            with col, st.container(border=True):
                comp = m.comparison
                st.metric(f"{m.label} · {m.period}", m.display,
                          delta=f"{comp.display} {comp.label}" if comp and comp.display else None,
                          delta_color="off", help=f"{m.formula}" + (f"\n\n{m.note}" if m.note else ""))
    if res.interpretation:
        st.subheader("By area")
        st.caption("Calculated statements only. Judgements about cause are in Changes & evidence, with sources.")
        cols = st.columns(2)
        for i, area in enumerate(res.interpretation):
            with cols[i % 2], st.container(border=True):
                st.markdown(f"**{area.title}**")
                if not area.available:
                    st.caption(area.note or "Not enough data for this area.")
                for o in area.observations:
                    st.markdown(f"- {esc(o)}")


def chart(c: s.Chart) -> alt.Chart:
    rows = []
    for si, series in enumerate(c.series):
        for period, value, mid in zip(c.periods, series.values, series.metric_ids):
            rows.append({"Period": period, "Series": series.name, "Value": value, "Metric ID": mid or "-",
                         "order": si})
    df = pd.DataFrame(rows)
    names = [x.name for x in c.series]
    color = alt.Color("Series:N", scale=alt.Scale(domain=names, range=SERIES_COLORS[:len(names)]),
                      legend=alt.Legend(orient="bottom", title=None, labelLimit=0, columns=3))
    tooltip = ["Period:N", "Series:N", alt.Tooltip("Value:Q", format=",.2f", title=c.unit), "Metric ID:N"]
    base = alt.Chart(df).encode(x=alt.X("Period:N", title=None, axis=alt.Axis(labelAngle=0)),
                                y=alt.Y("Value:Q", title=c.unit), color=color, tooltip=tooltip)
    if c.kind == "bar":
        mark = base.mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, stroke=None).encode(
            xOffset=alt.XOffset("Series:N", sort=names))
    else:
        mark = base.mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=70, filled=True))
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color="#33496a").encode(y="y:Q")
    return (mark + zero).properties(height=280, title=alt.Title(c.title, anchor="start"))


def metrics_tab(res: s.RunResult) -> None:
    cols = st.columns(2)
    for i, c in enumerate(res.charts):
        with cols[i % 2], st.container(border=True):
            st.altair_chart(chart(c), width="stretch")
            if c.caption:
                st.caption(c.caption)
            with st.expander("Values plotted"):
                df = pd.DataFrame({x.name: x.values for x in c.series}, index=c.periods)
                st.dataframe(df, width="stretch")
    st.subheader("Financials")
    st.caption(res.financials.caption)
    st.dataframe(table_frame(res.financials), width="stretch")
    st.subheader("Year-over-year changes")
    st.caption(res.year_over_year.caption)
    st.dataframe(table_frame(res.year_over_year), width="stretch")


def passage_ref(p: s.Passage | None, passage_id: str) -> str:
    if p is None:
        return f"`{passage_id}`"
    where = f"{p.form} filed {p.filing_date}, {esc(p.section)}"
    link = f"[{where}]({p.url})" if p.url and not p.is_fixture else where
    return f"**[P{p.number}]** {link} · accession {p.accession}"


def changes_tab(res: s.RunResult) -> None:
    if not res.changes:
        st.info("No change exceeded the configured thresholds, so nothing was investigated.")
        return
    passages = {p.id: p for p in res.passages}
    counts = pd.Series([c.evidence for c in res.changes]).value_counts()
    st.markdown("  ".join(badge(EVIDENCE_BADGE[k][0], f"{EVIDENCE_BADGE[k][1]}: {counts.get(k, 0)}")
                          for k in EVIDENCE_BADGE if counts.get(k, 0)))
    st.caption("Ranked by how far each change exceeded its threshold, not by importance. Observations are "
               "calculated by code; explanations are kept only when a quote was found verbatim in the filing.")
    for c in res.changes:
        color, label = EVIDENCE_BADGE[c.evidence]
        with st.container(border=True):
            st.markdown(f"#### {c.rank}. {esc(c.metric_label)} · {esc(c.comparison)}")
            st.markdown(f"{badge(color, label)} &nbsp; **{esc(c.magnitude)}** &nbsp; "
                        f"<span style='opacity:.75'>{esc(c.threshold)}</span>", unsafe_allow_html=True)
            f = c.finding
            if f is None:
                st.markdown(esc(c.description))
                st.caption("Not investigated (outside the investigation budget).")
                continue
            st.markdown(f"**Calculated observation**  \n{esc(f.calculated_observation)}")
            if f.management_explanation:
                st.markdown(f"**Management explanation (from the filing)**  \n{esc(f.management_explanation)}")
            for q in f.quotes:
                mark = (":green[:material/verified:] verified verbatim" if q.verified
                        else f":orange[:material/warning:] not verified ({esc(q.note)})")
                st.markdown(f"> {esc(q.text)}\n\n{passage_ref(passages.get(q.passage_id), q.passage_id)} · {mark}")
            if f.analyst_interpretation:
                st.markdown(f"**AI interpretation (not stated in the filing)**  \n{esc(f.analyst_interpretation)}")
            if f.unresolved_questions:
                st.markdown("**Unresolved**\n" + "\n".join(f"- {esc(q)}" for q in f.unresolved_questions))
            st.caption(f"{esc(f.status_reason)} · {esc(f.produced_by)} · {f.steps_used} tool step(s)"
                       + (" · step budget exhausted" if f.budget_exhausted else "")
                       + f" · metrics: {', '.join(c.metric_ids)}")
    if res.unresolved_questions:
        st.subheader("Open questions")
        for q in res.unresolved_questions:
            st.markdown(f"- {esc(q)}")


def quality_tab(res: s.RunResult) -> None:
    sev_order = {"blocking": 0, "recoverable": 1, "warning": 2, "info": 3}
    issues = sorted(res.issues, key=lambda i: sev_order.get(i.severity, 9))
    counts = pd.Series([i.severity for i in issues]).value_counts() if issues else pd.Series(dtype=int)
    cols = st.columns(4)
    for col, sev in zip(cols, ["blocking", "recoverable", "warning", "info"]):
        col.metric(sev.capitalize(), int(counts.get(sev, 0)))
    if issues:
        st.dataframe(pd.DataFrame([i.model_dump() for i in issues])[["severity", "code", "field", "period",
                                                                     "message"]],
                     width="stretch", hide_index=True)
    st.subheader("Missing or unavailable figures")
    if res.missing:
        st.dataframe(pd.DataFrame([m.model_dump() for m in res.missing]), width="stretch",
                     hide_index=True)
    else:
        st.caption("Every metric was calculated. Nothing was estimated or filled in.")
    st.subheader("Calculations")
    st.caption("Every metric with its formula and the XBRL facts or metrics it was computed from.")
    df = pd.DataFrame([{"Metric": c.label, "Period": c.period, "Value": c.display, "Status": c.status,
                        "Formula": c.formula, "Inputs": ", ".join(c.inputs), "Note": c.note or "",
                        "ID": c.metric_id} for c in res.calculations])
    st.dataframe(df, width="stretch", hide_index=True)
    if res.limitations:
        st.subheader("Limitations")
        for x in res.limitations:
            st.markdown(f"- {esc(x)}")


def sources_tab(res: s.RunResult) -> None:
    st.subheader("Fiscal periods")
    st.dataframe(pd.DataFrame([{
        "Fiscal year": p.label, "Start": p.start_date, "End": p.end_date, "Defining 10-K": p.accession,
        "Filed": p.filed, "Filing": p.filing_url} for p in res.overview.periods]),
        column_config={"Filing": st.column_config.LinkColumn("Filing", display_text="Open")},
        width="stretch", hide_index=True)
    st.subheader("Cited passages")
    if res.passages:
        st.dataframe(pd.DataFrame([{
            "Ref": f"P{p.number}", "Form": p.form, "Filed": p.filing_date, "Fiscal year": p.fiscal_period,
            "Section": p.section, "Characters": f"{p.char_start}–{p.char_end}", "Text": p.text,
            "Accession": p.accession, "URL": None if p.is_fixture else p.url} for p in res.passages]),
            column_config={"URL": st.column_config.LinkColumn("URL", display_text="Open"),
                           "Text": st.column_config.TextColumn("Text", width="large")},
            width="stretch", hide_index=True)
    else:
        st.caption("The report cites no filing passages.")
    st.subheader("Documents retrieved")
    st.dataframe(pd.DataFrame([{
        "Document": d.label, "Form": d.form, "Filed": d.filing_date, "Retrieved": d.retrieved_at,
        "From cache": d.from_cache, "SHA-256": d.sha256, "URL": None if d.is_fixture else d.url}
        for d in res.sources]),
        column_config={"URL": st.column_config.LinkColumn("URL", display_text="Open")},
        width="stretch", hide_index=True)


def review_tab(svc: DashboardService, res: s.RunResult) -> None:
    rd, status = res.review, res.status
    st.subheader("Automated review")
    passed = "passed" if rd.passed else ("did not pass" if rd.passed is False else "not run")
    st.markdown(f"The report's own checker {passed} after {rd.revision_cycles} revision cycle(s)"
                + (f", over {rd.claims} claims ({rd.excluded_claims} removed during revision)."
                   if rd.claims is not None else "."))
    if rd.defects:
        body = "\n".join(f"- {esc(d)}" for d in rd.defects)
        if rd.passed:
            st.warning("Points the reviewer raised (warnings; worth checking before you approve):\n" + body)
        else:
            st.error("Defects the reviewer could not resolve:\n" + body)
    with st.expander(f"Checks run ({len(rd.checks_run)})"):
        for c in rd.checks_run:
            st.markdown(f"- `{c}`")

    st.subheader("Human review")
    history = svc.human_reviews(status.id)
    for h in history:
        color, label = ("green", "Approved") if h["decision"] == "approved" else ("orange", "Changes requested")
        st.markdown(f"{badge(color, label)} by **{esc(h['reviewer'])}** · {h['at'][:16].replace('T', ' ')} UTC"
                    + (f"  \n{esc(h['note'])}" if h.get("note") else ""))
        disputed = [k for k, v in (h.get("checks") or {}).get("findings", {}).items() if v == "Disputed"]
        if disputed:
            st.caption("Disputed: " + ", ".join(disputed))

    run_state = S(status.state)
    if run_state == S.APPROVED:
        st.success(f"Approved {fmt_time(status.review.approved_at)}. The report is final.")
        return
    if run_state not in REVIEWABLE:
        st.caption("This run cannot be reviewed.")
        return

    rid = status.id
    if run_state == S.PARTIAL:
        st.warning("This is a partial result: blocking data issues left some metrics unavailable. "
                   "Approving it approves what is there; nothing missing was estimated.")
    st.markdown("Check each investigated change against the quoted filing passages, then decide. "
                "Approval needs every change confirmed. If anything is wrong, request changes and say what.")
    findings: dict[str, str] = {}
    for c in res.changes:
        if c.finding is None:
            continue
        label = f"{c.rank}. {c.metric_label} · {c.comparison} — {EVIDENCE_BADGE[c.evidence][1]}"
        findings[c.id] = st.segmented_control(label, REVIEW_CHOICES, default="Not checked",
                                              key=f"rv_{rid}_{c.id}") or "Not checked"
    figures = st.checkbox("I compared the key figures with the financial statements in the 10-K filings",
                          key=f"rv_{rid}_figures")
    limits = st.checkbox("I read the data quality issues and limitations", key=f"rv_{rid}_limits")
    reviewer = st.text_input("Your name", key=f"rv_{rid}_name")
    note = st.text_area("Note", key=f"rv_{rid}_note", max_chars=1000,
                        placeholder="What you checked, or what needs to change")
    all_confirmed = all(v == "Confirmed" for v in findings.values())
    disputed = [k for k, v in findings.items() if v == "Disputed"]
    can_approve = bool(reviewer.strip()) and figures and limits and all_confirmed
    can_request = bool(reviewer.strip()) and (bool(disputed) or bool(note.strip()))
    missing = []
    if not reviewer.strip():
        missing.append("your name")
    if not all_confirmed:
        missing.append(f"{sum(v != 'Confirmed' for v in findings.values())} change(s) not confirmed")
    if not (figures and limits):
        missing.append("both checkboxes")
    checks = {"findings": findings, "figures_checked": figures, "limitations_read": limits}
    a, b = st.columns(2)
    if a.button("Approve report", type="primary", disabled=not can_approve, width="stretch",
                key=f"rv_{rid}_approve", help=None if can_approve else "Needs " + ", ".join(missing)):
        try:
            svc.record_review(rid, "approved", reviewer, note, checks)
            st.toast("Report approved")
        except DashboardError as exc:
            st.error(str(exc))
        st.rerun()
    if b.button("Request changes", disabled=not can_request, width="stretch", key=f"rv_{rid}_reject",
                help=None if can_request else "Needs your name and a note or a disputed change"):
        try:
            svc.record_review(rid, "changes_requested", reviewer, note, checks)
            st.toast("Changes requested; the report stays unapproved")
        except DashboardError as exc:
            st.error(str(exc))
        st.rerun()
    if not can_approve:
        st.caption("To approve: " + ", ".join(missing) + ".")


def downloads_tab(svc: DashboardService, res: s.RunResult) -> None:
    try:
        out, items = svc.downloads(res.status.id)
    except DashboardError as exc:
        st.error(str(exc))
        return
    report = out / "report.html"
    if report.is_file():
        with st.expander("Preview the report", expanded=False):
            # A data: URL gives the iframe an opaque origin, so the report cannot reach the app.
            src = "data:text/html;base64," + base64.b64encode(report.read_bytes()).decode()
            if hasattr(st, "iframe"):
                st.iframe(src, height=900)
            else:  # Streamlit before st.iframe
                st.components.v1.iframe(src, height=900, scrolling=True)
    for d in items:
        cols = st.columns([3, 5, 2])
        cols[0].markdown(f"**{esc(d.label)}**")
        cols[1].caption(f"{d.description} · {d.size_bytes / 1024:,.0f} KB")
        mime = {"csv": "text/csv", "json": "application/json", "md": "text/markdown", "html": "text/html",
                "png": "image/png"}[d.format]
        cols[2].download_button("Download", data=(out / d.name).read_bytes(),
                                file_name=f"finsight-{res.status.id}-{d.name.replace('/', '-')}", mime=mime,
                                key=f"dl_{d.name}", width="stretch", on_click="ignore")


def run_page(svc: DashboardService, run_id: str) -> None:
    try:
        status = svc.status(run_id)
    except DashboardError as exc:
        st.error(str(exc))
        select_run(None)
        return
    if status.phase not in ("ready_for_review", "partial", "approved"):
        not_finished(svc, status)
        return
    header(status)
    res = svc.result(run_id)
    if res is None:
        st.error("This analysis finished but its results could not be loaded.")
        return
    review_label = "Review & approval" + (" · approved" if status.review.state == "approved" else "")
    tabs = st.tabs(["Summary", "Metrics & charts", f"Changes & evidence ({len(res.changes)})", "Data quality",
                    "Sources", review_label, "Downloads"])
    with tabs[0]:
        summary_tab(res)
    with tabs[1]:
        metrics_tab(res)
    with tabs[2]:
        changes_tab(res)
    with tabs[3]:
        quality_tab(res)
    with tabs[4]:
        sources_tab(res)
    with tabs[5]:
        review_tab(svc, res)
    with tabs[6]:
        downloads_tab(svc, res)


def home(svc: DashboardService) -> None:
    st.title("FinSight")
    st.markdown("Analyse how a US-listed company performed over its last three completed fiscal years, "
                "from its SEC filings. Choose a company in the sidebar and run the analysis.")
    cols = st.columns(4)
    steps = [("Collect & validate", "XBRL company facts and 10-K filings from SEC EDGAR, checked for "
                                    "consistency, restatements and missing values."),
             ("Calculate", "Margins, growth, cash flow, liquidity and leverage in Python, each with its "
                           "formula and input facts."),
             ("Investigate", "Significant changes are looked up in the 10-K. An explanation is kept only "
                             "when its quote is found verbatim."),
             ("Review", "An automated reviewer checks every claim. You confirm the findings and approve.")]
    for col, (t, d) in zip(cols, steps):
        with col, st.container(border=True):
            st.markdown(f"**{t}**")
            st.caption(d)
    avail = svc.availability()
    st.caption(("Live SEC data: available. " if avail.live_data else "Live SEC data: not configured. ")
               + (f"Investigator: Claude ({avail.model})." if avail.claude else
                  "Investigator: rule-based (set ANTHROPIC_API_KEY to use Claude)."))


def main() -> None:
    st.set_page_config(page_title="FinSight", page_icon=":material/analytics:", layout="wide")
    svc = get_service()
    sidebar(svc)
    run_id = st.query_params.get("run")
    if run_id:
        run_page(svc, run_id)
    else:
        home(svc)


if __name__ == "__main__":
    main()
