"""OFFLINE STUB: a rule-based stand-in for the LLM. It is not an AI model.

It lets the full Stage 2 workflow (tool calls, budgets, verification, report,
review) run offline and in tests without an API key. It behaves like a cautious
junior analyst following a checklist:

1. ask for the flagged metrics and search the focus-year 10-K with the topic's
   search terms (plus an expense breakdown for operating-margin changes);
2. read the top passages;
3. quote a sentence only if the flagged item is its subject ("Revenue increased
   ...", "Additions to property and equipment ..."), it contains explicit
   attribution language ("driven by", "due to", "primarily", "reflecting", ...)
   and, for the flagged metric itself, it moves in the flagged direction.
   Aspects with no such sentence become unresolved questions.

It never writes an analyst interpretation and never reviews narrative support:
the review step records that the LLM review was skipped. Everything it produces
is labelled ``offline-stub`` in findings, reports and run stats.
"""

from __future__ import annotations

import json
import re
from typing import Any

from finsight.llm.provider import LLMTurn, ToolCall, ToolSpec, UsageTracker
from finsight.config import LlmSettings

STUB_NAME = "offline-stub (rule-based, no LLM)"
CAUSAL = re.compile(r"\b(driven|due to|primarily|reflecting|reflected|as a result|because|attributable|"
                    r"resulted from|to fund|used .* proceeds)\b", re.IGNORECASE)
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z$])")

FAVORABLE_UP = {"revenue", "operating_margin", "free_cash_flow", "net_income", "operating_income"}
UP = re.compile(r"\b(increased|increase|grew|rose|higher|up from|improved|expanded)\b", re.IGNORECASE)
DOWN = re.compile(r"\b(decreased|decrease|declined|fell|lower|down from|worsened|contracted)\b", re.IGNORECASE)


def explains(sentence: str, aspect: dict, direction: str) -> bool:
    """Subject is the aspect, the sentence attributes a cause, and (if required) the direction matches."""
    low = sentence.strip().lower()
    if not any(re.match(rx, low) for rx in aspect["subjects"]):
        return False
    if not CAUSAL.search(sentence):
        return False
    if aspect.get("direction") and direction in ("increase", "decrease"):
        head = sentence[:160]
        return bool((UP if direction == "increase" else DOWN).search(head))
    return True


class _StubInvestigation:
    def __init__(self, usage: UsageTracker, purpose: str, ctx: dict[str, Any]):
        self.usage, self.purpose, self.ctx = usage, purpose, ctx
        self.phase = 0
        self.read: dict[str, str] = {}
        self.n = 0

    def _id(self) -> str:
        self.n += 1
        return f"stub_{self.n}"

    def _turn(self, calls: list[ToolCall]) -> LLMTurn:
        self.usage.check()
        self.usage.record(self.purpose, 0, 0)
        return LLMTurn(text="", tool_calls=calls, stop_reason="tool_use")

    def send(self, content) -> LLMTurn:
        flag, topic = self.ctx["flag"], self.ctx["topic"]
        if self.phase == 0:
            self.phase = 1
            calls = []
            for mid in flag["metric_ids"]:
                _, name, period = mid.split(":", 2)
                calls.append(ToolCall(self._id(), "get_validated_metric", {"name": name, "period": period}))
            calls.append(ToolCall(self._id(), "search_filing_sections", {
                "query": topic["query"], "sections": ["item_7", "item_8"],
                "fiscal_period": self.ctx["focus_period"], "limit": 8}))
            if topic["key"] == "operating_margin":
                calls.append(ToolCall(self._id(), "compare_financial_components", {
                    "prior_period": self.ctx["prior_period"], "current_period": self.ctx["focus_period"]}))
            return self._turn(calls)
        if self.phase == 1 and isinstance(content, list):
            self.phase = 2
            ids = []
            for r in content:
                try:
                    payload = json.loads(r.content)
                except (json.JSONDecodeError, TypeError):
                    continue
                for hit in payload.get("results", []) if isinstance(payload, dict) else []:
                    ids.append(hit["passage_id"])
            if ids:
                return self._turn([ToolCall(self._id(), "read_source_passage", {"passage_id": i}) for i in ids[:6]])
            return self._record()
        if self.phase == 2 and isinstance(content, list):
            for r in content:
                try:
                    payload = json.loads(r.content)
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(payload, dict) and "untrusted_filing_text" in payload:
                    self.read[payload["passage_id"]] = payload["untrusted_filing_text"]
        return self._record()

    def _record(self) -> LLMTurn:
        self.phase = 9
        topic, flag = self.ctx["topic"], self.ctx["flag"]
        quotes, found, unresolved = [], [], []
        for aspect, spec in topic["aspects"].items():
            best = None
            for pid, text in self.read.items():
                for sent in (s.strip() for line in text.split("\n") for s in _SENT.split(line)):
                    if len(sent) >= self.ctx.get("min_quote_chars", 20) and explains(sent, spec, flag["direction"]):
                        best = best or (pid, sent)
            if best and best[1] not in [q["quote"] for q in quotes]:
                quotes.append({"passage_id": best[0], "quote": best[1]})
                found.append(best[1])
            elif not best:
                unresolved.append(f"The retrieved 10-K text does not explain the change in {aspect}.")
        status = ("supported" if quotes and not unresolved else "partially_supported" if quotes else "unsupported")
        key, direction = topic["key"], flag["direction"]
        if direction == "transition":
            assessment = "favorable" if flag["observed"] and float(flag["observed"]) > 0 else "unfavorable"
        elif key in FAVORABLE_UP:
            assessment = "favorable" if direction == "increase" else "unfavorable"
        else:
            assessment = "ambiguous"
        rec = {
            "management_explanation": ("The 10-K states: " + " ".join(found)) if found else None,
            "quotes": quotes, "analyst_interpretation": None, "unresolved_questions": unresolved,
            "assessment": assessment, "proposed_evidence_status": status,
        }
        self.usage.check()
        self.usage.record(self.purpose, 0, 0)
        return LLMTurn(text="", tool_calls=[ToolCall(self._id(), "record_finding", rec)], stop_reason="tool_use")


class OfflineStubProvider:
    name = STUB_NAME
    is_stub = True

    def __init__(self, settings: LlmSettings | None = None):
        self.usage = UsageTracker(settings or LlmSettings())

    def start(self, purpose: str, system: str, tools: list[ToolSpec], context: dict[str, Any] | None = None):
        if purpose.startswith("investigate"):
            return _StubInvestigation(self.usage, purpose, context or {})
        raise NotImplementedError("the offline stub does not perform narrative review")
