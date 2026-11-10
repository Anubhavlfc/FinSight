import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { DEFAULT_SAMPLE } from "../api/sample";
import type { RunResult, RunStatus } from "../api/types";
import { ErrorNotice, Loading, Notice } from "../components/common";
import { Progress } from "../components/Progress";
import { ResultView } from "../components/ResultView";
import { displayCompany } from "../lib/format";
import { lastRun, toApiError, useDocumentTitle } from "../lib/hooks";

const POLL_MS = 1000;
const MAX_BACKOFF_MS = 8000;

const isLive = (s: RunStatus) => s.phase === "queued" || s.phase === "running";

/**
 * One run, identified by the URL. The page polls the stored run state, so a refresh,
 * a second tab or a shared link all reconnect to the same run.
 */
export function RunPage() {
  const { runId = "" } = useParams();
  const location = useLocation();
  const reused = Boolean((location.state as { reused?: boolean } | null)?.reused);

  const [status, setStatus] = useState<RunStatus | null>(null);
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [pollError, setPollError] = useState<ApiError | null>(null);
  const [result, setResult] = useState<RunResult | null>(null);
  const [resultError, setResultError] = useState<ApiError | null>(null);
  const [tick, setTick] = useState(0);
  const failures = useRef(0);
  const hasStatus = useRef(false);

  const refresh = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    setStatus(null);
    setResult(null);
    setLoadError(null);
    setPollError(null);
    setResultError(null);
    failures.current = 0;
    hasStatus.current = false;
  }, [runId]);

  // Poll the run until it stops.
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = () => {
      api
        .run(runId, controller.signal)
        .then((s) => {
          failures.current = 0;
          hasStatus.current = true;
          setPollError(null);
          setLoadError(null);
          setStatus(s);
          if (isLive(s)) timer = setTimeout(poll, POLL_MS);
          else if (lastRun.get() === s.id) lastRun.clear();
        })
        .catch((err: unknown) => {
          if (controller.signal.aborted) return;
          const error = toApiError(err);
          if (error.status === 404 || error.status === 422) {
            setLoadError(error);
            return;
          }
          failures.current += 1;
          if (hasStatus.current) setPollError(error);
          else setLoadError(error);
          timer = setTimeout(poll, Math.min(MAX_BACKOFF_MS, POLL_MS * 2 ** failures.current));
        });
    };
    poll();
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [runId, tick]);

  // Load the results once the run has them, and again after a review action.
  const hasResults = status?.has_results ?? false;
  const reviewState = status?.review.state;
  useEffect(() => {
    if (!hasResults) {
      setResult(null);
      return;
    }
    const controller = new AbortController();
    setResultError(null);
    api
      .result(runId, controller.signal)
      .then(setResult)
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setResultError(toApiError(err));
      });
    return () => controller.abort();
  }, [runId, hasResults, reviewState, tick]);

  const company = status?.company ? displayCompany(status.company.name) : "";
  useDocumentTitle(status ? `${company || status.identifier} · ${hasResults ? "report" : status.phase.replace("_", " ")}` : "Analysis run");

  if (loadError && !status) {
    const missing = loadError.status === 404;
    return (
      <div className="page-narrow">
        <ErrorNotice
          error={loadError}
          title={missing ? "This analysis was not found" : "This analysis could not be loaded"}
          onRetry={missing ? undefined : refresh}
          extra={
            <>
              <Link className="btn btn-sm" to="/app/reports">Open the report archive</Link>
              <Link className="btn btn-sm btn-quiet" to={`/app/sample/${DEFAULT_SAMPLE}`}>Open the sample</Link>
            </>
          }
        />
        {!missing && <p className="muted pad-top">This page keeps retrying. The run itself is stored on the server and is not affected.</p>}
      </div>
    );
  }
  if (!status) return <Loading label="Loading the analysis" />;

  const banner = reused ? (
    <Notice tone="teal" icon="info" title="Connected to an analysis that was already running">
      <p>An identical analysis had been started, so a second one was not created.</p>
    </Notice>
  ) : null;

  if (hasResults) {
    if (resultError) return <div className="page-narrow"><ErrorNotice error={resultError} title="The results could not be loaded" onRetry={refresh} /></div>;
    if (!result) return <Loading label="Loading the results" />;
    return <ResultView result={result} source={{ kind: "run", id: runId }} banner={banner} onStatus={(s) => { setStatus(s); refresh(); }} />;
  }
  return (
    <>
      {banner}
      <Progress status={status} connectionError={pollError} onRefresh={refresh} />
    </>
  );
}
