import { useEffect, useRef, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import type { Change, Evidence, Passage } from "../api/types";
import { formatDate } from "../lib/format";
import { Badge, EvidenceBadge, ExternalLink, type Tone } from "./common";
import { Icon, type IconName } from "./Icon";

/** Filing text is rendered as plain text nodes only, never as HTML. */
export function PassageText({ passage, quotes = [], scrollToQuote }: { passage: Passage; quotes?: string[]; scrollToQuote?: boolean }) {
  const first = useRef<HTMLElement>(null);
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
  const firstMark = parts.findIndex((p) => p.mark);

  useEffect(() => {
    if (!scrollToQuote) return;
    // Wait a frame: the dialog that holds the passage opens after this renders.
    const frame = requestAnimationFrame(() => {
      const mark = first.current;
      const box = mark?.closest<HTMLElement>(".passage-text");
      if (mark && box) box.scrollTop += mark.getBoundingClientRect().top - box.getBoundingClientRect().top - box.clientHeight / 3;
    });
    return () => cancelAnimationFrame(frame);
  }, [scrollToQuote, passage.id]);

  return (
    <p className="passage-text" tabIndex={0} role="group" aria-label="Filing passage text">
      {parts.map((p, i) =>
        p.mark ? <mark key={i} ref={i === firstMark ? first : undefined}>{p.text}</mark> : <span key={i}>{p.text}</span>,
      )}
    </p>
  );
}

function PassageSource({ passage }: { passage: Passage }) {
  return (
    <dl className="passage-facts">
      <div><dt>Filing</dt><dd>{passage.form} for {passage.fiscal_period ?? "an unnamed period"}</dd></div>
      <div><dt>Filed</dt><dd className="num">{formatDate(passage.filing_date)}</dd></div>
      <div><dt>Section</dt><dd>{passage.section}</dd></div>
      <div>
        <dt>Source</dt>
        <dd>{passage.url ? <ExternalLink href={passage.url}>Open the filing on SEC.gov</ExternalLink> : "Synthetic sample text, no public link"}</dd>
      </div>
    </dl>
  );
}

function PassageLocator({ passage }: { passage: Passage }) {
  return (
    <details className="advanced">
      <summary><Icon name="chevron" size={12} /> Where this sits in the filing</summary>
      <p className="muted small">
        Accession <span className="mono">{passage.accession}</span>, characters {passage.char_start.toLocaleString("en-US")} to{" "}
        {passage.char_end.toLocaleString("en-US")} of the extracted text.
      </p>
    </details>
  );
}

/** A cited passage, listed under "Evidence and sources". */
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
      <footer>
        {passage.url ? <ExternalLink href={passage.url}>Open the filing on SEC.gov</ExternalLink> : <span className="muted">Synthetic sample text, no public link</span>}
        <PassageLocator passage={passage} />
      </footer>
    </article>
  );
}

/** The passage behind a quote, in context, in a dialog. */
function EvidenceDialog({ view, onClose }: { view: { passage: Passage; quote: string } | null; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (view && !el.open) el.showModal();
    if (!view && el.open) el.close();
  }, [view]);
  return (
    <dialog ref={ref} className="evidence-dialog" aria-labelledby="evidence-title" onClose={onClose}
      onClick={(e) => e.target === e.currentTarget && ref.current?.close()}>
      {view && (
        <div className="evidence-inner">
          <header className="evidence-head">
            <div>
              <p className="eyebrow">Evidence P{view.passage.number}</p>
              <h2 id="evidence-title">The passage in the filing</h2>
            </div>
            <button type="button" className="btn btn-sm" onClick={() => ref.current?.close()} autoFocus>
              <Icon name="x" size={14} /> Close
            </button>
          </header>
          <PassageSource passage={view.passage} />
          <p className="evidence-note">
            <Icon name="info" size={14} />
            <span>
              The highlighted sentence is the quotation. It was matched word for word against this filing text. That
              confirms what the company wrote, not that its explanation is correct.
            </span>
          </p>
          <PassageText passage={view.passage} quotes={[view.quote]} scrollToQuote />
          <PassageLocator passage={view.passage} />
        </div>
      )}
    </dialog>
  );
}

function Part({ icon, title, note, tone, children }: { icon: IconName; title: string; note?: string; tone?: string; children: ReactNode }) {
  return (
    <section className={`fpart${tone ? ` fpart-${tone}` : ""}`}>
      <h4 className="fpart-title"><Icon name={icon} size={15} /> {title}</h4>
      {note && <p className="fpart-note muted">{note}</p>}
      {children}
    </section>
  );
}

const DIRECTION: Record<Change["direction"], { icon: IconName; word: string }> = {
  increase: { icon: "up", word: "Increase" },
  decrease: { icon: "down", word: "Decrease" },
  transition: { icon: "refresh", word: "Sign change" },
};

const SUPPORT: Record<Evidence, string> = {
  supported:
    "Yes, in the filing's own words. The explanation is backed by a quotation that matches the filing text word for word. That confirms what the company wrote; it does not independently prove the cause.",
  partially_supported:
    "Partly. Some of the explanation is backed by a matching quotation. The rest was not found in the filing text that was searched.",
  unsupported:
    "No. The filing text that was searched contains no statement that explains this change. The change itself is calculated from reported figures; its cause is not established here.",
  not_investigated: "Not checked. This change was ranked below the per-analysis limit, so no explanation was looked for.",
};

/** The investigator's reading of a change. Shown with words and an icon; never as a recommendation. */
const READING: Record<string, { tone: Tone; icon: IconName; label: string }> = {
  favorable: { tone: "green", icon: "check", label: "Favorable" },
  unfavorable: { tone: "coral", icon: "alert", label: "Unfavorable" },
  ambiguous: { tone: "amber", icon: "half", label: "Depends on context" },
};

/** "Revenue rose 18.0% (FY2024→FY2025)." reads better as a title without the period it is listed under. */
export function changeTitle(change: Change): string {
  return change.description.replace(/\s*\(FY[^)]*\)\.?$/, "").replace(/\.$/, "");
}

function ChangeCard({ change, passages, defaultOpen, onEvidence }: {
  change: Change; passages: Map<string, Passage>; defaultOpen: boolean; onEvidence: (passage: Passage, quote: string) => void;
}) {
  const { hash } = useLocation();
  const anchor = `change-${change.rank}`;
  const [open, setOpen] = useState(defaultOpen || hash === `#${anchor}`);
  useEffect(() => {
    if (hash === `#${anchor}`) setOpen(true);
  }, [hash, anchor]);

  const f = change.finding;
  const dir = DIRECTION[change.direction];
  const panelId = `${anchor}-detail`;
  const reading = f ? READING[f.assessment] : undefined;
  const firstQuote = f?.quotes[0];
  // The rule-based investigator's explanation is the quotation itself; showing both would repeat it.
  const explanationIsQuote = Boolean(f?.management_explanation && f.quotes.length === 1 && firstQuote && f.management_explanation.includes(firstQuote.text));

  return (
    <li className={`change${open ? " open" : ""}`} id={anchor}>
      <button type="button" className="change-head" aria-expanded={open} aria-controls={panelId} onClick={() => setOpen((v) => !v)}>
        <span className="change-rank num" aria-label={`Rank ${change.rank}`}>{change.rank}</span>
        <span className="change-title">
          <span className="change-desc">{changeTitle(change)}</span>
          <span className="change-sub muted">{change.metric_label} · {change.comparison}</span>
        </span>
        <span className="change-magnitude num">
          <Icon name={dir.icon} size={13} />
          <span className="sr-only">{dir.word}: </span>
          {change.magnitude}
        </span>
        <EvidenceBadge evidence={change.evidence} label={change.evidence_label} />
        <Icon name="chevron" className="change-chevron" />
      </button>
      {open && (
        <div className="change-body" id={panelId}>
          <Part icon="calc" title="What the numbers show" note="Calculated from the company's reported figures.">
            <p>{f ? f.calculated_observation : change.description}</p>
          </Part>

          <Part icon="quote" title="What management says" note="The company's own account, quoted from its 10-K." tone="mgmt">
            {!f ? (
              <p className="muted">No explanation was looked for.</p>
            ) : (
              <>
                {f.management_explanation && !explanationIsQuote && <p>{f.management_explanation}</p>}
                {!f.management_explanation && f.quotes.length === 0 && (
                  <p className="muted">No explanation was found in the filing text that was searched.</p>
                )}
                {f.quotes.length > 0 && (
                  <ul className="quotes">
                    {f.quotes.map((q, i) => {
                      const p = passages.get(q.passage_id);
                      return (
                        <li key={`${q.passage_id}-${i}`}>
                          <blockquote>“{q.text}”</blockquote>
                          <p className="quote-meta">
                            <Badge tone={q.verified ? "teal" : "coral"} icon={q.verified ? "check" : "x"}>
                              {q.verified ? "Quote matches the filing word for word" : "Quote could not be matched to the filing"}
                            </Badge>
                            {p ? (
                              <button type="button" className="btn btn-sm" aria-haspopup="dialog" onClick={() => onEvidence(p, q.text)}>
                                <Icon name="doc" size={14} /> View evidence
                                <span className="sr-only">, passage P{p.number}</span>
                              </button>
                            ) : (
                              <span className="muted">Passage not included in the report</span>
                            )}
                          </p>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </>
            )}
          </Part>

          <Part icon="shield" title="Does the filing evidence support it?" tone={`ev-${change.evidence}`}>
            <p className="support-line">
              <EvidenceBadge evidence={change.evidence} label={change.evidence_label} />
              {reading && (
                <span className="reading">
                  <span className="muted">Investigator's reading of the change:</span>{" "}
                  <Badge tone={reading.tone} icon={reading.icon}>{reading.label}</Badge>
                </span>
              )}
            </p>
            <p className="secondary">{SUPPORT[change.evidence]}</p>
          </Part>

          {f?.analyst_interpretation && (
            <Part icon="spark" title="AI interpretation" note={`Written by ${f.produced_by}. Not a company statement.`} tone="ai">
              <p>{f.analyst_interpretation}</p>
            </Part>
          )}

          {f && f.unresolved_questions.length > 0 && (
            <Part icon="alert" title="Still unexplained" note="What the filings did not answer." tone="open">
              <ul className="bullets">{f.unresolved_questions.map((q) => <li key={q}>{q}</li>)}</ul>
            </Part>
          )}

          <details className="advanced">
            <summary><Icon name="chevron" size={12} /> Advanced details</summary>
            <dl className="kv small">
              <div><dt>Why it was flagged</dt><dd>{change.threshold}</dd></div>
              {f && <div><dt>Evidence status</dt><dd>Set by code: {f.status_reason}</dd></div>}
              {f && (
                <div>
                  <dt>Investigated by</dt>
                  <dd>{f.produced_by}, {f.steps_used} step{f.steps_used === 1 ? "" : "s"}{f.budget_exhausted ? " (step budget used up)" : ""}</dd>
                </div>
              )}
              <div><dt>Metric IDs</dt><dd className="mono">{change.metric_ids.join(", ")}</dd></div>
            </dl>
          </details>
        </div>
      )}
    </li>
  );
}

export function ChangeList({ changes, passages }: { changes: Change[]; passages: Passage[] }) {
  const byId = new Map(passages.map((p) => [p.id, p]));
  const [view, setView] = useState<{ passage: Passage; quote: string } | null>(null);
  return (
    <>
      <ol className="changes">
        {changes.map((c) => (
          <ChangeCard key={c.id} change={c} passages={byId} defaultOpen={c.rank === 1} onEvidence={(passage, quote) => setView({ passage, quote })} />
        ))}
      </ol>
      <EvidenceDialog view={view} onClose={() => setView(null)} />
    </>
  );
}
