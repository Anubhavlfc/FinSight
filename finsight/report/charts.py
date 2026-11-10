"""Chart specifications (data) and rendering (matplotlib PNG).

A ``ChartSpec`` holds the exact values plotted and the metric ID behind each
one, so the reviewer can check every chart value against the validated metrics
and the report can print the underlying dataset next to the chart.
Rendering reads only the spec.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from finsight.contracts import CalculatedMetric, ChartSpec, MetricStatus

# Categorical slots 1-3, fixed order (validated default palette from the dataviz guide).
SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a"]
SURFACE, INK, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"

CHARTS = [
    ("revenue_profit", "Revenue, operating income and net income", "USD",
     [("Revenue", "revenue"), ("Operating income", "operating_income"), ("Net income", "net_income")], "bar"),
    ("margins", "Gross, operating and net margin", "percent",
     [("Gross margin", "gross_margin"), ("Operating margin", "operating_margin"), ("Net margin", "net_margin")],
     "line"),
    ("cash_flow", "Operating cash flow, capital expenditure and free cash flow", "USD",
     [("Operating cash flow", "operating_cash_flow"), ("Capital expenditure", "capital_expenditure"),
      ("Free cash flow (calculated)", "free_cash_flow")], "bar"),
    ("cash_debt", "Cash and interest-bearing debt at year end", "USD",
     [("Cash and cash equivalents", "cash_and_equivalents"),
      ("Interest-bearing debt (calculated)", "interest_bearing_debt")], "bar"),
]


def build_chart_specs(metrics: list[CalculatedMetric], labels: list[str]) -> list[ChartSpec]:
    by_id = {m.id: m for m in metrics}
    specs = []
    for cid, title, unit, series, _kind in CHARTS:
        usd_vals = [abs(by_id[f"metric:{n}:{p}"].value) for _, n in series for p in labels
                    if f"metric:{n}:{p}" in by_id and by_id[f"metric:{n}:{p}"].value is not None]
        if unit == "USD":
            big = usd_vals and max(usd_vals) >= Decimal(10_000_000_000)
            scale, unit_label = (1e9, "USD billions") if big else (1e6, "USD millions")
        else:
            scale, unit_label = 1.0, "% of revenue"
        data, ids = {}, {}
        for name, metric in series:
            vals, mids = [], []
            for p in labels:
                m = by_id.get(f"metric:{metric}:{p}")
                ok = m is not None and m.status == MetricStatus.AVAILABLE and m.value is not None
                vals.append(float(m.value) / scale if ok else None)
                mids.append(m.id if m else None)
            if any(v is not None for v in vals):
                data[name], ids[name] = vals, mids
        if not data:
            continue
        missing = [f"{n} {p}" for n, vs in data.items() for p, v in zip(labels, vs) if v is None]
        caption = (f"Fiscal periods {labels[0]} to {labels[-1]}; values in {unit_label}; source: validated "
                   f"metrics (charts/{cid}.csv).")
        if missing:
            caption += " Not shown (unavailable): " + ", ".join(missing) + "."
        specs.append(ChartSpec(id=f"chart:{cid}", title=title, unit=unit_label, filename=f"{cid}.png",
                               periods=labels, series=data, metric_ids=ids, scale=scale, caption=caption))
    return specs


def write_chart_csv(spec: ChartSpec, path: Path) -> None:
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["series", "fiscal_period", f"value_{spec.unit.replace(' ', '_').replace('%', 'pct')}",
                    "metric_id"])
        for name, vals in spec.series.items():
            for p, v, mid in zip(spec.periods, vals, spec.metric_ids[name]):
                w.writerow([name, p, "" if v is None else repr(v), mid or ""])


def render_chart(spec: ChartSpec, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    kind = next(k for cid, *_rest, k in CHARTS if f"chart:{cid}" == spec.id)
    fig, ax = plt.subplots(figsize=(7.2, 3.8), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    x = list(range(len(spec.periods)))
    names = list(spec.series)
    fmt = (lambda v: f"{v:,.1f}%") if spec.unit.startswith("%") else \
        ((lambda v: f"{v:,.1f}") if "billions" in spec.unit else (lambda v: f"{v:,.0f}"))
    if kind == "bar":
        n = len(names)
        width = 0.8 / n
        for i, name in enumerate(names):
            xs = [xi - 0.4 + width * (i + 0.5) for xi in x]
            vals = spec.series[name]
            bars = ax.bar([xx for xx, v in zip(xs, vals) if v is not None], [v for v in vals if v is not None],
                          width=width * 0.92, color=SERIES_COLORS[i], label=name, zorder=2)
            for b, v in zip(bars, [v for v in vals if v is not None]):
                ax.annotate(fmt(v), (b.get_x() + b.get_width() / 2, v), ha="center",
                            va="bottom" if v >= 0 else "top", fontsize=7, color=MUTED,
                            xytext=(0, 2 if v >= 0 else -2), textcoords="offset points")
        ax.axhline(0, color=MUTED, linewidth=0.8, zorder=3)
    else:
        for i, name in enumerate(names):
            pts = [(xi, v) for xi, v in zip(x, spec.series[name]) if v is not None]
            ax.plot([p[0] for p in pts], [p[1] for p in pts], color=SERIES_COLORS[i], linewidth=2, marker="o",
                    markersize=5, label=name, zorder=2)
            if pts:
                ax.annotate(fmt(pts[-1][1]), pts[-1], xytext=(6, 0), textcoords="offset points", va="center",
                            fontsize=7, color=MUTED)
    ax.set_xticks(x, spec.periods, color=INK, fontsize=9)
    ax.set_ylabel(spec.unit, color=MUTED, fontsize=8)
    ax.tick_params(axis="y", colors=MUTED, labelsize=8)
    ax.grid(axis="y", color=GRID, linewidth=0.6, zorder=0)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.set_title(spec.title, loc="left", fontsize=10, color=INK)
    ax.legend(frameon=False, fontsize=7.5, ncols=len(names), loc="upper left", bbox_to_anchor=(0, -0.08))
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
