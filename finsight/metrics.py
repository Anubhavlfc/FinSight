"""Deterministic financial metrics.

Every metric records its formula and the fact / metric IDs it was computed from.
Values keep full ``Decimal`` precision; rounding is for presentation only.

Definitions
-----------
revenue                 Reported revenue (see config/mappings.toml for tags).
revenue_change          revenue[t] - revenue[t-1], USD.
revenue_growth          (revenue[t] - revenue[t-1]) / revenue[t-1] x 100. Suppressed when revenue[t-1] <= 0.
gross_margin            gross_profit / revenue x 100. Not applicable when gross profit is neither reported
                        nor derivable (e.g. service companies without cost of revenue).
operating_margin        operating_income / revenue x 100.
net_margin              net_income / revenue x 100 (net income attributable to the parent where tagged so).
*_margin_change         margin[t] - margin[t-1], in percentage points.
operating_cash_flow     Reported net cash from operating activities.
capital_expenditure     Purchases of property, plant and equipment, as a positive outflow.
free_cash_flow          operating_cash_flow - capital_expenditure. A calculated (non-GAAP) measure.
free_cash_flow_growth   Suppressed when the prior-year value is <= 0; the absolute change is still given.
cash_and_equivalents    Reported cash and cash equivalents at year end.
interest_bearing_debt   Long-term debt including current maturities (noncurrent + current balance-sheet
                        carrying amounts, else LongTermDebt) + commercial paper + short-term borrowings
                        (ShortTermBorrowings, else OtherShortTermBorrowings).
                        Lease liabilities are excluded, except where the filer tags debt and finance
                        leases together; the metric note then says so. Total liabilities is never used
                        as a substitute.
current_ratio           current_assets / current_liabilities. Not applicable for unclassified balance sheets.
net_income_change       net_income[t] - net_income[t-1], USD.
net_income_growth       Suppressed when the prior-year value is <= 0 (a loss base); the absolute change is still given.
capital_expenditure_change / _growth   Change in capex (a positive outflow), USD and %.
<expense>_pct_of_revenue  cost_of_revenue, research_and_development, selling_and_marketing,
                        general_and_administrative, selling_general_and_administrative as a share of revenue.
                        Used to break down operating-margin changes. Not applicable when the line is not reported.

No metric here needs an opening balance, so no extra prior-year balance is fetched. Metrics that
would (e.g. return on average equity) must fetch it or be marked unavailable.
"""

from __future__ import annotations

from decimal import Decimal, localcontext

from finsight.contracts import CalculatedMetric, FactStatus, FinancialFact, MetricStatus, ValidationReport

HUNDRED = Decimal(100)

METRIC_NAMES = (
    "revenue", "revenue_change", "revenue_growth",
    "gross_profit", "gross_margin", "gross_margin_change",
    "operating_income", "operating_margin", "operating_margin_change",
    "net_income", "net_margin", "net_margin_change",
    "operating_cash_flow", "operating_cash_flow_change",
    "capital_expenditure",
    "free_cash_flow", "free_cash_flow_change", "free_cash_flow_growth",
    "cash_and_equivalents", "cash_and_equivalents_change",
    "interest_bearing_debt", "interest_bearing_debt_change", "interest_bearing_debt_growth",
    "current_ratio", "current_ratio_change",
    "net_income_change", "net_income_growth",
    "capital_expenditure_change", "capital_expenditure_growth",
)

# Expense lines used to break down operating-margin changes (reported only by some filers).
EXPENSE_COMPONENTS = ("cost_of_revenue", "research_and_development", "selling_and_marketing",
                      "general_and_administrative", "selling_general_and_administrative")

# Fields that are optional because some business models do not report them.
NOT_APPLICABLE_WHEN_MISSING = {"gross_profit", "current_assets", "current_liabilities", "cost_of_revenue",
                               "research_and_development", "selling_and_marketing", "general_and_administrative",
                               "selling_general_and_administrative"}


def mid(name: str, period: str) -> str:
    return f"metric:{name}:{period}"


class _Builder:
    def __init__(self, report: ValidationReport):
        self.report = report
        self.facts = {(f.field, f.fiscal_period): f for f in report.accepted_facts}
        self.out: dict[str, CalculatedMetric] = {}
        self.labels = [p.label for p in report.fiscal_periods]

    def fact(self, field: str, lbl: str) -> FinancialFact | None:
        return self.facts.get((field, lbl))

    def add(self, m: CalculatedMetric) -> CalculatedMetric:
        self.out[m.id] = m
        return m

    def missing_status(self, fields: list[str], lbl: str) -> tuple[MetricStatus, str]:
        missing = [f for f in fields if self.fact(f, lbl) is None]
        if missing and all(f in NOT_APPLICABLE_WHEN_MISSING for f in missing):
            return MetricStatus.NOT_APPLICABLE, f"not reported by the company: {', '.join(missing)}"
        return MetricStatus.UNAVAILABLE, f"missing validated input: {', '.join(missing)}"

    def reported(self, name: str, field: str, lbl: str) -> CalculatedMetric:
        f = self.fact(field, lbl)
        if f is None:
            status, note = self.missing_status([field], lbl)
            return self.add(CalculatedMetric(id=mid(name, lbl), name=name, fiscal_period=lbl, value=None,
                                             unit="USD", formula=f"{field} (reported)", status=status,
                                             note=note, is_calculated_measure=False))
        note = "derived: " + f.location.removeprefix("derived: ") if f.status == FactStatus.DERIVED else ""
        return self.add(CalculatedMetric(id=mid(name, lbl), name=name, fiscal_period=lbl, value=f.value,
                                         unit="USD", formula=f"{field} (reported)", input_fact_ids=[f.id],
                                         note=note, is_calculated_measure=f.status == FactStatus.DERIVED))

    def ratio(self, name: str, num_field: str, den_field: str, lbl: str, scale: Decimal, unit: str,
              formula: str) -> CalculatedMetric:
        n, d = self.fact(num_field, lbl), self.fact(den_field, lbl)
        if n is None or d is None:
            status, note = self.missing_status([num_field, den_field], lbl)
            return self.add(CalculatedMetric(id=mid(name, lbl), name=name, fiscal_period=lbl, value=None,
                                             unit=unit, formula=formula, status=status, note=note))
        if d.value <= 0:
            return self.add(CalculatedMetric(
                id=mid(name, lbl), name=name, fiscal_period=lbl, value=None, unit=unit, formula=formula,
                input_fact_ids=[n.id, d.id], status=MetricStatus.UNAVAILABLE,
                note=f"{den_field} is {d.value:,}; the ratio is undefined or misleading for a zero/negative base"))
        with localcontext() as ctx:
            ctx.prec = 34
            value = n.value / d.value * scale
        return self.add(CalculatedMetric(id=mid(name, lbl), name=name, fiscal_period=lbl, value=value,
                                         unit=unit, formula=formula, input_fact_ids=[n.id, d.id]))

    def diff(self, name: str, base: str, prev: str, cur: str, unit: str) -> CalculatedMetric:
        """Absolute change between two metrics (USD, percentage points or x)."""
        a, b = self.out.get(mid(base, prev)), self.out.get(mid(base, cur))
        lbl = f"{prev}->{cur}"
        formula = f"{base}[{cur}] - {base}[{prev}]"
        if not a or not b or a.value is None or b.value is None:
            na = any(m and m.status == MetricStatus.NOT_APPLICABLE for m in (a, b))
            return self.add(CalculatedMetric(id=mid(name, lbl), name=name, fiscal_period=lbl, value=None,
                                             unit=unit, formula=formula,
                                             status=MetricStatus.NOT_APPLICABLE if na else MetricStatus.UNAVAILABLE,
                                             note=f"{base} unavailable for one of the years",
                                             input_metric_ids=[m.id for m in (a, b) if m]))
        return self.add(CalculatedMetric(id=mid(name, lbl), name=name, fiscal_period=lbl,
                                         value=b.value - a.value, unit=unit, formula=formula,
                                         input_metric_ids=[a.id, b.id]))

    def growth(self, name: str, base: str, prev: str, cur: str) -> CalculatedMetric:
        a, b = self.out.get(mid(base, prev)), self.out.get(mid(base, cur))
        lbl = f"{prev}->{cur}"
        formula = f"({base}[{cur}] - {base}[{prev}]) / {base}[{prev}] x 100"
        if not a or not b or a.value is None or b.value is None:
            return self.add(CalculatedMetric(id=mid(name, lbl), name=name, fiscal_period=lbl, value=None,
                                             unit="percent", formula=formula, status=MetricStatus.UNAVAILABLE,
                                             note=f"{base} unavailable for one of the years",
                                             input_metric_ids=[m.id for m in (a, b) if m]))
        if a.value <= 0:
            change = b.value - a.value
            return self.add(CalculatedMetric(
                id=mid(name, lbl), name=name, fiscal_period=lbl, value=None, unit="percent", formula=formula,
                input_metric_ids=[a.id, b.id], status=MetricStatus.SUPPRESSED,
                note=f"prior-year {base} is {a.value:,} (zero or negative), so a percentage change would mislead; "
                     f"absolute change is {change:,} USD"))
        with localcontext() as ctx:
            ctx.prec = 34
            value = (b.value - a.value) / a.value * HUNDRED
        return self.add(CalculatedMetric(id=mid(name, lbl), name=name, fiscal_period=lbl, value=value,
                                         unit="percent", formula=formula, input_metric_ids=[a.id, b.id]))

    def debt(self, lbl: str) -> CalculatedMetric:
        name = "interest_bearing_debt"
        formula = ("long-term debt incl. current maturities (noncurrent + current, else LongTermDebt) "
                   "+ commercial paper + short-term borrowings; leases excluded unless tagged together with debt")
        total, nonc, cur = (self.fact("long_term_debt_total", lbl), self.fact("long_term_debt_noncurrent", lbl),
                            self.fact("long_term_debt_current", lbl))
        notes: list[str] = []
        parts: list[FinancialFact] = []
        # Prefer the balance-sheet lines: some filers tag LongTermDebt at face value
        # (before unamortized discount and issuance costs), which does not tie to the
        # balance sheet. Use LongTermDebt only when the current portion is missing.
        if nonc and cur:
            parts += [nonc, cur]
        elif total:
            parts.append(total)
        elif nonc:
            parts.append(nonc)
            if cur:
                parts.append(cur)
            else:
                notes.append("current portion of long-term debt not reported; not included")
        else:
            return self.add(CalculatedMetric(
                id=mid(name, lbl), name=name, fiscal_period=lbl, value=None, unit="USD", formula=formula,
                status=MetricStatus.UNAVAILABLE,
                note="no long-term debt tag reported. The company may have no debt, but that is not assumed; "
                     "verify in the filing's debt note"))
        cp, stb, other = (self.fact("commercial_paper", lbl), self.fact("short_term_borrowings", lbl),
                          self.fact("other_short_term_borrowings", lbl))
        if cp:
            parts.append(cp)
        else:
            notes.append("commercial paper not reported; not included")
        if stb:
            parts.append(stb)
        elif other:
            # OtherShortTermBorrowings is a component of ShortTermBorrowings, so it is only
            # added when the total is not reported (otherwise it would be counted twice).
            parts.append(other)
            notes.append("short-term borrowings other than commercial paper from OtherShortTermBorrowings")
        else:
            notes.append("short term borrowings not reported; not included")
        if any(p.tag and "CapitalLease" in p.tag for p in parts):
            notes.append("includes finance lease obligations: the filer tags debt and finance leases together")
        value = sum((p.value for p in parts), Decimal(0))
        return self.add(CalculatedMetric(id=mid(name, lbl), name=name, fiscal_period=lbl, value=value,
                                         unit="USD", formula=formula, input_fact_ids=[p.id for p in parts],
                                         note="; ".join(notes)))

    def fcf(self, lbl: str) -> CalculatedMetric:
        name = "free_cash_flow"
        formula = "operating_cash_flow - capital_expenditure (capex as a positive outflow)"
        o, c = self.fact("operating_cash_flow", lbl), self.fact("capital_expenditure", lbl)
        if o is None or c is None:
            status, note = self.missing_status(["operating_cash_flow", "capital_expenditure"], lbl)
            return self.add(CalculatedMetric(id=mid(name, lbl), name=name, fiscal_period=lbl, value=None,
                                             unit="USD", formula=formula, status=MetricStatus.UNAVAILABLE,
                                             note=note))
        return self.add(CalculatedMetric(id=mid(name, lbl), name=name, fiscal_period=lbl, value=o.value - c.value,
                                         unit="USD", formula=formula, input_fact_ids=[o.id, c.id],
                                         note="calculated non-GAAP measure"))


def compute_metrics(report: ValidationReport) -> list[CalculatedMetric]:
    b = _Builder(report)
    for lbl in b.labels:
        b.reported("revenue", "revenue", lbl)
        b.reported("gross_profit", "gross_profit", lbl)
        b.reported("operating_income", "operating_income", lbl)
        b.reported("net_income", "net_income", lbl)
        b.reported("operating_cash_flow", "operating_cash_flow", lbl)
        b.reported("capital_expenditure", "capital_expenditure", lbl)
        b.reported("cash_and_equivalents", "cash_and_equivalents", lbl)
        b.ratio("gross_margin", "gross_profit", "revenue", lbl, HUNDRED, "percent", "gross_profit / revenue x 100")
        b.ratio("operating_margin", "operating_income", "revenue", lbl, HUNDRED, "percent",
                "operating_income / revenue x 100")
        b.ratio("net_margin", "net_income", "revenue", lbl, HUNDRED, "percent", "net_income / revenue x 100")
        b.ratio("current_ratio", "current_assets", "current_liabilities", lbl, Decimal(1), "x",
                "current_assets / current_liabilities")
        b.fcf(lbl)
        b.debt(lbl)
        for comp in EXPENSE_COMPONENTS:
            b.reported(comp, comp, lbl)
            b.ratio(f"{comp}_pct_of_revenue", comp, "revenue", lbl, HUNDRED, "percent",
                    f"{comp} / revenue x 100")
    for prev, cur in zip(b.labels, b.labels[1:]):
        b.diff("revenue_change", "revenue", prev, cur, "USD")
        b.growth("revenue_growth", "revenue", prev, cur)
        for margin in ("gross_margin", "operating_margin", "net_margin"):
            b.diff(f"{margin}_change", margin, prev, cur, "percentage_points")
        b.diff("operating_cash_flow_change", "operating_cash_flow", prev, cur, "USD")
        b.diff("free_cash_flow_change", "free_cash_flow", prev, cur, "USD")
        b.growth("free_cash_flow_growth", "free_cash_flow", prev, cur)
        b.diff("cash_and_equivalents_change", "cash_and_equivalents", prev, cur, "USD")
        b.diff("interest_bearing_debt_change", "interest_bearing_debt", prev, cur, "USD")
        b.growth("interest_bearing_debt_growth", "interest_bearing_debt", prev, cur)
        b.diff("current_ratio_change", "current_ratio", prev, cur, "x")
        b.diff("net_income_change", "net_income", prev, cur, "USD")
        b.growth("net_income_growth", "net_income", prev, cur)
        b.diff("capital_expenditure_change", "capital_expenditure", prev, cur, "USD")
        b.growth("capital_expenditure_growth", "capital_expenditure", prev, cur)
        for comp in EXPENSE_COMPONENTS:
            b.diff(f"{comp}_pct_of_revenue_change", f"{comp}_pct_of_revenue", prev, cur, "percentage_points")
    return list(b.out.values())
