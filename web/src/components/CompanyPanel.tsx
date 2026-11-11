import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { DEFAULT_SAMPLE } from "../api/sample";
import type { CompanyHit, CompanyProfile } from "../api/types";
import { displayCompany, formatDate } from "../lib/format";
import { lastRun, toApiError, useDebounced } from "../lib/hooks";
import { useService } from "../lib/service";
import { ErrorNotice, Notice, SyntheticBadge } from "./common";
import { Icon } from "./Icon";

const LIVE_EXAMPLES = ["MSFT", "COST", "NVDA"];
const SAMPLE_EXAMPLE = "DEMO";
const SAMPLE_PATH = `/app/sample/${DEFAULT_SAMPLE}`;

export const ELIGIBILITY =
  "Works for US-listed operating companies that file an annual Form 10-K in US dollars. Banks, insurers, broker-dealers, REITs and foreign filers are not supported.";

type ProfileState =
  | { state: "idle" }
  | { state: "loading"; identifier: string }
  | { state: "ready"; profile: CompanyProfile }
  | { state: "error"; identifier: string; error: ApiError };

/** Why the service cannot be used, in the words a visitor needs. The cause is whatever the health check reported. */
export function offlineReason(error: ApiError): string {
  if (error.code === "no_service") {
    return "This site is running without an analysis service, so new analyses cannot start here.";
  }
  if (error.code === "timeout") return "The analysis service took too long to answer, so new analyses cannot start right now.";
  return "The analysis service did not answer, so new analyses cannot start right now.";
}

interface CompanyPanelProps {
  /** "page" is the workspace start screen; "drawer" is the compact New analysis sheet. */
  variant?: "page" | "drawer";
  onSubmitted?: () => void;
}

/** Company search, the confirmation of what will be analysed, and the one action that starts it. */
export function CompanyPanel({ variant = "page", onSubmitted }: CompanyPanelProps) {
  const { service, retry } = useService();
  const navigate = useNavigate();
  const ids = useId();
  const inputRef = useRef<HTMLInputElement>(null);

  const [query, setQuery] = useState("");
  const debounced = useDebounced(query.trim(), 220);
  const [hits, setHits] = useState<CompanyHit[]>([]);
  const [searchNote, setSearchNote] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<ApiError | null>(null);
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(0);

  const [selection, setSelection] = useState<ProfileState>({ state: "idle" });
  const [year, setYear] = useState<number | null>(null);
  const [question, setQuestion] = useState("");
  const [investigator, setInvestigator] = useState<"auto" | "rule_based">("auto");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<ApiError | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const online = service.kind === "online";
  const offline = service.kind === "offline";
  const health = service.health;
  const profile = selection.state === "ready" ? selection.profile : null;
  const maxQuestion = health?.limits.question_max_chars ?? 400;

  // ---- search -------------------------------------------------------------------------
  useEffect(() => {
    if (!online || debounced.length === 0 || (profile && debounced === inputLabel(profile))) {
      setHits([]);
      setSearching(false);
      return;
    }
    const controller = new AbortController();
    setSearching(true);
    setSearchError(null);
    api
      .search(debounced, controller.signal)
      .then((res) => {
        setHits(res.results);
        setSearchNote(res.note ?? null);
        setCursor(0);
        setSearching(false);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setHits([]);
        setSearching(false);
        setSearchError(toApiError(err));
      });
    return () => controller.abort();
  }, [debounced, online, profile]);

  const select = (identifier: string) => {
    setOpen(false);
    setFormError(null);
    setSubmitError(null);
    setSelection({ state: "loading", identifier });
    api
      .company(identifier)
      .then((p) => {
        setSelection({ state: "ready", profile: p });
        setYear(p.default_fiscal_year ?? null);
        setQuery(inputLabel(p));
      })
      .catch((err: unknown) => setSelection({ state: "error", identifier, error: toApiError(err) }));
  };

  const clear = () => {
    setSelection({ state: "idle" });
    setQuery("");
    setHits([]);
    setYear(null);
    setFormError(null);
    setSubmitError(null);
    inputRef.current?.focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" && hits.length > 0) {
      e.preventDefault();
      setOpen(true);
      setCursor((c) => (open ? (c + 1) % hits.length : 0));
    } else if (e.key === "ArrowUp" && hits.length > 0) {
      e.preventDefault();
      setOpen(true);
      setCursor((c) => (c - 1 + hits.length) % hits.length);
    } else if (e.key === "Escape") {
      setOpen(false);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (open && !profile && hits[cursor]) select(hits[cursor].cik);
      else start();
    }
  };

  // ---- submit -------------------------------------------------------------------------
  const submit = (e: FormEvent) => {
    e.preventDefault();
    start();
  };

  const start = () => {
    if (submitting) return;
    setSubmitError(null);
    if (!profile) {
      setFormError(
        query.trim()
          ? "Pick a company from the suggestions so the analysis uses the exact SEC registrant."
          : "Search for a company and pick one from the list first.",
      );
      inputRef.current?.focus();
      return;
    }
    if (!profile.supported || year === null) {
      setFormError("This company cannot be analysed. Choose another company.");
      return;
    }
    setFormError(null);
    setSubmitting(true);
    api
      .createRun({
        identifier: profile.cik,
        latest_fiscal_year: year,
        question: question.trim() || null,
        investigator,
      })
      .then((created) => {
        lastRun.set(created.run_id);
        onSubmitted?.();
        navigate(`/app/runs/${created.run_id}`, { state: { reused: created.reused } });
      })
      .catch((err: unknown) => {
        const error = toApiError(err);
        setSubmitError(error);
        if (error.network) retry();
      })
      .finally(() => setSubmitting(false));
  };

  const listId = `${ids}-list`;
  const showList = open && online && query.trim().length > 0 && !profile;
  const examples = online ? (health?.live_data.available ? [...LIVE_EXAMPLES, SAMPLE_EXAMPLE] : [SAMPLE_EXAMPLE]) : [];
  const selectedOption = profile?.fiscal_years.find((o) => o.fiscal_year === year) ?? null;
  // With nothing chosen and no service, the search cannot do anything: offer the sample instead of dead controls.
  const fallback = offline && selection.state === "idle";

  return (
    <form className={`start start-${variant}`} onSubmit={submit} noValidate aria-label="New analysis">
      {fallback ? (
        <div className="start-offline" role="status">
          <span className="start-offline-icon"><Icon name="alert" size={18} /></span>
          <div>
            <p className="start-offline-title">Live analysis is unavailable</p>
            <p className="secondary">
              {offlineReason(service.error)} The sample report is bundled with this site and works in full.
            </p>
            <div className="start-offline-actions">
              <Link className="btn btn-primary" to={SAMPLE_PATH}>
                Explore sample report <Icon name="arrow" size={14} />
              </Link>
              <button type="button" className="btn" onClick={retry}>
                <Icon name="refresh" size={14} /> Retry connection
              </button>
            </div>
          </div>
        </div>
      ) : (
        <div className="field search-field">
          <label htmlFor={`${ids}-q`}>Search by company name or ticker</label>
          <div className="combo">
            <Icon name="search" size={18} className="combo-icon" />
            <input
              ref={inputRef}
              id={`${ids}-q`}
              className="input"
              type="text"
              role="combobox"
              aria-autocomplete="list"
              aria-expanded={showList}
              aria-controls={listId}
              aria-activedescendant={showList && hits[cursor] ? `${ids}-opt-${cursor}` : undefined}
              aria-describedby={`${ids}-q-hint`}
              aria-invalid={formError ? true : undefined}
              placeholder="For example Microsoft or MSFT"
              autoComplete="off"
              spellCheck={false}
              maxLength={80}
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setOpen(true);
                setFormError(null);
                if (selection.state !== "idle") setSelection({ state: "idle" });
              }}
              onFocus={() => setOpen(true)}
              onBlur={() => setTimeout(() => setOpen(false), 120)}
              onKeyDown={onKeyDown}
            />
            {(searching || selection.state === "loading" || (service.kind === "checking" && query.trim().length > 0)) && (
              <span className="spinner combo-spinner" aria-hidden="true" />
            )}
            {profile && (
              <button type="button" className="combo-clear" onClick={clear} aria-label="Clear the selected company">
                <Icon name="x" size={16} />
              </button>
            )}
            {showList && (
              <ul className="combo-list" id={listId} role="listbox" aria-label="Matching companies">
                {hits.map((hit, i) => (
                  <li
                    key={hit.cik}
                    id={`${ids}-opt-${i}`}
                    role="option"
                    aria-selected={i === cursor}
                    className={i === cursor ? "on" : ""}
                    onMouseEnter={() => setCursor(i)}
                    onMouseDown={(e) => {
                      e.preventDefault();
                      select(hit.cik);
                    }}
                  >
                    <span className="combo-name">{displayCompany(hit.name)}</span>
                    <span className="combo-ticker">{hit.ticker ?? "No ticker"}</span>
                    <span className="combo-meta muted">
                      {hit.synthetic ? "Synthetic sample" : (hit.exchange ?? `CIK ${Number(hit.cik)}`)}
                    </span>
                  </li>
                ))}
                {searching && hits.length === 0 && (
                  <li className="combo-empty" role="presentation">Searching…</li>
                )}
                {hits.length === 0 && !searching && !searchError && debounced === query.trim() && (
                  <li className="combo-empty" role="presentation">
                    No company matches “{query.trim()}”. Try the ticker, or fewer letters of the name.
                  </li>
                )}
                {searchError && (
                  <li className="combo-empty" role="presentation">
                    {searchError.message}
                  </li>
                )}
                {searchNote && (
                  <li className="combo-note" role="presentation">
                    <Icon name="info" size={13} /> {searchNote}
                  </li>
                )}
              </ul>
            )}
          </div>
          <p className="sr-only" role="status">
            {showList && !searching ? `${hits.length} matching ${hits.length === 1 ? "company" : "companies"}` : ""}
          </p>
          <p className="hint" id={`${ids}-q-hint`}>{ELIGIBILITY}</p>
          {formError && (
            <p className="field-error" role="alert">
              <Icon name="alert" size={14} /> {formError}
            </p>
          )}
          {!profile && examples.length > 0 && (
            <div className="examples">
              <span className="label">Try</span>
              {examples.map((t) => (
                <button key={t} type="button" className="chip" disabled={submitting} onClick={() => select(t)}>
                  {t}
                  {t === SAMPLE_EXAMPLE && <span className="chip-note">synthetic</span>}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {selection.state === "error" && (
        <ErrorNotice
          error={selection.error}
          title="That company could not be loaded"
          onRetry={() => select(selection.identifier)}
          extra={<Link className="btn btn-sm" to={SAMPLE_PATH}>Explore sample report</Link>}
        />
      )}

      {profile && (
        <section className="selection rise" aria-label="Selected company">
          <div className="selection-head">
            <div>
              <p className="selection-name">{displayCompany(profile.name)}</p>
              <p className="selection-meta muted">
                {[profile.tickers.join(", ") || null, profile.exchange, profile.industry].filter(Boolean).join(" · ")}
              </p>
            </div>
            {profile.synthetic && <SyntheticBadge />}
          </div>

          {profile.supported && selectedOption ? (
            <dl className="selection-scope">
              <div>
                <dt>Fiscal years</dt>
                <dd className="num">{selectedOption.window[0]} to {selectedOption.label}</dd>
              </div>
              <div>
                <dt>Latest annual report</dt>
                <dd>
                  {selectedOption.label} ended {formatDate(selectedOption.period_end)}; its 10-K was filed{" "}
                  {formatDate(selectedOption.filed)}.
                </dd>
              </div>
            </dl>
          ) : null}

          {!profile.supported && (
            <Notice tone="amber" title="This company is outside the supported scope" role="status">
              <p>{profile.unsupported_reason}</p>
              <ul>
                {profile.next_steps.map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ul>
            </Notice>
          )}

          {profile.supported && (
            <details className="options">
              <summary>
                <Icon name="chevron" size={14} /> Analysis options
              </summary>
              <div className="options-body">
                <div className="field">
                  <label htmlFor={`${ids}-year`}>Fiscal years</label>
                  <select
                    id={`${ids}-year`}
                    className="select num"
                    value={year ?? ""}
                    onChange={(e) => setYear(Number(e.target.value))}
                  >
                    {profile.fiscal_years.map((o, i) => (
                      <option key={o.fiscal_year} value={o.fiscal_year}>
                        {o.window[0]} to {o.label}
                        {i === 0 ? " (latest)" : ""}
                        {o.complete ? "" : ` (${o.window.length} years on file)`}
                      </option>
                    ))}
                  </select>
                  {profile.notes.map((n) => (
                    <p key={n} className="hint">
                      <Icon name="info" size={12} className="inline-icon" /> {n}
                    </p>
                  ))}
                </div>

                <div className="field">
                  <label htmlFor={`${ids}-question`}>
                    Question to keep in mind <span className="optional">optional</span>
                  </label>
                  <textarea
                    id={`${ids}-question`}
                    className="textarea"
                    rows={2}
                    maxLength={maxQuestion}
                    placeholder="For example: why did margins change?"
                    value={question}
                    onChange={(e) => setQuestion(e.target.value)}
                    aria-describedby={`${ids}-question-hint`}
                  />
                  <p className="hint" id={`${ids}-question-hint`}>
                    Recorded in the report and given to the AI investigator as context. Reports on this server are
                    visible to everyone who uses it. <span className="num">{question.length}/{maxQuestion}</span>
                  </p>
                </div>

                {health?.investigator.claude_available && !profile.synthetic ? (
                  <div className="field">
                    <label htmlFor={`${ids}-inv`}>How explanations are found</label>
                    <select
                      id={`${ids}-inv`}
                      className="select"
                      value={investigator}
                      onChange={(e) => setInvestigator(e.target.value as "auto" | "rule_based")}
                    >
                      <option value="auto">Claude ({health.investigator.model})</option>
                      <option value="rule_based">Rule-based, no AI model</option>
                    </select>
                    <p className="hint">Quotes are checked against the filing text by code in both modes.</p>
                  </div>
                ) : (
                  <p className="hint">
                    Explanations are found by the rule-based investigator, without an AI model
                    {profile.synthetic ? " (the synthetic sample always runs this way)." : " (none is configured on this server)."}
                  </p>
                )}
              </div>
            </details>
          )}

          {submitError && (
            <ErrorNotice
              error={submitError}
              title={
                submitError.code === "rate_limited" || submitError.code === "busy"
                  ? "Not started yet"
                  : submitError.network
                    ? "The analysis did not start. Your selection is kept."
                    : "The analysis did not start"
              }
              onRetry={submitError.network || submitError.status >= 500 || submitError.status === 429 ? start : undefined}
              extra={submitError.network ? <Link className="btn btn-sm" to={SAMPLE_PATH}>Explore sample report</Link> : undefined}
            />
          )}

          <div className="selection-actions">
            <button type="submit" className="btn btn-primary btn-lg" disabled={submitting || !profile.supported}>
              {submitting ? (
                <>
                  <span className="spinner" aria-hidden="true" /> Starting…
                </>
              ) : (
                <>
                  Analyze company <Icon name="arrow" />
                </>
              )}
            </button>
            <button type="button" className="btn btn-quiet" onClick={clear}>
              Choose another company
            </button>
          </div>
          <p className="hint">
            The analysis runs in the background and this page follows it.
            {health ? ` Up to ${health.limits.runs_per_hour} new analyses per hour from one address.` : ""}
          </p>
        </section>
      )}
    </form>
  );
}

function inputLabel(p: CompanyProfile): string {
  const name = displayCompany(p.name);
  return p.tickers[0] ? `${p.tickers[0]} · ${name}` : name;
}
