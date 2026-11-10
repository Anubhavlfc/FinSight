import { Link } from "react-router-dom";

/** Lens with three rising bars: three fiscal years seen through one instrument. */
export function LogoMark({ size = 26 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true" focusable="false">
      <circle cx="16" cy="16" r="12.5" stroke="var(--teal)" strokeWidth="1.8" />
      <path d="M10 21.5v-4.5M16 21.5v-8.5M22 21.5v-12.5" stroke="var(--ink)" strokeWidth="2.6" strokeLinecap="round" />
    </svg>
  );
}

export function Logo({ to = "/" }: { to?: string }) {
  return (
    <Link to={to} className="logo" aria-label="FinSight AI home">
      <LogoMark />
      <span className="logo-word">
        FinSight<span className="logo-ai">AI</span>
      </span>
    </Link>
  );
}
