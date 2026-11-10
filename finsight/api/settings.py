"""API runtime settings, read from environment variables (see ``.env.example``)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from finsight.config import REPO_ROOT


def _int(name: str, default: int, low: int, high: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(low, min(high, value))


def _list(name: str) -> list[str]:
    return [v.strip().rstrip("/") for v in os.environ.get(name, "").split(",") if v.strip()]


@dataclass(frozen=True)
class ApiSettings:
    cors_origins: list[str] = field(default_factory=list)
    max_concurrent_runs: int = 2
    max_queued_runs: int = 4
    runs_per_hour: int = 6            # new analyses per client address
    lookups_per_minute: int = 60      # search / profile requests per client address
    run_budget_seconds: int = 900
    public_investigator: str = "auto"  # auto | rule_based
    reviewer_token: str | None = None
    trusted_proxy_hops: int = 0
    web_dist: Path | None = None
    repo_url: str = "https://github.com/Anubhavlfc/FinSight"

    @classmethod
    def from_env(cls) -> "ApiSettings":
        dist_env = os.environ.get("FINSIGHT_WEB_DIST", "").strip()
        dist = Path(dist_env) if dist_env else REPO_ROOT / "web" / "dist"
        investigator = os.environ.get("FINSIGHT_PUBLIC_INVESTIGATOR", "auto").strip().lower()
        return cls(
            cors_origins=_list("FINSIGHT_CORS_ORIGINS"),
            max_concurrent_runs=_int("FINSIGHT_MAX_CONCURRENT_RUNS", 2, 1, 8),
            max_queued_runs=_int("FINSIGHT_MAX_QUEUED_RUNS", 4, 0, 50),
            runs_per_hour=_int("FINSIGHT_RUNS_PER_HOUR", 6, 1, 1000),
            lookups_per_minute=_int("FINSIGHT_LOOKUPS_PER_MINUTE", 60, 5, 6000),
            run_budget_seconds=_int("FINSIGHT_RUN_BUDGET_SECONDS", 900, 30, 7200),
            public_investigator="rule_based" if investigator in ("rule_based", "stub") else "auto",
            reviewer_token=os.environ.get("FINSIGHT_REVIEWER_TOKEN", "").strip() or None,
            trusted_proxy_hops=_int("FINSIGHT_TRUSTED_PROXY_HOPS", 0, 0, 5),
            web_dist=dist if dist.is_dir() else None,
            repo_url=os.environ.get("FINSIGHT_REPO_URL", "").strip() or cls.repo_url,
        )
