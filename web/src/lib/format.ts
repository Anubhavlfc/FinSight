const dateFmt = new Intl.DateTimeFormat("en-US", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });
const dateTimeFmt = new Intl.DateTimeFormat("en-US", {
  year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "UTC",
});
const timeFmt = new Intl.DateTimeFormat("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZone: "UTC" });

/** "Jul 29, 2026". Dates from filings are calendar dates, so they are shown in UTC to avoid shifting a day. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "not available";
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso);
  return Number.isNaN(d.getTime()) ? iso : dateFmt.format(d);
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "not available";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : `${dateTimeFmt.format(d)} UTC`;
}

export function formatTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : timeFmt.format(d);
}

/** 4.2 s, 1 min 05 s, 1 h 02 min */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0 s";
  if (seconds < 10) return `${seconds.toFixed(1)} s`;
  const s = Math.round(seconds);
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`;
  return `${Math.floor(s / 3600)} h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")} min`;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10_240 ? 1 : 0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function periodRange(periods: string[]): string {
  if (periods.length === 0) return "Period not set";
  if (periods.length === 1) return periods[0]!;
  return `${periods[0]} to ${periods[periods.length - 1]}`;
}

/** Fixture company names carry a "(SYNTHETIC FIXTURE)" suffix; the UI shows that as a badge instead. */
export function cleanCompanyName(name: string): string {
  return name.replace(/\s*\(SYNTHETIC FIXTURE\)\s*$/i, "");
}

export const displayCompany = cleanCompanyName;
