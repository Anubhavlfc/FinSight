/** Build-time configuration. Nothing secret belongs here: this file ships to the browser. */
const env = import.meta.env;

/** Origin of the API. Empty means "same origin as this page" (FastAPI serving the built app, or the dev proxy). */
export const API_BASE: string = (env.VITE_API_BASE_URL ?? "").replace(/\/+$/, "");

/** Path prefix the app is served from, always with a trailing slash. */
export const BASE_URL: string = env.BASE_URL;

export const REPO_URL = "https://github.com/Anubhavlfc/FinSight";
export const DOCS_URL = `${REPO_URL}#readme`;
export const ARCHITECTURE_URL = `${REPO_URL}/blob/main/docs/architecture.md`;
export const SEC_EDGAR_URL = "https://www.sec.gov/search-filings/edgar-application-programming-interfaces";

/** URL of a file under public/, correct for any base path. */
export const asset = (path: string): string => `${BASE_URL}${path.replace(/^\/+/, "")}`;
