"""Tables and file exports (CSV and JSON). Rounding happens here only."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pandas as pd

from finsight.contracts import CalculatedMetric, MetricStatus, ValidationReport, WorkflowRun

FINANCIAL_ROWS = [
    ("revenue", "Revenue"),
    ("gross_profit", "Gross profit"),
    ("operating_income", "Operating income"),
    ("net_income", "Net income"),
    ("operating_cash_flow", "Operating cash flow"),
    ("capital_expenditure", "Capital expenditure"),
    ("free_cash_flow", "Free cash flow (calculated)"),
    ("cash_and_equivalents", "Cash and cash equivalents"),
    ("interest_bearing_debt", "Interest-bearing debt (calculated)"),
    ("gross_margin", "Gross margin %"),
    ("operating_margin", "Operating margin %"),
    ("net_margin", "Net margin %"),
    ("current_ratio", "Current ratio (x)"),
]

CHANGE_ROWS = [
    ("revenue_growth", "Revenue growth %"),
    ("gross_margin_change", "Gross margin change (pp)"),
    ("operating_margin_change", "Operating margin change (pp)"),
    ("net_margin_change", "Net margin change (pp)"),
    ("free_cash_flow_growth", "Free cash flow growth %"),
    ("free_cash_flow_change", "Free cash flow change"),
    ("cash_and_equivalents_change", "Cash change"),
    ("interest_bearing_debt_change", "Debt change"),
]


def _fmt(m: CalculatedMetric | None, usd_scale: Decimal = Decimal(1_000_000)) -> str:
    if m is None:
        return ""
    if m.value is None:
        return {"not_applicable": "n/a", "suppressed": "suppressed"}.get(m.status.value, "unavailable")
    if m.unit == "USD":
        return f"{(m.value / usd_scale):,.0f}"
    if m.unit in ("percent", "percentage_points"):
        return f"{m.value:+.1f}" if m.unit == "percentage_points" or "growth" in m.name else f"{m.value:.1f}"
    return f"{m.value:.2f}"


def presentation_tables(metrics: list[CalculatedMetric], labels: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    by_id = {m.id: m for m in metrics}
    fin = pd.DataFrame(
        [[_fmt(by_id.get(f"metric:{name}:{lbl}")) for lbl in labels] for name, _ in FINANCIAL_ROWS],
        index=[title for _, title in FINANCIAL_ROWS], columns=labels)
    comps = [f"{a}->{b}" for a, b in zip(labels, labels[1:])]
    chg = pd.DataFrame(
        [[_fmt(by_id.get(f"metric:{name}:{c}")) for c in comps] for name, _ in CHANGE_ROWS],
        index=[title for _, title in CHANGE_ROWS], columns=comps)
    return fin, chg


def metrics_frame(metrics: list[CalculatedMetric]) -> pd.DataFrame:
    return pd.DataFrame([{
        "metric_id": m.id, "metric": m.name, "fiscal_period": m.fiscal_period,
        "value": None if m.value is None else str(m.value), "unit": m.unit, "status": m.status.value,
        "formula": m.formula, "input_fact_ids": ";".join(m.input_fact_ids),
        "input_metric_ids": ";".join(m.input_metric_ids), "note": m.note,
    } for m in metrics])


def facts_frame(report: ValidationReport) -> pd.DataFrame:
    rows = []
    for f in report.accepted_facts + report.alternative_facts + report.rejected_candidates:
        rows.append({
            "fact_id": f.id, "field": f.field, "fiscal_period": f.fiscal_period, "status": f.status.value,
            "value": str(f.value), "raw_value": str(f.raw_value), "unit": f.unit,
            "period_start": f.period_start, "period_end": f.period_end, "tag": f"{f.taxonomy}:{f.tag}" if f.tag else "",
            "accession": f.accession, "form": f.form, "filed": f.filed, "method": f.extraction_method,
            "reason": f.status_reason, "location": f.location,
        })
    return pd.DataFrame(rows)


def financial_dataset_frame(report: ValidationReport) -> pd.DataFrame:
    """One row per standardized field, one column per fiscal year (full precision, USD)."""
    labels = [p.label for p in report.fiscal_periods]
    fields = sorted({f.field for f in report.accepted_facts})
    data = {lbl: [next((str(f.value) for f in report.accepted_facts if f.field == fld and f.fiscal_period == lbl), "")
                  for fld in fields] for lbl in labels}
    return pd.DataFrame(data, index=pd.Index(fields, name="field"))


def write_outputs(out_dir: Path, run: WorkflowRun, results: dict) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    plan_cp, val_cp, analysis = results.get("plan"), results.get("validation"), results.get("analysis")
    summary = {
        "run_id": run.id, "state": run.state.value, "request": run.request.model_dump(mode="json"),
        "error": run.error, "needs_input": run.needs_input, "stats": run.stats,
        "history": [t.model_dump(mode="json") for t in run.history],
    }
    if plan_cp:
        summary["company"] = plan_cp.plan.company.model_dump(mode="json")
        summary["fiscal_periods"] = [p.model_dump(mode="json") for p in
                                     (val_cp.report.fiscal_periods if val_cp else plan_cp.plan.fiscal_periods)]
    p = out_dir / "run_summary.json"
    p.write_text(json.dumps(summary, indent=2, default=str))
    written.append(p)
    if val_cp:
        rep = val_cp.report
        for name, frame in [("financial_dataset.csv", financial_dataset_frame(rep)),
                            ("facts_audit_trail.csv", facts_frame(rep))]:
            path = out_dir / name
            frame.to_csv(path, index=name == "financial_dataset.csv")
            written.append(path)
        issues = pd.DataFrame([{"id": i.id, "severity": i.severity.value, "code": i.code, "field": i.field,
                                "fiscal_period": i.fiscal_period, "message": i.message} for i in rep.issues])
        path = out_dir / "validation_issues.csv"
        issues.to_csv(path, index=False)
        written.append(path)
    if analysis:
        path = out_dir / "metrics.csv"
        metrics_frame(analysis.metrics).to_csv(path, index=False)
        written.append(path)
    full = {
        "run": run.model_dump(mode="json"),
        "plan": plan_cp.plan.model_dump(mode="json") if plan_cp else None,
        "sources": [d.model_dump(mode="json") for d in (plan_cp.documents if plan_cp else [])]
                   + ([results["collection"].company_facts_document.model_dump(mode="json")]
                      if results.get("collection") else []),
        "validation_report": val_cp.report.model_dump(mode="json") if val_cp else None,
        "analysis": analysis.model_dump(mode="json") if analysis else None,
    }
    path = out_dir / "analysis.json"
    path.write_text(json.dumps(full, indent=2, default=str))
    written.append(path)
    return written


def status_counts(metrics: list[CalculatedMetric]) -> dict[str, int]:
    out: dict[str, int] = {}
    for m in metrics:
        out[m.status.value] = out.get(m.status.value, 0) + 1
    return out


__all__ = ["presentation_tables", "write_outputs", "metrics_frame", "facts_frame", "MetricStatus"]
