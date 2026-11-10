"""Load runtime settings and field mappings from ``config/``.

Environment variables:

* ``FINSIGHT_SEC_USER_AGENT`` - required for live SEC access. Identifies you to the SEC.
* ``FINSIGHT_CONFIG_DIR`` - alternative config directory.
* ``FINSIGHT_HOME`` - base directory for cache, database and outputs (default: cwd).
* ``FINSIGHT_LLM_MODEL`` - Claude model for investigation and review (default in settings.toml).
* ``ANTHROPIC_API_KEY`` - read by the Anthropic SDK itself; never stored or logged by FinSight.
* ``FINSIGHT_ANTHROPIC_API_KEY`` - fallback key name for hosts that strip ``ANTHROPIC_API_KEY``.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_DIR = REPO_ROOT / "config"
SEC_USER_AGENT_ENV = "FINSIGHT_SEC_USER_AGENT"
LLM_MODEL_ENV = "FINSIGHT_LLM_MODEL"


class SecSettings(BaseModel):
    max_requests_per_second: float = 5.0
    timeout_seconds: float = 30.0
    max_attempts: int = Field(default=3, ge=1, le=10)
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 30.0
    max_retry_after_seconds: float = 60.0
    submissions_ttl_seconds: int = 3600
    max_history_pages: int = 3


class PeriodSettings(BaseModel):
    annual_min_days: int = 350
    annual_max_days: int = 380
    end_date_tolerance_days: int = 3
    min_gap_days: int = 350
    max_gap_days: int = 380


class ValidationSettings(BaseModel):
    abs_tolerance_usd: float = 5_000_000
    rel_tolerance: float = 0.001
    max_recovery_attempts: int = Field(default=1, ge=0, le=5)


class AnalysisSettings(BaseModel):
    revenue_change_pct: float = 10.0
    operating_margin_change_pp: float = 3.0
    free_cash_flow_change_pct: float = 20.0
    debt_increase_pct: float = 25.0
    net_income_change_pct: float = 20.0
    capex_change_pct: float = 25.0
    materiality_pct_of_revenue: float = 0.5


class InvestigationSettings(BaseModel):
    enabled: bool = True
    max_findings: int = Field(default=8, ge=0, le=30, description="Change flags investigated, in rank order")
    max_steps_per_finding: int = Field(default=3, ge=1, le=10,
                                       description="Model turns that may call investigative tools, per finding")
    max_tool_calls_per_step: int = Field(default=6, ge=1, le=20)
    max_additional_sources: int = Field(default=2, ge=0, le=5, description="Extra filings per run")
    search_results: int = Field(default=5, ge=1, le=10)
    passage_max_chars: int = Field(default=1200, ge=300, le=4000)
    min_quote_chars: int = Field(default=20, ge=5)
    max_revision_cycles: int = Field(default=2, ge=0, le=5)


class LlmSettings(BaseModel):
    provider: Literal["auto", "anthropic", "stub"] = "auto"
    model: str = "claude-opus-5-5"          # overridden by FINSIGHT_LLM_MODEL
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    max_tokens: int = Field(default=16000, ge=1024)
    timeout_seconds: float = 300.0
    max_retries: int = Field(default=2, ge=0, le=6, description="SDK retries for 429/5xx/connection errors")
    server_side_fallback: bool = True       # re-run a refused request on a fallback model (beta)
    max_model_calls: int = Field(default=60, ge=1, description="Per run, all stages")
    max_total_tokens: int = Field(default=1_500_000, ge=10_000, description="Input + output tokens per run")
    # USD per million tokens, for the cost ESTIMATE only. Check current pricing before relying on it.
    price_input_per_mtok: float = 4.0
    price_output_per_mtok: float = 20.0


class UnsupportedSicRange(BaseModel):
    low: int
    high: int
    profile: str
    reason: str


class ScopeSettings(BaseModel):
    unsupported_sic_ranges: list[UnsupportedSicRange] = Field(default_factory=list)


class RuntimeSettings(BaseModel):
    cache_dir: str = ".finsight/cache"
    database: str = ".finsight/finsight.sqlite3"
    output_dir: str = "out"
    fixtures_dir: str = "fixtures/demo"


class Settings(BaseModel):
    sec: SecSettings = Field(default_factory=SecSettings)
    periods: PeriodSettings = Field(default_factory=PeriodSettings)
    validation: ValidationSettings = Field(default_factory=ValidationSettings)
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings)
    scope: ScopeSettings = Field(default_factory=ScopeSettings)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    investigation: InvestigationSettings = Field(default_factory=InvestigationSettings)
    llm: LlmSettings = Field(default_factory=LlmSettings)
    config_dir: Path = DEFAULT_CONFIG_DIR
    home: Path = Field(default_factory=Path.cwd)

    def path(self, relative: str) -> Path:
        p = Path(relative)
        return p if p.is_absolute() else self.home / p

    @property
    def cache_dir(self) -> Path:
        return self.path(self.runtime.cache_dir)

    @property
    def database_path(self) -> Path:
        return self.path(self.runtime.database)

    @property
    def output_dir(self) -> Path:
        return self.path(self.runtime.output_dir)

    @property
    def fixtures_dir(self) -> Path:
        p = Path(self.runtime.fixtures_dir)
        return p if p.is_absolute() else REPO_ROOT / p


def load_settings(config_dir: Path | None = None, home: Path | None = None) -> Settings:
    config_dir = Path(config_dir or os.environ.get("FINSIGHT_CONFIG_DIR") or DEFAULT_CONFIG_DIR)
    home = Path(home or os.environ.get("FINSIGHT_HOME") or Path.cwd())
    data: dict = {}
    settings_file = config_dir / "settings.toml"
    if settings_file.exists():
        data = tomllib.loads(settings_file.read_text())
    return Settings(**data, config_dir=config_dir, home=home)


def llm_model(settings: "Settings") -> str:
    return os.environ.get(LLM_MODEL_ENV, "").strip() or settings.llm.model


def sec_user_agent() -> str | None:
    value = os.environ.get(SEC_USER_AGENT_ENV, "").strip()
    return value or None


# --------------------------------------------------------------------------- mappings


@dataclass(frozen=True)
class FieldMapping:
    name: str
    period_type: Literal["duration", "instant"]
    tags: tuple[str, ...]
    sign: Literal["as_reported", "positive_outflow"] = "as_reported"
    required_for: tuple[str, ...] = ()
    optional: bool = False
    derive_from: str | None = None
    missing_severity: Literal["info", "warning"] = "warning"
    competing_difference_severity: Literal["info", "warning"] = "warning"


@dataclass(frozen=True)
class Mappings:
    fields: dict[str, FieldMapping] = field(default_factory=dict)
    company_overrides_applied: tuple[str, ...] = ()

    def __getitem__(self, name: str) -> FieldMapping:
        return self.fields[name]


def load_mappings(config_dir: Path | None = None, cik: str | None = None) -> Mappings:
    config_dir = Path(config_dir or DEFAULT_CONFIG_DIR)
    raw = tomllib.loads((config_dir / "mappings.toml").read_text())["fields"]
    overrides: dict = {}
    applied: list[str] = []
    if cik:
        company_file = config_dir / "companies" / f"{cik}.toml"
        if company_file.exists():
            overrides = tomllib.loads(company_file.read_text()).get("fields", {})
            applied = sorted(overrides)
    fields: dict[str, FieldMapping] = {}
    for name, spec in raw.items():
        tags = list(overrides.get(name, {}).get("tags", [])) + [
            t for t in spec["tags"] if t not in overrides.get(name, {}).get("tags", [])
        ]
        fields[name] = FieldMapping(
            name=name,
            period_type=spec["period_type"],
            tags=tuple(tags),
            sign=spec.get("sign", "as_reported"),
            required_for=tuple(spec.get("required_for", [])),
            optional=bool(spec.get("optional", False)),
            derive_from=spec.get("derive_from"),
            missing_severity=spec.get("missing_severity", "warning"),
            competing_difference_severity=spec.get("competing_difference_severity", "warning"),
        )
    return Mappings(fields=fields, company_overrides_applied=tuple(applied))
