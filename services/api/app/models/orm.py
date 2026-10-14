"""Persistence models — one class per table in the spec's recommended list."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.models.base import Base, TimestampMixin, UUIDMixin, utcnow

# JSON column that is native JSONB on PostgreSQL and plain JSON elsewhere.
JSONType = JSONB().with_variant(JSON(), "sqlite")


def new_uuid() -> str:
    return str(uuid.uuid4())


class Conversation(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "conversations"

    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )
    runs: Mapped[list["AgentRun"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "messages"

    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user|assistant|system
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Assistant messages can carry structured activity groups / tool cards.
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Intent(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "intents"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    traffic_class: Mapped[str] = mapped_column(String(64), nullable=False)  # video|voice|web
    src_host: Mapped[str] = mapped_column(String(64), nullable=False)
    dst_host: Mapped[str] = mapped_column(String(64), nullable=False)
    max_latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    max_packet_loss_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    protected_flows: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONType, nullable=False, default=list
    )
    policy: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    runs: Mapped[list["AgentRun"]] = relationship(back_populates="intent")


class AgentRun(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "agent_runs"

    conversation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="SET NULL"), index=True
    )
    intent_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("intents.id", ondelete="SET NULL"), nullable=True
    )
    trigger: Mapped[str] = mapped_column(
        String(32), default="reactive", nullable=False
    )  # reactive | proactive
    status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False
    )  # running|completed|failed|cancelled|abstained
    outcome: Mapped[str | None] = mapped_column(String(64), nullable=True)
    intent_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_event_seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancellation_requested: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    conversation: Mapped[Conversation | None] = relationship(back_populates="runs")
    intent: Mapped[Intent | None] = relationship(back_populates="runs")
    events: Mapped[list["RunEvent"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="RunEvent.seq"
    )
    actions: Mapped[list["ActionRecord"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    verification: Mapped["VerificationResult | None"] = relationship(
        back_populates="run", cascade="all, delete-orphan", uselist=False
    )

    def next_seq(self) -> int:
        self.last_event_seq += 1
        return self.last_event_seq


class RunEvent(Base, UUIDMixin):
    """Durable, ordered agent activity event (drives SSE replay)."""

    __tablename__ = "run_events"

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    run: Mapped[AgentRun] = relationship(back_populates="events")


class ActionRecord(Base, UUIDMixin, TimestampMixin):
    """Proposed mutation + its deterministic validation + execution result."""

    __tablename__ = "action_records"

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    action_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    risk_level: Mapped[str] = mapped_column(String(16), default="low", nullable=False)
    validation_status: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # approved | rejected
    validation_checks: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONType, nullable=False, default=list
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_status: Mapped[str] = mapped_column(
        String(32), default="not_executed", nullable=False
    )  # not_executed | executed | failed | skipped_awaiting_approval
    execution_result: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[AgentRun] = relationship(back_populates="actions")


class VerificationResult(Base, UUIDMixin, TimestampMixin):
    """Before/after measurements and the verified recovery outcome."""

    __tablename__ = "verification_results"

    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_runs.id", ondelete="CASCADE"), unique=True
    )
    outcome: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # success | partial | failed | unverified
    before_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    after_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    before_packet_loss_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    after_packet_loss_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    before_utilization_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    after_utilization_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    protected_flow_before: Mapped[dict[str, Any] | None] = mapped_column(
        JSONType, nullable=True
    )
    protected_flow_after: Mapped[dict[str, Any] | None] = mapped_column(
        JSONType, nullable=True
    )
    intent_satisfied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    protected_ok: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    measurements: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    run: Mapped[AgentRun] = relationship(back_populates="verification")


class LabSession(Base, UUIDMixin, TimestampMixin):
    """Local lab connection state / heartbeat."""

    __tablename__ = "lab_sessions"

    bridge_url: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), default="unknown", nullable=False
    )  # connected | degraded | offline | unknown
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    controller_info: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class TelemetrySample(Base, UUIDMixin):
    """Historical telemetry for charts and post-hoc analysis."""

    __tablename__ = "telemetry_samples"

    measured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    node_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    link_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    flow_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    packet_loss_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    utilization_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    tx_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    rx_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    extra: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)


class AuditEntry(Base, UUIDMixin):
    """Immutable audit trail of every meaningful decision."""

    __tablename__ = "audit_entries"

    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category: Mapped[str] = mapped_column(
        String(48), nullable=False
    )  # evidence | validation | execution | verification | monitor | safety
    severity: Mapped[str] = mapped_column(String(16), default="info", nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    detail: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )


__all__ = [
    "ActionRecord",
    "AgentRun",
    "AuditEntry",
    "Conversation",
    "Intent",
    "LabSession",
    "Message",
    "RunEvent",
    "TelemetrySample",
    "VerificationResult",
    "new_uuid",
]
