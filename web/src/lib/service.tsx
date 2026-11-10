import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, ApiError } from "../api/client";
import type { Health } from "../api/types";

export type ServiceState =
  | { kind: "checking"; health: null; error: null }
  | { kind: "online"; health: Health; error: null }
  | { kind: "offline"; health: null; error: ApiError };

interface ServiceContext {
  service: ServiceState;
  /** Re-check the analysis service now. */
  retry: () => void;
  checkedAt: Date | null;
}

const Ctx = createContext<ServiceContext | null>(null);
const RECHECK_MS = 60_000;

/** Tracks whether the analysis service is reachable. The landing page and sample never depend on it. */
export function ServiceProvider({ children }: { children: ReactNode }) {
  const [service, setService] = useState<ServiceState>({ kind: "checking", health: null, error: null });
  const [checkedAt, setCheckedAt] = useState<Date | null>(null);
  const inFlight = useRef<AbortController | null>(null);

  const check = useCallback((showChecking: boolean) => {
    inFlight.current?.abort();
    const controller = new AbortController();
    inFlight.current = controller;
    if (showChecking) setService({ kind: "checking", health: null, error: null });
    api
      .health(controller.signal)
      .then((health) => {
        setService({ kind: "online", health, error: null });
        setCheckedAt(new Date());
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        const error = err instanceof ApiError ? err : new ApiError(0, { message: "The analysis service could not be reached." }, true);
        setService({ kind: "offline", health: null, error });
        setCheckedAt(new Date());
      });
  }, []);

  useEffect(() => {
    check(true);
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") check(false);
    }, RECHECK_MS);
    return () => {
      clearInterval(timer);
      inFlight.current?.abort();
    };
  }, [check]);

  const value = useMemo(() => ({ service, retry: () => check(true), checkedAt }), [service, check, checkedAt]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useService(): ServiceContext {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useService must be used inside ServiceProvider");
  return ctx;
}
