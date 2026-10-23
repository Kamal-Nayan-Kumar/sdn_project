"""Publish a run event from anywhere that has the run id.

The workflow nodes do not receive the SSE record; they receive the typed
`AgentState`, which already carries `run_id`. This module turns that id into a
publish call, so a node can report a fact at the moment it produces it rather
than waiting for the caller to translate a finished state.

Publishing is best-effort by design: a run that outlives its registry entry
(the backend restarted mid-run) must still finish its work, so a missing
record is a no-op rather than an exception.
"""

from __future__ import annotations

from typing import Any

from app.streaming.events import EventType
from app.streaming.registry import registry


def emit(run_id: str | None, type_: EventType, *,
         step: str | None = None, **payload: Any) -> None:
    """Append one event to `run_id`'s stream, if that run still exists."""
    if not run_id:
        return
    record = registry.get(run_id)
    if record is None:
        return
    record.publish(type_, step=step, payload=payload)


def emit_state(state: dict[str, Any], type_: EventType, *,
               step: str | None = None, **payload: Any) -> None:
    """`emit` for a node that holds the graph state."""
    emit(state.get("run_id"), type_, step=step, **payload)
