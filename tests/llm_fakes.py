"""Test doubles for the LLM layer. No network, no API key.

``ScriptedProvider`` stands in for a real model (``is_stub = False``) so the code
paths that only run with Claude (LLM review, revision routing) are exercised.
A script is a function ``(purpose, context, content, turn_number) -> LLMTurn``.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from finsight.config import LlmSettings
from finsight.llm.provider import LLMTurn, ToolCall, ToolSpec, UsageTracker

Script = Callable[[str, dict, Any, int], LLMTurn]


def tool_turn(*calls: tuple[str, dict]) -> LLMTurn:
    return LLMTurn(text="", tool_calls=[ToolCall(f"call_{i}_{name}", name, inp) for i, (name, inp) in
                                        enumerate(calls)], stop_reason="tool_use", input_tokens=100,
                   output_tokens=50)


def record(**kw) -> LLMTurn:
    base = {"management_explanation": None, "quotes": [], "analyst_interpretation": None,
            "unresolved_questions": [], "assessment": "ambiguous", "proposed_evidence_status": "unsupported"}
    base.update(kw)
    return tool_turn(("record_finding", base))


def results_json(content) -> list[dict]:
    out = []
    for r in content if isinstance(content, list) else []:
        try:
            out.append(json.loads(r.content))
        except (json.JSONDecodeError, TypeError):
            out.append({})
    return out


class _Session:
    def __init__(self, provider: "ScriptedProvider", purpose: str, context: dict, tools: list[ToolSpec]):
        self.p, self.purpose, self.context, self.tools = provider, purpose, context, tools
        self.n = 0
        self.received: list[Any] = []

    def send(self, content) -> LLMTurn:
        self.p.usage.check()
        self.received.append(content)
        turn = self.p.script(self.purpose, self.context, content, self.n)
        self.n += 1
        self.p.usage.record(self.purpose, turn.input_tokens, turn.output_tokens)
        return turn


class ScriptedProvider:
    is_stub = False

    def __init__(self, script: Script, settings: LlmSettings | None = None, name: str = "scripted-test-llm"):
        self.script = script
        self.name = name
        self.usage = UsageTracker(settings or LlmSettings())
        self.sessions: list[_Session] = []

    def start(self, purpose: str, system: str, tools: list[ToolSpec], context: dict | None = None):
        s = _Session(self, purpose, context or {}, tools)
        self.sessions.append(s)
        return s
