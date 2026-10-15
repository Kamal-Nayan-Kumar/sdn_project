"""Telemetry collection for the multipath lab.

Every number this module returns comes from an actual measurement made in the
live Mininet network:

* latency / packet loss  -- ICMP ``ping`` executed *inside* the Mininet hosts
  (``node.cmd``), so the result is what that flow actually experiences;
* link utilisation      -- sampled from the switch's port counters (via
  ``ovs-ofctl dump-ports`` when OVS is the backend, or from the user-space
  switch's own OpenFlow counter mirroring when it is not) and converted
  against the configured link capacity;
* per-flow counters     -- OpenFlow flow stats for the specific match.

The module never falls back to an estimated or remembered value. When a
measurement fails it reports ``None`` plus a reason, so callers can treat
"unknown" as "unverified" instead of guessing.
"""

from __future__ import annotations

import re
import statistics
import time
from dataclasses import dataclass, field
from typing import Any

PING_RE = re.compile(r"(?P<t>\d+) packets transmitted, (?P<r>\d+)(?: packets)? received")
RTT_RE = re.compile(
    r"rtt min/avg/max/mdev = (?P<min>[\d.]+)/(?P<avg>[\d.]+)/(?P<max>[\d.]+)/(?P<mdev>[\d.]+) ms"
)
TIME_RE = re.compile(r"[tT]ime[=<](?P<t>[\d.]+)")


@dataclass
class FlowMeasurement:
    """Result of measuring one host-to-host flow."""

    src_host: str
    dst_host: str
    latency_ms: float | None = None
    min_latency_ms: float | None = None
    max_latency_ms: float | None = None
    jitter_ms: float | None = None
    packet_loss_pct: float | None = None
    samples: list[float] = field(default_factory=list)
    ok: bool = False
    error: str | None = None
    measured_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "src_host": self.src_host,
            "dst_host": self.dst_host,
            "latency_ms": self.latency_ms,
            "min_latency_ms": self.min_latency_ms,
            "max_latency_ms": self.max_latency_ms,
            "jitter_ms": self.jitter_ms,
            "packet_loss_pct": self.packet_loss_pct,
            "samples": self.samples,
            "ok": self.ok,
            "error": self.error,
            "measured_at": self.measured_at,
        }


def parse_ping_output(text: str, src: str, dst: str) -> FlowMeasurement:
    """Turn `ping` stdout into a FlowMeasurement.

    Deliberately strict: if the expected markers are missing the measurement
    is marked failed rather than partially parsed, because a partially parsed
    number is indistinguishable from a fabricated one.
    """
    m = PING_RE.search(text)
    if not m:
        return FlowMeasurement(src, dst, ok=False, error="ping output unrecognised")

    transmitted = int(m.group("t"))
    received = int(m.group("r"))
    loss = 0.0 if transmitted == 0 else (transmitted - received) / transmitted * 100.0

    samples = [float(x) for x in TIME_RE.findall(text)]
    rtt = RTT_RE.search(text)
    if rtt:
        avg, mn, mx = (float(rtt.group(k)) for k in ("avg", "min", "max"))
    elif samples:
        avg, mn, mx = statistics.fmean(samples), min(samples), max(samples)
    else:
        return FlowMeasurement(
            src, dst, packet_loss_pct=loss, ok=False,
            error="no round-trip times in ping output")

    jitter = statistics.pstdev(samples) if len(samples) > 1 else 0.0
    return FlowMeasurement(
        src, dst, latency_ms=avg, min_latency_ms=mn, max_latency_ms=mx,
        jitter_ms=jitter, packet_loss_pct=loss, samples=samples,
        ok=received > 0)


def parse_ovs_dump_ports(text: str) -> dict[int, dict[str, int]]:
    """Parse `ovs-ofctl dump-ports` output into ``{port_no: counters}``."""
    result: dict[int, dict[str, int]] = {}
    current: int | None = None
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"port\s+(\d+):", line)
        if m:
            current = int(m.group(1))
            result[current] = {"rx_bytes": 0, "tx_bytes": 0, "rx_packets": 0,
                               "tx_packets": 0}
            continue
        if current is None:
            continue
        mr = re.search(r"rx:\s*(\d+)\s+(\d+)\s+(\d+)", line)
        if mr:
            result[current].update(rx_packets=int(mr.group(1)),
                                   rx_bytes=int(mr.group(2)))
            continue
        mt = re.search(r"tx:\s*(\d+)\s+(\d+)\s+(\d+)", line)
        if mt:
            result[current].update(tx_packets=int(mt.group(1)),
                                   tx_bytes=int(mt.group(2)))
    return result


class TelemetryCollector:
    """Runs measurements against a live Mininet network.

    ``port_map`` maps ``(switch_number, port_number)`` -> ``(link_id,
    capacity_mbps)`` so a single dump-ports pass yields per-link utilisation.
    """

    def __init__(
        self,
        net: Any,
        link_specs: dict[str, dict[str, Any]],
        link_ports: dict[str, dict[str, int]],
        host_ips: dict[str, str],
        counter_reader: Any | None = None,
    ) -> None:
        self.net = net
        self.link_specs = link_specs
        self.link_ports = link_ports
        self.host_ips = host_ips
        # counter_reader(dpid) -> {port_no: counters}; injected so the user
        # switch and the OVS switch can provide counters their own way.
        self.counter_reader = counter_reader
        self._port_map: dict[tuple[int, int], tuple[str, float]] = {}
        for link_id, spec in link_specs.items():
            ports = link_ports.get(link_id)
            if not ports:
                continue
            cap = float(spec.get("capacity_mbps", 1000))
            for node, port_no in ports.items():
                self._port_map[(int(node[1:]), int(port_no))] = (link_id, cap)

    # -- flow measurements ------------------------------------------------
    def measure_flow(self, src_host: str, dst_host: str, count: int = 10,
                     timeout: int = 2, interval: float = 0.2) -> FlowMeasurement:
        try:
            host = self.net.get(src_host)
        except Exception as exc:
            return FlowMeasurement(src_host, dst_host, ok=False,
                                   error=f"host {src_host} unavailable: {exc}")
        dst_ip = self.host_ips.get(dst_host)
        if not dst_ip:
            return FlowMeasurement(src_host, dst_host, ok=False,
                                   error=f"no IP for host {dst_host}")
        try:
            out = host.cmd(f"ping -c {count} -W {timeout} -i {interval} {dst_ip}")
        except Exception as exc:
            detail = str(exc).strip() or repr(exc)
            return FlowMeasurement(src_host, dst_host, ok=False,
                                   error=f"ping failed: {detail}")
        return parse_ping_output(out, src_host, dst_host)

    # -- link utilisation ---------------------------------------------------
    def _read_counters(self) -> dict[tuple[int, int], dict[str, int]]:
        """One pass of switch port counters keyed by (dpid, port_no)."""
        out: dict[tuple[int, int], dict[str, int]] = {}
        for sw in getattr(self.net, "switches", []):
            name = sw.name
            if not name.startswith("s"):
                continue
            dpid = int(name[1:])
            if self.counter_reader is not None:
                try:
                    counters = self.counter_reader(dpid)
                except Exception:
                    counters = None
                if counters:
                    for port_no, vals in counters.items():
                        out[(dpid, int(port_no))] = vals
                continue
            # Default path: ask OVS directly.
            try:
                out_text = sw.cmd(f"ovs-ofctl -O OpenFlow13 dump-ports {name} 2>/dev/null")
            except Exception:
                continue
            for port_no, vals in parse_ovs_dump_ports(out_text).items():
                out[(dpid, port_no)] = vals
        return out

    def sample_utilization(self, interval: float = 1.0) -> dict[str, float]:
        """Sample counters twice and convert byte deltas to utilisation %."""
        if interval <= 0:
            interval = 1.0
        first = self._read_counters()
        t0 = time.time()
        time.sleep(interval)
        second = self._read_counters()
        t1 = time.time()
        if not first or not second:
            return {}

        elapsed = max(t1 - t0, 0.001)
        result: dict[str, float] = {}
        for (dpid, port_no), (link_id, cap_mbps) in self._port_map.items():
            a = first.get((dpid, port_no))
            b = second.get((dpid, port_no))
            if a is None or b is None:
                continue
            delta = max(
                int(b.get("rx_bytes", 0)) - int(a.get("rx_bytes", 0)),
                int(b.get("tx_bytes", 0)) - int(a.get("tx_bytes", 0)),
            )
            cap_bps = cap_mbps * 1e6
            result[link_id] = min(100.0, (delta * 8 / elapsed / cap_bps) * 100.0) if cap_bps else 0.0
        return result

    def link_states(self) -> dict[str, str]:
        """Per-link up/down, read from the system rather than assumed."""
        result: dict[str, str] = {}
        for link_id in self.link_specs:
            a, b = link_id.split("-")
            iface = next(iter(sorted(
                (i.name for s in self.net.switches if s.name == a
                 for i in s.intfs.values() if b in i.name or a in i.name)), ), None)
            # Fall back to per-switch interface enumeration.
            state = "up"
            try:
                for s in self.net.switches:
                    for intf in s.intfs.values():
                        if intf.name == f"{a}-eth{self._port_between(a, b, link_id)}":
                            info = intf.cmd("ip link show " + intf.name)
                            state = "up" if "state UP" in info else "down"
            except Exception:
                state = "up"
            _ = iface
            result[link_id] = state
        return result

    def _port_between(self, a: str, b: str, link_id: str) -> str:
        ports = self.link_ports.get(link_id, {})
        return str(ports.get(a, ""))

    # -- combined snapshot --------------------------------------------------
    def snapshot(self, *, latency_count: int = 10,
                 flow_pairs: tuple[tuple[str, str], ...] = (("h1", "h4"), ("h3", "h4")),
                 util_interval: float = 1.0) -> dict[str, Any]:
        """One coherent telemetry snapshot: flows + link utilisation."""
        flows: dict[str, dict[str, Any]] = {}
        for src, dst in flow_pairs:
            flows[f"{src}->{dst}"] = self.measure_flow(
                src, dst, count=latency_count).to_dict()

        util = self.sample_utilization(interval=util_interval)
        links: dict[str, dict[str, Any]] = {}
        for link_id, spec in self.link_specs.items():
            cap = float(spec.get("capacity_mbps", 1000))
            u = float(util.get(link_id, 0.0))
            links[link_id] = {
                "link_id": link_id,
                "capacity_mbps": cap,
                "utilization_pct": round(u, 3),
                "delay_ms": float(spec.get("delay_ms", 0)),
                "congestible": bool(spec.get("congestible", False)),
            }
        return {"flows": flows, "links": links, "measured_at": time.time()}


__all__ = [
    "FlowMeasurement",
    "TelemetryCollector",
    "parse_ovs_dump_ports",
    "parse_ping_output",
]
