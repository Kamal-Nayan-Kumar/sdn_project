"""Regression tests for the defects the first review round found.

Each test names the failure it prevents. They are grouped by the paper
section the defect appeared in.
"""

from __future__ import annotations

import pytest

from app.agents.workflow import (
    _default_intent,
    _is_violated,
    _path_delay,
    _switch_nodes,
)
from app.safety.gates import (
    SafetyPolicy,
    latency_violation,
    validate_reroute,
    verify_recovery,
)
from app.schemas.domain import (
    ActionType,
    FlowTelemetry,
    LinkStatus,
    ProtectedFlowSpec,
    RerouteAction,
    VerificationStatus,
)

from .conftest import NOW, make_flows, make_intent, make_snapshot

VIDEO_FAST = ["h1", "s1", "s2", "s4", "h4"]
VIDEO_SLOW = ["h1", "s1", "s3", "s4", "h4"]


def action(path: list[str], **overrides) -> RerouteAction:
    fields = {
        "action_type": ActionType.REROUTE_FLOW,
        "flow_id": "h1->h4",
        "src_host": "h1",
        "dst_host": "h4",
        "path": path,
        "created_at": NOW,
    }
    fields.update(overrides)
    return RerouteAction(**fields)


def policy(**overrides) -> SafetyPolicy:
    fields = {"intent_src": "h1", "intent_dst": "h4"}
    fields.update(overrides)
    return SafetyPolicy(**fields)


# ---------------------------------------------------------------------------
# Section V-C, Safety Check 7: the protected-flow rule
# ---------------------------------------------------------------------------


class TestProtectedFlowCheck:
    def test_a_shared_destination_does_not_block_every_path(self):
        """Two flows that share a destination host share its access edge.

        The check compared *node* sets, so every path to the shared
        destination intersected the protected flow's node set and the gate
        rejected every legitimate repair. The comparison is over inter-switch
        edges.
        """
        voice = ProtectedFlowSpec(
            flow_id="h3->h4", label="voice", src_host="h3", dst_host="h4")
        pol = policy(protected_flows=[voice])
        # The voice flow runs entirely on s4, so it uses no inter-switch edge.
        snapshot = make_snapshot()
        result = validate_reroute(action(VIDEO_SLOW), snapshot=snapshot,
                                  policy=pol, run_id="r", action_id="a", now=NOW)
        assert result.approved, result.rejection_reason

    def test_a_path_crossing_the_protected_flows_link_is_rejected(self):
        """The check must still fire when it matters.

        A protected flow that traverses an inter-switch edge blocks any
        candidate that takes that edge.
        """
        voice = ProtectedFlowSpec(
            flow_id="h3->h4", label="voice", src_host="h3", dst_host="h4")
        pol = policy(protected_flows=[voice], protected_links=["s3-s4"])
        snapshot = make_snapshot()
        result = validate_reroute(action(VIDEO_SLOW), snapshot=snapshot,
                                  policy=pol, run_id="r", action_id="a", now=NOW)
        assert not result.approved
        assert "protected_flows_unmodified" in [
            c.name for c in result.failed_checks]


# ---------------------------------------------------------------------------
# Section V-C, Safety Check 8: the flow scope
# ---------------------------------------------------------------------------


class TestFlowScopeCheck:
    def test_an_action_for_the_wrong_flow_is_rejected(self):
        """Check 8 must be more than a restatement of check 3.

        `path_well_formed` checks the path against the action's own
        endpoints; `flow_scope` checks the action against the *intent's*
        endpoints, so a reroute of some other flow cannot pass.
        """
        snapshot = make_snapshot()
        result = validate_reroute(action(VIDEO_SLOW, src_host="h2",
                                         dst_host="h3"),
                                  snapshot=snapshot, policy=policy(),
                                  run_id="r", action_id="a", now=NOW)
        assert not result.approved
        assert "flow_scope" in [c.name for c in result.failed_checks]

    def test_the_right_flow_still_passes(self):
        snapshot = make_snapshot()
        result = validate_reroute(action(VIDEO_SLOW), snapshot=snapshot,
                                  policy=policy(), run_id="r", action_id="a",
                                  now=NOW)
        assert result.approved, result.rejection_reason


# ---------------------------------------------------------------------------
# Section V-D / Eq. (7): the verification gate
# ---------------------------------------------------------------------------


class TestVerificationGate:
    def test_a_protected_flow_breach_is_a_failure_even_after_an_improvement(self):
        """A recovery that harms a protected flow is not a partial success.

        The improvement test ran before the protected-flow test, so an
        action that improved the latency while breaking a protected flow was
        reported as a partial improvement. The protected-flow test now comes
        first, which is what the code's own comment always said it should.
        """
        from app.schemas.domain import ProtectedFlowStatus

        protected = [ProtectedFlowStatus(
            flow_id="h3->h4", label="voice", latency_ms=90.0,
            packet_loss_pct=0.0, max_latency_ms=20.0, ok=False,
            reason="the protected voice flow is outside its constraint",
            path_unmodified=True)]
        result = verify_recovery(
            intent=make_intent(max_latency_ms=50.0),
            before=make_flows(latency_ms=120.0),
            after=make_flows(latency_ms=30.0),
            protected=[protected[0]], protected_statuses=protected)
        assert result.status is VerificationStatus.FAILED

    def test_the_gate_uses_the_same_predicate_as_the_evidence_gate(self):
        """Gate 3 must judge the post-action latency the way Gate 1 does.

        The verification gate compared the *mean* with the target while the
        evidence gate compared the sample distribution, so the two gates
        could disagree about the same measurement.
        """
        intent = make_intent(max_latency_ms=50.0)
        # One slow sample out of ten is noise, not a breach.
        flow = make_flows(latency_ms=45.0, samples=[45.0] * 9 + [80.0])
        assert _is_violated(flow, intent) is False
        result = verify_recovery(intent=intent,
                                 before=make_flows(latency_ms=120.0),
                                 after=flow)
        assert result.status is VerificationStatus.SUCCESS

        # Three slow samples out of ten are a breach.
        breach = make_flows(latency_ms=55.0, samples=[45.0] * 7 + [80.0] * 3)
        assert _is_violated(breach, intent) is True
        result = verify_recovery(intent=intent,
                                 before=make_flows(latency_ms=120.0),
                                 after=breach)
        assert result.status is VerificationStatus.PARTIAL


# ---------------------------------------------------------------------------
# Section VI / Algorithm 1: the planner must not make things worse
# ---------------------------------------------------------------------------


class TestPlannerImprovement:
    def test_the_path_delay_helper_sums_switch_edges_only(self):
        snapshot = make_snapshot()
        # s1-s2 and s2-s4 are 5 ms each in the test topology.
        assert _path_delay(snapshot, ["s1", "s2", "s4"]) == pytest.approx(10.0)
        # The host access edges are not switch edges.
        assert _path_delay(snapshot, ["h1", "s1"]) == pytest.approx(0.0)

    def test_switch_nodes_strips_the_hosts(self):
        assert _switch_nodes(["h1", "s1", "s3", "s4", "h4"]) == ["s1", "s3", "s4"]


# ---------------------------------------------------------------------------
# The default intent must declare the protected flow
# ---------------------------------------------------------------------------


class TestDefaultIntent:
    def test_the_protected_voice_flow_is_declared(self):
        """A flow nobody declares is a flow nobody protects.

        The project describes h3->h4 as the protected voice flow, but the
        default intent left `protected_flows` empty, so the protected-flow
        machinery was never exercised by a real run.
        """
        intent = _default_intent()
        assert intent.protected_flows
        spec = intent.protected_flows[0]
        assert (spec.src_host, spec.dst_host) == ("h3", "h4")
        assert spec.max_latency_ms is not None


# ---------------------------------------------------------------------------
# The shared predicate
# ---------------------------------------------------------------------------


class TestSharedPredicate:
    def test_the_two_gates_call_the_same_rule(self):
        flow = make_flows(latency_ms=46.0, samples=[51.0] * 3 + [40.0] * 7)
        assert latency_violation(flow, 50.0) is True

    def test_an_unmeasurable_flow_is_unknown(self):
        assert latency_violation(None, 50.0) is None
        assert latency_violation(make_flows(latency_ms=-1.0), 50.0) is None
