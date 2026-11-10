"""A small provider interface between FinSight and an LLM.

The investigator and reviewer only see ``LLMProvider`` / ``LLMSession``. Two
implementations exist:

* ``AnthropicProvider`` - the official Anthropic SDK (``anthropic`` package).
  Model from ``FINSIGHT_LLM_MODEL`` (default in settings.toml), credentials
  resolved by the SDK itself (``ANTHROPIC_API_KEY`` etc.) and never logged.
* ``OfflineStubProvider`` (``finsight.llm.stub``) - a deterministic, rule-based
  stand-in so the whole workflow runs offline without an API key. Its output is
  labelled as such everywhere it appears.

The orchestrator owns the loop: each ``send`` is one model call, counted
against the run budget in ``UsageTracker`` before it is made.
"""

from __future__ import annotations

import os

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel

from finsight.config import LlmSettings
from finsight.errors import FinSightError

log = logging.getLogger(__name__)


class LLMError(FinSightError):
    """Base class for provider failures."""


class LLMTransientError(LLMError):
    """Rate limit, overload, network: persisted after the SDK's bounded retries. Resumable."""


class LLMConfigurationError(LLMError):
    """Bad credentials, unknown model, malformed request. Needs a human fix."""


class LLMRefusal(LLMError):
    """The model declined the request (stop_reason == "refusal")."""


class BudgetExceeded(LLMError):
    """The run's model-call or token budget is used up. Stop gracefully, keep partial results."""


@dataclass
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]

    def json_schema(self) -> dict:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return schema


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolResult:
    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass
class LLMTurn:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class UsageTracker:
    settings: LlmSettings
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    by_purpose: dict[str, int] = field(default_factory=dict)

    def check(self) -> None:
        if self.calls >= self.settings.max_model_calls:
            raise BudgetExceeded(f"model-call budget of {self.settings.max_model_calls} used up")
        if self.input_tokens + self.output_tokens >= self.settings.max_total_tokens:
            raise BudgetExceeded(f"token budget of {self.settings.max_total_tokens:,} used up")

    def record(self, purpose: str, input_tokens: int, output_tokens: int, cache_read: int = 0) -> None:
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cache_read_tokens += cache_read
        self.by_purpose[purpose] = self.by_purpose.get(purpose, 0) + 1

    def summary(self, provider_name: str, is_stub: bool) -> dict[str, Any]:
        cost = (self.input_tokens * self.settings.price_input_per_mtok
                + self.output_tokens * self.settings.price_output_per_mtok) / 1_000_000
        return {
            "provider": provider_name, "is_stub": is_stub, "model_calls": self.calls,
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens, "calls_by_purpose": dict(self.by_purpose),
            "estimated_cost_usd": 0.0 if is_stub else round(cost, 4),
            "cost_note": "no LLM used" if is_stub else
            "estimate from configured per-token prices; cache discounts not applied",
        }

    def restore(self, saved: dict[str, Any]) -> None:
        self.calls = saved.get("model_calls", 0)
        self.input_tokens = saved.get("input_tokens", 0)
        self.output_tokens = saved.get("output_tokens", 0)
        self.cache_read_tokens = saved.get("cache_read_tokens", 0)
        self.by_purpose = dict(saved.get("calls_by_purpose", {}))


class LLMSession(Protocol):
    def send(self, content: str | list[ToolResult]) -> LLMTurn: ...


class LLMProvider(Protocol):
    name: str
    is_stub: bool
    usage: UsageTracker

    def start(self, purpose: str, system: str, tools: list[ToolSpec],
              context: dict[str, Any] | None = None) -> LLMSession:
        """Open a conversation. ``context`` is structured data the stub may use; real models
        receive the same information in the prompt text."""
        ...


# --------------------------------------------------------------------------- Anthropic


class _AnthropicSession:
    def __init__(self, provider: "AnthropicProvider", purpose: str, system: str, tools: list[ToolSpec]):
        self.p = provider
        self.purpose = purpose
        # Stable prefix (tools, then system) so repeated sessions can reuse the prompt cache.
        self.system = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        self.tools = [{"name": t.name, "description": t.description, "input_schema": t.json_schema()}
                      for t in tools]
        self.messages: list[dict] = []

    def send(self, content: str | list[ToolResult]) -> LLMTurn:
        if isinstance(content, str):
            self.messages.append({"role": "user", "content": content})
        else:
            self.messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": r.tool_call_id, "content": r.content,
                 **({"is_error": True} if r.is_error else {})} for r in content]})
        self.p.usage.check()
        resp = self.p._create(system=self.system, tools=self.tools, messages=self.messages)
        u = resp.usage
        self.p.usage.record(self.purpose, getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0,
                            getattr(u, "cache_read_input_tokens", 0) or 0)
        if resp.stop_reason == "refusal":
            raise LLMRefusal(f"model declined the {self.purpose} request")
        # Pass the full content back unchanged (thinking blocks included) on the next turn.
        self.messages.append({"role": "assistant", "content": resp.content})
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [ToolCall(id=b.id, name=b.name, input=_as_dict(b.input))
                 for b in resp.content if b.type == "tool_use"]
        return LLMTurn(text=text, tool_calls=calls, stop_reason=resp.stop_reason or "",
                       input_tokens=getattr(u, "input_tokens", 0) or 0,
                       output_tokens=getattr(u, "output_tokens", 0) or 0)


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


class AnthropicProvider:
    is_stub = False

    def __init__(self, settings: LlmSettings, model: str, client: Any = None):
        self.settings = settings
        self.model = model
        self.name = f"anthropic:{model}"
        self.usage = UsageTracker(settings)
        if client is None:
            import anthropic

            kwargs: dict[str, Any] = {}
            fallback_key = os.environ.get("FINSIGHT_ANTHROPIC_API_KEY", "").strip()
            if fallback_key and not os.environ.get("ANTHROPIC_API_KEY", "").strip():
                kwargs["api_key"] = fallback_key
            client = anthropic.Anthropic(timeout=settings.timeout_seconds, max_retries=settings.max_retries,
                                         **kwargs)
        self._client = client

    def start(self, purpose: str, system: str, tools: list[ToolSpec],
              context: dict[str, Any] | None = None) -> _AnthropicSession:
        return _AnthropicSession(self, purpose, system, tools)

    def _create(self, **kwargs):
        import anthropic

        params = dict(model=self.model, max_tokens=self.settings.max_tokens,
                      output_config={"effort": self.settings.effort}, tool_choice={"type": "auto"}, **kwargs)
        try:
            if self.settings.server_side_fallback:
                # On a policy refusal the API re-runs the request on a fallback model in the same call.
                return self._client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **params)
            return self._client.messages.create(**params)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.NotFoundError,
                anthropic.BadRequestError) as exc:
            raise LLMConfigurationError(f"Anthropic API rejected the request: {exc.__class__.__name__}: "
                                        f"{getattr(exc, 'message', exc)}") from exc
        except anthropic.RateLimitError as exc:
            raise LLMTransientError("Anthropic API rate limit persisted after retries") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise LLMTransientError(f"Anthropic API error {exc.status_code} persisted after retries") from exc
            raise LLMConfigurationError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMTransientError("could not reach the Anthropic API") from exc
