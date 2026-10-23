"""The safe tool layer.

Design rules (from the spec, enforced here rather than merely documented):

  * Tools are an allow-list. The agent can only ever call what is exported
    from ``TOOL_REGISTRY`` below; there is no generic "run" tool.
  * Every tool has a Pydantic input schema. Unknown/extra keys are rejected.
  * Tools that mutate speak only to the lab bridge, and only through the two
    bridge methods that exist (`start_congestion`, `reroute`). A tool can
    never become an arbitrary shell.
  * Read tools never mutate and never hang: they convert every lab failure
    into a structured result rather than raising.
  * No tool returns a fabricated number. When the lab cannot measure, the
    value is None plus a reason.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

from app.schemas.domain import (
    FlowKind,
    FlowTelemetry,
    IntentSpec,
    LinkTelemetry,
    NetworkSnapshot,
    PathCandidate,
    RerouteAction,
    Topology,
    TopologyLink,
    TopologyNode,
)
from app.services.lab_bridge import LabBridgeClient, LabUnavailable

#: Every tool the graph is allowed to call, with its Pydantic input schema.
TOOL_REGISTRY: dict[str, type[BaseModel]] = {}


def tool(name: str):
    """Register a tool schema in the allow-list."""

    def deco(cls: type[BaseModel]) -> type[BaseModel]:
        cls.__tool_name__ = name  # type: ignore[attr-defined]
        TOOL_REGISTRY[name] = cls
        return cls

    return deco


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ToolResult(BaseModel):
    """Uniform result envelope for every tool call."""

    tool: str
    ok: bool
    outputs: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    duration_ms: float | None = None


# ---------------------------------------------------------------------------
# read-only tools
# ---------------------------------------------------------------------------


@tool("get_topology")
class GetTopologyInput(BaseModel):
    model_config = {"extra": "forbid"}


@tool("get_network_snapshot")
class GetNetworkSnapshotInput(BaseModel):
    model_config = {"extra": "forbid"}
    latency_count: int = Field(default=10, ge=1, le=50)
    measure_protected: dict[str, str] = Field(
        default_factory=dict,
        description="flow_id -> 'src,dst' pairs to measure for protected flows")


@tool("evaluate_paths")
class EvaluatePathsInput(BaseModel):
    model_config = {"extra": "forbid"}
    src_host: str
    dst_host: str
    avoid_links: list[str] = Field(default_factory=list)


@tool("get_link_telemetry")
class GetLinkTelemetryInput(BaseModel):
    model_config = {"extra": "forbid"}
    link_ids: list[str] | None = None


@tool("measure_flow")
class MeasureFlowInput(BaseModel):
    model_config = {"extra": "forbid"}
    src_host: str
    dst_host: str
    count: int = Field(default=10, ge=1, le=50)


@tool("propose_reroute")
class ProposeRerouteInput(BaseModel):
    model_config = {"extra": "forbid"}
    flow_id: str
    src_host: str
    dst_host: str
    path: list[str] = Field(min_length=3)
    reason: str = ""


# ---------------------------------------------------------------------------
# mutating tools (allow-listed)
# ---------------------------------------------------------------------------


@tool("apply_reroute")
class ApplyRerouteInput(BaseModel):
    """Emit-audit only: this tool is called *after* validation has approved."""

    model_config = {"extra": "forbid"}
    action_id: str = Field(min_length=1)
    flow_id: str
    src_host: str
    dst_host: str
    path: list[str] = Field(min_length=3)


@tool("start_congestion_scenario")
class StartCongestionInput(BaseModel):
    model_config = {"extra": "forbid"}
    link_id: str = Field(pattern=r"^s\d+-s\d+$")
    rate_mbps: float = Field(gt=0, le=10_000)
    duration_seconds: float = Field(default=0.0, ge=0)


@tool("reset_lab")
class ResetLabInput(BaseModel):
    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------


def _new_action_id() -> str:
    return "act_" + uuid.uuid4().hex[:16]


def run_tool(name: str, inputs: dict[str, Any],
             client: LabBridgeClient,
             observer: "ToolObserver | None" = None) -> ToolResult:
    """Dispatch a tool call through the allow-list.

    Unknown tools raise; that is intentional -- the graph must not be able to
    invent behaviour at runtime.

    `observer`, when supplied, is called with (`"started"`/`"completed"`,
    tool name, payload) around the call. The graph uses it to publish live tool
    events; tests call the tool directly and pass nothing.
    """
    if name not in TOOL_REGISTRY:
        raise KeyError(f"tool '{name}' is not on the allow-list")

    schema = TOOL_REGISTRY[name]
    try:
        params = schema.model_validate(inputs or {})
    except Exception as exc:
        return ToolResult(tool=name, ok=False, error=f"invalid inputs: {exc}")

    if observer is not None:
        observer("started", name, {"inputs": _brief(inputs)})

    started = time.monotonic()
    try:
        outputs = _DISPATCH[name](params, client)
    except LabUnavailable as exc:
        result = ToolResult(tool=name, ok=False, error=str(exc),
                            duration_ms=_elapsed(started))
    except Exception as exc:
        result = ToolResult(tool=name, ok=False,
                            error=f"{type(exc).__name__}: {exc}",
                            duration_ms=_elapsed(started))
    else:
        result = ToolResult(tool=name, ok=True, outputs=outputs,
                            duration_ms=_elapsed(started))

    if observer is not None:
        observer("completed", name, {
            "ok": result.ok,
            "duration_ms": result.duration_ms,
            "error": result.error,
            "summary": _summarise_outputs(name, result.outputs),
        })
    return result


#: (`started`|`completed`, tool name, payload)
ToolObserver = Callable[[str, str, dict[str, Any]], None]


def _brief(inputs: dict[str, Any], limit: int = 160) -> dict[str, Any]:
    """A short, secret-free echo of the tool inputs for the activity log."""
    brief: dict[str, Any] = {}
    for key, value in (inputs or {}).items():
        if isinstance(value, str):
            brief[key] = value if len(value) <= limit else value[:limit] + "…"
        elif isinstance(value, (int, float, bool)) or value is None:
            brief[key] = value
        elif isinstance(value, list):
            brief[key] = value[:6]
        elif isinstance(value, dict):
            brief[key] = {k: str(v)[:60] for k, v in list(value.items())[:6]}
        else:
            brief[key] = str(value)[:limit]
    return brief


def _summarise_outputs(name: str, outputs: dict[str, Any]) -> str:
    """One line describing what a tool returned, without dumping the payload."""
    if not outputs:
        return "no output"
    if name == "get_network_snapshot":
        snapshot = outputs.get("snapshot")
        try:
            data = json.loads(snapshot) if isinstance(snapshot, str) else snapshot
            flows = data.get("flows") or {}
            links = data.get("links") or {}
            congested = data.get("congested_links") or []
            return (f"{len(flows)} flows, {len(links)} links measured"
                    + (f", congested: {', '.join(congested)}" if congested else ""))
        except Exception:
            return "snapshot collected"
    if name == "evaluate_paths":
        return f"{len(outputs.get('candidates') or [])} candidate paths"
    if name == "apply_reroute":
        controller = outputs.get("controller") or {}
        path = controller.get("path") or outputs.get("path") or []
        rules = controller.get("flow_rules_installed", 0)
        return f"path {'-'.join(path) if path else 'unknown'}, {rules} rules installed"
    if name == "get_topology":
        return f"{len(outputs.get('nodes') or [])} nodes, {len(outputs.get('links') or [])} links"
    if name == "get_link_telemetry":
        return f"{len(outputs.get('links') or [])} links"
    return ", ".join(f"{k}={str(v)[:40]}" for k, v in list(outputs.items())[:3])


def _elapsed(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 2)


def _pick(source: dict[str, Any], model: type[BaseModel],
          **defaults: Any) -> dict[str, Any]:
    """Keep only the keys `model` declares, applying `defaults` for the rest."""
    allowed = set(model.model_fields)
    out = {k: v for k, v in source.items() if k in allowed}
    for key, value in defaults.items():
        out.setdefault(key, value)
    return out


def _links_of(snapshot_raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalise a snapshot's links to a list of dicts.

    The lab serves `links` as an object keyed by `link_id`, while
    `/v1/topology` serves them as a list. Callers here want a flat iterable, and
    silently iterating the dict would yield its keys -- which fails much later
    with a confusing "string indices must be integers" error. Normalising once,
    here, keeps that mistake impossible.
    """
    links = snapshot_raw.get("links", [])
    if isinstance(links, dict):
        return [dict(value, link_id=key) for key, value in links.items()
                if isinstance(value, dict)]
    return [l for l in links if isinstance(l, dict)]


# -- read-only implementations ---------------------------------------------


def _get_topology(_p: GetTopologyInput, client: LabBridgeClient) -> dict[str, Any]:
    raw = client.topology()
    return {
        "nodes": raw.get("nodes", []),
        "links": raw.get("links", []),
        "controller": raw.get("controller"),
        "switch_backend": raw.get("switch_backend"),
    }


def _parse_flow(raw_flow: dict[str, Any], src: str, dst: str,
                kind: FlowKind) -> FlowTelemetry:
    def _num(key: str) -> float | None:
        value = raw_flow.get(key)
        return float(value) if isinstance(value, (int, float)) else None

    return FlowTelemetry(
        flow_id=raw_flow.get("flow_id") or f"{src}->{dst}",
        src_host=src, dst_host=dst, kind=kind,
        path=list(raw_flow.get("path", [])),
        latency_ms=float(raw_flow["latency_ms"])
        if raw_flow.get("latency_ms") is not None else -1.0,
        packet_loss_pct=float(raw_flow.get("packet_loss_pct") or 0.0),
        jitter_ms=_num("jitter_ms"),
        # Carry the per-packet distribution through. The lab measures each flow
        # with a multi-packet ping; keeping only the mean made a latency SLO
        # unfalsifiable against a flow whose mean sits under target while its
        # packets do not.
        samples=[float(s) for s in (raw_flow.get("samples") or [])
                 if isinstance(s, (int, float))],
        min_latency_ms=_num("min_latency_ms"),
        max_latency_ms=_num("max_latency_ms"),
        throughput_mbps=raw_flow.get("throughput_mbps"),
        measured_at=datetime.fromtimestamp(raw_flow.get("measured_at", 0), tz=timezone.utc)
        if raw_flow.get("measured_at") else _now(),
    )


def _get_network_snapshot(p: GetNetworkSnapshotInput,
                          client: LabBridgeClient) -> dict[str, Any]:
    raw = client.snapshot()
    topo_raw = client.topology()

    # Project onto the declared schema rather than splatting the lab's payload
    # into it: the lab serves extra descriptive fields (`role`, `label`) and the
    # domain models forbid extras on purpose, so the contract stays the schema
    # and the lab stays free to add presentation data.
    nodes = [TopologyNode(**_pick(n, TopologyNode, kind=n.get("kind", "host")))
             for n in topo_raw.get("nodes", [])]
    links = [TopologyLink(**_pick(l, TopologyLink, status=l.get("status", "up")))
             for l in topo_raw.get("links", [])]
    topology = Topology(nodes=nodes, links=links,
                       controller=topo_raw.get("controller"))

    link_tel = [
        LinkTelemetry(
            link_id=l["link_id"],
            utilization_pct=float(l.get("utilization_pct", 0.0)),
            capacity_mbps=float(l.get("capacity_mbps", 1000)),
            latency_ms=float(l.get("delay_ms")) if l.get("delay_ms") else None,
            measured_at=datetime.fromtimestamp(
                raw.get("measured_at", 0), tz=timezone.utc) if raw.get("measured_at") else _now(),
        )
        for l in _links_of(raw)
    ]

    flows: list[FlowTelemetry] = []
    for key, raw_flow in raw.get("flows", {}).items():
        src, _, dst = key.partition("->")
        kind = FlowKind.VIDEO if f"{src}->{dst}" == "h1->h4" else FlowKind.VOICE
        flows.append(_parse_flow(raw_flow, src, dst, kind))
    for spec, pair in (p.measure_protected or {}).items():
        src, _, dst = pair.partition(",")
        if not src or not dst:
            continue
        raw_flow = raw.get("flows", {}).get(f"{src}->{dst}")
        if raw_flow is None:
            # The lab does not know about this pair yet; measure it directly.
            raw_flow = client.get(f"/v1/flows/{src}/{dst}").get("flow", {})
        flows.append(_parse_flow(raw_flow, src, dst, FlowKind.VOICE))

    snapshot = NetworkSnapshot(
        topology=topology, links=link_tel, flows=flows,
        congested_links=list(raw.get("congested_links", [])),
        active_scenario=raw.get("active_scenario"),
        controller_reachable=bool(raw.get("controller_reachable", True)),
        captured_at=datetime.fromtimestamp(
            raw.get("measured_at", 0), tz=timezone.utc) if raw.get("measured_at") else _now(),
    )
    return {"snapshot": snapshot.model_dump_json()}


def _evaluate_paths(p: EvaluatePathsInput,
                    client: LabBridgeClient) -> dict[str, Any]:
    raw_paths = client.candidate_paths(p.src_host, p.dst_host)
    snap_raw = client.snapshot()
    snap_links = _links_of(snap_raw)
    util = {l["link_id"]: float(l.get("utilization_pct", 0.0)) for l in snap_links}
    # Union the explicit list with any link the lab reports as not up. A
    # congested link is exactly the case where the two can disagree, and
    # trusting only the explicit list means a link flagged `down` is still
    # scored as a healthy choice.
    congested = set(snap_raw.get("congested_links", []))
    congested.update(
        l["link_id"] for l in snap_links
        if l.get("status") in ("down", "degraded")
    )
    avoid = set(p.avoid_links) | congested

    candidates: list[PathCandidate] = []
    for rp in raw_paths:
        link_ids = rp.get("links", [])
        delays = {l["link_id"]: float(l.get("delay_ms", 0.0)) for l in snap_links}
        # Delay is a property of the whole path. Summing only the links that
        # were *not* filtered out made a path cheaper the more of it ran over a
        # link we had already decided to avoid -- so the worst candidate scored
        # best.
        total_delay = sum(delays.get(l, 0.0) for l in link_ids)
        max_util = max((util.get(l, 0.0) for l in link_ids), default=0.0)
        candidates.append(PathCandidate(
            nodes=rp.get("nodes", []), links=link_ids,
            total_delay_ms=total_delay,
            min_capacity_mbps=rp.get("min_capacity_mbps"),
            max_utilization_pct=round(max_util, 3),
            avoids_congestion=not (set(link_ids) & avoid),
            avoids_protected=True,
        ))
    candidates.sort(key=lambda c: c.score)
    return {
        "candidates": [c.model_dump() for c in candidates],
        "avoided_links": sorted(avoid),
    }


def _get_link_telemetry(p: GetLinkTelemetryInput,
                        client: LabBridgeClient) -> dict[str, Any]:
    raw = client.snapshot()
    links = raw.get("links", [])
    if p.link_ids:
        wanted = set(p.link_ids)
        links = [l for l in links if l["link_id"] in wanted]
    return {"links": links}


def _measure_flow(p: MeasureFlowInput,
                  client: LabBridgeClient) -> dict[str, Any]:
    raw = client.snapshot()
    key = f"{p.src_host}->{p.dst_host}"
    raw_flow = raw.get("flows", {}).get(key)
    if raw_flow is None:
        return {"measured": None, "reason": f"no measurement for {key}"}
    flow = _parse_flow(raw_flow, p.src_host, p.dst_host, FlowKind.VIDEO)
    return {"measured": flow.model_dump()}


# -- mutating implementations -----------------------------------------------


def _start_congestion(p: StartCongestionInput,
                      client: LabBridgeClient) -> dict[str, Any]:
    return client.start_congestion(
        link_id=p.link_id, rate_mbps=p.rate_mbps,
        duration_seconds=p.duration_seconds)


def _reset_lab(_p: ResetLabInput,
               client: LabBridgeClient) -> dict[str, Any]:
    return client.reset()


def _apply_reroute(p: ApplyRerouteInput,
                   client: LabBridgeClient) -> dict[str, Any]:
    """Hand an *already validated* reroute to the controller.

    Callers must have run `safety.gates.validate_reroute` first and found it
    approved. This function does not re-validate and does not refuse on
    judgement grounds -- that separation is what keeps the LLM out of the
    safety decision.
    """
    return client.reroute(src_host=p.src_host, dst_host=p.dst_host, path=p.path)


def _propose_reroute(p: ProposeRerouteInput,
                     _client: LabBridgeClient) -> dict[str, Any]:
    """Build a typed RerouteAction (validation happens separately)."""
    action = RerouteAction(
        flow_id=p.flow_id, src_host=p.src_host, dst_host=p.dst_host,
        path=p.path, reason=p.reason)
    return {"action": action.model_dump(), "action_id": _new_action_id()}


_DISPATCH = {
    "get_topology": _get_topology,
    "get_network_snapshot": _get_network_snapshot,
    "evaluate_paths": _evaluate_paths,
    "get_link_telemetry": _get_link_telemetry,
    "measure_flow": _measure_flow,
    "propose_reroute": _propose_reroute,
    "apply_reroute": _apply_reroute,
    "start_congestion_scenario": _start_congestion,
    "reset_lab": _reset_lab,
}


__all__ = [
    "ApplyRerouteInput",
    "EvaluatePathsInput",
    "GetNetworkSnapshotInput",
    "GetTopologyInput",
    "ProposeRerouteInput",
    "ResetLabInput",
    "StartCongestionInput",
    "TOOL_REGISTRY",
    "ToolResult",
    "run_tool",
    "tool",
]
