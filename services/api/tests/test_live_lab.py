"""Tests that require a running network lab.

Excluded by default:
    python -m pytest -m "not live"
Run with:
    LAB_API_TOKEN=$(cat /tmp/opencode/lab-token) python -m pytest -m live
"""

from __future__ import annotations

import pytest

from app.services.lab_bridge import LabBridgeClient, LabUnavailable
from app.tools import _links_of, run_tool

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def lab() -> LabBridgeClient:
    try:
        client = LabBridgeClient()
        client.get("/v1/status")
    except LabUnavailable as exc:
        pytest.skip(f"network lab not reachable: {exc}")
    return client


def test_lab_is_reachable(lab):
    status = lab.get("/v1/status")
    assert status["running"] is True
    assert status["controller_reachable"] is True


def test_all_switches_are_connected(lab):
    status = lab.get("/v1/status")
    assert status["connected_switches"] >= 4, (
        "a lab with fewer than 4 datapaths cannot demonstrate a reroute")


def test_topology_has_both_alternative_paths(lab):
    paths = lab.candidate_paths("h1", "h4")
    # h1->s1->{s2|s3|s2-s3}->s4->h4: more than one route must exist, otherwise
    # the recovery demo has nothing to switch to.
    assert len(paths) >= 2, paths


def test_snapshot_reads_as_a_domain_model(lab):
    result = run_tool("get_network_snapshot", {"latency_count": 2}, lab)
    assert result.ok, result.error
    from app.schemas.domain import NetworkSnapshot

    snapshot = NetworkSnapshot.model_validate_json(result.outputs["snapshot"])
    flow = next((f for f in snapshot.flows if f.flow_id == "h1->h4"), None)
    assert flow is not None, "the demo flow h1->h4 is not measured"
    assert flow.latency_ms >= 0
    assert flow.packet_loss_pct < 100.0, "no traffic is flowing at all"
    assert snapshot.controller_reachable


def test_every_reported_flow_path_resolves_to_real_links(lab):
    """Without a resolvable path the evidence gate cannot name a suspect link,
    and the agent abstains rather than rerouting.

    The lab reports a flow's path as switches only, while a candidate path
    includes its host endpoints -- both must resolve to edges the topology
    knows about.
    """
    raw = lab.get("/v1/snapshot")
    ids = {l["link_id"] for l in _links_of(raw)}

    for key, flow in raw["flows"].items():
        path = flow.get("path") or []
        assert path, f"{key} reports no path"
        edges = [f"{min(a, b)}-{max(a, b)}" for a, b in zip(path, path[1:])]
        unknown = set(edges) - ids
        assert not unknown, (
            f"{key} path references links the topology does not contain: "
            f"{sorted(unknown)}")

        # The path must contain at least one link the snapshot reports
        # telemetry for, otherwise "where is the latency accruing" has no
        # answer and the agent cannot justify a reroute.
        switches = [n for n in path if not n.startswith("h")]
        if len(switches) >= 2:
            switch_edges = [f"{min(a, b)}-{max(a, b)}"
                            for a, b in zip(switches, switches[1:])]
            assert set(switch_edges) & ids, (
                f"{key} path has no edge the snapshot reports on: "
                f"{switch_edges}")
