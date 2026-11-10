import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { DEFAULT_SAMPLE } from "../api/sample";
import type { ActivityItem, RunStatus, StageStatus } from "../api/types";
import { displayCompany, formatDuration, formatTime, periodRange } from "../lib/format";
import { toApiError, useNow } from "../lib/hooks";
import type { ApiError } from "../api/client";
import { Badge, ErrorNotice, Notice, PhaseBadge, SyntheticBadge } from "./common";
import { Diagnostics } from "./Diagnostics";
import { Icon, type IconName } from "./Icon";

const STAGE_ICON: Record<StageStatus["state"], IconName> = {
  done: "check", active: "dot", pending: "ring", failed: "x", skipped: "dash",
};
const STAGE_TEXT: Record<StageStatus["state"], string> = {
  done: "Done", active: "In progress", pending: "Waiting", failed: "Stopped here", skipped: "Skipped",
};

function stageDuration(stage: StageStatus): string | null {
  if (!stage.started_at || !stage.finished_at || stage.key === "ready") return null;
  const s = (new Date(stage.finished_at).getTime() - new Date(stage.started_at).getTime()) / 1000;
  return s < 0.05 ? "under 0.1 s" : formatDuration(s);
}

export function StageList({ stages, paused }: { stages: StageStatus[]; paused?: boolean }) {
  return (
    <ol className="stages" aria-label="Analysis stages">
      {stages.map((stage, i) => {
        const state = paused && stage.state === "active" ? "pending" : stage.state;
        return (
          <li key={stage.key} className={`stage is-${state}`} aria-current={stage.state === "active" ? "step" : undefined}>
            <span className="stage-marker" aria-hidden="true">
              <Icon name={STAGE_ICON[state]} size={13} />
            </span>
            <div className="stage-main">
              <p className="stage-title">
                <span className="num muted">{i + 1}</span> {stage.label}
                <span className="stage-state">
                  {paused && stage.state === "active" ? "Paused here" : STAGE_TEXT[state]}
                  {stageDuration(stage) && <span className="num"> · {stageDuration(stage)}</span>}
                </span>
              </p>
              {stage.detail && <p className="stage-detail">{stage.detail}</p>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

const ACTIVITY_ICON: Record<ActivityItem["kind"], IconName> = { info: "dot", success: "check", warning: "alert", error: "x" };
const ACTIVITY_WORD: Record<ActivityItem["kind"], string> = { info: "", success: "Done: ", warning: "Warning: ", error: "Error: " };

export function ActivityFeed({ items }: { items: ActivityItem[] }) {
  if (items.length === 0) return <p className="muted">Nothing has happened yet. The run is waiting for a worker.</p>;
  return (
    <ol className="activity" aria-label="Activity, newest first">
      {[...items].reverse().map((item, i) => (
        <li key={`${item.at}-${i}`} className={`kind-${item.kind}`}>
          <time className="num muted" dateTime={item.at}>
            {formatTime(item.at)}
          </time>
          <Icon name={ACTIVITY_ICON[item.kind]} size={12} />
          <p>
            <span className="sr-only">{ACTIVITY_WORD[item.kind]}</span>
            {item.message}
          </p>
        </li>
      ))}
    </ol>
  );
}

interface ProgressProps {
  status: RunStatus;
  /** Set when a poll failed; the last known status stays on screen. */
  connectionError: ApiError | null;
  onRefresh: () => void;
}

/** Everything shown before a run has results: live stages, activity, and recovery when it stops. */
export function Progress({ status, connectionError, onRefresh }: ProgressProps) {
  const live = status.phase === "queued" || status.phase === "running";
  const now = useNow(live);
  const [acting, setActing] = useState(false);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [showDev, setShowDev] = useState(false);
  const elapsed = live ? Math.max(status.elapsed_seconds, (now - new Date(status.created_at).getTime()) / 1000) : status.elapsed_seconds;

  const resume = (company?: string) => {
    if (acting) return;
    setActing(true);
    setActionError(null);
    api
      .resume(status.id, company)
      .then(onRefresh)
      .catch((err: unknown) => setActionError(toApiError(err)))
      .finally(() => setActing(false));
  };

  const company = status.company ? displayCompany(status.company.name) : status.identifier;
  return (
    <div className="progress">
      <header className="run-head">
        <div>
          <p className="eyebrow">Analysis run</p>
          <h1>{company}</h1>
          <p className="run-meta">
            {status.company?.ticker && <span className="mono">{status.company.ticker}</span>}
            <span>{status.periods.length ? periodRange(status.periods) : "Fiscal years not selected yet"}</span>
            <span className="mono">{status.id}</span>
          </p>
        </div>
        <div className="run-head-side">
          <PhaseBadge phase={status.phase} />
          {status.synthetic && <SyntheticBadge />}
        </div>
      </header>

      <section className="panel now" aria-label="Current status">
        <div className="now-main">
          {live && <span className="spinner" aria-hidden="true" />}
          <p role="status" aria-live="polite" className="now-text">
            {status.current_activity}
          </p>
        </div>
        <dl className="now-facts">
          <div>
            <dt>Elapsed</dt>
            <dd className="num">{formatDuration(elapsed)}</dd>
          </div>
          <div>
            <dt>Stages done</dt>
            <dd className="num">
              {status.stages.filter((s) => s.state === "done" && s.key !== "ready").length} of {status.stages.length - 1}
            </dd>
          </div>
          {status.investigation && (
            <div>
              <dt>Changes investigated</dt>
              <dd className="num">
                {status.investigation.investigated} of {Math.min(status.investigation.flagged, status.investigation.limit)}
              </dd>
            </div>
          )}
          {status.investigator && (
            <div>
              <dt>Investigator</dt>
              <dd>{status.investigator.name}</dd>
            </div>
          )}
        </dl>
      </section>

      {connectionError && (
        <Notice tone="amber" title="Connection to the analysis service was lost" role="status"
          actions={<button type="button" className="btn btn-sm" onClick={onRefresh}><Icon name="refresh" size={14} /> Check now</button>}>
          <p>Showing the last known status. The run continues on the server, and this page keeps retrying.</p>
        </Notice>
      )}

      {status.phase === "failed" && status.error && (
        <Notice tone="coral" title={`Stopped at ${status.error.stage ?? "an early stage"}`} role="alert"
          actions={
            <>
              {status.error.retryable && (
                <button type="button" className="btn btn-sm btn-primary" disabled={acting} onClick={() => resume()}>
                  <Icon name="refresh" size={14} /> {acting ? "Retrying…" : "Retry from this stage"}
                </button>
              )}
              <Link className="btn btn-sm" to="/app">Start a different analysis</Link>
              <Link className="btn btn-sm btn-quiet" to={`/app/sample/${DEFAULT_SAMPLE}`}>Open the sample</Link>
            </>
          }>
          <p>{status.error.message}</p>
          {status.error.next_steps.length > 0 && (
            <ul>{status.error.next_steps.map((s) => <li key={s}>{s}</li>)}</ul>
          )}
          <p className="muted">No figures were substituted. Completed stages are kept.</p>
        </Notice>
      )}

      {status.phase === "interrupted" && (
        <Notice tone="amber" title="This analysis was interrupted" role="alert"
          actions={<button type="button" className="btn btn-sm btn-primary" disabled={acting} onClick={() => resume()}>
            <Icon name="refresh" size={14} /> {acting ? "Resuming…" : "Resume"}</button>}>
          <p>{status.current_activity}</p>
        </Notice>
      )}

      {status.phase === "needs_input" && status.needs_input && (
        <section className="panel" aria-labelledby="needs-input-title">
          <div className="panel-head"><h2 id="needs-input-title">{status.needs_input.question}</h2></div>
          <ul className="candidates">
            {status.needs_input.candidates.map((c) => (
              <li key={c.cik}>
                <div>
                  <p>{displayCompany(c.name)}</p>
                  <p className="muted mono">{c.tickers.join(", ") || "no ticker"} · CIK {c.cik}</p>
                </div>
                <button type="button" className="btn btn-sm" disabled={acting} onClick={() => resume(c.cik)}>Choose</button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {actionError && <ErrorNotice error={actionError} onRetry={() => setActionError(null)} />}

      {status.warnings.length > 0 && (
        <Notice tone="amber" title={`${status.warnings.length} warning${status.warnings.length === 1 ? "" : "s"}`}>
          <ul>{status.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
        </Notice>
      )}

      <div className="progress-grid">
        <section className="panel" aria-labelledby="stages-title">
          <div className="panel-head">
            <h2 id="stages-title">Stages</h2>
            <span className="muted">Reported by the backend as each stage finishes</span>
          </div>
          <div className="panel-body"><StageList stages={status.stages} paused={!live && status.phase !== "needs_input"} /></div>
        </section>
        <section className="panel" aria-labelledby="activity-title">
          <div className="panel-head">
            <h2 id="activity-title">Activity</h2>
            <Badge>{status.activity.length} event{status.activity.length === 1 ? "" : "s"}</Badge>
          </div>
          <div className="panel-body"><ActivityFeed items={status.activity} /></div>
        </section>
      </div>

      <section className="dev-toggle">
        <button type="button" className="btn btn-sm btn-quiet" aria-expanded={showDev} onClick={() => setShowDev((v) => !v)}>
          <Icon name="code" size={14} /> {showDev ? "Hide" : "Show"} technical details
        </button>
        {showDev && <Diagnostics source={{ kind: "run", id: status.id }} refreshKey={status.updated_at} />}
      </section>
    </div>
  );
}
