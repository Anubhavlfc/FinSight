import { useState } from "react";
import type { Change, Passage } from "../api/types";
import { formatDate } from "../lib/format";
import { Badge, EvidenceBadge, ExternalLink } from "./common";
import { Icon, type IconName } from "./Icon";

/** Filing text is rendered as plain text nodes only, never as HTML. */
export function PassageText({ passage, quotes = [] }: { passage: Passage; quotes?: string[] }) {
  const text = passage.text;
  const ranges = quotes
    .map((q) => ({ start: text.indexOf(q), length: q.length }))
    .filter((r) => r.start >= 0)
    .sort((a, b) => a.start - b.start);
  const parts: { text: string; mark: boolean }[] = [];
  let pos = 0;
  for (const r of ranges) {
    if (r.start < pos) continue;
    if (r.start > pos) parts.push({ text: text.slice(pos, r.start), mark: false });
    parts.push({ text: text.slice(r.start, r.start + r.length), mark: true });
    pos = r.start + r.length;
  }
  if (pos < text.length) parts.push({ text: text.slice(pos), mark: false });
  return (
    <p className="passage-text">
      {parts.map((p, i) => (p.mark ? <mark key={i}>{p.text}</mark> : <span key={i}>{p.text}</span>))}
    </p>
  );
}

export function PassageCard({ passage, quotes }: { passage: Passage; quotes?: string[] }) {
  return (
    <article className="passage" id={`passage-${passage.number}`}>
      <header>
        <Badge square>P{passage.number}</Badge>
        <p className="passage-meta">
          <strong>{passage.form}</strong> for {passage.fiscal_period ?? "an unnamed period"}, filed {formatDate(passage.filing_date)} ·{" "}
          {passage.section}
        </p>
      </header>
      <PassageText passage={passage} quotes={quotes} />
      <footer className="muted">
        <span className="mono">{passage.accession}</span> · characters {passage.char_start.toLocaleString("en-US")} to{" "}
        {passage.char_end.toLocaleString("en-US")} of the extracted text ·{" "}
        {passage.url ? <ExternalLink href={passage.url}>Open the filing on SEC.gov</ExternalLink> : "Synthetic fixture text, no public URL"}
      </footer>
    </article>
  );
}

function Block({ icon, label, tone, note, children }: {
  icon: IconName; label: string; tone: "calc" | "mgmt" | "ai" | "open"; note: string; children: React.ReactNode;
}) {
  return (
    <section className={`finding-block kind-${tone}`}>
      <p className="finding-label">
        <Icon name={icon} size={14} /> {label} <span className="muted">· {note}</span>
      </p>
      {children}
    </section>
  );
}

const DIRECTION: Record<Change["direction"], { icon: IconName; word: string }> = {
  increase: { icon: "up", word: "Increase" },
  decrease: { icon: "down", word: "Decrease" },
  transition: { icon: "refresh", word: "Sign change" },
};

function ChangeCard({ change, passages, defaultOpen }: { change: Change; passages: Map<string, Passage>; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const [shown, setShown] = useState<string | null>(null);
  const f = change.finding;
  const dir = DIRECTION[change.direction];
  const panelId = `change-${change.rank}-detail`;
  return (
    <li className={`change${open ? " open" : ""}`} id={`change-${change.rank}`}>
      <button type="button" className="change-head" aria-expanded={open} aria-controls={panelId} onClick={() => setOpen((v) => !v)}>
        <span className="change-rank num" aria-label={`Rank ${change.rank}`}>{String(change.rank).padStart(2, "0")}</span>
        <span className="change-title">
          <span className="change-desc">{change.description}</span>
          <span className="change-sub muted">
            <Icon name={dir.icon} size={12} /> {dir.word} · {change.metric_label} · {change.comparison}
          </span>
        </span>
        <span className="change-magnitude num">{change.magnitude}</span>
        <EvidenceBadge evidence={change.evidence} label={change.evidence_label} />
        <Icon name="chevron" className="change-chevron" />
      </button>
      {open && (
        <div className="change-body" id={panelId}>
          <Block icon="calc" label="Calculated observation" tone="calc" note="written by code from validated metrics">
            <p>{f ? f.calculated_observation : change.description}</p>
            <p className="muted small">{change.threshold}. Metric IDs: <span className="mono">{change.metric_ids.join(", ")}</span></p>
          </Block>

          {f ? (
            <>
              <Block icon="quote" label="Management explanation" tone="mgmt" note="the company's own account, quoted from its 10-K">
                {f.management_explanation ? <p>{f.management_explanation}</p> : (
                  <p className="muted">No explanation with a verified quote was found in the retrieved 10-K text.</p>
                )}
                {f.quotes.length > 0 && (
                  <ul className="quotes">
                    {f.quotes.map((q, i) => {
                      const p = passages.get(q.passage_id);
                      const key = `${q.passage_id}-${i}`;
                      return (
                        <li key={key}>
                          <blockquote>“{q.text}”</blockquote>
                          <p className="quote-meta">
                            <Badge tone={q.verified ? "teal" : "coral"} icon={q.verified ? "check" : "x"}>
                              {q.verified ? "Verified verbatim in the filing" : "Not verified"}
                            </Badge>
                            {p ? (
                              <button type="button" className="link-btn" aria-expanded={shown === key} onClick={() => setShown(shown === key ? null : key)}>
                                {shown === key ? "Hide" : "Show"} source passage P{p.number}
                              </button>
                            ) : (
                              <span className="muted">Passage not included in the report</span>
                            )}
                          </p>
                          {p && shown === key && <PassageCard passage={p} quotes={[q.text]} />}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </Block>

              {f.analyst_interpretation && (
                <Block icon="spark" label="AI interpretation" tone="ai" note={`written by ${f.produced_by}; not a company statement`}>
                  <p>{f.analyst_interpretation}</p>
                </Block>
              )}

              {f.unresolved_questions.length > 0 && (
                <Block icon="alert" label="Unresolved questions" tone="open" note="what the filings did not explain">
                  <ul className="bullets">{f.unresolved_questions.map((q) => <li key={q}>{q}</li>)}</ul>
                </Block>
              )}

              <p className="finding-foot muted small">
                Evidence status is set by code: {f.status_reason}. Investigated by {f.produced_by} in {f.steps_used} step
                {f.steps_used === 1 ? "" : "s"}{f.budget_exhausted ? " (step budget used up)" : ""}. Investigator's reading of the change:{" "}
                {f.assessment}.
              </p>
            </>
          ) : (
            <Block icon="alert" label="Not investigated" tone="open" note="outside the investigation limit for this run">
              <p className="muted">This change met the investigation settings but was ranked below the per-run limit, so no explanation was looked for.</p>
            </Block>
          )}
        </div>
      )}
    </li>
  );
}

export function ChangeList({ changes, passages }: { changes: Change[]; passages: Passage[] }) {
  const byId = new Map(passages.map((p) => [p.id, p]));
  return (
    <ol className="changes">
      {changes.map((c) => (
        <ChangeCard key={c.id} change={c} passages={byId} defaultOpen={c.rank === 1} />
      ))}
    </ol>
  );
}
