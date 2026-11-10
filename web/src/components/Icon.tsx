import type { ReactNode } from "react";

export type IconName =
  | "check" | "alert" | "x" | "clock" | "search" | "up" | "down" | "flat" | "external" | "download"
  | "doc" | "chevron" | "menu" | "github" | "dot" | "half" | "ring" | "dash" | "quote" | "calc"
  | "spark" | "arrow" | "info" | "refresh" | "building" | "layers" | "shield" | "archive" | "code" | "plus";

const paths: Record<IconName, ReactNode> = {
  check: <path d="M3.5 8.5l3 3 6-7" />,
  alert: (<><path d="M8 2.2l6.3 11.3H1.7L8 2.2z" /><path d="M8 6.5v3.2M8 11.6v.2" /></>),
  x: <path d="M4 4l8 8M12 4l-8 8" />,
  clock: (<><circle cx="8" cy="8" r="6" /><path d="M8 4.8V8l2.2 1.6" /></>),
  search: (<><circle cx="7" cy="7" r="4.5" /><path d="M10.5 10.5L14 14" /></>),
  up: <path d="M8 13V3M4 7l4-4 4 4" />,
  down: <path d="M8 3v10M4 9l4 4 4-4" />,
  flat: <path d="M3 8h10" />,
  external: (<><path d="M9 3h4v4M13 3L7.5 8.5" /><path d="M11 9.5V13H3V5h3.5" /></>),
  download: (<><path d="M8 2.5v8M4.8 7.5L8 10.7l3.2-3.2" /><path d="M3 13.5h10" /></>),
  doc: (<><path d="M4 2h5.5L12 4.5V14H4V2z" /><path d="M6 7h4M6 9.5h4M6 12h2.5" /></>),
  chevron: <path d="M6 3.5L10.5 8 6 12.5" />,
  menu: <path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11" />,
  github: (
    <path
      fill="currentColor"
      stroke="none"
      d="M8 1.3a6.7 6.7 0 00-2.1 13.1c.3 0 .5-.2.5-.4v-1.3c-1.9.4-2.3-.8-2.3-.8-.3-.8-.7-1-.7-1-.6-.4 0-.4 0-.4.7 0 1 .7 1 .7.6 1 1.6.7 2 .6 0-.5.2-.8.4-1-1.5-.1-3-.7-3-3.3 0-.7.2-1.3.7-1.8-.1-.2-.3-.9.1-1.8 0 0 .6-.2 1.8.7a6.3 6.3 0 013.4 0c1.3-.9 1.8-.7 1.8-.7.4.9.2 1.6.1 1.8.4.5.7 1.1.7 1.8 0 2.6-1.6 3.2-3 3.3.2.2.4.6.4 1.2v1.8c0 .2.1.4.5.4A6.7 6.7 0 008 1.3z"
    />
  ),
  dot: <circle cx="8" cy="8" r="4" fill="currentColor" stroke="none" />,
  half: (<><circle cx="8" cy="8" r="5" /><path d="M8 3a5 5 0 010 10z" fill="currentColor" stroke="none" /></>),
  ring: <circle cx="8" cy="8" r="5" />,
  dash: <path d="M4 8h8" />,
  quote: <path d="M3 11V8.2C3 6 4.2 4.8 6.2 4.5M9 11V8.2c0-2.2 1.2-3.4 3.2-3.7M3 11h3V8.2H3M9 11h3V8.2H9" />,
  calc: (<><rect x="3" y="2" width="10" height="12" rx="1.5" /><path d="M5.5 5h5M5.5 8h1M8 8h.1M10.4 8h.1M5.5 11h1M8 11h.1M10.4 11h.1" /></>),
  spark: <path d="M8 2l1.4 4.6L14 8l-4.6 1.4L8 14l-1.4-4.6L2 8l4.6-1.4L8 2z" />,
  arrow: <path d="M3 8h10M9 4l4 4-4 4" />,
  info: (<><circle cx="8" cy="8" r="6" /><path d="M8 7.2v4M8 4.8v.2" /></>),
  refresh: (<><path d="M13 8a5 5 0 11-1.5-3.6" /><path d="M13 2.5v2.8h-2.8" /></>),
  building: (<><path d="M3 14V3h6v11M9 7h4v7M1.5 14h13" /><path d="M5 5.5h2M5 8h2M5 10.5h2M10.7 9.5h.6M10.7 11.8h.6" /></>),
  layers: <path d="M8 2l6 3-6 3-6-3 6-3zM2 8l6 3 6-3M2 11l6 3 6-3" />,
  shield: (<><path d="M8 1.8l5 1.8v4c0 3-2.1 5.3-5 6.6-2.9-1.3-5-3.6-5-6.6v-4l5-1.8z" /><path d="M5.8 8l1.6 1.6 2.9-3.2" /></>),
  archive: (<><rect x="2" y="3" width="12" height="3" rx="0.8" /><path d="M3 6v7h10V6M6.5 9h3" /></>),
  code: <path d="M5.5 4.5L2 8l3.5 3.5M10.5 4.5L14 8l-3.5 3.5" />,
  plus: <path d="M8 3v10M3 8h10" />,
};

interface IconProps {
  name: IconName;
  size?: number;
  /** Icons are decorative by default; pass a label when one stands alone. */
  label?: string;
  className?: string;
}

export function Icon({ name, size = 16, label, className }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
    >
      {paths[name]}
    </svg>
  );
}
