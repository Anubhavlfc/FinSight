"""Build, store and render a research report for a run. Shared by the CLI, dashboard and exports."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from finsight.config import Settings
from finsight.contracts import WorkflowRun
from finsight.llm.factory import make_provider
from finsight.research.builder import ResearchError, build_research
from finsight.research.models import MarketSnapshot, ResearchReport
from finsight.research.pdf import RenderStats, render_pdf
from finsight.sec.sources import DataSource
from finsight.storage import Store

CHECKPOINT = "research"
PDF_NAME = "research_report.pdf"


def load_market_file(path: Path, ticker: str) -> MarketSnapshot | None:
    """A JSON file holding one snapshot or a list of them; returns the one for ``ticker``."""
    raw = json.loads(Path(path).read_text())
    items = raw if isinstance(raw, list) else raw.get("snapshots", [raw])
    for it in items:
        if str(it.get("ticker", "")).upper() == ticker.upper():
            return MarketSnapshot.model_validate(it)
    return None


def manual_snapshot(ticker: str, price: str | float | Decimal, as_of: date, source: str,
                    source_url: str | None = None, note: str = "") -> MarketSnapshot:
    return MarketSnapshot(ticker=ticker.upper(), price=Decimal(str(price)), as_of=as_of, source=source.strip(),
                          source_url=(source_url or "").strip() or None, note=note)


def generate(store: Store, settings: Settings, run: WorkflowRun, results: dict, market: MarketSnapshot | None,
             source: DataSource | None, use_llm: bool = True) -> ResearchReport:
    provider = None
    if use_llm and not run.request.offline:
        provider, _why = make_provider(settings, run.request.model_copy(update={"llm": "auto"}))
    report = build_research(run, results, market, source, provider)
    store.save_checkpoint(run.id, CHECKPOINT, report)
    return report


def render(report: ResearchReport, results: dict, out_path: Path) -> RenderStats:
    if results.get("validation") is None or results.get("analysis") is None:
        raise ResearchError("The run has no validated metrics.")
    from finsight.report.charts import build_chart_specs

    labels = [p.label for p in results["validation"].report.fiscal_periods]
    draft = results.get("draft")
    charts = draft.charts if draft is not None and draft.charts else build_chart_specs(results["analysis"].metrics,
                                                                                     labels)
    return render_pdf(report, results["analysis"].metrics, results["validation"].report, charts, out_path,
                      flagged_changes=results["analysis"].changes)
