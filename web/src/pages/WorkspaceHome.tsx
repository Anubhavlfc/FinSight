import { Link, useOutletContext } from "react-router-dom";
import { api } from "../api/client";
import { DEFAULT_SAMPLE } from "../api/sample";
import type { RunList, RunStatus } from "../api/types";
import { CompanyPanel } from "../components/CompanyPanel";
import { Notice, PhaseBadge, ReviewBadge } from "../components/common";
import { Icon } from "../components/Icon";
import { displayCompany, formatDateTime, periodRange } from "../lib/format";
import { lastRun, useDocumentTitle, useLoad } from "../lib/hooks";
import { useService } from "../lib/service";
import type { WorkspaceContext } from "./WorkspaceLayout";

const STAGES: [string, string][] = [
  ["Planning", "Resolves the company at the SEC, checks it is in scope and picks the fiscal years from its 10-K filing history."],
  ["Collecting", "Downloads the company's XBRL facts from SEC EDGAR, with pacing, bounded retries and a local cache."],
  ["Validating", "Keeps annual figures only, applies restatements, chooses tags, and runs accounting checks. Problems are disclosed, never patched."],
  ["Calculating", "Computes margins, cash flow, debt and year-over-year changes in Python, and ranks the changes worth investigating."],
  ["Investigating", "Searches the 10-K text for the company's own explanation of each flagged change. Quotes are verified verbatim by code."],
  ["Writing", "Drafts the report from validated metrics and findings only. Every claim cites a metric or a passage."],
  ["Reviewing", "Re-checks every number, citation and quote, and sends defects back for at most two revision cycles."],
  ["Ready for review", "Stops and waits. A person approves the report; the system never approves its own work."],
];

export function WorkspaceHome() {
  useDocumentTitle("Workspace");
  const { desktop } = useOutletContext<WorkspaceContext>();
  const { service } = useService();
  const online = service.kind === "online";
  const recent = useLoad<RunList | null>((signal) => (online ? api.runs(signal) : Promise.resolve(null)), `recent:${online}`);
  const lastId = lastRun.get();
  const last = useLoad<RunStatus | null>(
    (signal) => (online && lastId ? api.run(lastId, signal).catch(() => null) : Promise.resolve(null)),
    `last:${lastId}:${online}`,
  );
  const active = last.state === "ready" && last.data && (last.data.phase === "queued" || last.data.phase === "running") ? last.data : null;
  const runs = recent.state === "ready" && recent.data ? recent.data.runs.slice(0, 5) : [];

  return (
    <div className="home">
      <header className="page-head">
        <div>
          <p className="eyebrow">Workspace</p>
          <h1>Analyze a company's last three fiscal years</h1>
          <p className="muted">
            {desktop ? "Choose a company in the panel on the left." : "Choose a company below."} The analysis runs in the
            background and this page shows each stage as the backend finishes it.
          </p>
        </div>
      </header>

      {active && (
        <Notice tone="teal" icon="clock" title="An analysis you started is still running"
          actions={<Link className="btn btn-sm btn-primary" to={`/app/runs/${active.id}`}>View progress <Icon name="arrow" size={13} /></Link>}>
          <p>{active.company ? displayCompany(active.company.name) : active.identifier}: {active.current_activity}</p>
        </Notice>
      )}

      {!desktop && <div className="panel inline-panel"><CompanyPanel /></div>}

      <div className="home-grid">
        <section className="panel" aria-labelledby="stages-h">
          <div className="panel-head"><h2 id="stages-h">What a run does</h2><span className="muted">Eight stages, in order</span></div>
          <ol className="explain-stages">
            {STAGES.map(([title, text], i) => (
              <li key={title}>
                <span className="num step-n">{i + 1}</span>
                <div><p className="step-title">{title}</p><p className="muted">{text}</p></div>
              </li>
            ))}
          </ol>
        </section>

        <div className="home-side">
          <section className="panel" aria-labelledby="recent-h">
            <div className="panel-head"><h2 id="recent-h">Recent reports</h2><Link to="/app/reports">All reports</Link></div>
            {runs.length > 0 ? (
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
            ) : (
              <div className="panel-body">
                <p className="muted">
                  {recent.state === "loading" || service.kind === "checking"
                    ? "Loading…"
                    : online
                      ? "No analyses yet. Reports appear here once a run starts."
                      : "Stored reports are unavailable while the analysis service is offline."}
                </p>
              </div>
            )}
          </section>

          <section className="panel sample-card" aria-labelledby="sample-h">
            <div className="panel-head"><h2 id="sample-h">Sample analysis</h2></div>
            <div className="panel-body">
              <p className="secondary">
                A finished report on a synthetic company: restated figures, a loss-to-profit swing, negative free cash
                flow, and one change the filings leave unexplained.
              </p>
              <Link className="btn" to={`/app/sample/${DEFAULT_SAMPLE}`}>Open the sample <Icon name="arrow" size={13} /></Link>
            </div>
          </section>

          <section className="panel" aria-labelledby="scope-h">
            <div className="panel-head"><h2 id="scope-h">Supported companies</h2></div>
            <div className="panel-body">
              <p className="secondary">{service.health?.supported_companies ?? "US domestic, nonfinancial operating companies that file Form 10-K in USD."}</p>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
