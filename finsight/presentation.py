"""Number formatting for reports, and the parser the reviewer uses to check it.

The writer only puts numbers into prose through ``fmt_metric``; the reviewer
parses every number back out of a claim with ``extract_numbers`` and checks it
against the metrics the claim cites. Keeping both here makes the round trip
testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from finsight.contracts import CalculatedMetric

BILLION = Decimal(1_000_000_000)
MILLION = Decimal(1_000_000)


def fmt_usd(v: Decimal, signed: bool = False) -> str:
    sign = "-" if v < 0 else ("+" if signed and v > 0 else "")
    a = abs(v)
    if a >= 10 * BILLION:
        return f"{sign}${a / BILLION:,.1f} billion"
    return f"{sign}${a / MILLION:,.0f} million"


def fmt_metric(m: CalculatedMetric, signed: bool | None = None) -> str:
    if m.value is None:
        return {"not_applicable": "n/a", "suppressed": "suppressed"}.get(m.status.value, "unavailable")
    v = m.value
    if m.unit == "USD":
        return fmt_usd(v, signed=bool(signed))
    if m.unit == "percentage_points":
        return f"{v:+.1f} percentage points"
    if m.unit == "percent":
        if signed or (signed is None and ("growth" in m.name)):
            return f"{v:+.1f}%"
        return f"{v:.1f}%"
    return f"{v:.2f}x"


@dataclass
class Number:
    value: Decimal
    decimals: int
    kind: str  # usd_million | usd_billion | percent | pp | x | plain
    text: str


_NUM = re.compile(
    r"(?P<sign>[-+−])?\s?(?P<usd>\$)?(?P<num>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<unit>%|percentage points?|pp\b|billion|million|x\b)?", re.IGNORECASE)
_STRIP = [
    re.compile(r"FY\d{4}(?:-\d{2}-\d{2})?"),  # fiscal period labels
    re.compile(r"\[(?:P|M)\d+\]"),             # citation markers
    re.compile(r"“[^”]*”|\"[^\"]*\""),         # quoted filing text is checked separately
    re.compile(r"\b(?:Note|Item)\s+\d+[A-C]?\b", re.IGNORECASE),
    re.compile(r"\b(?:19|20)\d{2}\b"),         # calendar years
]


def strip_non_claim_numbers(text: str) -> str:
    for rx in _STRIP:
        text = rx.sub(" ", text)
    return text


def extract_numbers(text: str) -> list[Number]:
    out = []
    for m in _NUM.finditer(strip_non_claim_numbers(text)):
        raw = m.group("num").replace(",", "")
        dec = len(raw.split(".")[1]) if "." in raw else 0
        val = Decimal(raw)
        if m.group("sign") in ("-", "−"):
            val = -val
        unit = (m.group("unit") or "").lower()
        kind = ("usd_billion" if unit == "billion" else "usd_million" if unit == "million"
                else "percent" if unit == "%" else "pp" if unit.startswith("percentage") or unit == "pp"
                else "x" if unit == "x" else "plain")
        out.append(Number(val, dec, kind, m.group(0).strip()))
    return out


def number_matches_metric(n: Number, m: CalculatedMetric) -> bool:
    if m.value is None:
        return False
    tol = Decimal("0.5") * (Decimal(10) ** -n.decimals) + Decimal("1e-9")
    if n.kind == "usd_million" and m.unit == "USD":
        cands = [m.value / MILLION]
    elif n.kind == "usd_billion" and m.unit == "USD":
        cands = [m.value / BILLION]
    elif n.kind == "percent" and m.unit == "percent":
        cands = [m.value]
    elif n.kind == "pp" and m.unit == "percentage_points":
        cands = [m.value]
    elif n.kind == "x" and m.unit in ("x", "ratio"):
        cands = [m.value]
    else:
        return False
    # Signs are often expressed in words ("fell 12%"), so compare magnitudes as well.
    return any(abs(n.value - c) <= tol or abs(abs(n.value) - abs(c)) <= tol for c in cands)
