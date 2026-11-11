import { Link } from "react-router-dom";
import { api } from "../api/client";
import { DEFAULT_SAMPLE } from "../api/sample";
import type { RunList, RunStatus } from "../api/types";
import { CompanyPanel } from "../components/CompanyPanel";
import { Notice, PhaseBadge, ReviewBadge, Skeleton, SyntheticBadge } from "../components/common";
import { Icon } from "../components/Icon";
import { displayCompany, formatDateTime, periodRange } from "../lib/format";
import { lastRun, useDocumentTitle, useLoad } from "../lib/hooks";
import { useService } from "../lib/service";

/** The pipeline's real stages. Kept out of the way until someone asks how it works. */
const STAGES: [string, string][] = [
  ["Planning", "Finds the company at the SEC, checks it is in scope and picks the fiscal years from its 10-K filing history."],
  ["Collecting", "Downloads the company's reported figures from SEC EDGAR."],
  ["Validating", "Keeps annual figures only, applies restatements and runs accounting checks. Problems are disclosed, never patched."],
  ["Calculating", "Computes margins, cash flow, debt and year-over-year changes, and ranks the changes worth investigating."],
  ["Investigating", "Searches the 10-K text for the company's own explanation of each flagged change. Quotes are checked word for word."],
  ["Writing", "Drafts the report from validated metrics and findings only. Every claim cites a metric or a passage."],
  ["Reviewing", "Re-checks every number, citation and quote, and sends defects back for at most two revision cycles."],
  ["Ready for review", "Stops and waits. A person approves the report; the system never approves its own work."],
];

export function WorkspaceHome() {
  useDocumentTitle("Analyze a company");
  const { service } = useService();
  const settled = service.kind !== "checking";
  const online = service.kind === "online";
  const recent = useLoad<RunList | null>((signal) => (settled ? api.runs(signal) : Promise.resolve(null)), `recent:${service.kind}`);
  const lastId = lastRun.get();
  const last = useLoad<RunStatus | null>(
    (signal) => (online && lastId ? api.run(lastId, signal).catch(() => null) : Promise.resolve(null)),
    `last:${lastId}:${online}`,
  );
  const active = last.state === "ready" && last.data && (last.data.phase === "queued" || last.data.phase === "running") ? last.data : null;
  const runs = recent.state === "ready" && recent.data ? recent.data.runs.slice(0, 5) : [];
  const recentLoading = !settled || recent.state === "loading";

  return (
    <div className="home">
      <header className="home-head">
        <h1>Analyze a company</h1>
        <p className="secondary">
          Pick a company to see three years of its financials, the biggest changes, and what its filings say about them.
        </p>
      </header>

      {active && (
        <Notice tone="teal" icon="clock" title="An analysis you started is still running"
          actions={<Link className="btn btn-sm btn-primary" to={`/app/runs/${active.id}`}>View progress <Icon name="arrow" size={13} /></Link>}>
          <p>{active.company ? displayCompany(active.company.name) : active.identifier}: {active.current_activity}</p>
        </Notice>
      )}

      <div className="start-card">
        <CompanyPanel />
      </div>

      <div className="home-below">
        <section className="panel sample-card" aria-labelledby="sample-h">
          <div className="panel-body">
            <div className="sample-card-top">
              <h2 id="sample-h">Sample report</h2>
              <SyntheticBadge />
            </div>
            <p className="secondary">
              A finished report on a made-up company: a loss that turns into a profit, free cash flow that goes
              negative, and one change the filings only partly explain.
            </p>
            <Link className="btn" to={`/app/sample/${DEFAULT_SAMPLE}`}>
              Explore sample report <Icon name="arrow" size={13} />
            </Link>
          </div>
        </section>

        <section className="panel" aria-labelledby="recent-h">
          <div className="panel-head">
            <h2 id="recent-h">Recent reports</h2>
            <Link to="/app/reports">All reports</Link>
          </div>
          {recentLoading ? (
            <div className="panel-body recent-loading" role="status">
              <span className="sr-only">Loading recent reports</span>
              <Skeleton height={18} />
              <Skeleton height={18} width="70%" />
            </div>
          ) : recent.state === "error" ? (
            <div className="panel-body state-row">
              <Icon name="alert" size={16} className="state-icon tone-amber" />
              <div>
                <p className="state-title">Recent reports could not be loaded</p>
                <p className="muted">
                  {recent.error.network
                    ? "Stored reports live on the analysis service, which is not reachable. The sample report does not need it."
                    : recent.error.message}
                </p>
                <button type="button" className="link-btn" onClick={recent.reload}>Try again</button>
              </div>
            </div>
          ) : runs.length === 0 ? (
            <div className="panel-body state-row">
              <Icon name="archive" size={16} className="state-icon" />
              <div>
                <p className="state-title">No reports yet</p>
                <p className="muted">Reports appear here as soon as an analysis starts.</p>
              </div>
            </div>
          ) : (
            <ul className="recent">
              {runs.map((r) => (
                <li key={r.id}>
                  <Link to={`/app/runs/${r.id}`}>
                    <span className="recent-name">{r.company ? displayCompany(r.company.name) : r.identifier}</span>
                    <span className="muted num">{periodRange(r.periods)} · {formatDateTime(r.created_at)}</span>
                  </Link>
                  <span className="recent-badges"><PhaseBadge phase={r.phase} />{r.has_results && <ReviewBadge review={r.review} />}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <details className="how">
        <summary><Icon name="chevron" size={14} /> How an analysis runs</summary>
        <div className="how-body">
          <p className="muted">
            Every analysis goes through the same eight stages, in order. The progress page reports each one as the
            service finishes it.
          </p>
          <ol className="how-stages">
            {STAGES.map(([title, text], i) => (
              <li key={title}>
                <span className="num how-n">{i + 1}</span>
                <div><p className="how-title">{title}</p><p className="muted">{text}</p></div>
              </li>
            ))}
          </ol>
        </div>
      </details>
    </div>
  );
}
