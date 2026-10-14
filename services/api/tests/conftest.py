"""Shared fixtures for the safety-gate tests.

The gates are the trust boundary of this project: they are the only thing
standing between a model's proposal and a live network change. They are pure and
deterministic, so they are tested against hand-built snapshots rather than the
network lab -- a test that needs a running lab cannot tell a policy bug from a
networking one.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.schemas.domain import (
    FlowKind,
    FlowTelemetry,
    IntentSpec,
    LinkStatus,
    LinkTelemetry,
    NetworkSnapshot,
    NodeKind,
    Topology,
    TopologyLink,
    TopologyNode,
    VerificationStatus,
)

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _now(offset_seconds: float = 0.0) -> datetime:
    return NOW + timedelta(seconds=offset_seconds)


def make_topology() -> Topology:
    """The project's multipath diamond, including host links.

    Mirrors `network-lab/topologies/multipath.py`: h1,h2 on s1; h3,h4 on s4;
    two independent s1->s4 paths with different delays.
    """
    nodes = [
        TopologyNode(id=h, kind=NodeKind.HOST, label=h)
        for h in ("h1", "h2", "h3", "h4")
    ] + [
        TopologyNode(id=s, kind=NodeKind.SWITCH, label=s)
        for s in ("s1", "s2", "s3", "s4")
    ]
    links = [
        # host access
        TopologyLink(id="h1-s1", source="h1", target="s1", delay_ms=0.1),
        TopologyLink(id="h2-s1", source="h2", target="s1", delay_ms=0.1),
        TopologyLink(id="h3-s4", source="h3", target="s4", delay_ms=0.1),
        TopologyLink(id="h4-s4", source="h4", target="s4", delay_ms=0.1),
        # the fast path (congestible)
        TopologyLink(id="s1-s2", source="s1", target="s2",
                     delay_ms=5.0, congestible=True),
        TopologyLink(id="s2-s4", source="s2", target="s4",
                     delay_ms=5.0, congestible=True),
        # the slow alternative
        TopologyLink(id="s1-s3", source="s1", target="s3", delay_ms=8.0),
        TopologyLink(id="s3-s4", source="s3", target="s4", delay_ms=12.0),
        # cross link, neither congestible nor used by the demo
        TopologyLink(id="s2-s3", source="s2", target="s3", delay_ms=5.0),
    ]
    return Topology(nodes=nodes, links=links, controller="test")


def make_snapshot(
    *,
    latency_ms: float | None = 40.0,
    packet_loss_pct: float = 0.0,
    path: list[str] | None = None,
    utilisation: dict[str, float] | None = None,
    age_seconds: float = 0.0,
    link_status: dict[str, LinkStatus] | None = None,
    flows: list[FlowTelemetry] | None = None,
) -> NetworkSnapshot:
    """A snapshot with sensible defaults; override only what a test cares about."""
    utilisation = utilisation or {}
    link_status = link_status or {}

    if flows is None:
        flows = [FlowTelemetry(
            flow_id="h1->h4", src_host="h1", dst_host="h4",
            kind=FlowKind.VIDEO, path=path if path is not None else ["s1", "s2", "s4"],
            latency_ms=latency_ms if latency_ms is not None else -1.0,
            packet_loss_pct=packet_loss_pct,
            measured_at=_now(-age_seconds),
        )]

    links = [
        LinkTelemetry(
            link_id=l.id,
            utilization_pct=utilisation.get(l.id, 0.0),
            capacity_mbps=l.capacity_mbps,
            latency_ms=l.delay_ms,
            status=link_status.get(l.id, l.status),
            measured_at=_now(-age_seconds),
        )
        for l in make_topology().links
    ]

    return NetworkSnapshot(
        topology=make_topology(), links=links, flows=flows,
        captured_at=_now(),
    )


def make_intent(**overrides) -> IntentSpec:
    base = {
        "name": "video latency intent",
        "traffic_class": FlowKind.VIDEO,
        "src_host": "h1",
        "dst_host": "h4",
        "max_latency_ms": 50.0,
        "protected_flows": [],
    }
    base.update(overrides)
    return IntentSpec(**base)


def make_flows(
    *,
    latency_ms: float,
    packet_loss_pct: float = 0.0,
    flow_id: str = "h1->h4",
    src: str = "h1",
    dst: str = "h4",
    path: list[str] | None = None,
    age_seconds: float = 0.0,
) -> FlowTelemetry:
    """A single flow measurement."""
    return FlowTelemetry(
        flow_id=flow_id, src_host=src, dst_host=dst, kind=FlowKind.VIDEO,
        path=path if path is not None else ["h1", "s1", "s2", "s4", "h4"],
        latency_ms=latency_ms,
        packet_loss_pct=packet_loss_pct,
        measured_at=_now(-age_seconds),
    )


@pytest.fixture
def topology() -> Topology:
    return make_topology()


@pytest.fixture
def intent() -> IntentSpec:
    return make_intent()


@pytest.fixture
def now() -> datetime:
    return NOW