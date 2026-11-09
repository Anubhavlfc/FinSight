"""Load runtime settings and field mappings from ``config/``.

Environment variables:

* ``FINSIGHT_SEC_USER_AGENT`` - required for live SEC access. Identifies you to the SEC.
* ``FINSIGHT_CONFIG_DIR`` - alternative config directory.
* ``FINSIGHT_HOME`` - base directory for cache, database and outputs (default: cwd).
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
    materiality_pct_of_revenue: float = 0.5


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
        )
    return Mappings(fields=fields, company_overrides_applied=tuple(applied))
