import { useMemo, useState } from "react";
import type { RunResult } from "../api/types";
import { formatDateTime } from "../lib/format";
import { Badge, EmptyState, ExternalLink } from "./common";
import { PassageCard } from "./Findings";

type Tab = "passages" | "calculations" | "missing" | "documents";

const SEVERITY_TONE = { blocking: "coral", recoverable: "amber", warning: "amber", info: "neutral" } as const;

export function Sources({ result }: { result: RunResult }) {
  const [tab, setTab] = useState<Tab>("passages");
  const [filter, setFilter] = useState("");
  const [showInfo, setShowInfo] = useState(false);
  const quotesByPassage = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const c of result.changes) for (const q of c.finding?.quotes ?? []) map.set(q.passage_id, [...(map.get(q.passage_id) ?? []), q.text]);
    return map;
  }, [result.changes]);

  const needle = filter.trim().toLowerCase();
  const calcs = result.calculations.filter(
    (c) => !needle || `${c.label} ${c.name} ${c.period} ${c.formula} ${c.metric_id}`.toLowerCase().includes(needle),
  );
  const issues = result.issues.filter((i) => showInfo || i.severity !== "info");
  const infoCount = result.issues.filter((i) => i.severity === "info").length;

  const tabs: { key: Tab; label: string; count: number }[] = [
    { key: "passages", label: "Filing passages", count: result.passages.length },
    { key: "calculations", label: "Calculations", count: result.calculations.length },
    { key: "missing", label: "Missing and limitations", count: result.missing.length + result.limitations.length },
    { key: "documents", label: "Source documents", count: result.sources.length },
  ];

  return (
    <div className="sources">
      <div className="tabs" role="tablist" aria-label="Sources and limitations">
        {tabs.map((t) => (
          <button key={t.key} type="button" role="tab" id={`tab-${t.key}`} aria-selected={tab === t.key} aria-controls={`tabpanel-${t.key}`}
            tabIndex={tab === t.key ? 0 : -1} onClick={() => setTab(t.key)}
            onKeyDown={(e) => {
              if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
              const i = tabs.findIndex((x) => x.key === tab);
              const next = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length]!;
              setTab(next.key);
              document.getElementById(`tab-${next.key}`)?.focus();
            }}>
            {t.label} <span className="num muted">{t.count}</span>
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`tabpanel-${tab}`} aria-labelledby={`tab-${tab}`} className="tabpanel">
        {tab === "passages" &&
          (result.passages.length === 0 ? (
            <EmptyState icon="doc" title="No filing passages are cited">
              <p>No explanation in this report quotes the 10-K text, so there are no passages to inspect.</p>
            </EmptyState>
          ) : (
            <div className="passage-list">
              <p className="muted">Every passage the report cites. Quoted sentences are highlighted; the character range locates each passage in the filing's extracted text.</p>
              {result.passages.map((p) => <PassageCard key={p.id} passage={p} quotes={quotesByPassage.get(p.id)} />)}
            </div>
          ))}

        {tab === "calculations" && (
          <>
            <div className="calc-tools">
              <label className="sr-only" htmlFor="calc-filter">Filter calculations</label>
              <input id="calc-filter" className="input" type="search" placeholder="Filter by metric, period or formula" value={filter} onChange={(e) => setFilter(e.target.value)} />
              <p className="muted num" role="status">{calcs.length} of {result.calculations.length} metrics</p>
            </div>
            <div className="table-scroll tall" tabIndex={0} role="group" aria-label="Scrollable table">
              <table className="data">
                <caption>Every metric with its formula and inputs. Reported figures are passed through; calculated measures are marked.</caption>
                <thead><tr><th scope="col">Metric</th><th scope="col">Period</th><th scope="col" className="num">Value</th><th scope="col">Formula</th><th scope="col">Inputs and notes</th></tr></thead>
                <tbody>
                  {calcs.map((c) => (
                    <tr key={c.metric_id}>
                      <th scope="row">{c.label} {c.is_calculated && <Badge square>calc</Badge>}</th>
                      <td className="num">{c.period}</td>
                      <td className={`num${c.status === "available" ? "" : " cell-missing"}`}>{c.display}</td>
                      <td className="mono wrap-any">{c.formula}</td>
                      <td className="wrap-any">
                        <span className="mono muted">{c.inputs.join(", ") || "no inputs recorded"}</span>
                        {c.note && <><br />{c.note}</>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}

        {tab === "missing" && (
          <div className="missing">
            <section>
              <h3>Figures that are not available</h3>
              {result.missing.length === 0 ? (
                <p className="muted">Every figure shown in the dashboard was available for all fiscal years.</p>
              ) : (
                <div className="table-scroll" tabIndex={0} role="group" aria-label="Scrollable table">
                  <table className="data">
                    <thead><tr><th scope="col">Figure</th><th scope="col">Period</th><th scope="col">Status</th><th scope="col">Reason</th></tr></thead>
                    <tbody>
                      {result.missing.map((m) => (
                        <tr key={`${m.label}-${m.period}`}>
                          <th scope="row">{m.label}</th><td className="num">{m.period}</td>
                          <td><Badge tone="amber" icon="dash">{m.status.replace("_", " ")}</Badge></td><td>{m.reason}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <p className="muted small">Nothing is estimated. A missing input makes the metrics that need it unavailable.</p>
            </section>
            <section>
              <h3>Limitations stated in the report</h3>
              {result.limitations.length === 0 ? <p className="muted">None recorded.</p> : <ul className="bullets">{result.limitations.map((l) => <li key={l}>{l}</li>)}</ul>}
            </section>
            {result.unresolved_questions.length > 0 && (
              <section>
                <h3>Unresolved questions</h3>
                <ul className="bullets">{result.unresolved_questions.map((q) => <li key={q}>{q}</li>)}</ul>
              </section>
            )}
            <section>
              <div className="row-between">
                <h3>Data validation issues</h3>
                {infoCount > 0 && (
                  <button type="button" className="btn btn-sm btn-quiet" aria-pressed={showInfo} onClick={() => setShowInfo((v) => !v)}>
                    {showInfo ? "Hide" : "Show"} {infoCount} informational
                  </button>
                )}
              </div>
              {issues.length === 0 ? <p className="muted">No warnings or blocking issues.</p> : (
                <ul className="issues">
                  {issues.map((i, n) => (
                    <li key={n}>
                      <Badge tone={SEVERITY_TONE[i.severity]} square>{i.severity}</Badge>
                      <p>{i.message} <span className="mono muted">{i.code}</span></p>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        )}

        {tab === "documents" && (
          <div className="table-scroll" tabIndex={0} role="group" aria-label="Scrollable table">
            <table className="data">
              <caption>Documents the figures and passages were read from, with retrieval time and content hash.</caption>
              <thead><tr><th scope="col">Document</th><th scope="col">Filing</th><th scope="col">Retrieved</th><th scope="col">SHA-256</th></tr></thead>
              <tbody>
                {result.sources.map((d) => (
                  <tr key={d.sha256 + d.kind}>
                    <th scope="row">
                      {d.url ? <ExternalLink href={d.url}>{d.label}</ExternalLink> : d.label}
                      {d.is_fixture && <> <Badge tone="amber" square>fixture</Badge></>}
                    </th>
                    <td className="mono">{d.accession ? `${d.form ?? ""} ${d.accession}` : "not a filing"}</td>
                    <td className="num">{formatDateTime(d.retrieved_at)}{d.from_cache ? " (cached copy)" : ""}</td>
                    <td className="mono">{d.sha256.slice(0, 16)}…</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
