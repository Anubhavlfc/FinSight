"""Anthropic provider against a fake SDK client (no network), provider choice, usage budgets."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import anthropic
import pytest

from finsight.config import LlmSettings, load_settings
from finsight.contracts import AnalysisRequest
from finsight.investigator.tools import TOOL_SPECS
from finsight.llm.factory import make_provider
from finsight.llm.provider import (
    AnthropicProvider,
    BudgetExceeded,
    LLMConfigurationError,
    LLMRefusal,
    LLMTransientError,
    ToolResult,
    UsageTracker,
)
from finsight.llm.stub import OfflineStubProvider


def block(type_, **kw):
    return SimpleNamespace(type=type_, **kw)


def response(content, stop="tool_use"):
    return SimpleNamespace(content=content, stop_reason=stop,
                           usage=SimpleNamespace(input_tokens=1200, output_tokens=300, cache_read_input_tokens=1000))


class FakeClient:
    def __init__(self, responses=None, error=None):
        self.calls, self.responses, self.error = [], list(responses or []), error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return self.responses.pop(0)


def test_request_shape_and_history():
    thinking = block("thinking", thinking="", signature="sig")
    tool = block("tool_use", id="toolu_1", name="search_filing_sections", input={"query": "revenue"})
    client = FakeClient([response([thinking, tool]), response([block("text", text="done")], stop="end_turn")])
    p = AnthropicProvider(LlmSettings(effort="high"), "claude-opus-5-5", client=client)
    s = p.start("investigate:x", "SYSTEM", TOOL_SPECS)
    t1 = s.send("hello")
    assert t1.tool_calls[0].name == "search_filing_sections" and t1.tool_calls[0].input == {"query": "revenue"}
    kw = client.calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["output_config"] == {"effort": "high"}
    assert kw["betas"] == ["server-side-fallback-2026-07-01"] and kw["fallbacks"] == "default"
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    names = [t["name"] for t in kw["tools"]]
    assert names == [t.name for t in TOOL_SPECS]
    assert kw["tools"][0]["input_schema"]["additionalProperties"] is False
    assert kw["tool_choice"] == {"type": "auto"}
    s.send([ToolResult("toolu_1", '{"results": []}', is_error=True)])
    msgs = client.calls[1]["messages"]
    assert msgs[1] == {"role": "assistant", "content": [thinking, tool]}  # passed back unchanged
    assert msgs[2]["content"][0] == {"type": "tool_result", "tool_use_id": "toolu_1",
                                     "content": '{"results": []}', "is_error": True}
    assert p.usage.calls == 2 and p.usage.input_tokens == 2400 and p.usage.cache_read_tokens == 2000
    assert p.usage.summary(p.name, False)["estimated_cost_usd"] > 0


def test_fallback_can_be_switched_off():
    client = FakeClient([response([block("text", text="x")], stop="end_turn")])
    AnthropicProvider(LlmSettings(server_side_fallback=False), "m", client=client).start("r", "S", []).send("hi")
    assert "betas" not in client.calls[0] and "fallbacks" not in client.calls[0]


def test_refusal_raises():
    client = FakeClient([response([], stop="refusal")])
    with pytest.raises(LLMRefusal):
        AnthropicProvider(LlmSettings(), "m", client=client).start("r", "S", []).send("hi")


@pytest.mark.parametrize("error, expected", [
    (anthropic.APIConnectionError(request=__import__("httpx2").Request("POST", "https://x")), LLMTransientError),
])
def test_sdk_errors_are_mapped(error, expected):
    with pytest.raises(expected):
        AnthropicProvider(LlmSettings(), "m", client=FakeClient(error=error)).start("r", "S", []).send("hi")


def test_status_errors_are_mapped():
    import httpx2

    req = httpx2.Request("POST", "https://api.example/v1/messages")
    for status, cls, expected in [(429, anthropic.RateLimitError, LLMTransientError),
                                  (401, anthropic.AuthenticationError, LLMConfigurationError),
                                  (529, anthropic.InternalServerError, LLMTransientError)]:
        err = cls("boom", response=httpx2.Response(status, request=req), body=None)
        with pytest.raises(expected):
            AnthropicProvider(LlmSettings(), "m", client=FakeClient(error=err)).start("r", "S", []).send("hi")


def test_budget_is_checked_before_each_call():
    u = UsageTracker(LlmSettings(max_model_calls=1))
    u.record("x", 10, 10)
    with pytest.raises(BudgetExceeded):
        u.check()
    u = UsageTracker(LlmSettings(max_total_tokens=10_000))
    u.record("x", 9_000, 1_000)
    with pytest.raises(BudgetExceeded):
        u.check()


def test_provider_choice(monkeypatch, tmp_path):
    s = load_settings(home=tmp_path)
    req = dict(identifier="X", cutoff_date=date(2026, 1, 1))
    p, note = make_provider(s, AnalysisRequest(offline=True, **req))
    assert isinstance(p, OfflineStubProvider) and "offline" in note
    p, note = make_provider(s, AnalysisRequest(**req))
    assert isinstance(p, OfflineStubProvider) and "ANTHROPIC_API_KEY" in note
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    monkeypatch.setenv("FINSIGHT_LLM_MODEL", "claude-sonnet-5-5")
    p, _ = make_provider(s, AnalysisRequest(**req))
    assert isinstance(p, AnthropicProvider) and p.model == "claude-sonnet-5-5"
    p, _ = make_provider(s, AnalysisRequest(llm="stub", **req))
    assert isinstance(p, OfflineStubProvider)


def test_finsight_key_fallback(monkeypatch, tmp_path):
    s = load_settings(home=tmp_path)
    monkeypatch.setenv("FINSIGHT_ANTHROPIC_API_KEY", "fallback-key-not-used")
    p, _ = make_provider(s, AnalysisRequest(identifier="X", cutoff_date=date(2026, 1, 1)))
    assert isinstance(p, AnthropicProvider)
    assert p._client.api_key == "fallback-key-not-used"
