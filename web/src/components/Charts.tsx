import { useId, useRef, useState } from "react";
import type { Chart } from "../api/types";
import { useWidth } from "../lib/hooks";

const SERIES_COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)"];
const HEIGHT = 250;
const M = { top: 14, right: 16, bottom: 30, left: 52 };

/** Values arrive in the chart's display unit (USD millions, USD billions or %). */
function formatter(unit: string): (v: number) => string {
  if (unit.startsWith("%")) return (v) => `${v.toFixed(1)}%`;
  const digits = unit.includes("billions") ? 1 : 0;
  const nf = new Intl.NumberFormat("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return (v) => nf.format(v);
}

function niceTicks(min: number, max: number, count = 4): number[] {
  if (min === max) max = min + 1;
  const rough = (max - min) / count;
  const pow = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= rough) ?? 10 * pow;
  const start = Math.floor(min / step) * step;
  const ticks: number[] = [];
  for (let v = start; v <= max + step * 0.999; v += step) ticks.push(Number(v.toFixed(10)));
  return ticks;
}

function barPath(x: number, w: number, y0: number, y1: number): string {
  // Rounded at the data end, square at the baseline.
  const h = Math.abs(y1 - y0);
  const r = Math.min(4, w / 2, h);
  if (h < 0.5) return "";
  if (y1 < y0) {
    return `M${x},${y0}V${y1 + r}Q${x},${y1} ${x + r},${y1}H${x + w - r}Q${x + w},${y1} ${x + w},${y1 + r}V${y0}Z`;
  }
  return `M${x},${y0}V${y1 - r}Q${x},${y1} ${x + r},${y1}H${x + w - r}Q${x + w},${y1} ${x + w},${y1 - r}V${y0}Z`;
}

function Plot({ chart }: { chart: Chart }) {
  const holder = useRef<HTMLDivElement>(null);
  const width = useWidth(holder);
  const [active, setActive] = useState<number | null>(null);
  const fmt = formatter(chart.unit);
  const isLine = chart.kind === "line";
  const right = isLine ? 54 : M.right;
  const innerW = width - M.left - right;
  const innerH = HEIGHT - M.top - M.bottom;

  const values = chart.series.flatMap((s) => s.values).filter((v): v is number => v !== null);
  const ticks = niceTicks(Math.min(0, ...values), Math.max(0, ...values));
  const lo = ticks[0]!;
  const hi = ticks[ticks.length - 1]!;
  const y = (v: number) => M.top + innerH - ((v - lo) / (hi - lo)) * innerH;
  const n = chart.periods.length;
  const band = innerW / n;
  const center = (i: number) => M.left + band * (i + 0.5);

  const k = chart.series.length;
  const gap = 2;
  const barW = Math.max(6, Math.min(24, (band * 0.72 - gap * (k - 1)) / k));
  const groupW = barW * k + gap * (k - 1);

  // Direct end labels for lines, skipped where two would overlap (the legend, tooltip and table carry those).
  const endLabels: { y: number; text: string }[] = [];
  if (isLine) {
    const candidates = chart.series
      .map((s) => s.values[n - 1])
      .filter((v): v is number => v !== null && v !== undefined)
      .map((v) => ({ y: y(v), text: fmt(v) }))
      .sort((a, b) => a.y - b.y);
    for (const c of candidates) {
      if (endLabels.every((e) => Math.abs(e.y - c.y) >= 14)) endLabels.push(c);
    }
  }

  const tooltipLeft = active === null ? 0 : Math.min(Math.max(center(active), 96), width - 96);

  return (
    <div className="plot" ref={holder} onMouseLeave={() => setActive(null)}>
      <svg width={width} height={HEIGHT} role="group" aria-label={`${chart.title}, ${chart.unit}`}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={M.left} x2={width - right} y1={y(t)} y2={y(t)} stroke="var(--grid)" strokeWidth={1} />
            <text x={M.left - 8} y={y(t)} dy="0.32em" textAnchor="end" className="tick">
              {fmt(t)}
            </text>
          </g>
        ))}
        {active !== null && (
          <rect x={M.left + band * active} y={M.top} width={band} height={innerH} fill="rgba(255,255,255,0.04)" />
        )}
        {!isLine &&
          chart.series.map((s, si) =>
            s.values.map((v, pi) =>
              v === null ? null : (
                <path
                  key={`${s.name}-${chart.periods[pi]}`}
                  d={barPath(center(pi) - groupW / 2 + si * (barW + gap), barW, y(0), y(v))}
                  fill={SERIES_COLORS[si]}
                />
              ),
            ),
          )}
        {isLine &&
          chart.series.map((s, si) => {
            const pts = s.values.map((v, pi) => (v === null ? null : ([center(pi), y(v)] as const)));
            const d = pts
              .map((p, i) => (p ? `${i === 0 || !pts[i - 1] ? "M" : "L"}${p[0]},${p[1]}` : ""))
              .join("");
            return (
              <g key={s.name}>
                <path d={d} fill="none" stroke={SERIES_COLORS[si]} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
                {pts.map((p, pi) =>
                  p ? (
                    <circle key={chart.periods[pi]} cx={p[0]} cy={p[1]} r={4.5} fill={SERIES_COLORS[si]} stroke="var(--surface-1)" strokeWidth={2} />
                  ) : null,
                )}
              </g>
            );
          })}
        <line x1={M.left} x2={width - right} y1={y(0)} y2={y(0)} stroke="var(--line-strong)" strokeWidth={1} />
        {endLabels.map((e) => (
          <text key={e.text + e.y} x={center(n - 1) + 10} y={e.y} dy="0.32em" className="tick end-label">
            {e.text}
          </text>
        ))}
        {chart.periods.map((p, pi) => (
          <text key={p} x={center(pi)} y={HEIGHT - 9} textAnchor="middle" className={`tick period${active === pi ? " on" : ""}`}>
            {p}
          </text>
        ))}
        {chart.periods.map((p, pi) => (
          <rect
            key={p}
            x={M.left + band * pi}
            y={M.top}
            width={band}
            height={innerH + M.bottom}
            fill="transparent"
            tabIndex={0}
            role="img"
            aria-label={`${p}: ${chart.series
              .map((s) => `${s.name} ${s.values[pi] === null || s.values[pi] === undefined ? "not available" : fmt(s.values[pi]!)}`)
              .join("; ")} (${chart.unit})`}
            className="hit"
            onMouseEnter={() => setActive(pi)}
            onFocus={() => setActive(pi)}
            onBlur={() => setActive(null)}
          />
        ))}
      </svg>
      {active !== null && (
        <div className="tooltip" style={{ left: tooltipLeft }} aria-hidden="true">
          <p className="tooltip-title">
            {chart.periods[active]} <span className="muted">· {chart.unit}</span>
          </p>
          {chart.series.map((s, si) => {
            const v = s.values[active];
            return (
              <p key={s.name} className="tooltip-row">
                <span className={`swatch${isLine ? " line" : ""}`} style={{ background: SERIES_COLORS[si] }} />
                <span>{s.name}</span>
                <span className="num">{v === null || v === undefined ? "not available" : fmt(v)}</span>
              </p>
            );
          })}
        </div>
      )}
    </div>
  );
}

function ValuesTable({ chart }: { chart: Chart }) {
  const fmt = formatter(chart.unit);
  return (
    <div className="table-scroll" tabIndex={0} role="group" aria-label="Scrollable table">
      <table className="data">
        <caption>
          {chart.title} ({chart.unit})
        </caption>
        <thead>
          <tr>
            <th scope="col">Series</th>
            {chart.periods.map((p) => (
              <th key={p} scope="col" className="num">
                {p}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {chart.series.map((s) => (
            <tr key={s.name}>
              <th scope="row">{s.name}</th>
              {s.values.map((v, i) => (
                <td key={chart.periods[i]} className={`num${v === null ? " cell-missing" : ""}`}>
                  {v === null ? "not available" : fmt(v)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ChartCard({ chart, compact }: { chart: Chart; compact?: boolean }) {
  const [view, setView] = useState<"chart" | "table">("chart");
  const id = useId();
  const isLine = chart.kind === "line";
  return (
    <figure className="chart-card panel" aria-labelledby={`${id}-title`}>
      <div className="chart-head">
        <div>
          <h3 id={`${id}-title`}>{chart.title}</h3>
          <p className="muted chart-unit">{chart.unit}</p>
        </div>
        {!compact && (
          <div className="seg" role="group" aria-label={`View for ${chart.title}`}>
            <button type="button" aria-pressed={view === "chart"} onClick={() => setView("chart")}>
              Chart
            </button>
            <button type="button" aria-pressed={view === "table"} onClick={() => setView("table")}>
              Table
            </button>
          </div>
        )}
      </div>
      {view === "chart" ? (
        <>
          <ul className="legend" aria-label="Series">
            {chart.series.map((s, si) => (
              <li key={s.name}>
                <span className={`swatch${isLine ? " line" : ""}`} style={{ background: SERIES_COLORS[si] }} />
                {s.name}
              </li>
            ))}
          </ul>
          <Plot chart={chart} />
        </>
      ) : (
        <ValuesTable chart={chart} />
      )}
      {!compact && <figcaption className="chart-caption muted">{chart.caption}</figcaption>}
    </figure>
  );
}
