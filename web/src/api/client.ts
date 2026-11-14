import { API_BASE } from "../config";
import type {
  CompanyProfile,
  Diagnostics,
  ErrorBody,
  Health,
  RunCreate,
  RunCreated,
  RunList,
  RunResult,
  ReviewRequest,
  RunStatus,
  SearchResponse,
} from "./types";

/** Every failed request becomes one of these, so screens can show a message, next steps and a retry. */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly nextSteps: string[];
  readonly retryAfter: number | null;
  readonly fields: Record<string, string>;
  /** True when the server could not be reached at all (offline, sleeping, blocked). */
  readonly network: boolean;

  constructor(status: number, body: Partial<ErrorBody> & { message: string }, network = false) {
    super(body.message);
    this.name = "ApiError";
    this.status = status;
    this.code = body.code ?? (network ? "network" : "http_error");
    this.nextSteps = body.next_steps ?? [];
    this.retryAfter = body.retry_after_seconds ?? null;
    this.fields = body.fields ?? {};
    this.network = network;
  }
}

const TIMEOUT_MS = 20_000;

async function request<T>(path: string, init: RequestInit = {}, signal?: AbortSignal): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(new DOMException("timeout", "TimeoutError")), TIMEOUT_MS);
  const onAbort = () => controller.abort(signal?.reason);
  signal?.addEventListener("abort", onAbort, { once: true });
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}), ...init.headers },
      signal: controller.signal,
    });
  } catch (err) {
    if (signal?.aborted) throw err;
    const timedOut = err instanceof DOMException && err.name === "TimeoutError";
    throw new ApiError(
      0,
      {
        code: timedOut ? "timeout" : "network",
        message: timedOut
          ? "The analysis service took too long to answer."
          : "The analysis service could not be reached.",
        next_steps: ["Check your connection and retry.", "The sample analysis works without the service."],
      },
      true,
    );
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onAbort);
  }
  const type = response.headers.get("content-type") ?? "";
  if (!type.includes("application/json")) {
    // A static host answers /api/* with an HTML page: the service is simply not there.
    // Reported as "service unavailable" whatever the host's own status was, so a static
    // host's 404 page is never mistaken for "this run does not exist".
    throw new ApiError(
      503,
      {
        code: "no_service",
        message: "No analysis service is connected to this site.",
        next_steps: ["The sample analysis works without the service."],
      },
      true,
    );
  }
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const b = (body ?? {}) as Partial<ErrorBody>;
    throw new ApiError(response.status, { ...b, message: b.message ?? `Request failed (${response.status}).` });
  }
  return body as T;
}

const post = <T>(path: string, data: unknown, headers?: Record<string, string>) =>
  request<T>(path, { method: "POST", body: JSON.stringify(data), headers });

export const api = {
  health: (signal?: AbortSignal) => request<Health>("/api/health", {}, signal),
  search: (q: string, signal?: AbortSignal) =>
    request<SearchResponse>(`/api/companies/search?q=${encodeURIComponent(q)}&limit=8`, {}, signal),
  company: (identifier: string, signal?: AbortSignal) =>
    request<CompanyProfile>(`/api/companies/${encodeURIComponent(identifier)}`, {}, signal),
  createRun: (body: RunCreate) => post<RunCreated>("/api/runs", body),
  runs: (signal?: AbortSignal) => request<RunList>("/api/runs?limit=50", {}, signal),
  run: (id: string, signal?: AbortSignal) => request<RunStatus>(`/api/runs/${encodeURIComponent(id)}`, {}, signal),
  result: (id: string, signal?: AbortSignal) =>
    request<RunResult>(`/api/runs/${encodeURIComponent(id)}/result`, {}, signal),
  diagnostics: (id: string, signal?: AbortSignal) =>
    request<Diagnostics>(`/api/runs/${encodeURIComponent(id)}/diagnostics`, {}, signal),
  resume: (id: string, company?: string) =>
    post<RunCreated>(`/api/runs/${encodeURIComponent(id)}/resume`, company ? { company } : {}),
  review: (id: string, body: ReviewRequest, token?: string) =>
    post<RunStatus>(`/api/runs/${encodeURIComponent(id)}/review`, body, token ? { "X-Reviewer-Token": token } : undefined),
  fileUrl: (id: string, name: string, inline = false) =>
    `${API_BASE}/api/runs/${encodeURIComponent(id)}/files/${name.split("/").map(encodeURIComponent).join("/")}${inline ? "?inline=true" : ""}`,
};
