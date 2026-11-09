"""Workflow state machine.

Every state change goes through ``transition`` so an illegal jump (for example
straight from COLLECTING to APPROVED) fails loudly instead of corrupting a run.

    CREATED -> PLANNING -> COLLECTING -> VALIDATING -> ANALYZING
            -> INVESTIGATING -> DRAFTING -> REVIEWING -> READY_FOR_REVIEW -> APPROVED

Side states: NEEDS_INPUT (ambiguity the user must resolve), PARTIAL (a result
exists but blocking issues make some metrics unavailable), FAILED (resumable
from the stage that failed).

Stage 1 has no investigation or drafting yet, so ANALYZING may go straight to
READY_FOR_REVIEW or PARTIAL.
"""

from __future__ import annotations

from finsight.contracts import RunState as S
from finsight.contracts import StateTransition, WorkflowRun, utcnow
from finsight.errors import InvalidTransitionError

ALLOWED: dict[S, set[S]] = {
    S.CREATED: {S.PLANNING, S.FAILED},
    S.PLANNING: {S.COLLECTING, S.NEEDS_INPUT, S.FAILED},
    S.NEEDS_INPUT: {S.PLANNING, S.FAILED},
    S.COLLECTING: {S.VALIDATING, S.FAILED},
    # VALIDATING -> COLLECTING is the bounded recovery path (e.g. refetch stale company facts).
    S.VALIDATING: {S.ANALYZING, S.COLLECTING, S.FAILED},
    S.ANALYZING: {S.INVESTIGATING, S.READY_FOR_REVIEW, S.PARTIAL, S.FAILED},
    S.INVESTIGATING: {S.DRAFTING, S.PARTIAL, S.FAILED},
    S.DRAFTING: {S.REVIEWING, S.FAILED},
    # REVIEWING -> DRAFTING / INVESTIGATING are revision cycles, capped by the orchestrator.
    S.REVIEWING: {S.DRAFTING, S.INVESTIGATING, S.READY_FOR_REVIEW, S.PARTIAL, S.FAILED},
    S.READY_FOR_REVIEW: {S.APPROVED, S.DRAFTING, S.COLLECTING},
    S.PARTIAL: {S.APPROVED, S.COLLECTING, S.PLANNING},
    # A failed run resumes at the stage that failed.
    S.FAILED: {S.PLANNING, S.COLLECTING, S.VALIDATING, S.ANALYZING},
    S.APPROVED: set(),
}

TERMINAL = {S.READY_FOR_REVIEW, S.PARTIAL, S.APPROVED, S.NEEDS_INPUT, S.FAILED}


def can_transition(src: S, dst: S) -> bool:
    return dst in ALLOWED.get(src, set())


def transition(run: WorkflowRun, dst: S, reason: str) -> WorkflowRun:
    src = run.state
    if not can_transition(src, dst):
        raise InvalidTransitionError(f"Transition {src.value} -> {dst.value} is not allowed ({reason})")
    now = utcnow()
    run.history.append(StateTransition(from_state=src, to_state=dst, at=now, reason=reason))
    run.state = dst
    run.updated_at = now
    return run
