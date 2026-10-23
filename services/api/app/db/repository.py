"""Database repository for persisting runs, events, and audit records."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_engine, session_scope
from app.models.base import Base
from app.models.orm import ActionRecord, AgentRun, AuditEntry, Conversation, Message, RunEvent, VerificationResult
from app.streaming.events import EventType, StreamEvent
from app.streaming.registry import RunRecord, registry

LOG = logging.getLogger(__name__)


def init_db() -> None:
    """Create all database tables if they do not exist."""
    try:
        engine = get_engine()
        Base.metadata.create_all(bind=engine)
        LOG.info("database tables initialized successfully")
    except Exception as exc:
        LOG.exception("failed to initialize database tables: %s", exc)


def save_run_record(record: RunRecord) -> None:
    """Persist or update an AgentRun and its events in the database."""
    try:
        with session_scope() as session:
            final = record.final_state or {}
            intent = final.get("intent")
            intent_data = intent.model_dump() if hasattr(intent, "model_dump") else intent

            run = session.get(AgentRun, record.run_id)
            if run is None:
                run = AgentRun(
                    id=record.run_id,
                    trigger=record.trigger,
                    status="completed" if record.terminal and not record.error else ("failed" if record.error else "running"),
                    outcome=record.outcome,
                    intent_snapshot=intent_data,
                    started_at=record.created_at,
                    finished_at=record.finished_at,
                    error=record.error,
                )
                session.add(run)
            else:
                run.status = "completed" if record.terminal and not record.error else ("failed" if record.error else "running")
                run.outcome = record.outcome
                run.finished_at = record.finished_at
                run.error = record.error
                if intent_data:
                    run.intent_snapshot = intent_data

            # Save events not yet in DB
            existing_seqs = set(
                session.scalars(
                    select(RunEvent.seq).where(RunEvent.run_id == record.run_id)
                ).all()
            )

            with record.lock:
                events_to_add = [e for e in list(record.history) if e.seq not in existing_seqs]

            for ev in events_to_add:
                row = RunEvent(
                    run_id=record.run_id,
                    seq=ev.seq,
                    type=ev.type.value if hasattr(ev.type, "value") else str(ev.type),
                    step=ev.step,
                    tool=ev.payload.get("tool") if isinstance(ev.payload, dict) else None,
                    status=ev.payload.get("status") if isinstance(ev.payload, dict) else None,
                    payload=ev.payload,
                )
                session.add(row)

            # Persist verification result if available
            verification = final.get("verification")
            if verification and not run.verification:
                before = final.get("baseline")
                after = final.get("after")
                v_row = VerificationResult(
                    run_id=record.run_id,
                    outcome=str(getattr(verification, "status", "")).replace("VerificationStatus.", "").lower(),
                    intent_satisfied=bool(getattr(verification, "intent_satisfied", False)),
                    protected_ok=bool(getattr(verification, "protected_ok", True)),
                    before_latency_ms=getattr(before, "latency_ms", None) if before else None,
                    after_latency_ms=getattr(after, "latency_ms", None) if after else None,
                    before_packet_loss_pct=getattr(before, "packet_loss_pct", None) if before else None,
                    after_packet_loss_pct=getattr(after, "packet_loss_pct", None) if after else None,
                    reason=getattr(verification, "reason", None),
                )
                session.add(v_row)

            # Persist audit entry
            if record.terminal:
                audit = session.scalar(select(AuditEntry).where(AuditEntry.run_id == record.run_id))
                if not audit:
                    a_row = AuditEntry(
                        run_id=record.run_id,
                        category="execution",
                        severity="info",
                        summary=f"Run {record.run_id} completed with outcome: {record.outcome}",
                        detail={
                            "user_message": record.user_message,
                            "trigger": record.trigger,
                            "diagnosis": final.get("diagnosis_summary"),
                            "explanation": final.get("explanation"),
                        },
                    )
                    session.add(a_row)

    except Exception as exc:
        LOG.exception("failed to persist run %s: %s", record.run_id, exc)


def load_runs_into_registry(limit: int = 50) -> None:
    """Load persisted runs into memory registry on application startup."""
    try:
        with session_scope() as session:
            stmt = select(AgentRun).order_by(AgentRun.created_at.desc()).limit(limit)
            runs = session.scalars(stmt).all()
            for r in reversed(runs):
                if registry.get(r.id):
                    continue
                record = registry.create(
                    run_id=r.id,
                    user_message=r.intent_snapshot.get("name", "Restored run") if r.intent_snapshot else "Restored run",
                    trigger=r.trigger,
                )
                record.terminal = r.status in ("completed", "failed", "abstained")
                record.outcome = r.outcome
                record.error = r.error
                record.created_at = r.started_at or record.created_at
                record.finished_at = r.finished_at
                record.final_state = {
                    "intent": r.intent_snapshot,
                    "final_outcome": r.outcome,
                }
                # Load events
                ev_stmt = select(RunEvent).where(RunEvent.run_id == r.id).order_by(RunEvent.seq)
                evs = session.scalars(ev_stmt).all()
                for ev in evs:
                    try:
                        etype = EventType(ev.type)
                    except ValueError:
                        etype = EventType.NOTE
                    stream_ev = StreamEvent(
                        type=etype,
                        run_id=ev.run_id,
                        seq=ev.seq,
                        step=ev.step,
                        payload=ev.payload or {},
                        timestamp=ev.created_at.timestamp() if ev.created_at else 0.0,
                    )
                    record.history.append(stream_ev)
            LOG.info("loaded %d persisted runs into registry", len(runs))
    except Exception as exc:
        LOG.exception("failed to load runs from database: %s", exc)
