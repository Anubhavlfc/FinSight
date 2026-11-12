"""Valuation and rating. Deterministic: same inputs, same target price.

Inputs come from three places, each labelled in the report:
* validated engine metrics (free cash flow, revenue, cash, debt), cited by metric ID;
* SEC company facts (diluted shares, short-term investments), cited by tag and accession;
* stated assumptions (``config/research.toml``) and the dated market price.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, localcontext
from pathlib import Path

from finsight.contracts import CalculatedMetric, MetricStatus
from finsight.research.models import DcfYear, MarketSnapshot, RateInput, Recommendation, Valuation, money

D = Decimal
HUNDRED = D(100)


@dataclass
class ValuationSettings:
    forecast_years: int = 10
    base_fcf_method: str = "average_margin"   # or "latest"
    equity_risk_premium_pct: Decimal = D("5.0")
    beta: Decimal = D("1.0")
    terminal_growth_pct: Decimal = D("3.0")
    stage1_growth_floor_pct: Decimal = D("0.0")
    stage1_growth_cap_pct: Decimal = D("15.0")
    risk_free: RateInput | None = None
    buy_at_or_above_pct: Decimal = D("10.0")
    sell_at_or_below_pct: Decimal = D("-10.0")
    discount_rate_override_pct: Decimal | None = None
    overrides: list[str] = field(default_factory=list)

    @property
    def discount_rate_pct(self) -> Decimal | None:
        if self.discount_rate_override_pct is not None:
            return self.discount_rate_override_pct
        if self.risk_free is None:
            return None
        return self.risk_free.value_pct + self.beta * self.equity_risk_premium_pct


def load_valuation_settings(config_dir: Path) -> ValuationSettings:
    path = Path(config_dir) / "research.toml"
    raw = tomllib.loads(path.read_text()) if path.exists() else {}
    v, r = raw.get("valuation", {}), raw.get("recommendation", {})
    rf = v.get("risk_free")
    s = ValuationSettings()
    for k in ("equity_risk_premium_pct", "beta", "terminal_growth_pct", "stage1_growth_floor_pct",
              "stage1_growth_cap_pct"):
        if k in v:
            setattr(s, k, D(str(v[k])))
    s.forecast_years = int(v.get("forecast_years", s.forecast_years))
    s.base_fcf_method = str(v.get("base_fcf_method", s.base_fcf_method))
    if s.base_fcf_method not in ("average_margin", "latest"):
        raise ValueError("valuation.base_fcf_method must be 'average_margin' or 'latest'")
    for k in ("buy_at_or_above_pct", "sell_at_or_below_pct"):
        if k in r:
            setattr(s, k, D(str(r[k])))
    if rf:
        as_of = rf.get("as_of")
        s.risk_free = RateInput(value_pct=D(str(rf["value_pct"])),
                                as_of=as_of if isinstance(as_of, date) else (date.fromisoformat(as_of) if as_of else None),
                                source=rf.get("source", "configured"), source_url=rf.get("source_url"))
    return s


def _m(by_id: dict[str, CalculatedMetric], name: str, period: str) -> CalculatedMetric | None:
    m = by_id.get(f"metric:{name}:{period}")
    return m if m is not None and m.status == MetricStatus.AVAILABLE and m.value is not None else None


def revenue_cagr_pct(by_id: dict[str, CalculatedMetric], labels: list[str]) -> tuple[Decimal | None, list[str]]:
    first, last = _m(by_id, "revenue", labels[0]), _m(by_id, "revenue", labels[-1])
    if first is None or last is None or first.value <= 0 or last.value <= 0 or len(labels) < 2:
        return None, []
    years = len(labels) - 1
    with localcontext() as ctx:
        ctx.prec = 28
        ratio = last.value / first.value
        cagr = (D(str(float(ratio) ** (1.0 / years))) - 1) * HUNDRED
    return cagr, [first.id, last.id]


def dcf_value(base_fcf: Decimal, g1_pct: Decimal, gt_pct: Decimal, r_pct: Decimal, years: int,
              net_cash: Decimal, shares: Decimal) -> tuple[Decimal, list[DcfYear], Decimal, Decimal, Decimal]:
    """Value per share, projection, PV of explicit FCF, terminal value, PV of terminal value."""
    with localcontext() as ctx:
        ctx.prec = 28
        r, gt, g1 = r_pct / HUNDRED, gt_pct / HUNDRED, g1_pct / HUNDRED
        if r <= gt:
            raise ValueError("discount rate must exceed terminal growth")
        fcf, pv, rows = base_fcf, D(0), []
        for t in range(1, years + 1):
            g = g1 + (gt - g1) * D(t - 1) / D(max(years - 1, 1))
            fcf = fcf * (1 + g)
            df = 1 / (1 + r) ** t
            pv += fcf * df
            rows.append(DcfYear(year=t, growth_pct=g * HUNDRED, fcf=fcf, discount_factor=df, present_value=fcf * df))
        tv = fcf * (1 + gt) / (r - gt)
        pv_tv = tv / (1 + r) ** years
        per_share = (pv + pv_tv + net_cash) / shares
        return per_share, rows, pv, tv, pv_tv


def value(metrics: list[CalculatedMetric], labels: list[str], shares: Decimal | None,
          short_term_investments: Decimal | None, market: MarketSnapshot | None,
          s: ValuationSettings) -> tuple[Valuation, dict[str, list[str]]]:
    """Returns the valuation and the metric IDs each input came from."""
    by_id = {m.id: m for m in metrics}
    latest = labels[-1]
    ids: dict[str, list[str]] = {}
    fcf, cash, debt = _m(by_id, "free_cash_flow", latest), _m(by_id, "cash_and_equivalents", latest), \
        _m(by_id, "interest_bearing_debt", latest)
    cagr, ids["stage1_growth"] = revenue_cagr_pct(by_id, labels)
    r = s.discount_rate_pct
    missing: list[str] = []
    if fcf is None:
        missing.append(f"free cash flow for {latest}")
    base, base_ids, margin = (fcf.value, [fcf.id], None) if fcf is not None else (None, [], None)
    if fcf is not None and s.base_fcf_method == "average_margin":
        pairs = [(_m(by_id, "free_cash_flow", p), _m(by_id, "revenue", p)) for p in labels]
        pairs = [(f, r) for f, r in pairs if f is not None and r is not None and r.value > 0]
        rev_latest = _m(by_id, "revenue", latest)
        if len(pairs) == len(labels) and rev_latest is not None:
            margin = sum(f.value / r.value for f, r in pairs) / len(pairs)
            base = rev_latest.value * margin
            base_ids = [x.id for pr in pairs for x in pr]
        else:
            missing.append("free cash flow and revenue for every year (normalised base)")
    if base is not None and base <= 0:
        return Valuation(available=False, reason="Base free cash flow is not positive, so a free-cash-flow DCF is "
                                                 "not meaningful."), ids
    if cash is None:
        missing.append(f"cash and cash equivalents for {latest}")
    if debt is None:
        missing.append(f"interest-bearing debt for {latest}")
    if shares is None or shares <= 0:
        missing.append("diluted share count")
    if cagr is None:
        missing.append("revenue for the first and last year (growth input)")
    if r is None:
        missing.append("risk-free rate or discount rate")
    if missing:
        return Valuation(available=False, reason="Valuation not performed; missing " + ", ".join(missing) + "."), ids
    g1 = min(max(cagr, s.stage1_growth_floor_pct), s.stage1_growth_cap_pct)
    sti = short_term_investments or D(0)
    net_cash = cash.value + sti - debt.value
    ids.update(base_fcf=base_ids, cash=[cash.id], debt=[debt.id])
    per_share, rows, pv, tv, pv_tv = dcf_value(base, g1, s.terminal_growth_pct, r, s.forecast_years,
                                               net_cash, shares)
    target = per_share.quantize(D("0.01"))
    v = Valuation(available=True, base_fcf=base, base_fcf_metric_id=fcf.id, base_fcf_margin_pct=
                  margin * HUNDRED if margin is not None else None, base_fcf_method=s.base_fcf_method,
                  forecast_years=s.forecast_years, discount_rate_pct=r,
                  terminal_growth_pct=s.terminal_growth_pct, stage1_growth_pct=g1, projection=rows, pv_explicit=pv,
                  terminal_value=tv, pv_terminal=pv_tv, enterprise_value=pv + pv_tv, cash=cash.value,
                  short_term_investments=sti, debt=debt.value, equity_value=pv + pv_tv + net_cash,
                  diluted_shares=shares, value_per_share=per_share, target_price=target)
    for dr in (r - 1, r - D("0.5"), r, r + D("0.5"), r + 1):
        row: dict[str, Decimal | None] = {}
        for gt in (s.terminal_growth_pct - D("0.5"), s.terminal_growth_pct, s.terminal_growth_pct + D("0.5")):
            try:
                row[f"{gt:.1f}"] = dcf_value(base, g1, gt, dr, s.forecast_years, net_cash, shares)[0]
            except ValueError:
                row[f"{gt:.1f}"] = None
        v.sensitivity[f"{dr:.2f}"] = row
    ni, rev = _m(by_id, "net_income", latest), _m(by_id, "revenue", latest)
    if market is not None:
        price = market.price
        v.upside_pct = (target / price - 1) * HUNDRED
        v.implied_stage1_growth_pct = _reverse_dcf(base, s, r, net_cash, shares, price)
        cap_now, cap_tgt = price * shares, target * shares
        mult = []
        if ni is not None and ni.value > 0:
            mult.append(("P/E (diluted, latest FY)", cap_now / ni.value, cap_tgt / ni.value,
                         f"market value of equity / net income {latest} ({ni.id})"))
            ids["pe"] = [ni.id]
        mult.append(("P/FCF (latest FY)", cap_now / fcf.value, cap_tgt / fcf.value,
                     f"market value of equity / free cash flow {latest} ({fcf.id})"))
        mult.append(("FCF yield", fcf.value / cap_now * HUNDRED, fcf.value / cap_tgt * HUNDRED,
                     "free cash flow / market value of equity, %"))
        if rev is not None and rev.value > 0:
            mult.append(("EV / revenue (latest FY)", (cap_now - net_cash) / rev.value, (cap_tgt - net_cash) / rev.value,
                         f"(market value of equity - net cash) / revenue {latest} ({rev.id})"))
        v.multiples = [{"name": n, "at_price": f"{a:.1f}{'%' if 'yield' in n else 'x'}",
                        "at_target": f"{b:.1f}{'%' if 'yield' in n else 'x'}", "basis": basis}
                       for n, a, b, basis in mult]
    return v, ids


def _reverse_dcf(base_fcf: Decimal, s: ValuationSettings, r: Decimal, net_cash: Decimal, shares: Decimal,
                 price: Decimal) -> Decimal | None:
    lo, hi = D("-30"), D("60")
    f = lambda g: dcf_value(base_fcf, g, s.terminal_growth_pct, r, s.forecast_years, net_cash, shares)[0] - price  # noqa: E731
    try:
        if f(lo) > 0 or f(hi) < 0:
            return None
        for _ in range(60):
            mid = (lo + hi) / 2
            if f(mid) > 0:
                hi = mid
            else:
                lo = mid
        return ((lo + hi) / 2).quantize(D("0.1"))
    except ValueError:
        return None


def rate(valuation: Valuation, market: MarketSnapshot | None, s: ValuationSettings) -> Recommendation:
    rule = (f"BUY if the target price is at least {s.buy_at_or_above_pct:.0f}% above the current price, "
            f"SELL if it is at least {abs(s.sell_at_or_below_pct):.0f}% below, otherwise HOLD.")
    if market is None:
        return Recommendation(rating="NOT RATED", rule=rule, reason="No current share price was supplied.")
    if not valuation.available or valuation.upside_pct is None:
        return Recommendation(rating="NOT RATED", rule=rule, reason=valuation.reason or "No valuation.")
    u = valuation.upside_pct
    rating = "BUY" if u >= s.buy_at_or_above_pct else "SELL" if u <= s.sell_at_or_below_pct else "HOLD"
    return Recommendation(rating=rating, rule=rule,
                          reason=f"Target price {valuation.target_price:,.2f} vs current price {money(market.price)} "
                                 f"({u:+.1f}%).")
