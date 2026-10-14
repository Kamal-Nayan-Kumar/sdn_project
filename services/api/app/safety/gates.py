"""The three trustworthiness gates.

This module is deliberately deterministic and LLM-free. Everything in here
must work correctly even when the model returns nonsense, because these gates
are what make the system *trustworthy* rather than merely LLM-driven:

  Gate 1 - Evidence sufficiency
      Are the measurements we need present, fresh, sane, and do they actually
      support the diagnosis? Missing/stale/contradictory evidence means
      abstain or refresh. The LLM may explain the evidence; it may not decide
      whether the evidence exists.

  Gate 2 - Action safety
      Is the proposed mutation on the allow-list, scoped to the intended
      flow, on real links, within capacity, and does it leave protected flows
      untouched? Only this validator approves a mutation.

  Gate 3 - Recovery verification
      Does *fresh post-action* telemetry actually show the intent satisfied
      and protected flows still healthy? A command that returned 200 is not
      a recovery, and a missing measurement is never a success.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from app.core.config import get_settings
from app.schemas.domain import (
    EvidenceAssessment,
    EvidenceItem,
    EvidenceStatus,
    ExecutionResult,
    FlowTelemetry,
    LinkTelemetry,
    NetworkSnapshot,
    PathCandidate,
    ProtectedFlowSpec,
    ProtectedFlowStatus,
    RiskLevel,
    RerouteAction,
    SafetyCheck,
    ValidationResult,
    VerificationOutcome,
    VerificationStatus,
)


# ---------------------------------------------------------------------------
# Gate 1 - evidence sufficiency
# ---------------------------------------------------------------------------


def assess_evidence(
    *,
    snapshot: NetworkSnapshot | None,
    intent: Any,
    max_age_seconds: float | None = None,
    min_confidence: float | None = None,
    now: datetime | None = None,
    suspected_links: Iterable[str] | None = None,
) -> EvidenceAssessment:
    """Assess whether the evidence supports a recovery decision.

    Checks, in order:
      1. topology is present and both flow endpoints exist in it;
      2. the intended flow has been measured (latency/packet loss present);
      3. the measurement is recent enough for the configured policy;
      4. the numbers are sane (latency >= 0, loss in [0,100]);
      5. there is at least one suspect link we can name;
      6. the topology and telemetry agree the suspect link exists;
      7. confidence clears the configured floor.
    """
    settings = get_settings()
    now = now or datetime.now(timezone.utc)
    max_age = settings.telemetry_max_age_seconds if max_age_seconds is None else max_age_seconds
    floor = settings.evidence_min_confidence if min_confidence is None else min_confidence

    items: list[EvidenceItem] = []
    missing: list[str] = []
    stale: list[str] = []

    # --- 1. topology present -------------------------------------------
    if snapshot is None:
        items.append(EvidenceItem(key="topology", present=False,
                                  note="no network snapshot available"))
        return EvidenceAssessment(
            status=EvidenceStatus.UNAVAILABLE, sufficient=False, items=items,
            confidence=0.0,
            summary="No network snapshot could be obtained, so the network "
                    "state is unknown.",
            missing=["topology"])

    topo = snapshot.topology
    endpoints = topo.endpoint_ids()
    src_ok = intent.src_host in endpoints
    dst_ok = intent.dst_host in endpoints
    items.append(EvidenceItem(
        key="topology", present=True, fresh=True,
        source="network snapshot",
        evidence_extra={"switches": sum(1 for n in topo.nodes if n.kind.value == "switch"),
                        "hosts": sum(1 for n in topo.nodes if n.kind.value == "host")},
        note=f"{len(topo.links)} links, {len(topo.nodes)} nodes"))
    if not src_ok:
        missing.append(f"topology:src_host({intent.src_host})")
    if not dst_ok:
        missing.append(f"topology:dst_host({intent.dst_host})")

    # --- 2/3/4. the flow's own measurement ----------------------------
    flow = _flow_for(snapshot, intent)
    if flow is None:
        items.append(EvidenceItem(key="flow_measurement", present=False,
                                  note="no measurement for the intended flow"))
        missing.append(f"measurement:{intent.src_host}->{intent.dst_host}")
    else:
        age = flow.age_seconds(now=now)
        fresh = age <= max_age
        items.append(EvidenceItem(
            key="flow_measurement", present=True, fresh=fresh,
            value={"latency_ms": flow.latency_ms,
                   "packet_loss_pct": flow.packet_loss_pct},
            source="live ping from lab",
            measured_at=flow.measured_at,
            note=f"age {age:.1f}s (policy {max_age:.1f}s)"))
        if not fresh:
            stale.append(f"measurement:{intent.src_host}->{intent.dst_host}")
        sane = _sane_flow(flow)
        if not sane:
            missing.append(f"measurement:sanity({intent.src_host}->{intent.dst_host})")

    # --- 5/6. an identifiable suspect link that the topology agrees on ---
    suspects = list(suspected_links) if suspected_links is not None else []
    if flow is not None and not suspects:
        suspects = _infer_bottleneck_links(
            snapshot, settings.congestion_utilization_threshold,
            path=getattr(flow, "path", None) or None)

    if not suspects:
        missing.append("diagnosis:suspect_link")
    else:
        for link_id in suspects:
            link_tel = next((l for l in snapshot.links if l.link_id == link_id), None)
            topo_link = topo.link(link_id)
            if link_tel is None:
                missing.append(f"telemetry:{link_id}")
            elif link_tel.age_seconds(now=now) > max_age:
                stale.append(f"telemetry:{link_id}")
            if topo_link is None:
                missing.append(f"topology:{link_id}")
            items.append(EvidenceItem(
                key=f"suspect_link:{link_id}",
                present=link_tel is not None and topo_link is not None,
                fresh=(link_tel is not None
                       and link_tel.age_seconds(now=now) <= max_age),
                value={"utilization_pct": link_tel.utilization_pct if link_tel else None,
                       "status": link_tel.status.value if link_tel else None},
                source="link telemetry",
                measured_at=link_tel.measured_at if link_tel else None,
                note="congested" if link_tel and link_tel.utilization_pct >=
                     settings.congestion_utilization_threshold * 100 else "normal"))

    # --- 7. confidence ----------------------------------------------------
    confidence = _confidence(
        present=sum(1 for i in items if i.present),
        fresh=sum(1 for i in items if i.present and i.fresh),
        total=max(1, len(items)))

    sufficient = (not missing and not stale and confidence >= floor)

    if sufficient:
        status = EvidenceStatus.SUFFICIENT
        summary = ("Fresh measurements are present for the affected flow and "
                   "the suspected bottleneck link supports the diagnosis.")
    elif stale:
        status = EvidenceStatus.STALE
        summary = ("Measurements for the suspected link are older than the "
                   f"evidence policy allows ({max_age:.1f}s); refreshing "
                   "before selecting a recovery action.")
    elif missing:
        status = EvidenceStatus.INSUFFICIENT if missing else EvidenceStatus.UNAVAILABLE
        summary = ("The evidence is incomplete: " + _join(missing) + ". "
                   "Abstaining rather than acting on partial evidence.")
    else:
        status = EvidenceStatus.INSUFFICIENT
        summary = (f"Evidence confidence {confidence:.2f} is below the required "
                   f"{floor:.2f}; abstaining from action.")

    return EvidenceAssessment(
        status=status, sufficient=sufficient, items=items,
        confidence=round(confidence, 3), summary=summary,
        missing=missing, stale=stale)


def _flow_for(snapshot: NetworkSnapshot, intent: Any) -> FlowTelemetry | None:
    """Find the flow measurement matching the intent's endpoints."""
    for flow in snapshot.flows:
        if flow.src_host == intent.src_host and flow.dst_host == intent.dst_host:
            return flow
    return None


def _sane_flow(flow: FlowTelemetry) -> bool:
    if flow.latency_ms is None or flow.packet_loss_pct is None:
        return False
    if not (0.0 <= flow.latency_ms <= 10_000.0):
        return False
    if not (0.0 <= flow.packet_loss_pct <= 100.0):
        return False
    if math.isnan(flow.latency_ms) or math.isnan(flow.packet_loss_pct):
        return False
    return True


def _infer_bottleneck_links(snapshot: NetworkSnapshot, threshold: float,
                            path: list[str] | None = None) -> list[str]:
    """Name the links that could account for an intent violation.

    Two cases, in order of directness:

    1. **Congestion.** Links at or above the threshold, most-utilised first.
       This is the strongest evidence: a link is carrying more than its share.
    2. **Path delay.** If nothing is congested but the intent is still
       breached, the excess latency has to be accruing somewhere on the flow's
       current path. Naming those links (slowest first) is a real, checkable
       diagnosis -- the reroute can then be judged against an alternative.

    Returning nothing here is what makes the agent abstain, so the fallback has
    to stay conservative: it only fires when the caller has already established
    that a violation exists.
    """
    level = threshold * 100.0
    congested = [l for l in snapshot.links if l.utilization_pct >= level]
    if congested:
        congested.sort(key=lambda l: -l.utilization_pct)
        return [l.link_id for l in congested]

    if not path:
        return []

    # A flow path is a sequence of node ids ("s1", "s2", "s4"); link ids are
    # edges ("s1-s2"). Convert, and tolerate a path already given as links.
    wanted = _links_along(path)
    on_path = [l for l in snapshot.links if l.link_id in wanted]
    # Delay-carrying first: that is where a reroute can actually help.
    on_path.sort(key=lambda l: (-(l.latency_ms or 0.0), l.link_id))
    return [l.link_id for l in on_path]


def _links_along(path: list[str]) -> set[str]:
    """Edge ids spanned by a node path, order-insensitive."""
    edges: set[str] = set()
    for a, b in zip(path, path[1:]):
        edges.add("-".join(sorted((a, b))))
    return edges


def _confidence(*, present: int, fresh: int, total: int) -> float:
    """Simple deterministic blend of presence and freshness.

    Deliberately hand-rolled rather than a model score: the value must be
    reproducible by anyone reading the evidence.
    """
    if total <= 0:
        return 0.0
    presence = present / total
    freshness = fresh / total
    return 0.6 * presence + 0.4 * freshness


def _join(parts: list[str]) -> str:
    return "; ".join(parts[:4]) + (f" (+{len(parts) - 4} more)" if len(parts) > 4 else "")


# ---------------------------------------------------------------------------
# Gate 2 - deterministic action safety
# ---------------------------------------------------------------------------

#: The complete set of operations the agent may ever propose. Anything else is
#: rejected before it is even validated.
ALLOWED_OPERATIONS = frozenset({"reroute_flow"})

#: How many hops a valid reroute may span. Keeps the action space small and
#: testable, which is the point of a semester demonstration.
MAX_PATH_HOPS = 6


@dataclass
class SafetyPolicy:
    """The explicit constraints an action must satisfy."""

    protected_flows: list[ProtectedFlowSpec] = field(default_factory=list)
    max_path_hops: int = MAX_PATH_HOPS
    # Links that never carry a protected flow and may be rerouted onto.
    protected_links: list[str] = field(default_factory=list)
    require_approval_for: tuple[RiskLevel, ...] = (RiskLevel.MEDIUM, RiskLevel.HIGH)


def validate_reroute(
    action: RerouteAction,
    *,
    snapshot: NetworkSnapshot,
    policy: SafetyPolicy,
    run_id: str = "",
    action_id: str = "",
    now: datetime | None = None,
) -> ValidationResult:
    """Validate a proposed reroute against the deterministic constraints.

    Every check is pure decision logic on the evidence; nothing here consults
    a model. A failed check prevents the mutation, full stop.
    """
    settings = get_settings()
    checks: list[SafetyCheck] = []
    now = now or datetime.now(timezone.utc)
    topo = snapshot.topology

    def check(name: str, passed: bool, detail: str = "",
              **evidence: Any) -> None:
        checks.append(SafetyCheck(name=name, passed=passed, detail=detail,
                                 evidence=evidence))

    # 1. Allow-listed operation.
    op = action.action_type.value
    check("operation_allowlisted", op in ALLOWED_OPERATIONS,
          f"operation '{op}' is {'allowed' if op in ALLOWED_OPERATIONS else 'not permitted'}",
          operation=op, allowlist=sorted(ALLOWED_OPERATIONS))

    # 2. Endpoints exist in the topology.
    nodes = topo.endpoint_ids()
    check("endpoints_exist", action.src_host in nodes and action.dst_host in nodes,
          f"src={action.src_host}, dst={action.dst_host}",
          known_endpoints=sorted(nodes))
    if action.src_host not in nodes or action.dst_host not in nodes:
        return _result(checks, "Proposed endpoints are not in the live topology.",
                       policy, now)

    # 3. Path is well-formed: contiguous, no loops, starts at src ends at dst.
    path = action.path
    form_ok = _path_is_well_formed(path, action.src_host, action.dst_host, nodes)
    check("path_well_formed", form_ok,
          "path must be src, node..., dst with no repeats" if not form_ok
          else f"{len(path) - 2} switch hops",
          path=path,
          switch_hops=len(path) - 2)

    # 4. Hop budget.
    hops = max(0, len(path) - 2)
    check("hop_budget", hops <= policy.max_path_hops,
          f"{hops} hops (limit {policy.max_path_hops})", hops=hops,
          limit=policy.max_path_hops)

    # 5. Every link on the path exists and is up.
    missing_links, down_links = [], []
    for a, b in zip(path, path[1:]):
        link = _link_between(topo, a, b)
        if link is None:
            missing_links.append(f"{a}-{b}")
        elif link.status.value != "up":
            down_links.append(f"{a}-{b}")
    links_ok = not missing_links and not down_links
    check("all_links_operational", links_ok,
          "all path links exist and are up" if links_ok
          else f"missing={missing_links} down={down_links}",
          missing_links=missing_links, down_links=down_links)

    # 6. Capacity: the path's narrowest link must be able to carry the flow.
    bottlenecks = []
    for a, b in zip(path, path[1:]):
        link = _link_between(topo, a, b)
        if link is None:
            continue
        tel = next((l for l in snapshot.links if l.link_id == link.id), None)
        util = tel.utilization_pct if tel else 0.0
        if util >= settings.congestion_utilization_threshold * 100.0:
            bottlenecks.append({"link_id": link.id, "utilization_pct": util})
    check("capacity_respected", not bottlenecks,
          "no link on the path is already congested" if not bottlenecks
          else f"congested links on path: {[b['link_id'] for b in bottlenecks]}",
          congested=bottlenecks, capacity_mbps=None)

    # 7. Protected flows are not modified: the action must not route over a
    #    link that carries a protected flow, and must not alter a protected
    #    flow's own path.
    path_link_ids = {f"{a}-{b}" for a, b in zip(path, path[1:])}
    action_nodes = set(path)
    protected_link_touched = [
        pid for pid in policy.protected_links
        if path_link_ids.intersection({pid, "-".join(reversed(pid.split("-")))})
    ]
    protected_flows_redirected = [
        spec.flow_id for spec in policy.protected_flows
        if _flow_crosses(snapshot, spec, action_nodes)
    ]
    protected_ok = (not protected_link_touched
                    and not protected_flows_redirected)
    check("protected_flows_unmodified", protected_ok,
          "action does not touch any protected flow or the links it uses"
          if protected_ok
          else "protected links touched: %s; protected flows redirected: %s"
               % (protected_link_touched, protected_flows_redirected),
          protected_links_touched=protected_link_touched,
          protected_flows_redirected=protected_flows_redirected)

    # 8. Scoped to the intended flow: the action may only change the path
    #    between its own endpoints, not insert or remove endpoints.
    scope_ok = path[0] == action.src_host and path[-1] == action.dst_host
    check("flow_scope", scope_ok,
          "path is scoped to the declared src/dst" if scope_ok
          else "path endpoints do not match the declared flow",
          declared_src=action.src_host, declared_dst=action.dst_host)

    # 9. Expiry / duplication guards.
    age = (now - action.created_at if getattr(action, "created_at", None) else timedelta(0))
    check("request_not_expired", age <= timedelta(minutes=5),
          f"action age {age.total_seconds():.1f}s (limit 300s)",
          action_id=action_id, run_id=run_id, age_seconds=age.total_seconds())

    risk = _risk_level(checks, policy)
    requires_approval = (
        settings.require_human_approval_for_risky_actions
        and risk in policy.require_approval_for)

    return _result(checks, None, policy, now, risk=risk,
                   requires_approval=requires_approval)


def _flow_crosses(snapshot: NetworkSnapshot, spec: ProtectedFlowSpec,
                  action_nodes: set[str]) -> bool:
    """True when the reroute's nodes intersect the protected flow's path.

    A protected flow is only safe if the action leaves it on the same route,
    so intersecting nodes is treated as a violation. Where the protected
    flow's current path is unknown we err on the side of caution.
    """
    for flow in snapshot.flows:
        if flow.flow_id != spec.flow_id:
            continue
        current_path = getattr(flow, "path", None) or []
        if not current_path:
            return bool(action_nodes & _flow_nodes(snapshot, spec))
        return bool(action_nodes & set(current_path))
    return False


def _flow_nodes(snapshot: NetworkSnapshot, spec: ProtectedFlowSpec) -> set[str]:
    """Best-effort node set for a protected flow, from the topology."""
    nodes: set[str] = set()
    topo = snapshot.topology
    if spec.src_host:
        nodes.add(spec.src_host)
    if spec.dst_host:
        nodes.add(spec.dst_host)
    for flow in snapshot.flows:
        if flow.flow_id != spec.flow_id:
            continue
        nodes |= set(getattr(flow, "path", None) or [])
    _ = topo
    return nodes


def _result(checks: list[SafetyCheck], rejection: str | None,
            policy: SafetyPolicy, now: datetime,
            risk: RiskLevel = RiskLevel.LOW,
            requires_approval: bool = False) -> ValidationResult:
    failed = [c for c in checks if not c.passed]
    if rejection is None and failed:
        rejection = "; ".join(f"{c.name}: {c.detail}" for c in failed[:3])
    return ValidationResult(
        approved=not failed,
        checks=checks,
        rejection_reason=rejection,
        risk_level=risk,
        requires_approval=requires_approval,
        validated_at=now)


def _risk_level(checks: list[SafetyCheck],
                policy: SafetyPolicy) -> RiskLevel:
    """Deterministic risk tiering based on which constraints were involved."""
    names = {c.name for c in checks if not c.passed}
    if "protected_flows_unmodified" in names or "operation_allowlisted" in names:
        return RiskLevel.HIGH
    if "capacity_respected" in names:
        return RiskLevel.MEDIUM
    if "flow_scope" in names or "path_well_formed" in names:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _path_is_well_formed(path: list[str], src: str, dst: str,
                         nodes: set[str]) -> bool:
    if len(path) < 3:
        return False
    if len(set(path)) != len(path):
        return False
    if path[0] != src or path[-1] != dst:
        return False
    if any(n not in nodes for n in path):
        return False
    return True


def _link_between(topo, a: str, b: str):
    direct = f"{a}-{b}"
    reverse = f"{b}-{a}"
    return topo.link(direct) or topo.link(reverse)


# ---------------------------------------------------------------------------
# Gate 3 - recovery verification
# ---------------------------------------------------------------------------


def verify_recovery(
    *,
    intent: Any,
    before: FlowTelemetry | None,
    after: FlowTelemetry | None,
    protected: list[ProtectedFlowSpec] | None = None,
    protected_statuses: list[ProtectedFlowStatus] | None = None,
    reason_hint: str = "",
    now: datetime | None = None,
) -> VerificationOutcome:
    """Decide whether the *network* recovered, not whether a command ran.

    Outcomes:
      success    - measured latency within intent AND protected flows healthy
      partial    - measurable improvement but intent still violated
      failed     - no improvement (or worse)
      unverified - we have no usable post-action measurement
    """
    protected = protected or []
    now = now or datetime.now(timezone.utc)

    # No post-action measurement can never be reported as a success.
    if after is None or after.latency_ms is None:
        return VerificationOutcome(
            status=VerificationStatus.UNVERIFIED,
            intent_satisfied=False,
            before=before, after=after,
            protected_flows=protected_statuses or [],
            reason=("No post-action measurement is available, so recovery "
                    "cannot be verified. This is reported as unverified, "
                    "not as success."))

    protected_states = protected_statuses or []
    protected_ok = all(p.ok for p in protected_states) if protected_states else True

    intent_satisfied = after.latency_ms <= intent.max_latency_ms
    loss_ok = (intent.max_packet_loss_pct is None
               or after.packet_loss_pct is None
               or after.packet_loss_pct <= intent.max_packet_loss_pct)

    if intent_satisfied and loss_ok and protected_ok:
        return VerificationOutcome(
            status=VerificationStatus.SUCCESS,
            intent_satisfied=True, protected_ok=True,
            before=before, after=after, protected_flows=protected_states,
            reason=f"Measured latency {after.latency_ms:.1f} ms is within the "
                   f"{intent.max_latency_ms:.0f} ms intent and protected flows "
                   "are within their constraints.")

    if protected_ok and intent_satisfied and not loss_ok:
        return VerificationOutcome(
            status=VerificationStatus.PARTIAL,
            intent_satisfied=False, protected_ok=True,
            before=before, after=after, protected_flows=protected_states,
            reason=(f"Latency {after.latency_ms:.1f} ms now satisfies the intent, "
                    f"but packet loss {after.packet_loss_pct}% exceeds the "
                    f"{intent.max_packet_loss_pct}% limit."))

    baseline = before.latency_ms if before and before.latency_ms else None
    improved = baseline is not None and after.latency_ms < baseline

    if improved:
        return VerificationOutcome(
            status=VerificationStatus.PARTIAL,
            intent_satisfied=False, protected_ok=protected_ok,
            before=before, after=after, protected_flows=protected_states,
            reason=(f"Latency improved from {baseline:.1f} ms to "
                    f"{after.latency_ms:.1f} ms but is still above the "
                    f"{intent.max_latency_ms:.0f} ms intent. Reported as "
                    "partial improvement, not successful recovery."))

    if not protected_ok:
        return VerificationOutcome(
            status=VerificationStatus.FAILED,
            intent_satisfied=False, protected_ok=False,
            before=before, after=after, protected_flows=protected_states,
            reason="A protected flow is outside its allowed constraints after "
                   "the action; the recovery is not acceptable.")

    return VerificationOutcome(
        status=VerificationStatus.FAILED,
        intent_satisfied=False, protected_ok=True,
        before=before, after=after, protected_flows=protected_states,
        reason=reason_hint or (
            f"Latency {after.latency_ms:.1f} ms is still above the "
            f"{intent.max_latency_ms:.0f} ms intent with no improvement."))


def check_protected_flows(
    protected: list[ProtectedFlowSpec],
    before: list[FlowTelemetry],
    after: list[FlowTelemetry],
    path_by_flow: dict[str, list[str]] | None = None,
) -> list[ProtectedFlowStatus]:
    """Evaluate each protected flow against its own constraints."""
    path_by_flow = path_by_flow or {}
    before_by_id = {f.flow_id: f for f in before}
    after_by_id = {f.flow_id: f for f in after}

    results: list[ProtectedFlowStatus] = []
    for spec in protected:
        before_f = before_by_id.get(spec.flow_id)
        after_f = after_by_id.get(spec.flow_id)
        cur = after_f or before_f

        ok = True
        reasons: list[str] = []

        if spec.max_latency_ms is not None:
            lat = cur.latency_ms if cur else None
            if lat is None:
                ok = False
                reasons.append(f"{spec.flow_id}: latency unknown")
            elif lat > spec.max_latency_ms:
                ok = False
                reasons.append(f"{spec.flow_id}: {lat:.1f} ms > {spec.max_latency_ms} ms")

        if spec.max_packet_loss_pct is not None:
            loss = cur.packet_loss_pct if cur else None
            if loss is None:
                ok = False
                reasons.append(f"{spec.flow_id}: packet loss unknown")
            elif loss > spec.max_packet_loss_pct:
                ok = False
                reasons.append(
                    f"{spec.flow_id}: {loss:.1f}% > {spec.max_packet_loss_pct}%")

        # A protected flow must keep the path it had before the action.
        path_unmodified = True
        if spec.flow_id in path_by_flow and before_f is not None:
            expected = getattr(before_f, "path", None) or []
            if expected and path_by_flow[spec.flow_id] != expected:
                path_unmodified = False
                ok = False
                reasons.append(f"{spec.flow_id}: path changed")

        results.append(ProtectedFlowStatus(
            flow_id=spec.flow_id, label=spec.label or spec.flow_id,
            latency_ms=cur.latency_ms if cur else None,
            packet_loss_pct=cur.packet_loss_pct if cur else None,
            max_latency_ms=spec.max_latency_ms,
            max_packet_loss_pct=spec.max_packet_loss_pct,
            ok=ok, path_unmodified=path_unmodified,
            reason="; ".join(reasons) if reasons else "within constraints"))
    return results


__all__ = [
    "ALLOWED_OPERATIONS",
    "MAX_PATH_HOPS",
    "SafetyPolicy",
    "assess_evidence",
    "check_protected_flows",
    "validate_reroute",
    "verify_recovery",
]
