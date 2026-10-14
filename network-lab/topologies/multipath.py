"""Multi-path Mininet topology for the self-healing SDN lab.

Topology (diamond with a long avoidance hop):

    h1 (video src) ---- s1 ---- s2 ---- s4 ---- h3 (voice)
                     \\     / \\          \\
                      s3 ------------------/  h4 (video dst)
                        \\________________/

Concretely, the switch fabric is:

    s1 --- s2  5 ms   congestible: the short video route
    s1 --- s3  8 ms
    s2 --- s3  5 ms
    s2 --- s4  5 ms   congestible: the controlled bottleneck
    s3 --- s4 12 ms   the long avoidance hop

Host attachments:
    h1 -> s1:1  video source
    h2 -> s1:2  background / congestion source
    h3 -> s4:1  voice host, protected-flow endpoint
    h4 -> s4:2  video destination

The shortest video path h1->h4 is  s1-s2-s4  (~21 ms ping RTT).
The best congestion-free alternative is  s1-s3-s4  (~41 ms ping RTT).
Both stay under the 50 ms video intent, so the reroute is a real fix and
not a trade of one violation for another.

This module is intentionally import-safe: it only needs `mininet` when
`build_mininet_topo()` is actually called.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Declarative description (kept free of Mininet imports so it can be
# imported by the backend / safety engine / tests without a live netns).
# ---------------------------------------------------------------------------

#: Ordered list of switch-level links with their capacity and delay.
#: Delays are calibrated so that ping RTT ≈ 2 × (sum of switch-link delays),
#: keeping every alternative path comfortably under the 50 ms video intent
#: while the congested shortest path inflates well past it.
SWITCH_LINKS: dict[str, dict[str, object]] = {
    # Shortest video route h1->s1->s2->s4->h4 (10 ms configured, ~21 ms RTT).
    "s1-s2": {"capacity_mbps": 1000, "delay_ms": 5, "congestible": True},
    # s2-s4 is the single controlled bottleneck of the congestion scenario.
    "s2-s4": {"capacity_mbps": 1000, "delay_ms": 5, "congestible": True},
    # Diagonal that lets one alternative hop over the bottleneck.
    "s1-s3": {"capacity_mbps": 1000, "delay_ms": 8, "congestible": False},
    "s2-s3": {"capacity_mbps": 1000, "delay_ms": 5, "congestible": False},
    # The long avoidance hop: s1-s3-s4 = 20 ms -> ~41 ms RTT.
    "s3-s4": {"capacity_mbps": 1000, "delay_ms": 12, "congestible": False},
}

HOST_ATTACH: dict[str, dict[str, object]] = {
    "h1": {"switch": "s1", "port": 1, "ip": "10.0.0.1/24", "mac": "00:00:00:00:00:01",
           "role": "video_source"},
    "h2": {"switch": "s1", "port": 2, "ip": "10.0.0.2/24", "mac": "00:00:00:00:00:02",
           "role": "background_source"},
    "h3": {"switch": "s4", "port": 1, "ip": "10.0.0.3/24", "mac": "00:00:00:00:00:03",
           "role": "voice_source"},
    "h4": {"switch": "s4", "port": 2, "ip": "10.0.0.4/24", "mac": "00:00:00:00:00:04",
           "role": "video_sink"},
}

HOST_IP = {h: str(spec["ip"]).split("/")[0] for h, spec in HOST_ATTACH.items()}
HOST_MAC = {h: str(spec["mac"]) for h, spec in HOST_ATTACH.items()}

# Switch-to-switch port assignment, deterministic and mirrored by the Ryu
# controller: lower switch id gets port 2+n, higher gets n.
LINK_PORTS: dict[str, dict[str, int]] = {}
for _i, _lid in enumerate(sorted(SWITCH_LINKS), start=1):
    _a, _b = _lid.split("-")
    _lo, _hi = (_a, _b) if _a < _b else (_b, _a)
    LINK_PORTS[_lid] = {_lo: 2 + _i, _hi: _i}


def switch_ids() -> list[str]:
    return sorted({p for lid in SWITCH_LINKS for p in lid.split("-")})


def host_ids() -> list[str]:
    return sorted(HOST_ATTACH)


def switch_to_switch_link_id(a: str, b: str) -> str:
    return "-".join(sorted((a, b)))


def neighbours(node: str) -> list[tuple[str, str]]:
    """All (link_id, peer) pairs for a node."""
    out = []
    for lid, spec in SWITCH_LINKS.items():
        a, b = lid.split("-")
        if a == node:
            out.append((lid, b))
        elif b == node:
            out.append((lid, a))
    return sorted(out)


def all_switch_paths(src_switch: str, dst_switch: str) -> list[list[str]]:
    """All simple switch paths, ordered by total configured delay."""
    results: list[list[str]] = []

    def walk(node: str, path: list[str], delay: float) -> None:
        if node == dst_switch:
            results.append((list(path), delay))
            return
        for lid, peer in neighbours(node):
            if peer in path:
                continue
            walk(peer, path + [peer], delay + float(SWITCH_LINKS[lid]["delay_ms"]))

    if src_switch == dst_switch:
        return [[src_switch]]
    walk(src_switch, [src_switch], 0.0)
    results.sort(key=lambda t: (t[1], len(t[0])))
    return [p for p, _ in results]


def path_delay(switch_path: list[str]) -> float:
    total = 0.0
    for i in range(len(switch_path) - 1):
        lid = switch_to_switch_link_id(switch_path[i], switch_path[i + 1])
        if lid not in SWITCH_LINKS:
            return float("inf")
        total += float(SWITCH_LINKS[lid]["delay_ms"])
    return total


def path_min_capacity(switch_path: list[str]) -> float | None:
    caps = []
    for i in range(len(switch_path) - 1):
        lid = switch_to_switch_link_id(switch_path[i], switch_path[i + 1])
        spec = SWITCH_LINKS.get(lid)
        if not spec:
            return None
        caps.append(float(spec["capacity_mbps"]))
    return min(caps) if caps else None


def switch_path_for_host_path(host_path: list[str]) -> list[str]:
    """Strip host endpoints from a [h1, s1, s2, s4, h4] path."""
    return [n for n in host_path if n not in HOST_ATTACH]


def host_path(src_host: str, dst_host: str, switch_path: list[str]) -> list[str]:
    return [src_host, *switch_path, dst_host]


def full_path_for_host(src_host: str, dst_host: str) -> list[str]:
    """Full node path for the *default shortest* switch route."""
    src_sw = str(HOST_ATTACH[src_host]["switch"])
    dst_sw = str(HOST_ATTACH[dst_host]["switch"])
    paths = all_switch_paths(src_sw, dst_sw)
    return host_path(src_host, dst_host, paths[0])


# ---------------------------------------------------------------------------
# Mininet construction
# ---------------------------------------------------------------------------


@dataclass
class LabTopology:
    """Description handed to the Mininet builder."""

    switch_links: dict[str, dict[str, object]] = field(
        default_factory=lambda: dict(SWITCH_LINKS)
    )
    host_attach: dict[str, dict[str, object]] = field(
        default_factory=lambda: dict(HOST_ATTACH)
    )

    def summary(self) -> dict[str, object]:
        return {
            "switches": switch_ids(),
            "hosts": host_ids(),
            "switch_links": {
                lid: {k: v for k, v in spec.items()} for lid, spec in self.switch_links.items()
            },
            "shortest_host_paths": {
                (src + "->" + dst): full_path_for_host(src, dst)
                for src in host_ids()
                for dst in host_ids()
                if src != dst
                if str(HOST_ATTACH[src]["switch"]) != str(HOST_ATTACH[dst]["switch"])
            },
        }


def build_mininet_topo(TopoClass=None):
    """Build the actual Mininet Topo object.

    Imported lazily so this module stays importable without Mininet.
    """
    if TopoClass is None:  # pragma: no cover - real Mininet path
        from mininet.topo import Topo as TopoClass

    topo = TopoClass("MultipathLab")

    host_nodes: dict[str, object] = {}
    for host, spec in HOST_ATTACH.items():
        host_nodes[host] = topo.addHost(
            host, ip=str(spec["ip"]), mac=str(spec["mac"])
        )

    switch_nodes: dict[str, object] = {}
    for sw in switch_ids():
        switch_nodes[sw] = topo.addSwitch(
            sw, protocols="OpenFlow13", failMode="standalone"
        )

    for lid in sorted(SWITCH_LINKS):
        spec = SWITCH_LINKS[lid]
        a, b = lid.split("-")
        ports = LINK_PORTS[lid]
        topo.addLink(
            switch_nodes[a], switch_nodes[b],
            port1=ports[a], port2=ports[b],
            bw=int(spec["capacity_mbps"]),   # type: ignore[arg-type]
            delay=f"{spec['delay_ms']}ms",   # type: ignore[arg-type]
        )

    for host, spec in HOST_ATTACH.items():
        sw = str(spec["switch"])
        topo.addLink(host_nodes[host], switch_nodes[sw], port2=int(spec["port"]))  # type: ignore[arg-type]

    return topo


if __name__ == "__main__":  # pragma: no cover
    import json

    print(json.dumps(LabTopology().summary(), indent=2))
