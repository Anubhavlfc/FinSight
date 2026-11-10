import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { sample } from "../api/sample";
import type { RunList, RunSummary, SampleEntry } from "../api/types";
import { Badge, EmptyState, ErrorNotice, Loading, PhaseBadge, ReviewBadge, SyntheticBadge } from "../components/common";
import { Icon } from "../components/Icon";
import { displayCompany, formatDateTime, periodRange } from "../lib/format";
import { useDocumentTitle, useLoad } from "../lib/hooks";

type Filter = "all" | "awaiting" | "approved" | "running";

const FILTERS: [Filter, string][] = [["all", "All"], ["awaiting", "Awaiting review"], ["approved", "Approved"], ["running", "In progress"]];

function matches(run: RunSummary, f: Filter): boolean {
  if (f === "awaiting") return run.review.state === "awaiting_review" || run.review.state === "needs_attention";
  if (f === "approved") return run.review.state === "approved";
  if (f === "running") return run.phase === "queued" || run.phase === "running";
  return true;
}

function Row({ run, to, sampleNote }: { run: RunSummary; to: string; sampleNote?: string }) {
  const name = run.company ? displayCompany(run.company.name) : run.identifier;
  const done = run.has_results;
  return (
    <tr>
      <th scope="row" data-label="Company">
        <Link to={to} className="report-name">{name}</Link>
        <span className="report-sub">
          {run.company?.ticker && <span className="mono">{run.company.ticker}</span>}
          {sampleNote ? <Badge tone="amber" icon="layers" square>{sampleNote}</Badge> : run.synthetic && <SyntheticBadge />}
        </span>
      </th>
      <td data-label="Period" className="num">{periodRange(run.periods)}</td>
      <td data-label="Created" className="num">{formatDateTime(run.created_at)}</td>
      <td data-label="Run status"><PhaseBadge phase={run.phase} /></td>
      <td data-label="Review status"><ReviewBadge review={run.review} /></td>
      <td data-label="Downloads">
        {run.formats.length > 0 ? (
          <span className="formats">{run.formats.map((f) => <Badge key={f} square>{f}</Badge>)}</span>
        ) : (
          <span className="muted">None yet</span>
        )}
      </td>
      <td data-label="" className="report-action">
        <Link to={to} className="btn btn-sm">
          {done ? "Open report" : "View run"} <Icon name="arrow" size={13} />
        </Link>
      </td>
    </tr>
  );
}

function ReportTable({ caption, children }: { caption: string; children: React.ReactNode }) {
  return (
    <div className="table-scroll">
      <table className="data reports">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Company</th><th scope="col">Period</th><th scope="col">Created</th><th scope="col">Run status</th>
            <th scope="col">Review status</th><th scope="col">Downloads</th><th scope="col"><span className="sr-only">Open</span></th>
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export function ReportsPage() {
  useDocumentTitle("Reports");
  const [filter, setFilter] = useState<Filter>("all");
  const runs = useLoad<RunList>((signal) => api.runs(signal), "runs");
  const samples = useLoad<SampleEntry[]>((signal) => sample.index(signal), "samples");
  const { reload } = runs;
  const anyActive = runs.state === "ready" && runs.data.runs.some((r) => r.phase === "queued" || r.phase === "running");

  // Keep the list current while something is still running.
  useEffect(() => {
    if (!anyActive) return;
    const t = setTimeout(reload, 3000);
    return () => clearTimeout(t);
  }, [anyActive, reload, runs.data]);

  const visible = runs.state === "ready" ? runs.data.runs.filter((r) => matches(r, filter)) : [];

  return (
    <div className="reports-page">
      <header className="page-head">
        <div>
          <p className="eyebrow">Archive</p>
          <h1>Reports</h1>
          <p className="muted">Every analysis stored on this server. Open a finished report without running it again.</p>
        </div>
        <div className="seg" role="group" aria-label="Filter reports">
          {FILTERS.map(([key, label]) => (
            <button key={key} type="button" aria-pressed={filter === key} onClick={() => setFilter(key)}>{label}</button>
          ))}
        </div>
      </header>

      <section className="panel" aria-labelledby="live-reports">
        <div className="panel-head">
          <h2 id="live-reports">Analyses on this server</h2>
          {runs.state === "ready" && <span className="muted num">{visible.length} of {runs.data.runs.length} shown</span>}
        </div>
        {runs.state === "loading" && <div className="panel-body"><Loading label="Loading reports" /></div>}
        {runs.state === "error" && (
          <div className="panel-body">
            <ErrorNotice error={runs.error} title="The archive could not be loaded" onRetry={runs.reload} />
            <p className="muted pad-top">Stored reports are not shown while the service is unreachable. The sample analyses below are still available.</p>
          </div>
        )}
        {runs.state === "ready" && runs.data.runs.length === 0 && (
          <div className="panel-body">
            <EmptyState icon="archive" title="No analyses have been run on this server yet"
              actions={<Link className="btn btn-primary" to="/app">Analyze a company</Link>}>
              <p>Reports appear here as soon as an analysis starts, and stay until the server's storage is cleared.</p>
            </EmptyState>
          </div>
        )}
        {runs.state === "ready" && runs.data.runs.length > 0 && visible.length === 0 && (
          <div className="panel-body"><p className="muted">No reports match this filter.</p></div>
        )}
        {visible.length > 0 && (
          <ReportTable caption="Analyses stored on this server">
            {visible.map((r) => <Row key={r.id} run={r} to={`/app/runs/${r.id}`} />)}
          </ReportTable>
        )}
      </section>

      <section className="panel" aria-labelledby="sample-reports">
        <div className="panel-head">
          <h2 id="sample-reports">Sample analyses</h2>
          <span className="muted">Bundled with the app. They load without the analysis service.</span>
        </div>
        {samples.state === "loading" && <div className="panel-body"><Loading label="Loading samples" /></div>}
        {samples.state === "error" && <div className="panel-body"><ErrorNotice error={samples.error} onRetry={samples.reload} title="The samples could not be loaded" /></div>}
        {samples.state === "ready" && (
          <ReportTable caption="Bundled sample analyses">
            {samples.data.map((e) => (
              <Row key={e.id} run={e.summary} to={`/app/sample/${e.id}`} sampleNote={e.summary.synthetic ? "Synthetic sample" : "Cached sample"} />
            ))}
          </ReportTable>
        )}
      </section>
    </div>
  );
}
