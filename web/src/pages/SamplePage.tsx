import { Link, useParams } from "react-router-dom";
import { sample } from "../api/sample";
import type { RunResult, SampleEntry } from "../api/types";
import { ErrorNotice, ReportSkeleton } from "../components/common";
import { ResultView } from "../components/ResultView";
import { displayCompany } from "../lib/format";
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
  useDocumentTitle(res.state === "ready" ? `Sample: ${displayCompany(res.data[0].overview.company.name)}` : "Sample report");

  if (res.state === "loading") return <ReportSkeleton label="Loading the sample report" />;
  if (res.state === "error") {
    return (
      <div className="page-narrow">
        <ErrorNotice error={res.error} title="The sample could not be loaded" onRetry={res.reload}
          extra={<Link className="btn btn-sm" to="/app/reports">Open the report archive</Link>} />
      </div>
    );
  }
  const [result, entry] = res.data;
  return <ResultView result={result} source={{ kind: "sample", id: sampleId }} generatedAt={entry?.generated_at} />;
}
