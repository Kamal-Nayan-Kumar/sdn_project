"""Tests for the pure logic that decides *what the evidence means*.

These functions sit between raw telemetry and a proposed network change. Each
test that came out of a real defect found while running the agent against a
live lab says so in its docstring.
"""

from __future__ import annotations

from app.agents.workflow import _is_violated, parse_intent_deterministically
from app.safety.gates import _links_along
from app.schemas.domain import FlowKind, FlowTelemetry
from app.tools import _links_of, _pick

from .conftest import make_flows, make_intent, make_snapshot


# ---------------------------------------------------------------------------
# Deriving paths
# ---------------------------------------------------------------------------


class TestLinksAlong:
    def test_node_path_becomes_edge_ids(self):
        assert _links_along(["h1", "s1", "s2", "s4", "h4"]) == {
            "h1-s1", "s1-s2", "s2-s4", "h4-s4",
        }

    def test_edge_ids_are_order_insensitive(self):
        """The lab names a link "s1-s2" whichever endpoint is the lower id, so
        a reversed traversal must still resolve."""
        assert _links_along(["s2", "s1"]) == {"s1-s2"}
        assert _links_along(["h4", "s4"]) == {"h4-s4"}

    def test_single_node_path_has_no_edges(self):
        assert _links_along(["s1"]) == set()
        assert _links_along([]) == set()


class TestLinksOf:
    """The lab serves snapshot links as an object keyed by link_id while
    /v1/topology serves them as an array. A consumer that iterated the object
    would get keys, and then fail far from the cause with 'string indices
    must be integers'.
    """

    def test_object_shape(self):
        payload = {
            "links": {
                "s1-s2": {"link_id": "s1-s2", "utilization_pct": 12.5},
                "s2-s4": {"link_id": "s2-s4", "utilization_pct": 0.0},
            },
        }
        out = _links_of(payload)
        assert {l["link_id"] for l in out} == {"s1-s2", "s2-s4"}
        assert out[0]["utilization_pct"] in (12.5, 0.0)

    def test_array_shape(self):
        payload = {"links": [{"link_id": "s1-s2", "utilization_pct": 1.0}]}
        assert [l["link_id"] for l in _links_of(payload)] == ["s1-s2"]

    def test_missing_key_is_empty(self):
        assert _links_of({}) == []

    def test_object_entries_that_are_not_dicts_are_skipped(self):
        assert _links_of({"links": {"s1-s2": None, "bad": 3}}) == []


class TestPick:
    """The lab sends presentation fields the strict domain models refuse.
    Extras are dropped rather than the schema being loosened, so the schema
    stays the contract."""

    def test_keeps_declared_fields_only(self):
        from app.schemas.domain import TopologyNode

        source = {"id": "h1", "kind": "host", "label": "h1",
                  "role": "video_source", "ip": "10.0.0.1"}
        # label and ip are genuinely declared on TopologyNode, so they stay;
        # `role` is presentation the model does not define, so it is dropped.
        assert _pick(source, TopologyNode) == {
            "id": "h1", "kind": "host", "label": "h1", "ip": "10.0.0.1"}

    def test_defaults_do_not_override_source(self):
        from app.schemas.domain import TopologyLink

        source = {"id": "s1-s2", "source": "s1", "target": "s2", "status": "down"}
        assert _pick(source, TopologyLink, status="up")["status"] == "down"
        assert _pick({"id": "x", "source": "a", "target": "b"},
                     TopologyLink, status="up")["status"] == "up"


# ---------------------------------------------------------------------------
# Detecting a violation
# ---------------------------------------------------------------------------


class TestIsViolated:
    def test_over_intent_is_violated(self):
        flow = make_flows(latency_ms=120.0)
        assert _is_violated(flow, make_intent(max_latency_ms=50.0)) is True

    def test_within_intent_is_not_violated(self):
        flow = make_flows(latency_ms=30.0)
        assert _is_violated(flow, make_intent(max_latency_ms=50.0)) is False

    def test_packet_loss_is_a_violation_even_when_fast(self):
        """Latency is not the only intent term."""
        flow = make_flows(latency_ms=20.0, packet_loss_pct=5.0)
        assert _is_violated(flow, make_intent(max_latency_ms=50.0)) is True

    def test_no_measurement_is_unknown_not_not_violated(self):
        """`None` means "could not measure". Collapsing it into `False` would
        make an unmeasured network look healthy."""
        assert _is_violated(None, make_intent()) is None


# ---------------------------------------------------------------------------
# Deterministic intent parsing
# ---------------------------------------------------------------------------


class TestDeterministicIntentParsing:
    """Without an LLM the agent previously substituted the default 50 ms intent
    for whatever the operator had asked for. Only stated facts may be used;
    anything absent keeps the default rather than being invented."""

    def test_stated_latency_is_honoured(self):
        intent = parse_intent_deterministically(
            "keep video h1 to h4 under 10 ms")
        assert intent.max_latency_ms == 10.0

    def test_stated_endpoints_are_honoured(self):
        intent = parse_intent_deterministically(
            "protect the voice flow from h3 to h4")
        assert (intent.src_host, intent.dst_host) == ("h3", "h4")

    def test_stated_class_is_honoured(self):
        assert parse_intent_deterministically("voice h1->h4").traffic_class \
            is FlowKind.VOICE

    def test_unstated_facts_keep_the_default(self):
        intent = parse_intent_deterministically("make it faster please")
        assert intent.src_host == "h1"
        assert intent.dst_host == "h4"
        assert intent.max_latency_ms == 50.0

    def test_units_and_spacing_are_tolerated(self):
        assert parse_intent_deterministically(
            "under 40ms").max_latency_ms == 40.0
        assert parse_intent_deterministically(
            "under 40 milliseconds").max_latency_ms == 40.0

    def test_a_zero_or_negative_target_is_ignored(self):
        assert parse_intent_deterministically("under 0 ms").max_latency_ms == 50.0

    def test_default_intent_is_left_untouched(self):
        base = make_intent(max_latency_ms=50.0)
        parse_intent_deterministically("h1 to h4 under 10 ms", base)
        assert base.max_latency_ms == 50.0

    def test_result_is_immutable_input_safe(self):
        """IntentSpec is frozen; the parser must compose a new object rather
        than raise."""
        intent = parse_intent_deterministically("h2 to h4")
        assert intent.src_host == "h2" and intent.dst_host == "h4"
