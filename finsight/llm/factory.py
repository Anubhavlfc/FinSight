"""Choose the LLM provider for a run."""

from __future__ import annotations

import os

from finsight.config import Settings, llm_model
from finsight.contracts import AnalysisRequest
from finsight.llm.provider import AnthropicProvider, LLMProvider
from finsight.llm.stub import OfflineStubProvider


# FINSIGHT_ANTHROPIC_API_KEY is a fallback for hosts that reserve or strip ANTHROPIC_API_KEY
# (e.g. Claude Code cloud sessions do not pass it through to the process).
CREDENTIAL_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "FINSIGHT_ANTHROPIC_API_KEY")


def has_anthropic_credentials() -> bool:
    return any(os.environ.get(k, "").strip() for k in CREDENTIAL_VARS)


def make_provider(settings: Settings, request: AnalysisRequest) -> tuple[LLMProvider, str]:
    """Returns the provider and a one-line note on why it was chosen."""
    choice = request.llm if request.llm != "auto" else settings.llm.provider
    if choice == "stub":
        return OfflineStubProvider(settings.llm), "offline stub requested"
    if choice == "anthropic":
        return AnthropicProvider(settings.llm, llm_model(settings)), "Anthropic requested"
    if request.offline:
        return OfflineStubProvider(settings.llm), "offline run: the rule-based stub was used (no LLM)"
    if not has_anthropic_credentials():
        return OfflineStubProvider(settings.llm), ("no ANTHROPIC_API_KEY set: the rule-based offline stub was used "
                                                   "(no LLM)")
    return AnthropicProvider(settings.llm, llm_model(settings)), f"Anthropic API, model {llm_model(settings)}"
