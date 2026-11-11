import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, ApiError } from "../api/client";
import { sample } from "../api/sample";
import type { Chart, DataTable, DownloadInfo, InterpretationArea, KeyMetric, RunResult, RunStatus } from "../api/types";
import { displayCompany, formatBytes, formatDate, formatDateTime, formatDuration, periodRange } from "../lib/format";
import { toApiError } from "../lib/hooks";
import { useService } from "../lib/service";
import { ChartCard, Sparkline } from "./Charts";
import { Badge, ErrorNotice, ExternalLink, Notice, PhaseBadge, ReviewBadge, StatusPair, SyntheticBadge } from "./common";
import { Diagnostics, type ResultSource } from "./Diagnostics";
import { ChangeList } from "./Findings";
import { Icon, type IconName } from "./Icon";
import { Sources } from "./Sources";

const SECTIONS = [
  ["summary", "Summary"],
  ["metrics", "Key metrics"],
  ["charts", "Charts"],
  ["changes", "Biggest changes"],
  ["financials", "Financials"],
  ["sources", "Evidence"],
  ["about", "Review and details"],
  ["downloads", "Downloads"],
] as const;

function Section({ id, title, lead, children, aside }: { id: string; title: string; lead?: ReactNode; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="result-section" id={id} aria-labelledby={`${id}-h`}>
      <div className="section-head">
        <div>
          <h2 id={`${id}-h`}>{title}</h2>
          {lead && <p className="muted section-lead">{lead}</p>}
        </div>
        {aside}
      </div>
      {children}
    </section>
  );
}

/** Section links that stay in reach while scrolling and mark the section being read. */
function SectionNav() {
  const [active, setActive] = useState<string>(SECTIONS[0][0]);
  const list = useRef<HTMLUListElement>(null);

  useEffect(() => {
    // The section being read is the last one whose top has passed under the sticky bars.
    let frame = 0;
    const update = () => {
      frame = 0;
      let current: string = SECTIONS[0][0];
      for (const [id] of SECTIONS) {
        const node = document.getElementById(id);
        if (node && node.getBoundingClientRect().top <= 180) current = id;
      }
      const atEnd = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2;
      if (atEnd && window.scrollY > 0) {
        const last = [...SECTIONS].reverse().find(([id]) => document.getElementById(id));
        if (last) current = last[0];
      }
      setActive(current);
    };
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);

  // Keep the marked link in view inside the strip without moving the page.
  useEffect(() => {
    const ul = list.current;
    const link = ul?.querySelector<HTMLElement>(`a[href="#${active}"]`);
    if (!ul || !link) return;
    const left = link.offsetLeft - 16;
    const right = link.offsetLeft + link.offsetWidth + 16;
    if (left < ul.scrollLeft) ul.scrollLeft = left;
    else if (right > ul.scrollLeft + ul.clientWidth) ul.scrollLeft = right - ul.clientWidth;
  }, [active]);

  return (
    <nav className="section-nav" aria-label="Report sections">
      <ul ref={list}>
        {SECTIONS.map(([id, label]) => (
          <li key={id}><a href={`#${id}`} aria-current={active === id ? "true" : undefined}>{label}</a></li>
        ))}
      </ul>
    </nav>
  );
}

// ---------------------------------------------------------------- key metrics

const DIRECTION_ICON: Record<"up" | "down" | "flat" | "none", IconName> = { up: "up", down: "down", flat: "flat", none: "dash" };
const DIRECTION_WORD = { up: "Up", down: "Down", flat: "Unchanged", none: "" } as const;

/**
 * One metric: latest value, its fiscal year, the change from the year before and the three-year
 * shape. Changes are drawn in a neutral colour: whether "up" is good depends on the metric.
 */
export function MetricTile({ metric, compact }: { metric: KeyMetric; compact?: boolean }) {
  const available = metric.status === "available";
  const c = metric.comparison;
  return (
    <article className={`metric${compact ? " metric-compact" : ""}`} aria-label={`${metric.label}, ${metric.period}`}>
      <header>
        <h3>{metric.label}</h3>
        <span className="metric-tag" title={metric.formula}>
          {metric.is_calculated ? "calculated" : "reported"}
        </span>
      </header>
      <p className="metric-value num">
        {available ? (
          <>
            {metric.value_text}
            {metric.unit_text && <span className="metric-unit">{metric.unit_text}</span>}
          </>
        ) : (
          <span className="metric-na">Not available</span>
        )}
      </p>
      <p className="metric-compare">
        {c && c.display && (
          <span className="delta num">
            <Icon name={DIRECTION_ICON[c.direction]} size={12} />
            <span className="sr-only">{DIRECTION_WORD[c.direction]} </span>
            {c.display} {c.label}
          </span>
        )}
        {c && !c.display && <span className="muted">No comparison {c.label}</span>}
        <span className="muted metric-period">{metric.period}</span>
      </p>
      {!compact && (
        <div className="metric-trend">
          <Sparkline values={metric.series.map((p) => p.value)} />
          <dl className="metric-series">
            {metric.series.map((p) => (
              <div key={p.period}>
                <dt>{p.period}</dt>
                <dd className={`num${p.value === null ? " cell-missing" : ""}`}>{shortDisplay(p.display)}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
      {!compact && (metric.note || c?.note) && (
        <p className="metric-note">
          <Icon name="info" size={12} /> <span>{[metric.note, c?.note].filter(Boolean).join(" ")}</span>
        </p>
      )}
    </article>
  );
}

/** "$1,239 million" is long for a three-column strip; the unit words are abbreviated for display only. */
function shortDisplay(text: string): string {
  return text.replace(" million", "M").replace(" billion", "B");
}

export const PRIMARY_METRICS = ["revenue", "operating_margin", "net_income", "free_cash_flow"];

// ---------------------------------------------------------------- charts

const CHART_GROUPS: { title: string; ids: string[] }[] = [
  { title: "Revenue and profitability", ids: ["chart:revenue_profit", "chart:margins"] },
  { title: "Cash flow", ids: ["chart:cash_flow"] },
  { title: "Cash and debt", ids: ["chart:cash_debt"] },
];

function ChartGroups({ charts }: { charts: Chart[] }) {
  const known = new Set(CHART_GROUPS.flatMap((g) => g.ids));
  const groups = [
    ...CHART_GROUPS.map((g) => ({ title: g.title, charts: g.ids.map((id) => charts.find((c) => c.id === id)).filter((c): c is Chart => Boolean(c)) })),
    { title: "Other charts", charts: charts.filter((c) => !known.has(c.id)) },
  ].filter((g) => g.charts.length > 0);
  return (
    <div className="chart-groups">
      {groups.map((g) => (
        <section key={g.title} className={`chart-group${g.charts.length > 1 ? " is-wide" : ""}`} aria-label={g.title}>
          <h3 className="group-title">{g.title}</h3>
          <div className="chart-row">{g.charts.map((c) => <ChartCard key={c.id} chart={c} />)}</div>
        </section>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- tables

export function FinancialTable({ table }: { table: DataTable }) {
  return (
    <div className="table-scroll" tabIndex={0} role="group" aria-label="Scrollable table">
      <table className="data">
        <caption>{table.caption}</caption>
        <thead>
          <tr>
            <th scope="col">Line item</th>
            {table.columns.map((c) => (
              <th key={c} scope="col" className="num">{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row) => (
            <tr key={row.name}>
              <th scope="row">{row.label}</th>
              {row.cells.map((cell, i) => (
                <td key={table.columns[i]} className={`num${cell.status === "available" ? "" : " cell-missing"}`} title={cell.metric_id ?? undefined}>
                  {cell.display}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------- interpretation

const AREA_ICON: Record<InterpretationArea["key"], IconName> = {
  profitability: "layers", liquidity: "ring", cash_generation: "refresh", leverage: "building",
};

function Interpretation({ areas, changes }: { areas: InterpretationArea[]; changes: RunResult["changes"] }) {
  const byId = new Map(changes.map((c) => [c.id, c]));
  return (
    <div className="areas">
      {areas.map((a) => (
        <article key={a.key} className="area panel">
          <header className="panel-head">
            <h4><Icon name={AREA_ICON[a.key]} size={15} className="inline-icon" /> {a.title}</h4>
            {!a.available && <Badge tone="amber" icon="dash">Not enough data</Badge>}
          </header>
          <div className="panel-body">
            {a.observations.length > 0 ? (
              <ul className="bullets">{a.observations.map((o) => <li key={o} className="num">{o}</li>)}</ul>
            ) : (
              <p className="muted">{a.note ?? "No measures available."}</p>
            )}
            {a.related_change_ids.length > 0 && (
              <p className="area-links">
                <span className="muted">Investigated changes:</span>{" "}
                {a.related_change_ids.map((id) => {
                  const c = byId.get(id);
                  return c ? <a key={id} href={`#change-${c.rank}`}>#{c.rank} {c.metric_label}, {c.comparison}</a> : null;
                })}
              </p>
            )}
          </div>
        </article>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- downloads

const FORMAT_LABEL: Record<DownloadInfo["format"], string> = { html: "HTML", md: "Markdown", csv: "CSV", json: "JSON", png: "PNG" };

function Downloads({ downloads, source }: { downloads: DownloadInfo[]; source: ResultSource }) {
  const url = (name: string) => (source.kind === "run" ? api.fileUrl(source.id, name) : sample.fileUrl(source.id, name));
  const groups: [string, DownloadInfo[]][] = [
    ["Report", downloads.filter((d) => d.format === "html" || d.format === "md")],
    ["Data", downloads.filter((d) => (d.format === "csv" || d.format === "json") && !d.name.startsWith("charts/"))],
    ["Charts", downloads.filter((d) => d.name.startsWith("charts/"))],
  ];
  if (downloads.length === 0) return <p className="muted">No files were generated for this run.</p>;
  return (
    <div className="downloads">
      {groups.filter(([, items]) => items.length > 0).map(([title, items]) => (
        <section key={title} className="panel">
          <div className="panel-head"><h3>{title}</h3><span className="muted num">{items.length} file{items.length === 1 ? "" : "s"}</span></div>
          <ul className="download-list">
            {items.map((d) => (
              <li key={d.name}>
                <a href={url(d.name)} download={d.name.replace("/", "-")} className="download">
                  <Icon name="download" />
                  <span className="download-main">
                    <span className="download-label">{d.label}</span>
                    <span className="muted download-desc">{d.description}</span>
                  </span>
                  <span className="download-meta num"><Badge square>{FORMAT_LABEL[d.format]}</Badge> {formatBytes(d.size_bytes)}</span>
                </a>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- review

function ReviewPanel({ result, source, onStatus }: { result: RunResult; source: ResultSource; onStatus?: (s: RunStatus) => void }) {
  const { service } = useService();
  const status = result.status;
  const [note, setNote] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const canApprove = source.kind === "run" && (status.review.state === "awaiting_review" || status.review.state === "needs_attention");
  const needsToken = service.health?.approval === "token_required";

  const approve = () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    api
      .approve(status.id, note.trim(), token.trim() || undefined)
      .then((s) => onStatus?.(s))
      .catch((err: unknown) => setError(toApiError(err)))
      .finally(() => setBusy(false));
  };

  return (
    <section className="panel review" aria-labelledby="review-h">
      <div className="panel-head">
        <h3 id="review-h">Report review</h3>
        <ReviewBadge review={status.review} />
      </div>
      <div className="panel-body">
        <dl className="facts">
          <div><dt>Automated checks</dt><dd>{result.review.passed === null ? "Not run" : result.review.passed ? "Passed" : "Did not pass"}</dd></div>
          <div><dt>Checks run</dt><dd className="num">{result.review.checks_run.length}</dd></div>
          <div><dt>Revision cycles</dt><dd className="num">{result.review.revision_cycles}</dd></div>
          <div><dt>Claims checked</dt><dd className="num">{result.review.claims ?? "not recorded"}</dd></div>
        </dl>
        {result.review.defects.length > 0 && (
          <Notice tone="coral" title={`${result.review.defects.length} review defect${result.review.defects.length === 1 ? "" : "s"} remain`}>
            <ul>{result.review.defects.map((d) => <li key={d}>{d}</li>)}</ul>
          </Notice>
        )}
        {status.review.state === "approved" && (
          <p className="approved-line"><Icon name="shield" /> Approved {formatDateTime(status.review.approved_at)}{status.review.approval_note ? `: “${status.review.approval_note}”` : ""}</p>
        )}
        {source.kind === "sample" && status.review.state !== "approved" && (
          <p className="muted">This sample was never approved. Approval is an explicit analyst action on a live run.</p>
        )}
        {canApprove && (
          <div className="approve">
            <p className="muted">The automated review does not approve a report. An analyst does, after reading it.</p>
            <div className="approve-row">
              <div className="field">
                <label htmlFor="approve-note">Review note <span className="optional">optional</span></label>
                <input id="approve-note" className="input" maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} placeholder="What you checked" />
              </div>
              {needsToken && (
                <div className="field">
                  <label htmlFor="approve-token">Reviewer token</label>
                  <input id="approve-token" className="input" type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} />
                </div>
              )}
              <button type="button" className="btn btn-primary" disabled={busy || service.kind !== "online" || (needsToken && !token.trim())} onClick={approve}>
                <Icon name="shield" /> {busy ? "Approving…" : "Mark as approved"}
              </button>
            </div>
            {error && <ErrorNotice error={error} title="The report was not approved" />}
          </div>
        )}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- notes

/** Everything a reader should know before relying on the report, in one place instead of a stack of banners. */
function ReportNotes({ result, source, generatedAt }: { result: RunResult; source: ResultSource; generatedAt?: string }) {
  const { status } = result;
  const rows: { key: string; icon: IconName; title: string; text: ReactNode }[] = [];
  if (status.synthetic) {
    rows.push({
      key: "synthetic",
      icon: "layers",
      title: source.kind === "sample" ? "Sample report on synthetic data" : "Synthetic sample data",
      text: "The company, its figures and its filing text are made up to show the full workflow. They do not describe any real company.",
    });
  } else if (source.kind === "sample") {
    rows.push({
      key: "cached",
      icon: "archive",
      title: "Stored sample report",
      text: `A saved copy of an analysis of real SEC filings, generated ${formatDateTime(generatedAt ?? result.overview.analysis_completed_at)}. It is not refreshed, so newer filings are not included.`,
    });
  }
  if (status.phase === "partial") {
    rows.push({
      key: "partial",
      icon: "half",
      title: "Partial results",
      text: "Some required figures were missing or failed validation, so the metrics that depend on them are marked as not available. Nothing was estimated.",
    });
  }
  if (rows.length === 0 && status.warnings.length === 0) return null;
  return (
    <aside className="report-notes" aria-label="Before you rely on this report">
      {rows.map((r) => (
        <p key={r.key} className="report-note">
          <Icon name={r.icon} size={15} />
          <span><strong>{r.title}</strong> <span className="secondary">{r.text}</span></span>
        </p>
      ))}
      {status.warnings.length > 0 && (
        <details className="report-note-more">
          <summary>
            <Icon name="chevron" size={13} />
            {status.warnings.length} more thing{status.warnings.length === 1 ? "" : "s"} to know before relying on this report
          </summary>
          <ul className="bullets">{status.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
        </details>
      )}
    </aside>
  );
}

// ---------------------------------------------------------------- the dashboard

interface ResultViewProps {
  result: RunResult;
  source: ResultSource;
  /** Extra notice above the report, e.g. "connected to an analysis that was already running". */
  banner?: ReactNode;
  /** When a bundled sample was generated. */
  generatedAt?: string;
  onStatus?: (s: RunStatus) => void;
}

export function ResultView({ result, source, banner, generatedAt, onStatus }: ResultViewProps) {
  const [showDev, setShowDev] = useState(false);
  const [showAllPoints, setShowAllPoints] = useState(false);
  const { status, overview } = result;
  const name = displayCompany(overview.company.name);
  const range = periodRange(overview.periods.map((p) => p.label));
  const latest = overview.periods[overview.periods.length - 1];
  const report = result.downloads.find((d) => d.name === "report.html");
  const reportUrl = report && (source.kind === "run" ? api.fileUrl(source.id, report.name, true) : sample.fileUrl(source.id, report.name));
  const counts = {
    supported: result.changes.filter((c) => c.evidence === "supported").length,
    partial: result.changes.filter((c) => c.evidence === "partially_supported").length,
    unsupported: result.changes.filter((c) => c.evidence === "unsupported").length,
    skipped: result.changes.filter((c) => c.evidence === "not_investigated").length,
  };
  const primary = PRIMARY_METRICS.map((n) => result.key_metrics.find((m) => m.name === n)).filter((m): m is KeyMetric => Boolean(m));
  const secondary = result.key_metrics.filter((m) => !PRIMARY_METRICS.includes(m.name));
  const points = showAllPoints ? overview.headline : overview.headline.slice(0, 4);
  const tally = (
    <p className="evidence-tally">
      <Badge tone="teal" icon="check">{counts.supported} supported</Badge>
      <Badge tone="amber" icon="half">{counts.partial} partly</Badge>
      <Badge tone="coral" icon="ring">{counts.unsupported} unexplained</Badge>
      {counts.skipped > 0 && <Badge icon="dash">{counts.skipped} not investigated</Badge>}
    </p>
  );

  return (
    <div className="result">
      <header className="report-head">
        <div className="report-head-main">
          <p className="eyebrow">{source.kind === "sample" ? "Sample report" : "Analysis report"}</p>
          <h1>{name}</h1>
          <p className="run-meta">
            {overview.company.ticker && <span className="ticker">{overview.company.ticker}</span>}
            <span className="num">{range}</span>
            <span>Reported in {overview.currency}</span>
            <span>Filings through <span className="num">{formatDate(overview.cutoff_date)}</span></span>
          </p>
        </div>
        <div className="report-head-side">
          <div className="report-status">
            <StatusPair label="Processing"><PhaseBadge phase={status.phase} /></StatusPair>
            <StatusPair label="Analyst review"><ReviewBadge review={status.review} /></StatusPair>
            {status.synthetic && <SyntheticBadge />}
          </div>
          <div className="report-actions">
            {reportUrl && (
              <a className="btn btn-sm" href={reportUrl} target="_blank" rel="noreferrer noopener">
                <Icon name="doc" size={14} /> Open HTML report<span className="sr-only"> (opens in a new tab)</span>
              </a>
            )}
            <a className="btn btn-sm" href="#downloads"><Icon name="download" size={14} /> Downloads</a>
          </div>
        </div>
      </header>

      {banner}
      <ReportNotes result={result} source={source} generatedAt={generatedAt} />
      <SectionNav />

      <Section id="summary" title="Financial summary" lead={`What happened at ${name} over ${range}, stated from validated figures.`}>
        <div className="summary-grid">
          <div className="panel summary-points">
            <div className="panel-head">
              <h3>What changed</h3>
              <Badge icon="calc">Calculated, not interpreted</Badge>
            </div>
            <div className="panel-body">
              {overview.headline.length > 0 ? (
                <>
                  <ul className="bullets">{points.map((h) => <li key={h} className="num">{h}</li>)}</ul>
                  {overview.headline.length > 4 && (
                    <button type="button" className="link-btn" aria-expanded={showAllPoints} onClick={() => setShowAllPoints((v) => !v)}>
                      {showAllPoints ? "Show fewer points" : `Show ${overview.headline.length - 4} more points`}
                    </button>
                  )}
                </>
              ) : (
                <p className="muted">No summary statements were produced for this analysis.</p>
              )}
              {overview.question && <p className="question"><span className="muted">Analysis question:</span> {overview.question}</p>}
            </div>
          </div>
          <div className="panel summary-evidence">
            <div className="panel-head"><h3>Explanations at a glance</h3></div>
            <div className="panel-body">
              {result.changes.length > 0 ? (
                <>
                  <p className="secondary">
                    {result.changes.length} change{result.changes.length === 1 ? " was" : "s were"} large enough to look into. This is how
                    far the company's filings explain them.
                  </p>
                  {tally}
                  <a href="#changes">See the biggest changes <Icon name="arrow" size={13} className="inline-icon" /></a>
                </>
              ) : (
                <p className="secondary">No year-over-year change was large enough to look into.</p>
              )}
            </div>
          </div>
        </div>
      </Section>

      <Section id="metrics" title="Key metrics" lead={`Latest fiscal year (${latest?.label}), the change from the year before, and all three years.`}>
        <div className="metrics metrics-primary">{primary.map((m) => <MetricTile key={m.name} metric={m} />)}</div>
        {secondary.length > 0 && (
          <>
            <h3 className="group-title">Cash and debt</h3>
            <div className="metrics metrics-secondary">{secondary.map((m) => <MetricTile key={m.name} metric={m} />)}</div>
          </>
        )}
      </Section>

      <Section id="charts" title="Three-year charts" lead="Every chart plots validated figures. Switch any chart to its table for exact values.">
        <ChartGroups charts={result.charts} />
      </Section>

      <Section id="changes" title="Biggest changes"
        lead="The year-over-year changes large enough to look into, each with what the company's filings say about it. The order is a work queue, not a ranking of importance."
        aside={result.changes.length > 0 ? tally : undefined}>
        {result.changes.length === 0 ? (
          <Notice title="No change was large enough to look into"><p>Year-over-year movements were all inside the configured thresholds, so nothing was sent to the investigator.</p></Notice>
        ) : (
          <ChangeList changes={result.changes} passages={result.passages} />
        )}
      </Section>

      <Section id="financials" title="Detailed financials" lead="The full three-year tables and the individual measures behind the summary.">
        <div className="tables">
          <div className="panel"><div className="panel-head"><h3>Three-year financial table</h3></div><div className="panel-body flush"><FinancialTable table={result.financials} /></div></div>
          <div className="panel"><div className="panel-head"><h3>Year-over-year changes</h3></div><div className="panel-body flush"><FinancialTable table={result.year_over_year} /></div></div>
        </div>
        <h3 className="group-title">Measures by area</h3>
        <p className="muted section-lead">
          Individual measures, stated as calculated observations. FinSight does not produce an overall financial-health
          score or a buy or sell view.
        </p>
        <Interpretation areas={result.interpretation} changes={result.changes} />
      </Section>

      <Section id="sources" title="Evidence and sources" lead="The filing text behind each explanation, how every number was calculated, and what is missing.">
        <Sources result={result} />
      </Section>

      <Section id="about" title="Review and report details" lead="Who has checked this report, and exactly which company and filings it covers.">
        <div className="about-grid">
          <ReviewPanel result={result} source={source} onStatus={onStatus} />
          <section className="panel" aria-labelledby="company-h">
            <div className="panel-head"><h3 id="company-h">Company and analysis</h3></div>
            <div className="panel-body">
              <dl className="facts wide-facts">
                <div><dt>Company</dt><dd>{name}</dd></div>
                <div><dt>Ticker</dt><dd>{overview.company.tickers.join(", ") || "none"}</dd></div>
                <div><dt>Industry</dt><dd>{overview.industry ? `${overview.industry} (SIC ${overview.sic})` : "not stated"}</dd></div>
                <div><dt>Fiscal year end</dt><dd className="num">{overview.fiscal_year_end ?? "not stated"}</dd></div>
                <div><dt>Reporting currency</dt><dd>{overview.currency}</dd></div>
                <div><dt>Data source</dt><dd>{overview.data_origin_label}</dd></div>
                <div><dt>Filings available by</dt><dd className="num">{formatDate(overview.cutoff_date)}</dd></div>
                <div><dt>Analysis finished</dt><dd className="num">{formatDateTime(overview.analysis_completed_at)} <span className="muted">({formatDuration(status.elapsed_seconds)})</span></dd></div>
                <div><dt>Explanations found by</dt><dd>{status.investigator?.name ?? "Not used"}</dd></div>
                <div><dt>SEC company number (CIK)</dt><dd className="mono">{overview.company.cik}</dd></div>
              </dl>
            </div>
          </section>
        </div>
        <div className="panel">
          <div className="panel-head"><h3>Fiscal years and the 10-K behind each one</h3></div>
          <div className="table-scroll" tabIndex={0} role="group" aria-label="Scrollable table">
            <table className="data">
              <caption className="sr-only">Fiscal years analysed and the 10-K that defines each one</caption>
              <thead><tr><th scope="col">Fiscal year</th><th scope="col">Period</th><th scope="col">10-K filed</th><th scope="col">Accession</th></tr></thead>
              <tbody>
                {overview.periods.map((p) => (
                  <tr key={p.label}>
                    <th scope="row">{p.label}</th>
                    <td className="num left">{p.start_date ? `${formatDate(p.start_date)} to ` : "ended "}{formatDate(p.end_date)}</td>
                    <td className="num left">{formatDate(p.filed)}</td>
                    <td className="mono nowrap">{p.filing_url ? <ExternalLink href={p.filing_url}>{p.accession}</ExternalLink> : p.accession}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </Section>

      <Section id="downloads" title="Downloads" lead="Files written by the analysis engine for this report. Full-precision values are in the CSV and JSON files.">
        <Downloads downloads={result.downloads} source={source} />
      </Section>

      <section className="dev-toggle">
        <button type="button" className="btn btn-sm btn-quiet" aria-expanded={showDev} onClick={() => setShowDev((v) => !v)}>
          <Icon name="code" size={14} /> {showDev ? "Hide" : "Show"} technical details
        </button>
        {showDev && <Diagnostics source={source} />}
      </section>
    </div>
  );
}
