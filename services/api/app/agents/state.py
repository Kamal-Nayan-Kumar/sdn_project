"""Typed LangGraph state for the guarded recovery workflow.

The fields are explicit (rather than one blob) so that every step of the
workflow -- and every reviewer reading a persisted run -- can see exactly what
the agent believed at each point. Critically, the fields that the three gates
write to (`evidence_status`, `validation_result`, `verification_result`) are
populated by deterministic Python, never by the model.
"""

from __future__ import annotations

import operator
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from app.schemas.domain import (
    ActionType,
    EvidenceAssessment,
    EvidenceStatus,
    ExecutionResult,
    FlowTelemetry,
    IntentSpec,
    LinkTelemetry,
    NetworkSnapshot,
    PathCandidate,
    ProposedAction,
    RerouteAction,
    ValidationResult,
    VerificationOutcome,
    VerificationStatus,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ToolCallRecord(BaseModel):
    """One observable tool invocation, shown in the activity timeline."""

    tool: str
    status: Literal["started", "success", "error", "skipped"] = "started"
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] | None = None
    duration_ms: float | None = None
    error: str | None = None
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None

    def finish(self, *, outputs: dict[str, Any] | None = None, error: str | None = None) -> None:
        self.finished_at = utcnow()
        if error:
            self.status = "error"
            self.error = error
        else:
            self.status = "success"
            self.outputs = outputs
        if self.started_at:
            self.duration_ms = (self.finished_at - self.started_at).total_seconds() * 1000


class AgentState(TypedDict, total=False):
    """LangGraph state. All fields are explicit and typed."""

    # -- identity ------------------------------------------------------
    run_id: str
    conversation_id: str | None
    intent_id: str | None
    trigger: Literal["reactive", "proactive"]
    user_message: str

    # -- intent --------------------------------------------------------
    intent: IntentSpec | None
    intent_needs_clarification: bool
    clarification_question: str | None

    # -- observation ----------------------------------------------------
    snapshot: NetworkSnapshot | None
    baseline: FlowTelemetry | None
    protected_baseline: list[FlowTelemetry]
    observations: list[dict[str, Any]]
    last_snapshot_at: datetime | None

    # -- evidence (Gate 1) ----------------------------------------------
    evidence: EvidenceAssessment | None
    evidence_status: EvidenceStatus | None
    suspect_links: list[str]
    evidence_retries: int
    # Does the measurement actually breach the intent? None means "no usable
    # measurement", which is deliberately distinct from False ("measured and
    # within the intent").
    intent_violated: bool | None

    # -- diagnosis / planning --------------------------------------------
    diagnosis_summary: str
    planning_notes: list[str]
    candidate_actions: list[ProposedAction]
    selected_action: ProposedAction | None
    action_id: str
    alternatives_considered: list[dict[str, Any]]

    # -- safety (Gate 2) --------------------------------------------------
    validation: ValidationResult | None
    rejected_actions: list[dict[str, Any]]
    recovery_retries: int
    awaiting_approval: bool
    approved_by: str | None

    # -- execution ---------------------------------------------------------
    execution: ExecutionResult | None
    executed_path_ids: list[str]

    # -- verification (Gate 3) ---------------------------------------------
    verification: VerificationOutcome | None
    verification_status: VerificationStatus | None
    after: FlowTelemetry | None
    protected_after: list[FlowTelemetry]

    # -- bookkeeping -------------------------------------------------------
    tool_calls: Annotated[list[ToolCallRecord], operator.add]
    events: Annotated[list[dict[str, Any]], operator.add]
    llm_error: str | None
    cancelled: bool
    lab_offline: bool
    final_outcome: Literal[
        "intent_satisfied",
        "partial_improvement",
        "recovery_failed",
        "unverified",
        "abstained_insufficient_evidence",
        "action_rejected",
        "cancelled",
        "lab_offline",
        "llm_unavailable",
        "error",
    ] | None
    explanation: str
    started_at: datetime
    finished_at: datetime | None


def initial_state(**kwargs: Any) -> AgentState:
    """A fresh run state with sane defaults."""
    base: dict[str, Any] = {
        "run_id": "",
        "conversation_id": None,
        "intent_id": None,
        "trigger": "reactive",
        "user_message": "",
        "intent": None,
        "intent_needs_clarification": False,
        "clarification_question": None,
        "snapshot": None,
        "baseline": None,
        "protected_baseline": [],
        "observations": [],
        "last_snapshot_at": None,
        "evidence": None,
        "evidence_status": None,
        "suspect_links": [],
        "evidence_retries": 0,
        "intent_violated": None,
        "diagnosis_summary": "",
        "planning_notes": [],
        "candidate_actions": [],
        "selected_action": None,
        "action_id": "",
        "alternatives_considered": [],
        "validation": None,
        "rejected_actions": [],
        "recovery_retries": 0,
        "awaiting_approval": False,
        "approved_by": None,
        "execution": None,
        "executed_path_ids": [],
        "verification": None,
        "verification_status": None,
        "after": None,
        "protected_after": [],
        "tool_calls": [],
        "events": [],
        "llm_error": None,
        "cancelled": False,
        "lab_offline": False,
        "final_outcome": None,
        "explanation": "",
        "started_at": utcnow(),
        "finished_at": None,
    }
    base.update(kwargs)
    return AgentState(**base)


__all__ = [
    "AgentState",
    "PathCandidate",
    "RerouteAction",
    "ToolCallRecord",
    "initial_state",
]
