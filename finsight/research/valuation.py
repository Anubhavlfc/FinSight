"""Market valuation at the supplied price: market value, enterprise value and trading multiples.

Descriptive only. FinSight sets no price target and makes no buy/hold/sell call. Inputs:
* validated engine metrics (revenue, operating income, net income, free cash flow, cash, debt), by metric ID;
* SEC company facts (diluted shares, short-term investments), by tag and accession;
* the dated market price the user supplied.
"""

from __future__ import annotations

from decimal import Decimal

from finsight.contracts import CalculatedMetric, MetricStatus
from finsight.research.models import MarketSnapshot, Multiple, Valuation

D = Decimal
HUNDRED = D(100)


def _m(by_id: dict[str, CalculatedMetric], name: str, period: str) -> CalculatedMetric | None:
    m = by_id.get(f"metric:{name}:{period}")
    return m if m is not None and m.status == MetricStatus.AVAILABLE and m.value is not None else None


def value(metrics: list[CalculatedMetric], labels: list[str], shares: Decimal | None,
          short_term_investments: Decimal | None, market: MarketSnapshot | None) -> Valuation:
    by_id = {m.id: m for m in metrics}
    latest = labels[-1]
    if market is None:
        return Valuation(available=False, reason="No current share price was supplied, so market multiples are not "
                                                 "shown.")
    cash, debt = _m(by_id, "cash_and_equivalents", latest), _m(by_id, "interest_bearing_debt", latest)
    missing = [w for w, ok in (("diluted share count", shares is not None and shares > 0),
                               (f"cash and cash equivalents for {latest}", cash is not None),
                               (f"interest-bearing debt for {latest}", debt is not None)) if not ok]
    if missing:
        return Valuation(available=False, reason="Market multiples not calculated; missing " + ", ".join(missing) + ".")
    sti = short_term_investments or D(0)
    cap = market.price * shares
    net_cash = cash.value + sti - debt.value
    ev = cap - net_cash
    out: list[Multiple] = []

    def add(name: str, num: Decimal, metric: str, unit: str, basis: str) -> None:
        m = _m(by_id, metric, latest)
        if m is None or m.value <= 0:
            out.append(Multiple(name=name, value="n/m", basis=f"{basis}; {metric} {latest} is not positive or not "
                                                                "available", metric_ids=[m.id] if m else []))
            return
        x = num / m.value * (HUNDRED if unit == "%" else 1)
        out.append(Multiple(name=name, value=f"{x:.1f}{unit}", basis=f"{basis} ({m.id})", metric_ids=[m.id]))

    add("P/E", cap, "net_income", "x", f"market value of equity / net income {latest}")
    add("P/FCF", cap, "free_cash_flow", "x", f"market value of equity / free cash flow {latest}")
    fcf = _m(by_id, "free_cash_flow", latest)
    if fcf is not None:
        out.append(Multiple(name="FCF yield", value=f"{fcf.value / cap * HUNDRED:.1f}%",
                            basis=f"free cash flow {latest} / market value of equity ({fcf.id})", metric_ids=[fcf.id]))
    add("EV / revenue", ev, "revenue", "x", f"enterprise value / revenue {latest}")
    add("EV / operating income", ev, "operating_income", "x", f"enterprise value / operating income {latest}")
    return Valuation(available=True, market_cap=cap, cash=cash.value, short_term_investments=sti, debt=debt.value,
                     net_cash=net_cash, enterprise_value=ev, diluted_shares=shares, multiples=out,
                     input_metric_ids=[cash.id, debt.id])
