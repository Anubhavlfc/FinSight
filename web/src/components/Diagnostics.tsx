import { api } from "../api/client";
import { sample } from "../api/sample";
import type { Diagnostics as Diag } from "../api/types";
import { formatDateTime } from "../lib/format";
import { useLoad } from "../lib/hooks";
import { ErrorNotice, Loading } from "./common";

export type ResultSource = { kind: "run"; id: string } | { kind: "sample"; id: string };

function scalar(v: unknown): string {
  if (v === null || v === undefined) return "none";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/** Developer-facing view: raw run statistics, checkpoints, tool traces and source hashes. */
export function Diagnostics({ source, refreshKey = "" }: { source: ResultSource; refreshKey?: string }) {
  const res = useLoad<Diag>(
    (signal) => (source.kind === "run" ? api.diagnostics(source.id, signal) : sample.diagnostics(source.id, signal)),
    `${source.kind}:${source.id}:${refreshKey}`,
  );
  if (res.state === "loading") return <Loading label="Loading technical details" />;
  if (res.state === "error") return <ErrorNotice error={res.error} onRetry={res.reload} title="Technical details could not be loaded" />;
  const d = res.data;
  const usage = (d.stats["llm_usage"] ?? null) as Record<string, unknown> | null;
  return (
    <div className="diagnostics">
      <div className="diag-grid">
        <section className="panel">
          <div className="panel-head"><h3>Run statistics</h3><span className="mono muted">{d.run_id}</span></div>
          <div className="panel-body">
            <dl className="kv">
              {Object.entries(d.stats)
                .filter(([k]) => k !== "llm_usage")
                .map(([k, v]) => (
                  <div key={k}><dt className="mono">{k}</dt><dd className="mono">{scalar(v)}</dd></div>
                ))}
            </dl>
          </div>
        </section>
        <section className="panel">
          <div className="panel-head"><h3>Request and model usage</h3></div>
          <div className="panel-body">
            <dl className="kv">
              {Object.entries(d.request).map(([k, v]) => (
                <div key={k}><dt className="mono">{k}</dt><dd className="mono">{scalar(v)}</dd></div>
              ))}
              {usage &&
                Object.entries(usage)
                  .filter(([k]) => k !== "calls_by_purpose")
                  .map(([k, v]) => (
                    <div key={`u-${k}`}><dt className="mono">llm.{k}</dt><dd className="mono">{scalar(v)}</dd></div>
                  ))}
              <div><dt className="mono">checkpoints</dt><dd className="mono">{d.checkpoints.join(", ") || "none"}</dd></div>
            </dl>
          </div>
        </section>
      </div>

      <section className="panel">
        <div className="panel-head"><h3>State transitions</h3><span className="muted">{d.transitions.length} recorded</span></div>
        <div className="table-scroll">
          <table className="data">
            <thead><tr><th scope="col">Time (UTC)</th><th scope="col">From</th><th scope="col">To</th><th scope="col">Reason</th></tr></thead>
            <tbody>
              {d.transitions.map((t, i) => (
                <tr key={i}>
                  <td className="num mono">{formatDateTime(String(t["at"]))}</td>
                  <td className="mono">{String(t["from_state"])}</td>
                  <td className="mono">{String(t["to_state"])}</td>
                  <td>{String(t["reason"])}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {d.tool_traces.length > 0 && (
        <section className="panel">
          <div className="panel-head"><h3>Investigation tool traces</h3><span className="muted">Each finding's bounded tool loop</span></div>
          <div className="panel-body traces">
            {d.tool_traces.map((t) => {
              const calls = (t["tool_calls"] ?? []) as Record<string, unknown>[];
              return (
                <details key={String(t["finding"])}>
                  <summary>
                    <span className="mono">{String(t["finding"]).replace("finding:change:", "")}</span>
                    <span className="muted"> · {String(t["status"])} · {String(t["steps_used"])} step(s) · {calls.length} tool call(s) · {String(t["produced_by"])}</span>
                  </summary>
                  <ol>
                    {calls.map((c, i) => (
                      <li key={i} className="mono">
                        <span className="muted">step {String(c["step"])}</span> {String(c["tool"])} {c["ok"] ? "ok" : "ERROR"}: {String(c["summary"])}
                      </li>
                    ))}
                  </ol>
                  <p className="muted">Status rule: {String(t["status_reason"])}</p>
                </details>
              );
            })}
          </div>
        </section>
      )}

      {d.review_cycles.length > 0 && (
        <section className="panel">
          <div className="panel-head"><h3>Review cycles</h3></div>
          <div className="panel-body traces">
            {d.review_cycles.map((r, i) => {
              const defects = (r["defects"] ?? []) as Record<string, unknown>[];
              const checks = (r["checks_run"] ?? []) as string[];
              return (
                <details key={i} open={i === d.review_cycles.length - 1}>
                  <summary>
                    Cycle {String(r["revision_cycle"])}: {r["passed"] ? "passed" : "not passed"}
                    <span className="muted"> · {checks.length} checks · {defects.length} defect(s)</span>
                  </summary>
                  <p className="mono">{checks.join(", ")}</p>
                  {defects.length > 0 && (
                    <ul>{defects.map((x, j) => <li key={j} className="mono">[{String(x["severity"])}/{String(x["source"])}] {String(x["code"])}: {String(x["message"])}</li>)}</ul>
                  )}
                </details>
              );
            })}
          </div>
        </section>
      )}

      <section className="panel">
        <div className="panel-head"><h3>Source documents</h3><span className="muted">SHA-256 of the bytes each figure was read from</span></div>
        <div className="table-scroll">
          <table className="data">
            <thead><tr><th scope="col">Kind</th><th scope="col">URL</th><th scope="col">Retrieved (UTC)</th><th scope="col">SHA-256</th></tr></thead>
            <tbody>
              {d.sources.map((s) => (
                <tr key={String(s["id"])}>
                  <td className="mono">{String(s["kind"])}{s["from_cache"] ? " (cache)" : ""}</td>
                  <td className="mono wrap-any">{String(s["url"])}</td>
                  <td className="num mono">{formatDateTime(String(s["retrieved_at"]))}</td>
                  <td className="mono">{String(s["sha256"]).slice(0, 16)}…</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
