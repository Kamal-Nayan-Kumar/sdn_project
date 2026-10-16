"""Domain schemas for the network lab.

These Pydantic models are the single source of truth for what "the network"
looks like to the rest of the system. They are deliberately independent of
any specific transport (direct Mininet, Ryu REST, lab bridge WebSocket) so
that the tool layer, the safety validator and the API all speak the same
vocabulary.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StrictModel(BaseModel):
    """Base model that rejects unknown fields and is immutable by default."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------------------
# Topology
# ---------------------------------------------------------------------------


class NodeKind(str, Enum):
    HOST = "host"
    SWITCH = "switch"
    CONTROLLER = "controller"


class LinkStatus(str, Enum):
    UP = "up"
    DOWN = "down"
    DEGRADED = "degraded"


class TopologyNode(StrictModel):
    id: str = Field(description="Mininet/Ryu node id, e.g. 'h1' or 's1'")
    kind: NodeKind
    label: str
    # Position hint for the frontend (SVG coordinates). Optional.
    x: float | None = None
    y: float | None = None
    ip: str | None = None
    mac: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class TopologyLink(StrictModel):
    id: str = Field(description="Stable id, e.g. 's1-s2'")
    source: str
    target: str
    capacity_mbps: float = Field(default=1000.0, gt=0)
    delay_ms: float = Field(default=0.0, ge=0)
    status: LinkStatus = LinkStatus.UP
    # Whether a controlled congestion generator is attached to this link.
    congestible: bool = False


class Topology(StrictModel):
    nodes: list[TopologyNode]
    links: list[TopologyLink]
    controller: str | None = None
    captured_at: datetime = Field(default_factory=utcnow)

    def node(self, node_id: str) -> TopologyNode | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def link(self, link_id: str) -> TopologyLink | None:
        return next((l for l in self.links if l.id == link_id), None)

    def endpoint_ids(self) -> set[str]:
        return {n.id for n in self.nodes if n.kind in (NodeKind.HOST, NodeKind.SWITCH)}


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


class LinkTelemetry(StrictModel):
    link_id: str
    utilization_pct: float = Field(ge=0, description="Measured utilisation, 0-100")
    capacity_mbps: float = Field(gt=0)
    tx_bps: float = Field(default=0.0, ge=0)
    rx_bps: float = Field(default=0.0, ge=0)
    latency_ms: float | None = None
    status: LinkStatus = LinkStatus.UP
    measured_at: datetime = Field(default_factory=utcnow)

    @property
    def utilization_fraction(self) -> float:
        return self.utilization_pct / 100.0

    def age_seconds(self, now: datetime | None = None) -> float:
        now = now or utcnow()
        return max(0.0, (now - self.measured_at).total_seconds())


class FlowKind(str, Enum):
    VIDEO = "video"
    VOICE = "voice"
    WEB = "web"
    BACKGROUND = "background"
    OTHER = "other"


class FlowTelemetry(StrictModel):
    flow_id: str
    src_host: str
    dst_host: str
    kind: FlowKind = FlowKind.OTHER
    # Current forwarding path as a list of node ids: [h1, s1, s2, s4, h4].
    path: list[str] = Field(default_factory=list)
    latency_ms: float | None = Field(default=None, ge=-1, le=10_000)
    jitter_ms: float | None = None
    # The lab measures each flow with a multi-packet ping and reports the whole
    # distribution, not just its mean. Carrying `samples` and the observed peak
    # lets a latency SLO be judged on the behaviour it is meant to catch: a
    # flow whose mean sits under the target while individual packets breach it.
    samples: list[float] = Field(default_factory=list)
    min_latency_ms: float | None = None
    max_latency_ms: float | None = Field(default=None, ge=-1, le=10_000)
    packet_loss_pct: float | None = Field(default=None, ge=0, le=100)
    throughput_mbps: float | None = None
    measured_at: datetime = Field(default_factory=utcnow)

    def age_seconds(self, now: datetime | None = None) -> float:
        now = now or utcnow()
        return max(0.0, (now - self.measured_at).total_seconds())


class ProtectedFlowStatus(StrictModel):
    """Result of checking a protected flow against its constraints."""

    flow_id: str
    label: str
    latency_ms: float | None = None
    packet_loss_pct: float | None = None
    max_latency_ms: float | None = None
    max_packet_loss_pct: float | None = None
    ok: bool = True
    reason: str | None = None
    path_unmodified: bool = True


class NetworkSnapshot(StrictModel):
    """A single coherent view of the lab at one point in time."""

    topology: Topology
    links: list[LinkTelemetry] = Field(default_factory=list)
    flows: list[FlowTelemetry] = Field(default_factory=list)
    # Link ids currently carrying a controlled congestion generator.
    congested_links: list[str] = Field(default_factory=list)
    active_scenario: str | None = None
    controller_reachable: bool = True
    captured_at: datetime = Field(default_factory=utcnow)

    def link_utilization(self, link_id: str) -> float | None:
        for link in self.links:
            if link.link_id == link_id:
                return link.utilization_pct
        return None

    def flow(self, flow_id: str) -> FlowTelemetry | None:
        return next((f for f in self.flows if f.flow_id == flow_id), None)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


class PathCandidate(StrictModel):
    """A candidate route through the topology."""

    nodes: list[str] = Field(min_length=2)
    links: list[str] = Field(default_factory=list)
    total_delay_ms: float = Field(default=0.0, ge=0)
    min_capacity_mbps: float | None = None
    max_utilization_pct: float = Field(default=0.0, ge=0)
    # True when the path avoids every currently congested link.
    avoids_congestion: bool = True
    # True when the path avoids every link carrying a protected flow.
    avoids_protected: bool = True
    disjoint_from: list[str] | None = None

    @field_validator("nodes")
    @classmethod
    def _no_duplicates(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("path must not revisit a node")
        return v

    @property
    def score(self) -> float:
        """Lower is better.

        Routing a path that still crosses a congested link is not a recovery,
        so that is disqualifying rather than merely expensive. Ranking on
        delay alone picked a 18 ms detour through the congested s2-s4 link over
        the 20 ms path that avoided it entirely -- the agent "repaired" the
        flow by making it worse, then reported `recovery_failed` on a change
        that had never addressed the bottleneck.

        Among paths that avoid congestion, delay decides; utilisation only
        breaks ties between otherwise equal-delay options.
        """
        if not self.avoids_congestion:
            return float("inf")
        return self.total_delay_ms + self.max_utilization_pct / 100.0


class PathEvaluationResult(StrictModel):
    src: str
    dst: str
    candidates: list[PathCandidate]
    best_candidate_index: int | None = None
    notes: list[str] = Field(default_factory=list)

    @property
    def best(self) -> PathCandidate | None:
        if self.best_candidate_index is None:
            return None
        return self.candidates[self.best_candidate_index]


# ---------------------------------------------------------------------------
# Intents
# ---------------------------------------------------------------------------


class ProtectedFlowSpec(StrictModel):
    flow_id: str
    label: str = ""
    src_host: str | None = None
    dst_host: str | None = None
    max_latency_ms: float | None = Field(default=None, ge=0)
    max_packet_loss_pct: float | None = Field(default=None, ge=0, le=100)


class IntentSpec(StrictModel):
    """A structured performance intent, e.g. 'video h1->h4 under 50 ms'."""

    name: str = "video intent"
    traffic_class: FlowKind = FlowKind.VIDEO
    src_host: str
    dst_host: str
    max_latency_ms: float = Field(gt=0)
    max_packet_loss_pct: float | None = Field(default=None, ge=0, le=100)
    protected_flows: list[ProtectedFlowSpec] = Field(default_factory=list)
    policy: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


class ActionType(str, Enum):
    """The complete action allow-list. Nothing else may be executed."""

    REROUTE_FLOW = "reroute_flow"
    RESET_LAB = "reset_lab"
    # Read-only operations are not mutations and are listed separately.
    OBSERVE = "observe"
    EVALUATE_PATHS = "evaluate_paths"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RerouteAction(StrictModel):
    """The only network mutation this project performs."""

    action_type: Literal[ActionType.REROUTE_FLOW] = ActionType.REROUTE_FLOW
    flow_id: str = Field(description="Flow id being rerouted, e.g. 'F1'")
    src_host: str
    dst_host: str
    # Ordered list of node ids forming the new path (hosts included).
    path: list[str] = Field(min_length=2)
    priority: int = Field(default=100, ge=0, le=65535)
    reason: str = ""
    # Not part of the action semantics; used by the safety validator's
    # expiry/duplication guard only. Defaults to "now" so a freshly proposed
    # action always has an age.
    created_at: datetime = Field(default_factory=utcnow)

    @field_validator("path")
    @classmethod
    def _no_duplicates(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("path must not revisit a node")
        return v


ProposedAction = RerouteAction


class SafetyCheck(StrictModel):
    name: str
    passed: bool
    detail: str = ""
    # Human-readable evidence backing the check (never fabricated).
    evidence: dict[str, Any] = Field(default_factory=dict)


class ValidationResult(StrictModel):
    approved: bool
    checks: list[SafetyCheck] = Field(default_factory=list)
    rejection_reason: str | None = None
    risk_level: RiskLevel = RiskLevel.LOW
    requires_approval: bool = False
    validated_at: datetime = Field(default_factory=utcnow)

    @property
    def failed_checks(self) -> list[SafetyCheck]:
        return [c for c in self.checks if not c.passed]

    @property
    def checks_passed(self) -> int:
        return sum(1 for c in self.checks if c.passed)


class ExecutionResult(StrictModel):
    """Result of handing an approved action to the controller."""

    action_id: str
    action_type: ActionType
    status: Literal["executed", "failed", "skipped_awaiting_approval", "skipped_lab_offline"]
    controller_response: dict[str, Any] = Field(default_factory=dict)
    flow_rules_installed: int = Field(default=0, ge=0)
    error: str | None = None
    executed_at: datetime = Field(default_factory=utcnow)

    @property
    def ok(self) -> bool:
        return self.status == "executed" and self.flow_rules_installed > 0


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


class VerificationStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    UNVERIFIED = "unverified"


class EvidenceStatus(str, Enum):
    SUFFICIENT = "sufficient"
    INSUFFICIENT = "insufficient"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class EvidenceItem(StrictModel):
    key: str
    present: bool
    fresh: bool = True
    value: Any = None
    source: str = ""
    measured_at: datetime | None = None
    note: str = ""
    # Room for raw counter/evidence fields, so a check can carry the exact
    # numbers it was based on rather than a summary string only.
    evidence_extra: dict[str, Any] = Field(default_factory=dict)


class EvidenceAssessment(StrictModel):
    status: EvidenceStatus
    sufficient: bool
    items: list[EvidenceItem] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)
    summary: str = ""
    missing: list[str] = Field(default_factory=list)
    stale: list[str] = Field(default_factory=list)
    assessed_at: datetime = Field(default_factory=utcnow)


class VerificationOutcome(StrictModel):
    """Gate 3: did the *network* actually recover?"""

    status: VerificationStatus
    intent_satisfied: bool = False
    protected_ok: bool = True
    before: FlowTelemetry | None = None
    after: FlowTelemetry | None = None
    protected_flows: list[ProtectedFlowStatus] = Field(default_factory=list)
    reason: str = ""
    measured_at: datetime = Field(default_factory=utcnow)

    @property
    def verified_success(self) -> bool:
        return self.status is VerificationStatus.SUCCESS and self.intent_satisfied
