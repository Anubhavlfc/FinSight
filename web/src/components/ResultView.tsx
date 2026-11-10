import { useState, type ReactNode } from "react";
import { api, ApiError } from "../api/client";
import { sample } from "../api/sample";
import type { DataTable, DownloadInfo, InterpretationArea, KeyMetric, RunResult, RunStatus } from "../api/types";
import { displayCompany, formatBytes, formatDate, formatDateTime, formatDuration, periodRange } from "../lib/format";
import { toApiError } from "../lib/hooks";
import { useService } from "../lib/service";
import { ChartCard } from "./Charts";
import { Badge, ErrorNotice, ExternalLink, Notice, PhaseBadge, ReviewBadge, SyntheticBadge } from "./common";
import { Diagnostics, type ResultSource } from "./Diagnostics";
import { ChangeList } from "./Findings";
import { Icon, type IconName } from "./Icon";
import { Sources } from "./Sources";

const SECTIONS = [
  ["overview", "Overview"],
  ["metrics", "Key metrics"],
  ["charts", "Charts"],
  ["changes", "Biggest changes"],
  ["interpretation", "Interpretation"],
  ["sources", "Sources and limitations"],
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

// ---------------------------------------------------------------- key metrics

const DIRECTION_ICON: Record<"up" | "down" | "flat" | "none", IconName> = { up: "up", down: "down", flat: "flat", none: "dash" };
const DIRECTION_WORD = { up: "Up", down: "Down", flat: "Unchanged", none: "" } as const;

export function MetricTile({ metric }: { metric: KeyMetric }) {
  const available = metric.status === "available";
  const c = metric.comparison;
  return (
    <article className="metric" aria-label={`${metric.label}, ${metric.period}`}>
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
        <span className="muted">{metric.period}</span>
        {c && c.display && (
          <span className="delta num">
            <Icon name={DIRECTION_ICON[c.direction]} size={12} />
            <span className="sr-only">{DIRECTION_WORD[c.direction]} </span>
            {c.display} {c.label}
          </span>
        )}
        {c && !c.display && <span className="muted">no comparison {c.label}</span>}
      </p>
      <dl className="metric-series">
        {metric.series.map((p) => (
          <div key={p.period}>
            <dt>{p.period}</dt>
            <dd className={`num${p.value === null ? " cell-missing" : ""}`}>{shortDisplay(p.display)}</dd>
          </div>
        ))}
      </dl>
      {(metric.note || c?.note) && <p className="metric-note muted">{[metric.note, c?.note].filter(Boolean).join(" ")}</p>}
    </article>
  );
}

/** "$1,239 million" is long for a three-column strip; the unit words are abbreviated for display only. */
function shortDisplay(text: string): string {
  return text.replace(" million", "M").replace(" billion", "B");
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
            <h3><Icon name={AREA_ICON[a.key]} size={15} className="inline-icon" /> {a.title}</h3>
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

// ---------------------------------------------------------------- the dashboard

interface ResultViewProps {
  result: RunResult;
  source: ResultSource;
  /** Extra banner, e.g. the sample notice. */
  banner?: ReactNode;
  onStatus?: (s: RunStatus) => void;
}

export function ResultView({ result, source, banner, onStatus }: ResultViewProps) {
  const [showDev, setShowDev] = useState(false);
  const { status, overview } = result;
  const name = displayCompany(overview.company.name);
  const report = result.downloads.find((d) => d.name === "report.html");
  const reportUrl = report && (source.kind === "run" ? api.fileUrl(source.id, report.name, true) : sample.fileUrl(source.id, report.name));
  const counts = {
    supported: result.changes.filter((c) => c.evidence === "supported").length,
    partial: result.changes.filter((c) => c.evidence === "partially_supported").length,
    unsupported: result.changes.filter((c) => c.evidence === "unsupported").length,
    skipped: result.changes.filter((c) => c.evidence === "not_investigated").length,
  };

  return (
    <div className="result">
      <header className="run-head">
        <div>
          <p className="eyebrow">{source.kind === "sample" ? "Sample analysis" : "Analysis report"}</p>
          <h1>{name}</h1>
          <p className="run-meta">
            {overview.company.ticker && <span className="mono">{overview.company.ticker}</span>}
            <span>{periodRange(overview.periods.map((p) => p.label))}</span>
            <span>Reported in {overview.currency}</span>
            <span>Analysed {formatDateTime(overview.analysis_completed_at)}</span>
          </p>
        </div>
        <div className="run-head-side">
          <PhaseBadge phase={status.phase} />
          <ReviewBadge review={status.review} />
          {status.synthetic && <SyntheticBadge />}
        </div>
      </header>

      {banner}

      {status.synthetic && source.kind === "run" && (
        <Notice tone="amber" icon="layers" title="Synthetic fixture data">
          <p>This company, its figures and its filing text are made up to demonstrate the workflow offline. They do not describe any real company.</p>
        </Notice>
      )}
      {status.phase === "partial" && (
        <Notice tone="amber" title="Partial results">
          <p>Some required figures were missing or failed validation, so the metrics that depend on them are unavailable. Nothing was estimated. See “Missing and limitations” below.</p>
        </Notice>
      )}

      <nav className="section-nav" aria-label="Report sections">
        <ul>
          {SECTIONS.map(([id, label]) => (
            <li key={id}><a href={`#${id}`}>{label}</a></li>
          ))}
        </ul>
        {reportUrl && (
          <a className="btn btn-sm" href={reportUrl} target="_blank" rel="noreferrer noopener">
            <Icon name="doc" size={14} /> Open HTML report
          </a>
        )}
      </nav>

      <Section id="overview" title="Company overview">
        <div className="overview-grid">
          <div className="panel">
            <div className="panel-body">
              <dl className="facts wide-facts">
                <div><dt>Company</dt><dd>{name}</dd></div>
                <div><dt>Ticker</dt><dd className="mono">{overview.company.tickers.join(", ") || "none"}</dd></div>
                <div><dt>CIK</dt><dd className="mono">{overview.company.cik}</dd></div>
                <div><dt>Industry</dt><dd>{overview.industry ? `${overview.industry} (SIC ${overview.sic})` : "not stated"}</dd></div>
                <div><dt>Reporting currency</dt><dd>{overview.currency}</dd></div>
                <div><dt>Fiscal year end</dt><dd className="num">{overview.fiscal_year_end ?? "not stated"}</dd></div>
                <div><dt>Filings available by</dt><dd className="num">{formatDate(overview.cutoff_date)}</dd></div>
                <div><dt>Analysis finished</dt><dd className="num">{formatDateTime(overview.analysis_completed_at)} <span className="muted">({formatDuration(status.elapsed_seconds)})</span></dd></div>
                <div><dt>Data source</dt><dd>{overview.data_origin_label}</dd></div>
                <div><dt>Investigator</dt><dd>{status.investigator?.name ?? "Not used"}</dd></div>
              </dl>
              {overview.question && <p className="question"><span className="muted">Analysis question:</span> {overview.question}</p>}
            </div>
            <div className="table-scroll" tabIndex={0} role="group" aria-label="Scrollable table">
              <table className="data">
                <caption className="pad">Fiscal years analysed and the 10-K that defines each one</caption>
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
          <div className="overview-side">
            <ReviewPanel result={result} source={source} onStatus={onStatus} />
            {status.warnings.length > 0 && (
              <Notice tone="amber" title={`${status.warnings.length} thing${status.warnings.length === 1 ? "" : "s"} to know before relying on this report`}>
                <ul>{status.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
              </Notice>
            )}
          </div>
        </div>
        {overview.headline.length > 0 && (
          <section className="panel summary-panel">
            <div className="panel-head"><h3>Summary</h3><Badge icon="calc">Calculated</Badge></div>
            <div className="panel-body"><ul className="bullets two-col">{overview.headline.map((h) => <li key={h} className="num">{h}</li>)}</ul></div>
          </section>
        )}
      </Section>

      <Section id="metrics" title="Key metrics" lead={`Latest fiscal year (${overview.periods[overview.periods.length - 1]?.label}) with the change from the year before and all three years.`}>
        <div className="metrics">{result.key_metrics.map((m) => <MetricTile key={m.name} metric={m} />)}</div>
      </Section>

      <Section id="charts" title="Three-year charts" lead="Each chart plots validated metrics on one axis. Switch any chart to its table for exact values.">
        <div className="charts">{result.charts.map((c) => <ChartCard key={c.id} chart={c} />)}</div>
        <div className="tables">
          <div className="panel"><div className="panel-head"><h3>Three-year financial table</h3></div><div className="panel-body flush"><FinancialTable table={result.financials} /></div></div>
          <div className="panel"><div className="panel-head"><h3>Year-over-year changes</h3></div><div className="panel-body flush"><FinancialTable table={result.year_over_year} /></div></div>
        </div>
      </Section>

      <Section id="changes" title="Biggest changes"
        lead="Ranked by how far each change exceeds the investigation settings. The ranking is a work queue, not a judgement of importance."
        aside={
          <p className="evidence-tally">
            <Badge tone="teal" icon="check">{counts.supported} supported</Badge>
            <Badge tone="amber" icon="half">{counts.partial} partly</Badge>
            <Badge tone="coral" icon="ring">{counts.unsupported} unexplained</Badge>
            {counts.skipped > 0 && <Badge icon="dash">{counts.skipped} not investigated</Badge>}
          </p>
        }>
        {result.changes.length === 0 ? (
          <Notice title="No change exceeded the investigation settings"><p>Year-over-year movements were all inside the configured thresholds, so nothing was sent to the investigator.</p></Notice>
        ) : (
          <ChangeList changes={result.changes} passages={result.passages} />
        )}
      </Section>

      <Section id="interpretation" title="Financial interpretation"
        lead="Individual measures by area, stated as calculated observations. FinSight does not produce an overall financial-health score, because the engine has no documented method for one.">
        <Interpretation areas={result.interpretation} changes={result.changes} />
      </Section>

      <Section id="sources" title="Sources and limitations" lead="Inspect the filing text behind each explanation, how every number was calculated, and what is missing.">
        <Sources result={result} />
      </Section>

      <Section id="downloads" title="Downloads" lead="Files written by the analysis engine for this run. Full-precision values are in the CSV and JSON files.">
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
