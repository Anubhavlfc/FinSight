"""Compare a run's agentic findings with the single-prompt baseline on the same changes.

Measures, per approach (all computed by code):

* explained: changes with a management explanation backed by at least one verified quote;
* evidence status counts (supported / partially supported / unsupported);
* quotes proposed by the model, quotes that passed verbatim verification, and the failure rate;
* explanation numbers that appear in neither the verified quotes nor the cited metrics
  (the same rule as the reviewer's ``explanation_numbers`` check);
* model calls, tokens and estimated cost.

The agentic figures are read from the stored run, so only the baseline costs a model call.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from finsight.contracts import CalculatedMetric, EvidenceStatus, InvestigationFinding
from finsight.evaluation.baseline import BaselineResult, run_baseline
from finsight.evidence.text import normalise_for_match
from finsight.llm.provider import LLMProvider
from finsight.presentation import extract_numbers, number_matches_metric


def unsupported_numbers(f: InvestigationFinding, metrics: dict[str, CalculatedMetric]) -> list[str]:
    if not f.management_explanation:
        return []
    quote_text = normalise_for_match(" ".join(q.quote for q in f.quotes))
    cited = [metrics[m] for m in f.metric_ids if m in metrics]
    out = []
    for n in extract_numbers(f.management_explanation):
        if n.kind == "plain":
            continue
        digits = n.text.lstrip("+-−$ ").split()[0].rstrip("%").lower()
        if digits in quote_text or any(number_matches_metric(n, m) for m in cited):
            continue
        out.append(n.text)
    return out


def _agentic_proposed_quotes(f: InvestigationFinding) -> int:
    """Quotes in the model's last record_finding call (the stored finding keeps only verified ones)."""
    calls = [c for c in f.tool_calls if c.tool == "record_finding"]
    if not calls:
        return len(f.quotes)
    return len((calls[-1].input or {}).get("quotes") or [])


def summarise(findings: list[InvestigationFinding], proposed: dict[str, int],
              metrics: dict[str, CalculatedMetric], usage: dict) -> dict[str, Any]:
    status = {s.value: 0 for s in EvidenceStatus}
    for f in findings:
        status[f.evidence_status.value] += 1
    n_prop = sum(proposed.get(f.change_flag_id, 0) for f in findings)
    n_ver = sum(len(f.quotes) for f in findings)
    bad_numbers = {f.change_flag_id: unsupported_numbers(f, metrics) for f in findings}
    return {
        "changes": len(findings),
        "explained": sum(1 for f in findings if f.management_explanation and f.quotes),
        "evidence_status": status,
        "quotes_proposed": n_prop,
        "quotes_verified": n_ver,
        "quote_failure_rate": round((n_prop - n_ver) / n_prop, 4) if n_prop else None,
        "unsupported_explanation_numbers": sum(len(v) for v in bad_numbers.values()),
        "model_calls": usage.get("model_calls", 0),
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "estimated_cost_usd": usage.get("estimated_cost_usd", 0.0),
        "per_change": {f.change_flag_id: {"status": f.evidence_status.value,
                                          "explained": bool(f.management_explanation and f.quotes),
                                          "quotes_proposed": proposed.get(f.change_flag_id, 0),
                                          "quotes_verified": len(f.quotes),
                                          "unsupported_numbers": bad_numbers[f.change_flag_id],
                                          "management_explanation": f.management_explanation}
                       for f in findings},
    }


def evaluate(results: dict, provider: LLMProvider, *, per_flag: int = 6, min_quote_chars: int = 20) -> dict[str, Any]:
    """``results`` is ``orchestrator.load_results`` for a run that finished investigation."""
    plan, analysis, inv, text = (results.get("plan"), results.get("analysis"), results.get("investigation"),
                                 results.get("filing_text"))
    if not (plan and analysis and inv and text):
        raise ValueError("The run has no investigation to compare with. Run an analysis with investigation first.")
    flags_by_id = {f.id: f for f in analysis.changes}
    agentic = [f for f in inv.findings if f.change_flag_id in flags_by_id]
    flags = [flags_by_id[f.change_flag_id] for f in agentic]
    metrics = {m.id: m for m in analysis.metrics}
    base: BaselineResult = run_baseline(plan.plan, analysis.metrics, flags, text.passages, provider,
                                        per_flag=per_flag, min_quote_chars=min_quote_chars)
    agentic_usage = inv.llm_usage or {}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "company": plan.plan.company.name,
        "periods": [p.label for p in plan.plan.fiscal_periods],
        "agentic": {"producer": agentic[0].produced_by if agentic else None,
                    "note": "model calls and tokens cover the whole investigation stage",
                    **summarise(agentic, {f.change_flag_id: _agentic_proposed_quotes(f) for f in agentic},
                                metrics, agentic_usage)},
        "baseline": {"producer": f"baseline:{provider.name}", "excerpts_per_change": per_flag,
                     "prompt_chars": base.prompt_chars, "errors": base.errors,
                     **summarise(base.findings, base.proposed_quotes, metrics, base.llm_usage)},
        "baseline_findings": [f.model_dump(mode="json") for f in base.findings],
    }


def to_markdown(ev: dict[str, Any]) -> str:
    a, b = ev["agentic"], ev["baseline"]

    def pct(x):
        return "n/a" if x is None else f"{x * 100:.0f}%"

    rows = [
        ("Changes compared", a["changes"], b["changes"]),
        ("Explained with a verified quote", a["explained"], b["explained"]),
        ("Supported", a["evidence_status"]["supported"], b["evidence_status"]["supported"]),
        ("Partially supported", a["evidence_status"]["partially_supported"],
         b["evidence_status"]["partially_supported"]),
        ("Unsupported", a["evidence_status"]["unsupported"], b["evidence_status"]["unsupported"]),
        ("Quotes proposed", a["quotes_proposed"], b["quotes_proposed"]),
        ("Quotes verified verbatim", a["quotes_verified"], b["quotes_verified"]),
        ("Quote failure rate", pct(a["quote_failure_rate"]), pct(b["quote_failure_rate"])),
        ("Explanation numbers not in quotes or metrics", a["unsupported_explanation_numbers"],
         b["unsupported_explanation_numbers"]),
        ("Model calls", a["model_calls"], b["model_calls"]),
        ("Tokens (input + output)", f"{a['input_tokens'] + a['output_tokens']:,}",
         f"{b['input_tokens'] + b['output_tokens']:,}"),
        ("Estimated cost (USD)", f"{a['estimated_cost_usd']:.2f}", f"{b['estimated_cost_usd']:.2f}"),
    ]
    out = [f"# Agentic investigation vs single-prompt baseline: {ev['company']}", "",
           f"Periods {', '.join(ev['periods'])}. Agentic: `{a['producer']}`. Baseline: `{b['producer']}`, "
           f"{b['excerpts_per_change']} retrieved excerpts per change, one call.", "",
           "| Measure | Agentic | Baseline |", "|---|---:|---:|"]
    out += [f"| {r[0]} | {r[1]} | {r[2]} |" for r in rows]
    out += ["", "| Change | Agentic | Baseline |", "|---|---|---|"]
    for cid, pa in a["per_change"].items():
        pb = b["per_change"].get(cid, {})
        out.append(f"| `{cid}` | {pa['status']} ({pa['quotes_verified']}/{pa['quotes_proposed']} quotes) | "
                   f"{pb.get('status', '-')} ({pb.get('quotes_verified', 0)}/{pb.get('quotes_proposed', 0)} quotes) |")
    if b["errors"]:
        out += ["", "Baseline errors: " + "; ".join(b["errors"])]
    out += ["", "Both columns are scored by the same code: quotes are checked verbatim against the cited passage "
                "and the evidence status is set by code. Agentic model calls and tokens cover the whole "
                "investigation stage of the stored run."]
    return "\n".join(out) + "\n"


def write(ev: dict[str, Any], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    j, m = out_dir / "evaluation.json", out_dir / "evaluation.md"
    j.write_text(json.dumps(ev, indent=2, default=str))
    m.write_text(to_markdown(ev))
    return [j, m]
