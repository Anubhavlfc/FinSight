import { Link, useParams } from "react-router-dom";
import { sample } from "../api/sample";
import type { RunResult, SampleEntry } from "../api/types";
import { ErrorNotice, Loading, Notice } from "../components/common";
import { ResultView } from "../components/ResultView";
import { displayCompany, formatDateTime } from "../lib/format";
import { useDocumentTitle, useLoad } from "../lib/hooks";

/** A bundled analysis: static files produced by the real pipeline, readable without the analysis service. */
export function SamplePage() {
  const { sampleId = "" } = useParams();
  const res = useLoad<[RunResult, SampleEntry | undefined]>(
    async (signal) => {
      const [result, index] = await Promise.all([sample.result(sampleId, signal), sample.index(signal)]);
      return [result, index.find((e) => e.id === sampleId)];
    },
    sampleId,
  );
  useDocumentTitle(res.state === "ready" ? `Sample: ${displayCompany(res.data[0].overview.company.name)}` : "Sample analysis");

  if (res.state === "loading") return <Loading label="Loading the sample analysis" />;
  if (res.state === "error") {
    return (
      <div className="page-narrow">
        <ErrorNotice error={res.error} title="The sample could not be loaded" onRetry={res.reload}
          extra={<Link className="btn btn-sm" to="/app/reports">Open the report archive</Link>} />
      </div>
    );
  }
  const [result, entry] = res.data;
  const synthetic = result.status.synthetic;
  return (
    <ResultView
      result={result}
      source={{ kind: "sample", id: sampleId }}
      banner={
        <Notice tone="amber" icon="layers" title={synthetic ? "Sample analysis on synthetic data" : "Cached sample analysis"}>
          <p>
            {synthetic
              ? "The company, its figures and its filing text are made up, so the full workflow can be shown without credentials. They do not describe any real company."
              : "A stored copy of an earlier run on real SEC filings. It is not refreshed, so newer filings are not included."}{" "}
            Generated {formatDateTime(entry?.generated_at ?? result.overview.analysis_completed_at)} by the same pipeline that serves live runs.{" "}
            <Link to="/app">Run a live analysis</Link> to work with current filings.
          </p>
        </Notice>
      }
    />
  );
}
