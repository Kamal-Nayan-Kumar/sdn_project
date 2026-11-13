"""The paper's equations, checked against the code.

Every equation in `final_submission/paper/paper.tex` claims to be the
implementation. These tests are that claim, made executable: each one states
the equation, names the function it describes, and fails if the two drift
apart.

They exist because a formula that is merely plausible is worse than no
formula at all -- it looks like a specification while describing something
else. A reader who cannot check the correspondence has to trust it.

    Eq. (1)  D(p)            -> multipath.path_delay
    Eq. (2)  RTT(p)          -> the measured round-trip time
    Eq. (3)  I               -> schemas.domain.IntentSpec
    Eq. (4)  v(f, I)         -> agents.workflow._is_violated
    Eq. (5)  A(p)            -> tools._evaluate_paths / PathCandidate
    Eq. (6)  score(p)        -> schemas.domain.PathCandidate.score
    Eq. (7)  V(I, f-, f+, Q) -> safety.gates.verify_recovery
    Eq. (8)  the nine checks -> safety.gates.validate_reroute
    Eq. (9)  the graph       -> agents.graph.build_graph
"""

from __future__ import annotations

import pytest

from app.agents.graph import build_graph
from app.agents.workflow import VIOLATION_SAMPLE_FRACTION, _is_violated
from app.safety.gates import (
    MAX_PATH_HOPS,
    PathCandidate,
    assess_evidence,
    validate_reroute,
    verify_recovery,
)
from app.schemas.domain import (
    ActionType,
    EvidenceStatus,
    FlowTelemetry,
    IntentSpec,
    LinkStatus,
    NetworkSnapshot,
    ProtectedFlowSpec,
    RerouteAction,
    VerificationStatus,
)

from .conftest import NOW, make_flows, make_intent, make_snapshot

# ---------------------------------------------------------------------------
# Eq. (4) -- the violation predicate
# ---------------------------------------------------------------------------


class TestViolationEquation:
    """Eq. (4): $v(f,I)$, evaluated in order.

    unknown  if $f$ is absent or unmeasured
    true     if $p_f > 0$
    true     if $n \\ge 2$ and $|\\{i: s_i>\\tau\\}| \\ge \\gamma n$
    false    if $n \\ge 2$ and $|\\{i: s_i>\\tau\\}| < \\gamma n$
    true     if $n < 2$ and the peak $> \\tau$
    true     if $n < 2$, no peak, and the latency $> \\tau$
    false    otherwise
    """

    def test_gamma_is_the_documented_quarter(self):
        assert VIOLATION_SAMPLE_FRACTION == 0.25

    def test_absent_flow_is_unknown(self):
        """The first line of Eq. (4)."""
        assert _is_violated(None, make_intent()) is None

    def test_unmeasured_latency_is_unknown(self):
        assert _is_violated(make_flows(latency_ms=-1.0), make_intent()) is None

    def test_loss_alone_is_a_violation(self):
        """The second line: loss, not latency."""
        flow = make_flows(latency_ms=10.0, packet_loss_pct=5.0)
        assert _is_violated(flow, make_intent(max_latency_ms=50.0)) is True

    def test_distribution_rule_when_there_are_samples(self):
        """The third and fourth lines."""
        intent = make_intent(max_latency_ms=50.0)
        # 3 of 10 samples over the target: 0.3 >= 0.25 -> violated.
        over = make_flows(latency_ms=46.0, samples=[51.0] * 3 + [40.0] * 7)
        assert _is_violated(over, intent) is True
        # 2 of 10 samples over the target: 0.2 < 0.25 -> not violated.
        under = make_flows(latency_ms=46.0, samples=[51.0] * 2 + [40.0] * 8)
        assert _is_violated(under, intent) is False

    def test_distribution_rule_ignores_the_mean(self):
        """The mean must not be consulted when the samples are available."""
        intent = make_intent(max_latency_ms=50.0)
        flow = make_flows(latency_ms=30.0, samples=[51.0] * 3 + [40.0] * 7)
        assert _is_violated(flow, intent) is True

    def test_fallback_uses_the_peak_when_there_is_no_distribution(self):
        """The fifth line."""
        intent = make_intent(max_latency_ms=50.0)
        flow = make_flows(latency_ms=20.0, max_latency_ms=80.0, samples=[])
        assert _is_violated(flow, intent) is True

    def test_fallback_uses_the_latency_when_there_is_no_peak(self):
        """The sixth line: with no samples and no peak, the reported latency
        is the only number there is."""
        intent = make_intent(max_latency_ms=50.0)
        assert _is_violated(make_flows(latency_ms=80.0), intent) is True
        assert _is_violated(make_flows(latency_ms=20.0), intent) is False


# ---------------------------------------------------------------------------
# Eq. (5) and (6) -- candidate ranking
# ---------------------------------------------------------------------------


class TestRankingEquations:
    """Eq. (5): $A(p)$; Eq. (6): $\\mathrm{score}(p)$."""

    def test_a_path_crossing_a_degraded_link_is_not_congestion_free(self):
        clean = PathCandidate(nodes=["h1", "s1", "s3", "s4", "h4"],
                              links=["s1-s3", "s3-s4"], total_delay_ms=12.0)
        degraded = PathCandidate(nodes=["h1", "s1", "s2", "s4", "h4"],
                                 links=["s1-s2", "s2-s4"], total_delay_ms=8.0,
                                 avoids_congestion=False)
        assert clean.avoids_congestion is True
        assert degraded.avoids_congestion is False

    def test_score_is_infinite_for_a_degraded_path(self):
        """The first line of Eq. (6)."""
        degraded = PathCandidate(nodes=["h1", "s1", "s2", "s4", "h4"],
                                 links=["s2-s4"], total_delay_ms=1.0,
                                 avoids_congestion=False)
        assert degraded.score == float("inf")

    def test_score_is_delay_plus_a_tenth_of_the_peak_utilisation(self):
        """The second line of Eq. (6)."""
        candidate = PathCandidate(nodes=["h1", "s1", "s3", "s4", "h4"],
                                  links=["s1-s3", "s3-s4"], total_delay_ms=12.0,
                                  max_utilization_pct=40.0)
        assert candidate.score == pytest.approx(12.0 + 0.4)

    def test_a_degraded_path_never_wins_however_fast_it_is(self):
        clean = PathCandidate(nodes=["a1", "a2"], links=["x"],
                              total_delay_ms=99.0)
        degraded = PathCandidate(nodes=["b1", "b2"], links=["y"],
                                 total_delay_ms=1.0, avoids_congestion=False)
        assert min(clean.score, degraded.score) == clean.score


# ---------------------------------------------------------------------------
# Eq. (7) -- the recovery verdict
# ---------------------------------------------------------------------------


class TestVerificationEquation:
    """Eq. (7): $\\mathcal{V}$, evaluated as an ordered rule set."""

    def _outcome(self, before, after, intent=None, protected=None):
        intent = intent or make_intent(max_latency_ms=50.0)
        return verify_recovery(
            intent=intent, before=before, after=after,
            protected=protected or [], protected_statuses=protected or [])

    def test_no_post_action_measurement_is_unverified(self):
        """The first rule."""
        result = self._outcome(make_flows(latency_ms=120.0), None)
        assert result.status is VerificationStatus.UNVERIFIED

    def test_success_needs_latency_loss_and_protected_flows(self):
        """The second rule."""
        result = self._outcome(make_flows(latency_ms=120.0),
                               make_flows(latency_ms=30.0))
        assert result.status is VerificationStatus.SUCCESS

    def test_latency_ok_but_loss_broken_is_partial(self):
        """The third rule -- a distinct outcome, not a success."""
        intent = make_intent(max_latency_ms=50.0, max_packet_loss_pct=1.0)
        result = self._outcome(
            make_flows(latency_ms=120.0, packet_loss_pct=0.0),
            make_flows(latency_ms=20.0, packet_loss_pct=25.0),
            intent=intent)
        assert result.status is VerificationStatus.PARTIAL

    def test_improvement_without_reaching_the_target_is_partial(self):
        """The fourth rule."""
        result = self._outcome(make_flows(latency_ms=120.0),
                               make_flows(latency_ms=70.0))
        assert result.status is VerificationStatus.PARTIAL

    def test_no_improvement_is_failed(self):
        """The last rule."""
        result = self._outcome(make_flows(latency_ms=120.0),
                               make_flows(latency_ms=120.0))
        assert result.status is VerificationStatus.FAILED

    def test_a_regression_is_failed(self):
        result = self._outcome(make_flows(latency_ms=30.0),
                               make_flows(latency_ms=200.0))
        assert result.status is VerificationStatus.FAILED


# ---------------------------------------------------------------------------
# Eq. (8) -- the nine named safety checks
# ---------------------------------------------------------------------------

#: The nine checks, in the order the paper lists them.
PAPER_CHECKS = [
    "operation_allowlisted",
    "endpoints_exist",
    "path_well_formed",
    "hop_budget",
    "all_links_operational",
    "capacity_respected",
    "protected_flows_unmodified",
    "flow_scope",
    "request_not_expired",
]


class TestSafetyEquation:
    def test_the_validator_emits_exactly_the_nine_documented_checks(self):
        """Eq. (8), and the paper's claim that there are nine of them."""
        action = RerouteAction(
            action_type=ActionType.REROUTE_FLOW, flow_id="h1->h4",
            src_host="h1", dst_host="h4",
            path=["h1", "s1", "s3", "s4", "h4"], created_at=NOW)
        result = validate_reroute(action, snapshot=make_snapshot(),
                                  policy=None or __import__(
                                      "app.safety.gates", fromlist=["SafetyPolicy"]
                                  ).SafetyPolicy(),
                                  run_id="r", action_id="a", now=NOW)
        assert [c.name for c in result.checks] == PAPER_CHECKS
        assert result.checks_passed == 9

    def test_the_hop_budget_is_the_documented_one(self):
        assert MAX_PATH_HOPS == 6

    def test_a_degraded_link_fails_the_capacity_check(self):
        """Eq. (8), check 6: a degraded link is a capacity violation even
        when its utilisation is still zero."""
        snapshot = make_snapshot().model_copy(
            update={"congested_links": ["s2-s4"]})
        action = RerouteAction(
            action_type=ActionType.REROUTE_FLOW, flow_id="h1->h4",
            src_host="h1", dst_host="h4",
            path=["h1", "s1", "s2", "s4", "h4"], created_at=NOW)
        result = validate_reroute(action, snapshot=snapshot,
                                  policy=__import__(
                                      "app.safety.gates", fromlist=["SafetyPolicy"]
                                  ).SafetyPolicy(),
                                  run_id="r", action_id="a", now=NOW)
        assert not result.approved
        assert "capacity_respected" in [c.name for c in result.failed_checks]

    def test_a_protected_flows_path_may_not_be_shared(self):
        """Eq. (8), check 7: sharing a node with a protected flow counts."""
        from app.safety.gates import SafetyPolicy

        snapshot = make_snapshot()
        policy = SafetyPolicy(
            protected_flows=[ProtectedFlowSpec(
                flow_id="h3->h4", label="voice", src_host="h3", dst_host="h4")],
            protected_links=["s3-s4"])
        action = RerouteAction(
            action_type=ActionType.REROUTE_FLOW, flow_id="h1->h4",
            src_host="h1", dst_host="h4",
            path=["h1", "s1", "s3", "s4", "h4"], created_at=NOW)
        result = validate_reroute(action, snapshot=snapshot, policy=policy,
                                  run_id="r", action_id="a", now=NOW)
        assert not result.approved
        assert "protected_flows_unmodified" in [
            c.name for c in result.failed_checks]


# ---------------------------------------------------------------------------
# Eq. (9) -- the graph the algorithm describes
# ---------------------------------------------------------------------------


class _StubLabClient:
    def __init__(self) -> None:
        self.token = ""
        self.socket_path = "/tmp/does-not-exist.sock"

    def _request(self, *args, **kwargs):  # pragma: no cover - never called
        raise AssertionError("the graph must not talk to a lab to compile")


class TestGraphEquation:
    def test_the_graph_has_the_thirteen_documented_nodes(self):
        """The paper says thirteen nodes and names them."""
        compiled = build_graph(_StubLabClient())
        nodes = set(compiled.get_graph().nodes)
        # The compiled graph also carries the two LangGraph sentinels.
        nodes -= {"__start__", "__end__"}
        assert nodes == {"intent", "observe", "evidence", "diagnose", "validate",
                         "execute", "observe_after", "verify", "abstain",
                         "satisfied", "hold", "stop", "finish"}

    def test_the_graph_has_the_documented_edges(self):
        """Every edge the algorithm names must be in the compiled graph."""
        compiled = build_graph(_StubLabClient())
        graph = compiled.get_graph()
        edges = {(e.source, e.target) for e in graph.edges}

        # The main flow, in order.
        assert ("intent", "observe") in edges
        assert ("observe", "evidence") in edges
        assert ("diagnose", "validate") in edges
        assert ("execute", "observe_after") in edges
        assert ("observe_after", "verify") in edges
        assert ("verify", "finish") in edges
        # The branches.
        assert ("evidence", "satisfied") in edges
        assert ("evidence", "abstain") in edges
        assert ("evidence", "diagnose") in edges
        assert ("validate", "execute") in edges
        assert ("validate", "hold") in edges
        assert ("validate", "stop") in edges
        assert ("intent", "stop") in edges
        assert ("abstain", "stop") in edges
        assert ("satisfied", "stop") in edges
        assert ("stop", "__end__") in edges
        assert ("hold", "__end__") in edges
        assert ("finish", "__end__") in edges

    def test_the_verification_node_is_unconditionally_after_execution(self):
        """The paper's claim that no change can go unmeasured."""
        compiled = build_graph(_StubLabClient())
        edges = {(e.source, e.target) for e in compiled.get_graph().edges}
        assert ("execute", "observe_after") in edges
        assert ("observe_after", "verify") in edges
        # No edge lets the execution node reach END directly.
        assert ("execute", "__end__") not in edges
        assert ("execute", "finish") not in edges


# ---------------------------------------------------------------------------
# The evidence gate's freshness and confidence policy
# ---------------------------------------------------------------------------


class TestEvidenceEquation:
    def test_a_stale_measurement_is_never_sufficient(self):
        """Eq. (4)'s freshness requirement, in the evidence gate."""
        result = assess_evidence(
            snapshot=make_snapshot(latency_ms=120.0,
                                   utilisation={"s2-s4": 95.0},
                                   age_seconds=3600.0),
            intent=make_intent(), max_age_seconds=5.0, min_confidence=0.6,
            now=NOW)
        assert result.status is EvidenceStatus.STALE
        assert not result.sufficient

    def test_a_fresh_measurement_with_a_named_suspect_is_sufficient(self):
        result = assess_evidence(
            snapshot=make_snapshot(latency_ms=120.0,
                                   utilisation={"s2-s4": 95.0}),
            intent=make_intent(), max_age_seconds=5.0, min_confidence=0.6,
            now=NOW)
        assert result.status is EvidenceStatus.SUFFICIENT
        assert result.sufficient
        assert "suspect_link:s2-s4" in [i.key for i in result.items]
