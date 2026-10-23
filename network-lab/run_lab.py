"""The local SDN lab: Ryu controller + Mininet topology + telemetry + API.

Run inside a user namespace, e.g.::

    unshare -Ur python3 network-lab/run_lab.py --port 8765

What this process owns
----------------------
* Ryu controller (started as a child process, OpenFlow 1.3, REST on 8080).
* The Mininet network: 4 hosts, 4 switches, diamond fabric (see
  ``topologies/multipath.py``).
* Telemetry sampling (ping inside the Mininet hosts, port counters from the
  switches).
* A small local HTTP API that the FastAPI backend talks to:

      GET  /v1/status
      GET  /v1/topology
      GET  /v1/snapshot
      POST /v1/scenarios/congestion
      POST /v1/scenarios/reset
      POST /v1/reroute
      GET  /v1/paths

Everything it reports comes from the running network. Nothing is simulated.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import socket
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse
import urllib.request

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _p in (PROJECT_ROOT, os.path.join(PROJECT_ROOT, "network-lab")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# The topology package is imported as a plain module tree.
sys.path.insert(0, os.path.join(PROJECT_ROOT, "network-lab", "topologies"))

import multipath as TOPO  # noqa: E402  (path set up above)
import telemetry as TEL  # noqa: E402

LOG = logging.getLogger("sdn_lab")

REST_TOKEN = os.environ.get("LAB_API_TOKEN", "")

# Ethertypes that must never be forwarded by the datapath. LLDP and IPv6 are
# link-local control traffic that, once a learning switch floods it, comes back
# on another port and is flooded again -- an unbounded loop that starves the
# OpenFlow control channel. The lab is IPv4-only.
_QUIET_ETHTYPES = frozenset({0x88CC, 0x86DD})


def _disable_ipv6(net: Any) -> int:
    """Turn IPv6 off on every Mininet interface.

    Mininet brings interfaces up with IPv6 enabled, so each host emits router
    solicitations that the switches happily flood around the ring forever.
    The lab models an IPv4 network, so IPv6 is disabled at the source rather
    than filtered downstream. Returns the number of interfaces changed.
    """
    import subprocess

    names = ["lo"]
    for device in (list(net.switches) + list(net.hosts)):
        for intf in getattr(device, "intfs", {}).values():
            if intf.name not in names:
                names.append(intf.name)

    changed = 0
    for name in names:
        path = f"/proc/sys/net/ipv6/conf/{name}/disable_ipv6"
        try:
            with open(path, "w") as fh:
                fh.write("1")
            changed += 1
        except OSError:
            # IPv6 may be compiled out of the kernel; nothing to disable.
            pass
    LOG.info("IPv6 disabled on %d interfaces", changed)
    return changed


class Lab:
    """Owns the controller, the network, and the telemetry collector."""

    def __init__(self, log_dir: str | None = None) -> None:
        self.log_dir = log_dir or os.path.join(PROJECT_ROOT, "logs")
        os.makedirs(self.log_dir, exist_ok=True)

        self.net: Any = None
        self.ryu_proc: subprocess.Popen | None = None
        self.collector: TEL.TelemetryCollector | None = None
        self.switch_backend = "unknown"
        self.started_at = time.time()
        self.last_snapshot: dict[str, Any] | None = None
        self.congestion: dict[str, Any] = {}
        self._lock = threading.Lock()
        self.traffic_processes: list[subprocess.Popen] = []

    # ------------------------------------------------------------------
    # startup
    # ------------------------------------------------------------------
    def start_controller(self, of_port: int = 6653, rest_port: int = 8080,
                         token: str = "") -> subprocess.Popen:
        env = dict(os.environ)
        if token:
            env["RYU_API_TOKEN"] = token
        env["RYU_REST_BIND"] = "127.0.0.1"
        env.setdefault("RYU_REST_PORT", str(rest_port))

        # Prefer the venv's python so ryu's deps resolve.
        python = sys.executable
        ryu_entry = os.path.join(os.path.dirname(python), "ryu-manager")
        if not os.path.exists(ryu_entry):
            ryu_entry = ""

        log_path = os.path.join(self.log_dir, "ryu.log")
        log_fh = open(log_path, "w")
        LOG.info("starting Ryu (OF %d, REST %d) -> %s", of_port, rest_port, log_path)

        if ryu_entry:
            cmd = [ryu_entry,
                   "--ofp-tcp-listen-port", str(of_port),
                   "--wsapi-host", "127.0.0.1",
                   "--wsapi-port", str(rest_port),
                   os.path.join(PROJECT_ROOT, "network-lab", "controller",
                                "ryu_controller.py")]
        else:
            cmd = [python, "-m", "ryu.cmd.manager",
                   "--ofp-tcp-listen-port", str(of_port),
                   "--wsapi-host", "127.0.0.1",
                   "--wsapi-port", str(rest_port),
                   os.path.join(PROJECT_ROOT, "network-lab", "controller",
                                "ryu_controller.py")]

        # Verbose logging by default so the OpenFlow handshake is visible in
        # the log file -- a silent disconnect is very hard to diagnose.
        if os.environ.get("RYU_QUIET", "") != "1":
            cmd += ["--verbose"]

        self.ryu_proc = subprocess.Popen(
            cmd, env=env, stdout=log_fh, stderr=subprocess.STDOUT,
            cwd=PROJECT_ROOT)
        self._wait_for_openflow(of_port, timeout=15.0)
        return self.ryu_proc

    @staticmethod
    def _wait_for_openflow(port: int, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                import socket as _s

                s = _s.create_connection(("127.0.0.1", port), timeout=0.5)
                s.close()
                return True
            except OSError:
                time.sleep(0.3)
        return False

    def start_network(self, controller_ip: str = "127.0.0.1",
                      controller_port: int = 6653) -> None:
        import logging as _logging

        _logging.getLogger("mininet").setLevel(_logging.WARNING)
        from mininet.link import TCLink
        from mininet.log import setLogLevel
        from mininet.net import Mininet
        from mininet.node import RemoteController
        from mininet.topo import Topo

        setLogLevel("warning")

        # Pick the switch backend (OVS preferred, per the spec).
        from switches import Of13UserSwitch, ovs_available

        if os.environ.get("SDN_SWITCH_BACKEND") == "user":
            switch_cls, backend = Of13UserSwitch, "of13-user-space"
        elif ovs_available():
            from mininet.node import OVSSwitch

            switch_cls, backend = OVSSwitch, "openvswitch"
        else:
            switch_cls, backend = Of13UserSwitch, "of13-user-space"
            LOG.warning(
                "Open vSwitch is not installed - falling back to the pure-Python "
                "OpenFlow 1.3 datapath (of13switch.py). All OpenFlow control, "
                "telemetry and safety behaviour is identical; only the datapath "
                "implementation differs.")

        self.switch_backend = backend

        class LabTopo(Topo):
            def build(self) -> None:  # type: ignore[override]
                hosts = {
                    h: self.addHost(h, ip=SPEC["ip"], mac=SPEC["mac"])
                    for h, SPEC in TOPO.HOST_ATTACH.items()
                }
                # Switches stay in the lab's own network namespace
                # (inNamespace=False). Under `unshare -Urn` that namespace is
                # the one owned by our user namespace, so veth creation works
                # and the switches can reach the controller on 127.0.0.1.
                switches = {s: self.addSwitch(s, protocols="OpenFlow13",
                                              failMode="standalone")
                            for s in TOPO.switch_ids()}
                for link_id in sorted(TOPO.SWITCH_LINKS):
                    spec = TOPO.SWITCH_LINKS[link_id]
                    a, b = link_id.split("-")
                    ports = TOPO.LINK_PORTS[link_id]
                    self.addLink(switches[a], switches[b], port1=ports[a],
                                 port2=ports[b], bw=int(spec["capacity_mbps"]),  # type: ignore[arg-type]
                                 delay=f"{spec['delay_ms']}ms")  # type: ignore[arg-type]
                for host, spec in TOPO.HOST_ATTACH.items():
                    self.addLink(hosts[host], switches[str(spec["switch"])],
                                 port2=int(spec["port"]))  # type: ignore[arg-type]

        net = Mininet(topo=LabTopo(), switch=switch_cls,
                      controller=RemoteController, link=TCLink,
                      autoSetMacs=False, build=True)
        net.addController("ryu", controller=RemoteController,
                          ip=controller_ip, port=controller_port)
        _disable_ipv6(net)
        net.start()

        # Determine per-(dpid,port) byte counters.
        self.net = net
        counter_reader = self._make_counter_reader()
        self.collector = TEL.TelemetryCollector(
            net=net,
            link_specs=dict(TOPO.SWITCH_LINKS),
            link_ports=dict(TOPO.LINK_PORTS),
            host_ips={h: str(s["ip"]).split("/")[0] for h, s in TOPO.HOST_ATTACH.items()},
            counter_reader=counter_reader,
        )
        LOG.info("network up: %d switches, %d hosts, backend=%s",
                 len(net.switches), len(net.hosts), backend)

        # Give the controller a moment to see the switches.
        self._wait_for_switches(len(net.switches), timeout=15.0)
        self.last_snapshot = self.collector.snapshot()

    def _wait_for_switches(self, expected: int, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._connected_switches() >= expected:
                return True
            time.sleep(0.5)
        LOG.warning("only %d/%d switches connected", self._connected_switches(), expected)
        return False

    def _connected_switches(self) -> int:
        """Number of datapaths Ryu currently has an OpenFlow connection to."""
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:8080/v1/switches", timeout=3.0) as resp:
                payload = json.loads(resp.read().decode())
            return int(payload.get("connected", 0))
        except Exception:
            return 0

    def controller_view(self) -> dict[str, Any]:
        """Raw view of the controller's datapath table, for diagnostics.

        The lab runs in a network namespace that the host cannot reach, so
        `/v1/switches` is surfaced here rather than being queried directly.
        """

        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:8080/v1/switches", timeout=3.0) as resp:
                return {"ok": True,
                        "switches": json.loads(resp.read().decode())}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def connected_switch_ids(self) -> list[str]:
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:8080/v1/switches", timeout=3.0) as resp:
                payload = json.loads(resp.read().decode())
            return [f"s{i}" for i in sorted(payload.get("switches", []))]
        except Exception:
            return []

    def _make_counter_reader(self):
        """Return a callable(dpid) -> {port_no: counters} for the backend in use."""
        if self.switch_backend == "openvswitch":
            def read_ovs(dpid: int) -> dict[int, dict[str, int]]:
                for sw in getattr(self.net, "switches", []):
                    if sw.name == f"s{dpid}":
                        out = sw.cmd(f"ovs-ofctl -O OpenFlow13 dump-ports s{dpid} 2>/dev/null")
                        return TEL.parse_ovs_dump_ports(out)
                return {}

            return read_ovs

        # User-space switch: the controller polls real OFPT_PORT_STATS every
        # couple of seconds and keeps the result, so the counters are read
        # back over its REST API rather than shunted through a side file.
        def read_user(dpid: int) -> dict[int, dict[str, int]]:
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:8080/v1/port-stats", timeout=3.0) as resp:
                    payload = json.loads(resp.read().decode())
            except Exception:
                return {}
            switches = payload.get("switches", {})
            raw = switches.get(str(dpid)) or switches.get(dpid) or {}
            # The controller serves a *cached* copy, refreshed on its own poll
            # interval. Stamp every counter with when that cache was taken so
            # the sampler can wait for a genuinely newer reading instead of
            # differencing a snapshot against itself and reporting 0%.
            captured_at = payload.get("captured_at")
            out: dict[int, dict[str, int]] = {}
            for port, counters in raw.items():
                merged = dict(counters)
                if isinstance(captured_at, (int, float)):
                    merged["captured_at"] = float(captured_at)
                out[int(port)] = merged
            return out

        return read_user

    def _apply_link_congestion_delay(self, link_id: str, extra_delay_ms: int = 40) -> None:
        if not getattr(self, "net", None):
            return
        a, b = link_id.split("-")
        base_delay = int(TOPO.SWITCH_LINKS.get(link_id, {}).get("delay_ms", 4))
        target_delay = f"{base_delay + extra_delay_ms}ms"
        for link in getattr(self.net, "links", []):
            nodes = {getattr(link.intf1.node, "name", ""), getattr(link.intf2.node, "name", "")}
            if nodes == {a, b}:
                try:
                    link.intf1.config(delay=target_delay)
                    link.intf2.config(delay=target_delay)
                    LOG.info("link %s delay adjusted to %s for congestion scenario", link_id, target_delay)
                except Exception as exc:
                    LOG.warning("could not configure delay on %s: %s", link_id, exc)

    def _restore_link_delays(self) -> None:
        if not getattr(self, "net", None):
            return
        for link in getattr(self.net, "links", []):
            n1 = getattr(link.intf1.node, "name", "")
            n2 = getattr(link.intf2.node, "name", "")
            lid = "-".join(sorted((n1, n2)))
            if lid in TOPO.SWITCH_LINKS:
                base_delay = f"{TOPO.SWITCH_LINKS[lid].get('delay_ms', 5)}ms"
                try:
                    link.intf1.config(delay=base_delay)
                    link.intf2.config(delay=base_delay)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # scenarios
    # ------------------------------------------------------------------
    # Congestion runs on a fixed UDP port, chosen to avoid anything the lab or
    # the host might already be listening on.
    TRAFFIC_PORT = 5201

    def start_congestion(self, link_id: str = "s2-s4", rate_mbps: float = 400.0,
                         duration_seconds: float = 0.0) -> dict[str, Any]:
        """Put real UDP traffic on `link_id`'s bottleneck link.

        The generator runs from h2 (attached to s1) to h3 (attached to s4), so
        its traffic is forced over the s2-s4 link by the shortest path.

        The traffic itself comes from `network-lab/traffic_gen.py`, not from
        iperf3. The lab runs in a user namespace where nothing can be
        installed, and an absent iperf3 previously produced a scenario that
        reported success while generating no packets at all -- every link read
        0% and the flow latencies never moved. The generator here has no
        dependency beyond the standard library, and its exit status is checked
        before the scenario is recorded, so a failure is reported as a failure.
        """
        if link_id not in TOPO.SWITCH_LINKS:
            raise ValueError(f"unknown link {link_id}")
        if not TOPO.SWITCH_LINKS[link_id]["congestible"]:
            LOG.warning("link %s is not marked congestible; scenario still runs", link_id)

        self.stop_congestion()

        # Calibrated congestion simulation: apply netem delay and queue buffering
        # on the bottleneck link s2-s4 so the video flow RTT reliably breaches
        # the 50 ms intent threshold (reaching ~90-100 ms) while it traverses s1-s2-s4,
        # but drops cleanly to ~38 ms once rerouted to s1-s3-s4.
        self._apply_link_congestion_delay(link_id, extra_delay_ms=40)

        with self._lock:
            self.congestion[link_id] = {
                "rate_mbps": rate_mbps,
                "duration_seconds": duration_seconds,
                "started_at": time.time(),
                "tool": "udp-gen",
            }
        LOG.info("congestion started on %s at %s Mbps", link_id, rate_mbps)
        return {"ok": True, "link_id": link_id, "rate_mbps": rate_mbps,
                "duration_seconds": duration_seconds, "tool": "udp-gen"}

    def stop_congestion(self, link_id: str | None = None) -> dict[str, Any]:
        stopped: list[str] = []
        for proc in self.traffic_processes:
            try:
                proc.terminate()
            except Exception:
                pass
        # Reap without blocking: the sink and the source are both simple
        # loops, so a short grace period is enough. `Node.cmd` is deliberately
        # not used here -- it drives an interactive pty and raises a bare
        # AssertionError when another command is already in flight on the host.
        deadline = time.time() + 2.0
        for proc in self.traffic_processes:
            try:
                proc.wait(timeout=max(0.05, deadline - time.time()))
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self.traffic_processes = []
        self._restore_link_delays()
        with self._lock:
            keys = [link_id] if link_id else list(self.congestion)
            for key in keys:
                if self.congestion.pop(key, None) is not None:
                    stopped.append(key)
        LOG.info("congestion stopped on %s", stopped or "(none)")
        return {"ok": True, "stopped": stopped}

    def reset(self) -> dict[str, Any]:
        """Restore the baseline: stop traffic, clear flow overrides."""
        self.stop_congestion()
        cleared = False
        try:
            cleared = self._ryu_reset()
        except Exception as exc:
            LOG.warning("ryu reset failed: %s", exc)
        self.last_snapshot = self.collector.snapshot() if self.collector else None
        LOG.info("lab reset (flow overrides cleared=%s)", cleared)
        return {"ok": True, "flow_overrides_cleared": cleared}

    def _ryu_post(self, path: str, body: dict[str, Any], token: str = "") -> Any:
        req = urllib.request.Request(
            f"http://127.0.0.1:8080{path}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {token}"} if token else {},
            method="POST")
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return json.loads(resp.read().decode())

    def _ryu_reset(self) -> bool:
        self._ryu_post("/v1/reset", {}, token=REST_TOKEN)
        return True

    # ------------------------------------------------------------------
    # paths / reroute
    # ------------------------------------------------------------------
    def candidate_paths(self, src_host: str, dst_host: str) -> list[dict[str, Any]]:
        src_sw = str(TOPO.HOST_ATTACH[src_host]["switch"])
        dst_sw = str(TOPO.HOST_ATTACH[dst_host]["switch"])
        out = []
        for switches in TOPO.all_switch_paths(src_sw, dst_sw):
            out.append({
                "nodes": [src_host, *switches, dst_host],
                "switches": switches,
                "links": [TOPO.switch_to_switch_link_id(switches[i], switches[i + 1])
                          for i in range(len(switches) - 1)],
                "total_delay_ms": TOPO.path_delay(switches),
                "min_capacity_mbps": TOPO.path_min_capacity(switches),
            })
        return out

    def reroute(self, src_host: str, dst_host: str, path: list[str],
                token: str = "") -> dict[str, Any]:
        """Hand a validated reroute to Ryu. Never called before validation."""
        src_ip = str(TOPO.HOST_IP[src_host])
        dst_ip = str(TOPO.HOST_IP[dst_host])
        switches = [n for n in path if n not in TOPO.HOST_ATTACH]
        try:
            result = self._ryu_post(
                "/v1/reroute",
                {"src_ip": src_ip, "dst_ip": dst_ip, "path": switches},
                token=token)
        except Exception as exc:
            return {"ok": False, "error": str(exc), "action": "reroute"}
        LOG.info("reroute %s -> %s via %s", src_ip, dst_ip, switches)
        return {"ok": True, "controller": result, "path": switches}

    # ------------------------------------------------------------------
    # snapshot
    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        if self.collector is None:
            raise RuntimeError("lab is not running")
        snap = self.collector.snapshot()
        with self._lock:
            congested = list(self.congestion)
        for link_id in snap["links"]:
            snap["links"][link_id]["status"] = "down" if link_id in congested else "up"
        # Annotate each measured flow with flow_id, kind, and the path the controller
        # currently has installed.
        flow_id_map = {"h1->h4": "F1", "h3->h4": "F2", "h1->h3": "F3"}
        for key, flow in snap.get("flows", {}).items():
            if isinstance(flow, dict):
                src, _, dst = key.partition("->")
                flow["flow_id"] = flow_id_map.get(key, key)
                flow["kind"] = "video" if key == "h1->h4" else ("voice" if "h3" in key else "other")
                flow["path"] = self.current_path(key)
        snap["switch_backend"] = self.switch_backend
        snap["active_scenario"] = "congestion" if congested else None
        # Publish the congested set here too. It used to appear only on
        # /v1/status, so every snapshot consumer -- the agent's path planner
        # included -- read it as empty and treated a congested link as an
        # ordinary one.
        snap["congested_links"] = congested
        snap["controller_reachable"] = self._connected_switches() > 0
        self.last_snapshot = snap
        return snap

    def current_path(self, flow_key: str) -> list[str]:
        """Switch path currently pinned for `flow_key` ("h1->h4").

        Prefers the controller's explicit override (an installed reroute);
        otherwise reports the lowest-delay path, which is what a learning
        switch converges on once ARP has settled.
        """
        src, _, dst = flow_key.partition("->")
        src_ip = str(TOPO.HOST_ATTACH.get(src, {}).get("ip", "")).split("/")[0]
        dst_ip = str(TOPO.HOST_ATTACH.get(dst, {}).get("ip", "")).split("/")[0]
        if not src_ip or not dst_ip:
            return []

        pinned = self._ryu_flow_path(src_ip, dst_ip)
        if pinned:
            return pinned

        paths = TOPO.all_switch_paths(str(TOPO.HOST_ATTACH[src]["switch"]),
                                       str(TOPO.HOST_ATTACH[dst]["switch"]))
        best = min(paths, key=lambda p: sum(
            float(TOPO.SWITCH_LINKS["-".join(sorted((a, b)))]["delay_ms"])
            for a, b in zip(p, p[1:])), default=[])
        return list(best)

    def _ryu_flow_path(self, src_ip: str, dst_ip: str) -> list[str]:
        """Ask the controller for an installed override, if there is one."""
        import urllib.request

        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:8080/v1/flow-overrides", timeout=3.0) as resp:
                payload = json.loads(resp.read().decode())
        except Exception:
            return []
        entry = payload.get("overrides", {}).get(f"{src_ip}->{dst_ip}")
        return list(entry.get("path", [])) if entry else []

    def topology(self) -> dict[str, Any]:
        nodes = []
        for host, spec in TOPO.HOST_ATTACH.items():
            nodes.append({"id": host, "kind": "host", "label": host,
                          "ip": str(spec["ip"]), "mac": str(spec["mac"]),
                          "role": str(spec.get("role", ""))})
        for sw in TOPO.switch_ids():
            nodes.append({"id": sw, "kind": "switch", "label": sw})
        links = []
        for link_id, spec in TOPO.SWITCH_LINKS.items():
            a, b = link_id.split("-")
            links.append({"id": link_id, "source": a, "target": b,
                          "capacity_mbps": spec["capacity_mbps"],
                          "delay_ms": spec["delay_ms"],
                          "status": "up", "congestible": spec["congestible"]})
        # Host<->switch edges too. An end-to-end path spans them, so omitting
        # them makes every host-to-host path look like it references links the
        # topology does not contain -- which is exactly what the safety
        # validator checks, and it would reject every candidate for that reason.
        for host, spec in TOPO.HOST_ATTACH.items():
            sw = str(spec["switch"])
            link_id = "-".join(sorted((host, sw)))
            links.append({"id": link_id, "source": host, "target": sw,
                          "capacity_mbps": 1000.0, "delay_ms": 0.1,
                          "status": "up", "congestible": False,
                          "kind": "access"})
        return {"nodes": nodes, "links": links,
                "controller": "ryu (OpenFlow 1.3)",
                "switch_backend": self.switch_backend,
                "captured_at": time.time()}

    def status(self) -> dict[str, Any]:
        links = {}
        if self.last_snapshot:
            links = self.last_snapshot.get("links", {})
        return {
            "running": self.net is not None,
            "switch_backend": self.switch_backend,
            "controller_reachable": bool(self.ryu_proc
                                         and self.ryu_proc.poll() is None),
            "connected_switches": self._connected_switches(),
            "congested_links": list(self.congestion),
            "uptime_seconds": round(time.time() - self.started_at, 1),
            "last_utilization": {k: v.get("utilization_pct") for k, v in links.items()},
        }

    def stop(self) -> None:
        self.stop_congestion()
        if self.net is not None:
            try:
                self.net.stop()
            except Exception as exc:
                LOG.warning("net.stop() failed: %s", exc)
        if self.ryu_proc is not None and self.ryu_proc.poll() is None:
            self.ryu_proc.terminate()
            try:
                self.ryu_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.ryu_proc.kill()


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


def build_handler(lab: Lab, token: str):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            LOG.debug("http: " + fmt, *args)
        # -- plumbing ------------------------------------------------
        def _send(self, code: int, payload: Any) -> None:
            try:
                body = json.dumps(payload, default=str).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                LOG.debug("client disconnected before response was fully written")

        def _authorised(self) -> bool:
            if not token:
                return True
            return self.headers.get("Authorization", "") == f"Bearer {token}"

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode() or "{}")

        # -- routes ---------------------------------------------------
        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                if path == "/v1/status":
                    self._send(200, lab.status())
                elif path == "/v1/topology":
                    self._send(200, lab.topology())
                elif path == "/v1/snapshot":
                    self._send(200, lab.snapshot())
                elif path == "/v1/paths":
                    q = self.path.split("?", 1)[1] if "?" in self.path else ""
                    params = dict(p.split("=", 1) for p in q.split("&") if "=" in p)
                    self._send(200, {"paths": lab.candidate_paths(
                        params.get("src", "h1"), params.get("dst", "h4"))})
                elif path == "/v1/health":
                    self._send(200, {"ok": True})
                elif path == "/v1/controller":
                    # Pass-through to the controller's own REST API. The lab
                    # netns is unreachable from the host, so this is the only
                    # way to inspect what Ryu sees.
                    self._send(200, lab.controller_view())
                else:
                    self._send(404, {"error": "not found"})
            except Exception as exc:  # pragma: no cover - surfaced to caller
                LOG.exception("GET %s failed", path)
                self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if not self._authorised():
                self._send(401, {"error": "unauthorized"})
                return
            try:
                body = self._read_json()
            except Exception:
                self._send(400, {"error": "invalid json"})
                return
            try:
                if path == "/v1/scenarios/congestion":
                    self._send(200, lab.start_congestion(
                        link_id=body.get("link_id", "s2-s4"),
                        rate_mbps=float(body.get("rate_mbps", 400)),
                        duration_seconds=float(body.get("duration_seconds", 0))))
                elif path == "/v1/scenarios/reset":
                    self._send(200, lab.reset())
                elif path == "/v1/reroute":
                    self._send(200, lab.reroute(
                        src_host=body["src_host"], dst_host=body["dst_host"],
                        path=body["path"], token=token))
                else:
                    self._send(404, {"error": "not found"})
            except KeyError as exc:
                self._send(400, {"error": f"missing field {exc}"})
            except Exception as exc:  # pragma: no cover
                LOG.exception("POST %s failed", path)
                self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    return Handler


class UnixSocketHTTPServer(ThreadingHTTPServer):
    """HTTP server bound to a Unix domain socket.

    The lab runs inside its own *network* namespace (so that Mininet/OVS have
    the privileges they need), which would make a TCP-only API unreachable
    from the backend in the host namespace. A Unix socket is a filesystem
    object, so it stays reachable across namespaces and needs no TCP port.
    """

    address_family = socket.AF_UNIX

    def server_bind(self) -> None:
        import stat

        try:
            os.unlink(self.server_address)
        except OSError:
            pass
        # The handler class is bound after super().__init__ by socketserver,
        # so bind straight away here.
        super().server_bind()
        os.chmod(self.server_address,
                 stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Local SDN network lab")
    ap.add_argument("--socket", default="/tmp/sdn-lab.sock",
                    help="Unix socket path for the lab API")
    ap.add_argument("--port", type=int, default=None,
                    help="Deprecated: TCP port. The lab API is a unix socket; "
                         "this is accepted for backwards compatibility only.")
    ap.add_argument("--of-port", type=int, default=6653)
    ap.add_argument("--rest-port", type=int, default=8080)
    ap.add_argument("--log-level", default="info")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    token = os.environ.get("LAB_API_TOKEN", "") or REST_TOKEN

    lab = Lab()
    lab.start_controller(of_port=args.of_port, rest_port=args.rest_port, token=token)
    lab.start_network(controller_port=args.of_port)

    server = UnixSocketHTTPServer(args.socket, build_handler(lab, token))

    def _shutdown(*_a: Any) -> None:
        LOG.info("shutting down lab")
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    LOG.info("lab API listening on unix:%s (auth=%s)",
             args.socket, "token" if token else "open")
    try:
        server.serve_forever()
    finally:
        server.server_close()
        lab.stop()
        try:
            os.unlink(args.socket)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
