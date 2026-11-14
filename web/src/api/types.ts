import type { components } from "./schema";

type S = components["schemas"];

export type Health = S["Health"];
export type CompanyHit = S["CompanyHit"];
export type SearchResponse = S["SearchResponse"];
export type CompanyProfile = S["CompanyProfile"];
export type FiscalYearOption = S["FiscalYearOption"];
export type RunCreate = S["RunCreate"];
export type RunCreated = S["RunCreated"];
export type RunStatus = S["RunStatus"];
export type RunSummary = S["RunSummary"];
export type RunList = S["RunList"];
export type RunResult = S["RunResult"];
export type StageStatus = S["StageStatus"];
export type ActivityItem = S["ActivityItem"];
export type ReviewStatus = S["ReviewStatus"];
export type ReviewRequest = S["ReviewRequest"];
export type KeyMetric = S["KeyMetric"];
export type Chart = S["Chart"];
export type Change = S["Change"];
export type Finding = S["Finding"];
export type Passage = S["Passage"];
export type DataTable = S["DataTable"];
export type InterpretationArea = S["InterpretationArea"];
export type DownloadInfo = S["DownloadInfo"];
export type Diagnostics = S["Diagnostics"];
export type ErrorBody = S["ErrorBody"];
export type Phase = RunStatus["phase"];
export type Evidence = Change["evidence"];

/** One bundled sample analysis, listed in public/sample/index.json. */
export interface SampleEntry {
  id: string;
  title: string;
  description: string;
  generated_at: string;
  summary: RunSummary;
}
