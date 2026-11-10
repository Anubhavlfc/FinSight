import { useEffect, useRef, useState, type RefObject } from "react";
import { ApiError } from "../api/client";

export function useDebounced<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(t);
  }, [value, delayMs]);
  return debounced;
}

export type Loadable<T> =
  | { state: "loading"; data: null; error: null }
  | { state: "ready"; data: T; error: null }
  | { state: "error"; data: null; error: ApiError };

export const toApiError = (err: unknown): ApiError =>
  err instanceof ApiError ? err : new ApiError(0, { message: err instanceof Error ? err.message : "Something went wrong." });

/** Load once per `key`; `reload()` repeats the request. */
export function useLoad<T>(load: (signal: AbortSignal) => Promise<T>, key: string): Loadable<T> & { reload: () => void } {
  const [result, setResult] = useState<Loadable<T>>({ state: "loading", data: null, error: null });
  const [attempt, setAttempt] = useState(0);
  const loader = useRef(load);
  loader.current = load;
  useEffect(() => {
    const controller = new AbortController();
    setResult({ state: "loading", data: null, error: null });
    loader.current(controller.signal).then(
      (data) => setResult({ state: "ready", data, error: null }),
      (err: unknown) => {
        if (!controller.signal.aborted) setResult({ state: "error", data: null, error: toApiError(err) });
      },
    );
    return () => controller.abort();
  }, [key, attempt]);
  return { ...result, reload: () => setAttempt((n) => n + 1) };
}

/** Width of an element, for charts that draw text at real pixel sizes. */
export function useWidth(ref: RefObject<HTMLElement | null>, fallback = 560): number {
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const apply = () => setWidth(Math.max(240, Math.floor(el.getBoundingClientRect().width)));
    apply();
    const observer = new ResizeObserver(apply);
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}

/** Ticks once a second while `active`, so elapsed time moves between polls. */
export function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [active]);
  return now;
}

export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = title ? `${title} · FinSight AI` : "FinSight AI";
  }, [title]);
}

const LAST_RUN_KEY = "finsight:last-run";

/** The run this browser started most recently, so the workspace can offer a way back to it. */
export const lastRun = {
  get(): string | null {
    try {
      return localStorage.getItem(LAST_RUN_KEY);
    } catch {
      return null;
    }
  },
  set(id: string): void {
    try {
      localStorage.setItem(LAST_RUN_KEY, id);
    } catch {
      /* storage unavailable: the URL still identifies the run */
    }
  },
  clear(): void {
    try {
      localStorage.removeItem(LAST_RUN_KEY);
    } catch {
      /* nothing to clear */
    }
  },
};
