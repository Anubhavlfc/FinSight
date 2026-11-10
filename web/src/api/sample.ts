import { asset } from "../config";
import { ApiError } from "./client";
import type { Diagnostics, RunResult, SampleEntry } from "./types";

/**
 * Bundled sample analyses. They are static files produced by the real pipeline
 * (scripts/build_web_sample.py), so they load without the analysis service.
 */
async function load<T>(path: string, signal?: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(asset(path), { signal });
  } catch (err) {
    if (signal?.aborted) throw err;
    throw new ApiError(0, { message: "The sample files could not be loaded." }, true);
  }
  if (!response.ok || !(response.headers.get("content-type") ?? "").includes("json")) {
    throw new ApiError(404, { code: "sample_not_found", message: "That sample analysis is not bundled with this build." });
  }
  return (await response.json()) as T;
}

export const sample = {
  index: (signal?: AbortSignal) => load<SampleEntry[]>("sample/index.json", signal),
  result: (id: string, signal?: AbortSignal) => load<RunResult>(`sample/${encodeURIComponent(id)}/result.json`, signal),
  diagnostics: (id: string, signal?: AbortSignal) =>
    load<Diagnostics>(`sample/${encodeURIComponent(id)}/diagnostics.json`, signal),
  fileUrl: (id: string, name: string) => asset(`sample/${encodeURIComponent(id)}/files/${name}`),
};

export const DEFAULT_SAMPLE = "demo";
