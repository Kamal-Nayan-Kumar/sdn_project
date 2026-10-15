"""In-memory registry of agent runs and their event histories.

Events are kept per run so a client that reconnects can resume from the last
sequence number it saw instead of replaying the whole history or, worse,
silently missing events. This is why `seq` is part of the wire format.

The registry is deliberately process-local: the backend and the lab are both
local, single-user tools, so a durable queue would add a failure mode (writes,
restarts, migrations) without adding an affordance the project asks for.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from app.streaming.events import (
    TERMINAL_EVENTS,
    EventSequence,
    EventType,
    StreamEvent,
    utcnow,
)

LOG = logging.getLogger(__name__)

#: How many runs to retain. Runs are the unit the user reasons about, and they
#: hold complete event histories; a ring keeps memory bounded without needing a
#: database in the path of a live stream.
MAX_RUNS = 200

#: How many events to keep per run. Large enough that a whole run fits.
MAX_EVENTS_PER_RUN = 2000


@dataclass
class RunRecord:
    run_id: str
    user_message: str
    trigger: str = "reactive"
    created_at: Any = field(default_factory=utcnow)
    finished_at: Any = None
    outcome: str | None = None
    terminal: bool = False
    error: str | None = None
    final_state: dict[str, Any] | None = None
    history: deque[StreamEvent] = field(
        default_factory=lambda: deque(maxlen=MAX_EVENTS_PER_RUN))
    sequence: EventSequence = field(default_factory=EventSequence)
    listeners: list[Any] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def publish(self, type_: EventType, **payload: Any) -> StreamEvent:
        """Append an event and fan it out to every attached listener."""
        seq = self.sequence.next()
        event = StreamEvent(
            type=type_, run_id=self.run_id, seq=seq, **payload)
        with self.lock:
            self.history.append(event)
            listeners = list(self.listeners)
        for queue in listeners:
            try:
                queue.put_nowait(event)
            except Exception:  # pragma: no cover - listener closed first
                LOG.debug("listener for run %s is gone", self.run_id)
        return event

    def finish(self, *, outcome: str, error: str | None = None,
               final_state: dict[str, Any] | None = None) -> None:
        self.finished_at = utcnow()
        self.outcome = outcome
        self.terminal = True
        self.error = error
        if final_state is not None:
            self.final_state = final_state


class RunRegistry:
    """Every live run, plus bounded recall of finished ones."""

    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}
        self._order: deque[str] = deque(maxlen=MAX_RUNS)
        self._lock = threading.Lock()

    def create(self, run_id: str, user_message: str,
               trigger: str = "reactive") -> RunRecord:
        with self._lock:
            record = RunRecord(run_id=run_id, user_message=user_message,
                               trigger=trigger)
            self._runs[run_id] = record
            self._order.append(run_id)
            # Drop the oldest finished run once the ring wraps.
            while len(self._runs) > MAX_RUNS:
                oldest = next(iter(self._order))
                if self._runs[oldest].terminal:
                    del self._runs[oldest]
                else:
                    # Never evict a live run; it would orphan its listeners.
                    break
            return record

    def get(self, run_id: str) -> RunRecord | None:
        return self._runs.get(run_id)

    def require(self, run_id: str) -> RunRecord:
        record = self.get(run_id)
        if record is None:
            raise KeyError(f"unknown run {run_id!r}")
        return record

    def history(self, run_id: str) -> list[StreamEvent]:
        record = self.get(run_id)
        if record is None:
            return []
        with record.lock:
            return list(record.history)

    def after(self, run_id: str, seq: int) -> list[StreamEvent]:
        """Events strictly after `seq` -- what a resuming client needs."""
        return [e for e in self.history(run_id) if e.seq > seq]

    def summary(self) -> dict[str, Any]:
        return {
            "runs": [
                {
                    "run_id": r.run_id,
                    "trigger": r.trigger,
                    "outcome": r.outcome,
                    "terminal": r.terminal,
                    "created_at": r.created_at.isoformat(),
                    "finished_at": r.finished_at.isoformat()
                    if r.finished_at else None,
                    "events": r.sequence.last,
                }
                for r in reversed([self._runs[i] for i in self._order])
                if r.run_id in self._runs
            ],
            "count": len(self._runs),
        }


registry = RunRegistry()


def is_terminal(event: StreamEvent) -> bool:
    return event.type in TERMINAL_EVENTS
