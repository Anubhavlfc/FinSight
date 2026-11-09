"""Rule-based detection and ranking of significant year-over-year changes.

Thresholds come from ``[analysis]`` in config/settings.toml. They are settings
that decide what to investigate, not financial standards. Relative rules are
combined with an absolute materiality floor (a share of latest-year revenue)
so that large percentage swings on small amounts are not over-emphasised.

Ranking score = |observed| / threshold, so 2.0 means "twice the trigger". Scores
from different rules are only roughly comparable; they set investigation order,
not importance. Profit/loss transitions are always ranked first.
"""

from __future__ import annotations

from decimal import Decimal

from finsight.config import AnalysisSettings
from finsight.contracts import CalculatedMetric, ChangeFlag, MetricStatus

TRANSITION_SCORE = Decimal(1000)


def _get(metrics: dict[str, CalculatedMetric], name: str, period: str) -> CalculatedMetric | None:
    m = metrics.get(f"metric:{name}:{period}")
    return m if m and m.status == MetricStatus.AVAILABLE and m.value is not None else None


def detect_changes(metrics_list: list[CalculatedMetric], labels: list[str],
                   settings: AnalysisSettings) -> list[ChangeFlag]:
    metrics = {m.id: m for m in metrics_list}
    flags: list[ChangeFlag] = []
    latest_rev = _get(metrics, "revenue", labels[-1]) if labels else None
    materiality = (abs(latest_rev.value) * Decimal(str(settings.materiality_pct_of_revenue)) / 100
                   if latest_rev else Decimal(0))

    def add(rule, metric, comp, observed, unit, threshold, direction, desc, ids, score=None):
        threshold = Decimal(str(threshold))
        flags.append(ChangeFlag(
            id=f"change:{rule}:{comp}", rule=rule, metric=metric, comparison=comp, observed=observed,
            observed_unit=unit, threshold=threshold,
            score=score if score is not None else (abs(observed) / threshold if threshold else Decimal(0)),
            direction=direction, description=desc, metric_ids=ids))

    for prev, cur in zip(labels, labels[1:]):
        comp = f"{prev}->{cur}"

        # Revenue growth beyond threshold and material in absolute terms.
        g, ch = _get(metrics, "revenue_growth", comp), _get(metrics, "revenue_change", comp)
        if g and ch and abs(g.value) > Decimal(str(settings.revenue_change_pct)) and abs(ch.value) >= materiality:
            add("revenue_change", "revenue_growth", comp, g.value, "percent", settings.revenue_change_pct,
                "increase" if g.value > 0 else "decrease",
                f"Revenue {'rose' if g.value > 0 else 'fell'} {abs(g.value):.1f}% ({comp}).", [g.id, ch.id])

        # Operating margin movement in percentage points.
        om = _get(metrics, "operating_margin_change", comp)
        if om and abs(om.value) > Decimal(str(settings.operating_margin_change_pp)):
            add("operating_margin_change", "operating_margin_change", comp, om.value, "percentage_points",
                settings.operating_margin_change_pp, "increase" if om.value > 0 else "decrease",
                f"Operating margin moved {om.value:+.1f} percentage points ({comp}).", [om.id])

        # Free cash flow: % where meaningful, otherwise absolute change against materiality.
        fg, fc = _get(metrics, "free_cash_flow_growth", comp), _get(metrics, "free_cash_flow_change", comp)
        if fg and fc:
            if abs(fg.value) > Decimal(str(settings.free_cash_flow_change_pct)) and abs(fc.value) >= materiality:
                add("free_cash_flow_change", "free_cash_flow_growth", comp, fg.value, "percent",
                    settings.free_cash_flow_change_pct, "increase" if fg.value > 0 else "decrease",
                    f"Free cash flow {'rose' if fg.value > 0 else 'fell'} {abs(fg.value):.1f}% ({comp}).",
                    [fg.id, fc.id])
        elif fc and materiality and abs(fc.value) >= materiality:
            sup = metrics.get(f"metric:free_cash_flow_growth:{comp}")
            if sup and sup.status == MetricStatus.SUPPRESSED:
                add("free_cash_flow_change_nonpositive_base", "free_cash_flow_change", comp, fc.value, "USD",
                    materiality, "increase" if fc.value > 0 else "decrease",
                    f"Free cash flow changed by {fc.value:,.0f} USD from a zero or negative base ({comp}); "
                    "percentage change suppressed.", [fc.id, sup.id])

        # Profit <-> loss transitions.
        for name in ("net_income", "operating_income"):
            a, b = _get(metrics, name, prev), _get(metrics, name, cur)
            if a and b and (a.value < 0) != (b.value < 0):
                to = "profit" if b.value >= 0 else "loss"
                add(f"{name}_transition", name, comp, b.value - a.value, "USD", 1, "transition",
                    f"{name.replace('_', ' ').capitalize()} moved from {'loss' if a.value < 0 else 'profit'} "
                    f"to {to} ({comp}).", [a.id, b.id], score=TRANSITION_SCORE)

        # Material increase in interest-bearing debt.
        dg, dc = _get(metrics, "interest_bearing_debt_growth", comp), _get(metrics, "interest_bearing_debt_change", comp)
        if dc and dc.value > 0 and dc.value >= materiality:
            if dg and dg.value > Decimal(str(settings.debt_increase_pct)):
                add("debt_increase", "interest_bearing_debt_growth", comp, dg.value, "percent",
                    settings.debt_increase_pct, "increase",
                    f"Interest-bearing debt rose {dg.value:.1f}% ({comp}).", [dg.id, dc.id])
            elif dg is None and materiality:
                add("debt_increase_from_zero", "interest_bearing_debt_change", comp, dc.value, "USD",
                    materiality, "increase",
                    f"Interest-bearing debt increased by {dc.value:,.0f} USD from a zero base ({comp}).", [dc.id])

    flags.sort(key=lambda f: (-f.score, f.comparison, f.rule))
    return flags
