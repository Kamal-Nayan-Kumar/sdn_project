"""Tests for the three deterministic trustworthiness gates.

These are the project's trust boundary: the only thing standing between a
model's proposal and a live network change. They are pure functions over a
snapshot, so they are tested directly -- no lab, no model, no mocks. A gate
that fails here would let an unverified or unsafe change reach a real network.

The recurring theme of the negative tests is **abstention**: when evidence is
missing, stale or unsupportable, the correct behaviour is to decline, and the
tests assert that it does.
"""

from __future__ import annotations

import pytest

from app.safety.gates import (
    ALLOWED_OPERATIONS,
    MAX_PATH_HOPS,
    SafetyPolicy,
    assess_evidence,
    check_protected_flows,
    validate_reroute,
    verify_recovery,
)
from app.schemas.domain import (
    ActionType,
    EvidenceStatus,
    FlowTelemetry,
    LinkStatus,
    ProtectedFlowSpec,
    RerouteAction,
    VerificationStatus,
)

from .conftest import NOW, make_flows, make_intent, make_snapshot

FAST = ["h1", "s1", "s2", "s4", "h4"]
SLOW = ["h1", "s1", "s3", "s4", "h4"]

# Thresholds passed explicitly so the tests do not depend on .env.
EVIDENCE_KWARGS = dict(max_age_seconds=5.0, min_confidence=0.6, now=NOW)


def evidence(snapshot, **kwargs):
    return assess_evidence(
        snapshot=snapshot, intent=make_intent(), **EVIDENCE_KWARGS, **kwargs)


def action(path: list[str], **overrides) -> RerouteAction:
    fields = {
        "action_type": ActionType.REROUTE_FLOW,
        "flow_id": "h1->h4",
        "src_host": "h1",
        "dst_host": "h4",
        "path": path,
        "reason": "avoid the congested link",
        # Pinned so the validator's age guard sees a fixed input; otherwise it
        # defaults to wall-clock time and every call differs slightly.
        "created_at": NOW,
    }
    fields.update(overrides)
    return RerouteAction(**fields)


def validate(candidate, snapshot=None, policy=None):
    return validate_reroute(
        candidate,
        snapshot=snapshot if snapshot is not None else make_snapshot(),
        policy=policy or SafetyPolicy(),
        run_id="r1",
        action_id="act-1",
        now=NOW,
    )


def protected_voice() -> list[ProtectedFlowSpec]:
    return [ProtectedFlowSpec(
        flow_id="h3->h4", label="voice", src_host="h3", dst_host="h4",
        max_latency_ms=20.0, max_packet_loss_pct=1.0,
    )]


# ---------------------------------------------------------------------------
# Gate 1 - evidence
# ---------------------------------------------------------------------------


class TestAssessEvidence:
    def test_congested_link_is_sufficient(self):
        result = evidence(make_snapshot(
            latency_ms=120.0, path=["s1", "s2", "s4"],
            utilisation={"s2-s4": 95.0}))
        assert result.status is EvidenceStatus.SUFFICIENT
        assert result.sufficient
        assert "suspect_link:s2-s4" in [i.key for i in result.items]

    def test_path_delay_without_congestion_is_sufficient(self):
        """A pure latency violation has no congested link, but the current path
        is still a nameable diagnosis. Without this the agent could never act
        on a slow path at all."""
        result = evidence(make_snapshot(
            latency_ms=120.0, path=["s1", "s3", "s4"], utilisation={}))
        assert result.sufficient, result.summary
        assert "suspect_link:s1-s3" in [i.key for i in result.items]

    def test_no_snapshot_abstains(self):
        result = evidence(None)
        assert not result.sufficient
        assert result.status is not EvidenceStatus.SUFFICIENT

    def test_stale_measurement_is_not_sufficient(self):
        """Freshness is what makes evidence actionable; a stale reading must
        never authorise a change."""
        result = evidence(make_snapshot(
            latency_ms=120.0, utilisation={"s2-s4": 95.0},
            age_seconds=3600.0))
        assert not result.sufficient
        assert result.status is EvidenceStatus.STALE

    def test_missing_flow_measurement_abstains(self):
        assert not evidence(make_snapshot(flows=[])).sufficient

    def test_unknown_endpoint_abstains(self):
        """An intent naming a host the topology does not contain is not
        actionable, however healthy the telemetry looks."""
        snapshot = make_snapshot(latency_ms=120.0, utilisation={"s2-s4": 95.0})
        result = assess_evidence(
            snapshot=snapshot,
            intent=make_intent(src_host="h1", dst_host="h9"),
            **EVIDENCE_KWARGS)
        assert not result.sufficient

    def test_suspect_link_absent_from_topology_abstains(self):
        """A suspect the topology does not contain means the diagnosis is not
        grounded in the observed network."""
        result = evidence(
            make_snapshot(latency_ms=120.0, utilisation={"s2-s4": 95.0}),
            suspected_links=["s9-s9"])
        assert not result.sufficient

    def test_confidence_is_reproducible(self):
        """Confidence is a hand-rolled deterministic blend, so it must be
        stable across runs. A model-derived score would not be."""
        snapshot = make_snapshot(latency_ms=120.0, utilisation={"s2-s4": 95.0})
        a = evidence(snapshot)
        b = evidence(snapshot)
        assert a.confidence == b.confidence
        assert 0.0 <= a.confidence <= 1.0


# ---------------------------------------------------------------------------
# Gate 2 - deterministic action safety
# ---------------------------------------------------------------------------


class TestValidateReroute:
    def test_safe_reroute_is_approved(self):
        result = validate(action(SLOW))
        assert result.approved, result.rejection_reason
        assert result.risk_level is not None

    def test_operation_allow_list_is_exactly_one_entry(self):
        """The outermost guard: nothing outside this set may ever be
        executed, whatever a model proposes."""
        assert ALLOWED_OPERATIONS == {"reroute_flow"}

    def test_disallowed_operation_is_rejected(self):
        bad = action(SLOW).model_copy(update={"action_type": "delete_everything"})
        assert not validate(bad).approved

    def test_path_with_unknown_node_is_rejected(self):
        assert not validate(action(["h1", "s1", "s9", "s4", "h4"])).approved

    def test_path_not_starting_at_source_is_rejected(self):
        assert not validate(action(["h2", "s1", "s2", "s4", "h4"])).approved

    def test_path_not_ending_at_destination_is_rejected(self):
        assert not validate(action(["h1", "s1", "s2", "s4", "h3"])).approved

    def test_overlong_path_is_rejected(self):
        """A bounded hop count keeps the action space small enough to reason
        about and stops a runaway path being installed."""
        hops = ["h1"] + [f"s{i}" for i in range(1, MAX_PATH_HOPS + 4)] + ["h4"]
        assert not validate(action(hops)).approved

    def test_path_through_down_link_is_rejected(self):
        snapshot = make_snapshot(link_status={"s3-s4": LinkStatus.DOWN})
        assert not validate(action(SLOW), snapshot).approved

    def test_rerouting_onto_a_protected_link_is_rejected(self):
        """A link carrying protected traffic may not be moved onto without a
        deliberate policy decision."""
        policy = SafetyPolicy(
            protected_flows=protected_voice(), protected_links=["s1-s3"])
        snapshot = make_snapshot()
        result = validate(action(SLOW), snapshot, policy)
        assert not result.approved

    def test_every_rejection_names_its_failed_checks(self):
        """A rejection nobody can audit is not a trustworthy rejection."""
        result = validate(action(["h1", "s1", "s9", "s4", "h4"]))
        assert not result.approved
        assert result.failed_checks
        assert result.rejection_reason


# ---------------------------------------------------------------------------
# Gate 3 - verification
# ---------------------------------------------------------------------------


class TestVerifyRecovery:
    def test_latency_back_within_intent_is_success(self):
        result = verify_recovery(
            intent=make_intent(max_latency_ms=50.0),
            before=make_flows(latency_ms=120.0), after=make_flows(latency_ms=30.0))
        assert result.status is VerificationStatus.SUCCESS

    def test_improved_but_still_over_intent_is_partial(self):
        """Better is not the same as fixed. Claiming SUCCESS here would be the
        single most damaging thing this system could do."""
        result = verify_recovery(
            intent=make_intent(max_latency_ms=50.0),
            before=make_flows(latency_ms=120.0), after=make_flows(latency_ms=70.0))
        assert result.status is VerificationStatus.PARTIAL

    def test_no_improvement_at_all_is_failure(self):
        result = verify_recovery(
            intent=make_intent(max_latency_ms=50.0),
            before=make_flows(latency_ms=120.0), after=make_flows(latency_ms=120.0))
        assert result.status is VerificationStatus.FAILED

    def test_tiny_improvement_is_partial_not_failed(self):
        """A 2 ms gain is a real gain; the gate must not round it to zero."""
        result = verify_recovery(
            intent=make_intent(max_latency_ms=50.0),
            before=make_flows(latency_ms=120.0), after=make_flows(latency_ms=118.0))
        assert result.status is VerificationStatus.PARTIAL

    def test_regression_is_failure(self):
        result = verify_recovery(
            intent=make_intent(max_latency_ms=50.0),
            before=make_flows(latency_ms=30.0), after=make_flows(latency_ms=200.0))
        assert result.status is VerificationStatus.FAILED

    def test_missing_after_measurement_is_unverified(self):
        """A command that returned success is not a verified recovery."""
        result = verify_recovery(
            intent=make_intent(), before=make_flows(latency_ms=120.0),
            after=None)
        assert result.status is VerificationStatus.UNVERIFIED

    def test_packet_loss_introduced_is_not_success(self):
        """Faster is not better if the flow now loses packets."""
        result = verify_recovery(
            intent=make_intent(max_latency_ms=50.0, max_packet_loss_pct=1.0),
            before=make_flows(latency_ms=120.0, packet_loss_pct=0.0),
            after=make_flows(latency_ms=20.0, packet_loss_pct=25.0))
        assert result.status is not VerificationStatus.SUCCESS


class TestProtectedFlows:
    PROTECTED_PATH = ["h3", "s4"]

    def _flow(self, latency_ms: float) -> list[FlowTelemetry]:
        return [make_flows(latency_ms=latency_ms, flow_id="h3->h4",
                           src="h3", dst="h4", path=self.PROTECTED_PATH)]

    def test_unchanged_protected_flow_is_ok(self):
        statuses = check_protected_flows(
            protected=protected_voice(),
            before=self._flow(10.0), after=self._flow(10.0),
            path_by_flow={"h3->h4": self.PROTECTED_PATH})
        assert len(statuses) == 1
        assert statuses[0].ok

    def test_degraded_protected_flow_is_reported_not_ok(self):
        statuses = check_protected_flows(
            protected=protected_voice(),
            before=self._flow(10.0), after=self._flow(200.0),
            path_by_flow={"h3->h4": self.PROTECTED_PATH})
        assert not statuses[0].ok
        assert statuses[0].reason

    def test_missing_measurement_is_never_silently_ok(self):
        """No measurement must never be read as "unchanged"."""
        statuses = check_protected_flows(
            protected=protected_voice(), before=[], after=[],
            path_by_flow={"h3->h4": self.PROTECTED_PATH})
        assert len(statuses) == 1
        assert not statuses[0].ok

    def test_modified_protected_path_is_flagged(self):
        statuses = check_protected_flows(
            protected=protected_voice(),
            before=self._flow(10.0), after=self._flow(10.0),
            path_by_flow={"h3->h4": ["h3", "s1", "s2", "s4"]})
        assert not statuses[0].path_unmodified


# ---------------------------------------------------------------------------
# Auditability
# ---------------------------------------------------------------------------


def test_gates_are_pure_functions():
    """Two evaluations of identical inputs must agree exactly on everything
    except the assessment's own clock stamp. A safety decision that is not
    reproducible cannot be audited or defended."""
    snapshot = make_snapshot(latency_ms=120.0, utilisation={"s2-s4": 95.0})
    first, second = evidence(snapshot), evidence(snapshot)
    a, b = first.model_dump(), second.model_dump()
    for result in (a, b):
        result.pop("assessed_at", None)
    assert a == b

    x, y = validate(action(SLOW)), validate(action(SLOW))
    xd, yd = x.model_dump(), y.model_dump()
    for result in (xd, yd):
        result.pop("validated_at", None)
    assert xd == yd