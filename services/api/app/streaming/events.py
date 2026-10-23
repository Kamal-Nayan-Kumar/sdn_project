"""Server-Sent Event envelope shared by the backend and the frontend.

The spec requires that every event carries a run id and a timestamp, that
events are ordered (or carry a sequence number), that duplicates are possible,
and that there is always a terminal event. The frontend must be able to recover
after a reconnect, so these fields are part of the wire format, not a nicety.

Nothing in this module may carry a secret: these events are broadcast to a
browser tab.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EventType(str, Enum):
    """The complete vocabulary of run events."""

    # lifecycle
    RUN_STARTED = "run_started"
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_CANCELLED = "run_cancelled"

    # tools
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"

    # the three gates
    EVIDENCE_ASSESSED = "evidence_assessed"
    VALIDATION_COMPLETED = "validation_completed"
    VERIFICATION_COMPLETED = "verification_completed"

    # narrative
    INTENT_DETECTED = "intent_detected"
    DIAGNOSIS_SUMMARY = "diagnosis_summary"
    PLAN_CONSIDERED = "plan_considered"
    EXECUTION_COMPLETED = "execution_completed"

    NOTE = "note"

    # live model output. `thinking` is the agent narrating what it is doing
    # while a step runs; `token` is the answer the user reads. Both arrive as
    # incremental deltas so the transcript types out as it is generated.
    THINKING = "thinking"
    TOKEN = "token"

    # deterministic guardrail verdicts, surfaced so the operator can see a
    # request refused as clearly as one acted on.
    GUARDRAIL = "guardrail"


# The order a run is expected to publish them in. Used only to detect gaps, not
# to enforce it.
TERMINAL_EVENTS = frozenset({
    EventType.RUN_COMPLETED,
    EventType.RUN_FAILED,
    EventType.RUN_CANCELLED,
})


class StreamEvent(BaseModel):
    """One event on a run's SSE stream."""

    type: EventType
    run_id: str
    # Monotonic per run and 1-based, so a reconnecting client can ask for
    # "everything after seq N" rather than deduplicating on timestamps, which
    # two different clients would not agree on.
    seq: int = Field(ge=1)
    timestamp: float = Field(
        default_factory=time.time,
        description="Unix seconds, for the frontend's clock and ordering")
    step: str | None = Field(
        default=None, description="Workflow node that emitted this event")
    payload: dict[str, Any] = Field(default_factory=dict)

    def sse_frame(self, event_id: str | None = None) -> str:
        """Render as one SSE frame, `id:` included so EventSource tracks it."""
        lines = []
        if event_id is not None:
            lines.append(f"id: {event_id}")
        lines.append(f"event: {self.type.value}")
        lines.append(f"data: {self.model_dump_json()}")
        return "\n".join(lines) + "\n\n"


class EventSequence:
    """Allocates strictly increasing sequence numbers for one run.

    A single run's events are produced by one coroutine, so a counter is
    enough; the lock keeps that true if a run is ever driven concurrently.
    """

    def __init__(self) -> None:
        import threading

        self._lock = threading.Lock()
        self._seq = 0

    def next(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    @property
    def last(self) -> int:
        return self._seq
