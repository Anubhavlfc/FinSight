"""Rules for a person's review decision, shared by the CLI, the web API and the dashboard.

The automated reviewer never approves a report. A person does, and only after saying what they
checked. Every front end sends the same ``checks`` record and gets the same answer here, so a
report cannot be approved with less checking in one interface than in another.

``checks`` layout (stored verbatim in ``run.stats["human_reviews"]``)::

    {"findings": {"<change flag id>": "Confirmed" | "Disputed" | "Not checked", ...},
     "figures_checked": bool,   # compared the key figures with the 10-K statements
     "limitations_read": bool}  # read the data-quality issues and limitations
"""

from __future__ import annotations

from typing import Any, Iterable

CONFIRMED, DISPUTED, NOT_CHECKED = "Confirmed", "Disputed", "Not checked"
VERDICTS = (CONFIRMED, DISPUTED, NOT_CHECKED)
DECISIONS = ("approved", "changes_requested")
REQUIRED_CHECKS = {
    "figures_checked": "confirm you compared the key figures with the 10-K financial statements",
    "limitations_read": "confirm you read the data-quality issues and limitations",
}


class ReviewDecisionError(ValueError):
    """The decision cannot be recorded as given. The message says what is missing."""


def normalize_checks(checks: dict[str, Any] | None, finding_ids: Iterable[str]) -> dict[str, Any]:
    """Validate and normalise ``checks``; every investigated change gets a verdict."""
    checks = dict(checks or {})
    ids = list(finding_ids)
    raw = checks.get("findings") or {}
    if not isinstance(raw, dict):
        raise ReviewDecisionError("findings must map each change to a verdict")
    unknown = sorted(set(raw) - set(ids))
    if unknown:
        raise ReviewDecisionError(f"Unknown change(s) in the review: {', '.join(unknown)}")
    findings: dict[str, str] = {}
    for fid in ids:
        given = " ".join(str(raw.get(fid) or NOT_CHECKED).replace("_", " ").split()).lower()
        verdict = next((v for v in VERDICTS if v.lower() == given), None)
        if verdict is None:
            raise ReviewDecisionError(f"Verdict for {fid} must be one of {', '.join(VERDICTS)}")
        findings[fid] = verdict
    out = {"findings": findings}
    for key in REQUIRED_CHECKS:
        out[key] = bool(checks.get(key))
    return out


def approval_gaps(checks: dict[str, Any]) -> list[str]:
    """What is still missing before the report can be approved (empty when nothing is)."""
    gaps = [msg for key, msg in REQUIRED_CHECKS.items() if not checks.get(key)]
    findings = checks.get("findings") or {}
    disputed = [k for k, v in findings.items() if v == DISPUTED]
    unchecked = [k for k, v in findings.items() if v == NOT_CHECKED]
    if disputed:
        gaps.append(f"{len(disputed)} change(s) are disputed; request changes instead")
    if unchecked:
        gaps.append(f"confirm {len(unchecked)} investigated change(s) against the quoted filing passages")
    return gaps


def check_decision(decision: str, note: str, checks: dict[str, Any]) -> None:
    if decision not in DECISIONS:
        raise ReviewDecisionError(f"Unknown review decision {decision!r}")
    if decision == "approved":
        gaps = approval_gaps(checks)
        if gaps:
            raise ReviewDecisionError("To approve: " + "; ".join(gaps) + ".")
    else:
        disputed = [k for k, v in (checks.get("findings") or {}).items() if v == DISPUTED]
        if not note and not disputed:
            raise ReviewDecisionError("Requesting changes needs a note or at least one disputed change, "
                                      "so whoever revises the report knows what to fix.")
