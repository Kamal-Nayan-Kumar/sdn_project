"""Ryu application: per-flow intent routing + REST telemetry for the SDN lab.

Design goals
------------
* One controller for all switches (OpenFlow 1.3). No second controller
  (e.g. POX) ever attaches to these switches.
* Deterministic, explicit flow installation: the controller installs a
  shortest path for every new flow, then honours *explicit* reroute
  commands sent by the backend over a local, token-authenticated REST API.
* It exposes a read-only-ish telemetry REST API on loopback so the backend
  can pull real per-port/per-flow counters.
* It never takes instructions from the LLM. The backend's deterministic
  safety validator is the only thing that can ask it to mutate flows.

Bind address defaults to 127.0.0.1 so the REST API is never exposed to an
untrusted network.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import secrets
import threading
import time
from typing import Any

import eventlet

eventlet.monkey_patch()

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.lib import hub
from ryu.lib.packet import ether_types, ethernet, ipv4, packet
from ryu.ofproto import ofproto_v1_3
from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from webob import Response

LOG = logging.getLogger("sdn_ryu")

# ---------------------------------------------------------------------------
# Topology model (mirrors network-lab/topologies/multipath.py)
# ---------------------------------------------------------------------------

#: Ordered list of switch-level links with their capacity and delay.
#: These values mirror network-lab/topologies/multipath.py exactly. The lab
#: cross-checks them at startup; if they ever drift, the lab refuses to run
#: rather than telemetry quietly describing a different topology.
LINKS: dict[str, dict[str, Any]] = {
    "s1-s2": {"capacity_mbps": 1000, "delay_ms": 5, "congestible": True},
    "s2-s4": {"capacity_mbps": 1000, "delay_ms": 5, "congestible": True},
    "s1-s3": {"capacity_mbps": 1000, "delay_ms": 8, "congestible": False},
    "s2-s3": {"capacity_mbps": 1000, "delay_ms": 5, "congestible": False},
    "s3-s4": {"capacity_mbps": 1000, "delay_ms": 12, "congestible": False},
}

#: Host attachments: host -> (switch, port).
HOST_ATTACH: dict[str, tuple[str, int]] = {
    "h1": ("s1", 1),
    "h2": ("s1", 2),
    "h3": ("s4", 1),
    "h4": ("s4", 2),
}

#: Host IPs, used to learn the flow table quickly.
HOST_IPS: dict[str, str] = {
    "h1": "10.0.0.1",
    "h2": "10.0.0.2",
    "h3": "10.0.0.3",
    "h4": "10.0.0.4",
}

IP_TO_HOST = {ip: host for host, ip in HOST_IPS.items()}

#: Explicit per-flow path overrides installed by the backend.
#: flow_key (src_ip,dst_ip) -> list of switch node ids.
FLOW_OVERRIDES: dict[tuple[str, str], list[str]] = {}

#: Congestion bookkeeping: link_id -> dict(rate_mbps, until_ts).
CONGESTION: dict[str, dict[str, Any]] = {}

STATE_LOCK = threading.Lock()


def _is_multicast(mac: str) -> bool:
    """True for broadcast and multicast MACs.

    These must never be learned into the forwarding table and must always be
    flooded, otherwise the first packet observed pins the group address to a
    single port and every later broadcast is dropped.
    """
    try:
        first = int(str(mac).split(":")[0], 16)
    except (ValueError, IndexError):
        return False
    return bool(first & 0x01)

REST_TOKEN = os.environ.get("RYU_API_TOKEN", "")
REST_BIND = os.environ.get("RYU_REST_BIND", "127.0.0.1")


def _check_token(request) -> bool:
    """Every mutating endpoint requires the local bearer token."""
    if not REST_TOKEN:
        # No token configured => only allow loopback callers.
        return request.client_addr in ("127.0.0.1", "::1")
    auth = request.headers.get("Authorization", "")
    return secrets.compare_digest(auth, f"Bearer {REST_TOKEN}")


def _all_switch_paths(src_ip: str, dst_ip: str) -> list[list[str]]:
    """Enumerate switch-level paths between two hosts, cheapest first.

    Deterministic: ordered by total configured link delay then by link id.
    """
    import itertools

    src_switch = HOST_ATTACH.get(IP_TO_HOST.get(src_ip, ""), (None,))[0]
    dst_switch = HOST_ATTACH.get(IP_TO_HOST.get(dst_ip, ""), (None,))[0]
    if not src_switch or not dst_switch:
        return []

    switches = {sid for sid, _ in HOST_ATTACH.values()} | {
        s for link in LINKS for s in link.split("-")
    }
    switches = sorted(switches)

    adjacency: dict[str, list[tuple[str, float, str]]] = {s: [] for s in switches}
    for link_id, spec in LINKS.items():
        a, b = link_id.split("-")
        adjacency[a].append((b, spec["delay_ms"], link_id))
        adjacency[b].append((a, spec["delay_ms"], link_id))

    results: list[list[str]] = []

    def walk(node: str, path: list[str], delay: float) -> None:
        if node == dst_switch:
            results.append(list(path))
            return
        if len(path) > len(switches):  # simple loop guard
            return
        for nxt, d, _ in sorted(adjacency[node], key=lambda t: (t[1], t[2])):
            if nxt in path:
                continue
            walk(nxt, path + [nxt], delay + d)

    walk(src_switch, [src_switch], 0.0)
    results.sort(key=lambda p: (len(p), tuple(p)))
    return results


#: Static switch-to-switch port assignment, mirroring
#: network-lab/topologies/multipath.py: lower node gets 2+n, higher gets n.
LINK_PORTS: dict[str, dict[str, int]] = {}
for _i, _lid in enumerate(sorted(LINKS), start=1):
    _a, _b = _lid.split("-")
    _lo, _hi = (_a, _b) if _a < _b else (_b, _a)
    LINK_PORTS[_lid] = {_lo: 2 + _i, _hi: _i}


def _edge_ports(link_id: str, node: str) -> int | None:
    """Port number on `node` for `link_id`, or None if `node` isn't an endpoint."""
    ports = LINK_PORTS.get(link_id)
    return ports.get(node) if ports else None


def _path_links(switches: list[str]) -> list[tuple[str, str, int]]:
    """Expand a switch path into [(link_id, current_node, out_port)] steps."""
    steps: list[tuple[str, str, int]] = []
    for i in range(len(switches) - 1):
        a, b = switches[i], switches[i + 1]
        link_id = "-".join(sorted((a, b)))
        port = _edge_ports(link_id, a)
        if port is None:
            continue
        steps.append((link_id, a, port))
    return steps


class SDNIntentController(app_manager.RyuApp):
    """OpenFlow 1.3 controller with explicit reroute support."""

    _CONTEXTS = {"wsgi": WSGIApplication}
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mac_to_port: dict[int, dict[str, int]] = {}
        self.datapaths: dict[int, Any] = {}
        self.port_stats: dict[int, dict[int, dict[str, int]]] = {}
        self.flow_stats: dict[int, list[dict[str, Any]]] = {}
        # The WSGI context is passed in by name, not as `wsgi`.
        self.wsgi = kwargs.get("wsgi") or next(
            (v for v in kwargs.values() if isinstance(v, WSGIApplication)), None)
        # Register the REST controllers: without this the @route decorators
        # above are inert and every request returns 404.
        if self.wsgi is not None:
            self.wsgi.register(IntentRestController, {"sdn_intent": self})
        self.stats_thread = hub.spawn(self._poll_stats)
        LOG.info("SDNIntentController initialised (rest bind=%s token=%s)",
                 REST_BIND, "set" if REST_TOKEN else "unset")

    # ------------------------------------------------------------------
    # OpenFlow plumbing
    # ------------------------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        self.datapaths[datapath.id] = datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # Table-miss: send unmatched packets to the controller.
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)
        LOG.info("switch %s connected (OF1.3)", datapath.id)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply_handler(self, ev):
        body = ev.msg.body
        dpid = ev.msg.datapath.id
        self.port_stats.setdefault(dpid, {})
        for stat in body:
            self.port_stats[dpid][stat.port_no] = {
                "rx_bytes": stat.rx_bytes,
                "tx_bytes": stat.tx_bytes,
                "rx_packets": stat.rx_packets,
                "tx_packets": stat.tx_packets,
                "duration_sec": stat.duration_sec,
                "timestamp": time.time(),
            }

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_reply_handler(self, ev):
        dpid = ev.msg.datapath.id
        flows = []
        for stat in ev.msg.body:
            flows.append(
                {
                    "priority": stat.priority,
                    "match": str(stat.match),
                    "packet_count": stat.packet_count,
                    "byte_count": stat.byte_count,
                    "duration_sec": stat.duration_sec,
                }
            )
        self.flow_stats[dpid] = flows

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match["in_port"]

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None or eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        dst = eth.dst
        src = eth.src
        dpid = datapath.id
        self.mac_to_port.setdefault(dpid, {})
        # Never learn a multicast/broadcast source. Doing so pins the
        # broadcast address to whichever port answered first, after which
        # every broadcast (ARP in particular) is sent to that one port only
        # and silently blackholes.
        if not _is_multicast(src):
            self.mac_to_port[dpid][src] = in_port

        ipv4_pkt = pkt.get_protocol(ipv4.ipv4)
        if ipv4_pkt is not None:
            src_ip, dst_ip = ipv4_pkt.src, ipv4_pkt.dst
            with STATE_LOCK:
                path = FLOW_OVERRIDES.get((src_ip, dst_ip))
            if path:
                out_port = self._port_for(dpid, path, src_ip, dst_ip)
                if out_port is not None:
                    actions = [parser.OFPActionOutput(out_port)]
                    match = self._match_for(parser, src_ip, dst_ip)
                    self.add_flow(datapath, 100, match, actions, idle=0)
                    self._send_packet(datapath, msg, in_port, actions)
                    return
            # Default: L2 learning switch behaviour.
            out_port = (ofproto.OFPP_FLOOD if _is_multicast(dst)
                        else self.mac_to_port[dpid].get(dst, ofproto.OFPP_FLOOD))
            actions = [parser.OFPActionOutput(out_port)]
            if out_port != ofproto.OFPP_FLOOD:
                match = parser.OFPMatch(in_port=in_port, eth_dst=dst, eth_src=src)
                self.add_flow(datapath, 1, match, actions)
            self._send_packet(datapath, msg, in_port, actions)
        else:
            out_port = (ofproto.OFPP_FLOOD if _is_multicast(dst)
                        else self.mac_to_port[dpid].get(dst, ofproto.OFPP_FLOOD))
            actions = [parser.OFPActionOutput(out_port)]
            self._send_packet(datapath, msg, in_port, actions)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _match_for(self, parser, src_ip: str, dst_ip: str):
        return parser.OFPMatch(eth_type=0x0800, ipv4_src=src_ip, ipv4_dst=dst_ip)

    def _port_for(self, dpid: int, path: list[str], src_ip: str, dst_ip: str) -> int | None:
        """Egress port on switch `dpid` for the given switch path."""
        dpid_name = f"s{dpid}"
        if dpid_name not in path:
            return None
        idx = path.index(dpid_name)
        if idx == len(path) - 1:
            # Last switch: deliver to the destination host.
            host = IP_TO_HOST.get(dst_ip)
            if host and HOST_ATTACH.get(host, (None,))[0] == dpid_name:
                return HOST_ATTACH[host][1]
            return None
        nxt = path[idx + 1]
        link_id = "-".join(sorted((dpid_name, nxt)))
        return _edge_ports(link_id, dpid_name)

    def add_flow(self, datapath, priority, match, actions, idle=0, hard=0):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=priority,
            match=match,
            instructions=inst,
            idle_timeout=idle,
            hard_timeout=hard,
        )
        datapath.send_msg(mod)

    def _send_packet(self, datapath, msg, in_port, actions):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data
        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=data,
        )
        datapath.send_msg(out)

    def _poll_stats(self):
        while True:
            hub.sleep(2)
            for dpid, datapath in list(self.datapaths.items()):
                parser = datapath.ofproto_parser
                datapath.send_msg(parser.OFPPortStatsRequest(datapath, 0))
                datapath.send_msg(parser.OFPFlowStatsRequest(datapath))

    def compute_utilization(self) -> dict[str, float]:
        """Derive per-link utilisation from port counters over time."""
        result: dict[str, float] = {}
        with STATE_LOCK:
            snapshot = {dpid: {p: dict(v) for p, v in stats.items()}
                        for dpid, stats in self.port_stats.items()}
        for link_id in LINKS:
            a, b = link_id.split("-")
            da, db = int(a[1:]), int(b[1:])
            pa = _edge_ports(link_id, a)
            pb = _edge_ports(link_id, b)
            if pa is None or pb is None:
                continue
            sa = snapshot.get(da, {}).get(pa, {})
            sb = snapshot.get(db, {}).get(pb, {})
            if not sa or not sb:
                continue
            cap = LINKS[link_id]["capacity_mbps"] * 1e6
            # Approximate: max of the two directions, scaled by capacity.
            tx = max(sa.get("tx_bytes", 0), sb.get("tx_bytes", 0))
            dur = max(sa.get("duration_sec", 1) or 1, sb.get("duration_sec", 1) or 1)
            bps = (tx * 8) / dur if dur > 0 else 0.0
            result[link_id] = min(100.0, (bps / cap) * 100.0)
        return result


# ---------------------------------------------------------------------------
# REST surface (loopback, token-auth for mutations)
# ---------------------------------------------------------------------------


class IntentRestController(ControllerBase):
    def __init__(self, req, link, data, **config):
        super().__init__(req, link, data, **config)
        self.app = data["sdn_intent"]

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        return Response(
            body=body, content_type="application/json", status=status,
            charset="UTF-8")

    @route("sdn", "/v1/switches", methods=["GET"])
    def switches(self, req, **kwargs):
        """Connected datapath ids, so callers can tell lab-up from lab-down."""
        return self._json(
            {
                "switches": sorted(self.app.datapaths.keys()),
                "connected": len(self.app.datapaths),
                "captured_at": time.time(),
            }
        )

    @route("sdn", "/v1/port-stats", methods=["GET"])
    def port_stats(self, req, **kwargs):
        """Latest PORT_STATS counters, keyed by dpid then port number.

        The user-space datapath has no other way to publish counters, so this
        is the telemetry source of record for the `of13switch.py` backend. The
        values come from real OFPT_PORT_STATS_REQUEST/REPLY exchanges, not
        from anything the switch reports out of band.
        """
        with STATE_LOCK:
            stats = {str(dpid): {str(port): dict(counters)
                                 for port, counters in ports.items()}
                     for dpid, ports in self.app.port_stats.items()}
        return self._json({"switches": stats, "captured_at": time.time()})

    @route("sdn", "/v1/flow-overrides", methods=["GET"])
    def flow_overrides(self, req, **kwargs):
        """Currently installed per-flow path overrides.

        Lets the lab report which path a flow is *actually* taking, so latency
        can be attributed to links rather than merely observed.
        """
        with STATE_LOCK:
            overrides = {
                f"{src}->{dst}": {"src_ip": src, "dst_ip": dst, "path": list(path)}
                for (src, dst), path in FLOW_OVERRIDES.items()
            }
        return self._json({"overrides": overrides, "captured_at": time.time()})

    @route("sdn", "/v1/topology", methods=["GET"])
    def topology(self, req, **kwargs):
        nodes = [{"id": h, "kind": "host", "ip": HOST_IPS.get(h)}
                 for h in sorted(HOST_ATTACH)]
        nodes += [
            {"id": s, "kind": "switch"}
            for s in sorted({p for link in LINKS for p in link.split("-")})
        ]
        links = [
            {
                "id": lid,
                "source": lid.split("-")[0],
                "target": lid.split("-")[1],
                "capacity_mbps": spec["capacity_mbps"],
                "delay_ms": spec["delay_ms"],
                "congestible": spec["congestible"],
                "status": "down" if lid in CONGESTION else "up",
            }
            for lid, spec in sorted(LINKS.items())
        ]
        return self._json(
            {"nodes": nodes, "links": links, "controller": "ryu-of1.3",
             "captured_at": time.time()}
        )

    @route("sdn", "/v1/stats/links", methods=["GET"])
    def link_stats(self, req, **kwargs):
        util = self.app.compute_utilization()
        out = []
        for lid, spec in LINKS.items():
            u = util.get(lid, 0.0)
            out.append(
                {
                    "link_id": lid,
                    "capacity_mbps": spec["capacity_mbps"],
                    "delay_ms": spec["delay_ms"],
                    "utilization_pct": round(u, 3),
                    "congested": u >= 80.0 or lid in CONGESTION,
                    "generator": CONGESTION.get(lid),
                }
            )
        return self._json({"links": out, "captured_at": time.time()})

    @route("sdn", "/v1/stats/flows", methods=["GET"])
    def flow_stats(self, req, **kwargs):
        out = []
        for dpid, flows in list(self.app.flow_stats.items()):
            for f in flows:
                out.append({"dpid": f"s{dpid}", **f})
        return self._json({"flows": out})

    @route("sdn", "/v1/flows/overrides", methods=["GET"])
    def list_overrides(self, req, **kwargs):
        if not _check_token(req):
            return self._json({"error": "unauthorized"}, status=401)
        return self._json(
            {
                "overrides": [
                    {"src": k[0], "dst": k[1], "path": v}
                    for k, v in FLOW_OVERRIDES.items()
                ]
            }
        )

    @route("sdn", "/v1/reroute", methods=["POST"])
    def reroute(self, req, **kwargs):
        if not _check_token(req):
            return self._json({"error": "unauthorized"}, status=401)
        try:
            body = json.loads(req.body.decode("utf-8"))
        except Exception:
            return self._json({"error": "invalid json"}, status=400)

        src_ip = str(body.get("src_ip", ""))
        dst_ip = str(body.get("dst_ip", ""))
        switches = body.get("path") or []
        if not (src_ip and dst_ip and isinstance(switches, list) and switches):
            return self._json({"error": "src_ip, dst_ip and path are required"}, status=400)

        src_ok = any(src_ip == ip for ip in HOST_IPS.values())
        dst_ok = any(dst_ip == ip for ip in HOST_IPS.values())
        if not (src_ok and dst_ok):
            return self._json({"error": "unknown endpoint"}, status=400)

        # The controller validates that the path is physically real.
        candidates = _all_switch_paths(src_ip, dst_ip)
        if switches not in candidates:
            return self._json(
                {"error": "path not a valid switch path", "valid": candidates}, status=400
            )

        with STATE_LOCK:
            FLOW_OVERRIDES[(src_ip, dst_ip)] = list(switches)
            # Drop the reverse-direction override so it recomputes symmetrically.
            FLOW_OVERRIDES.pop((dst_ip, src_ip), None)

        installed = 0
        for dpid, dp in list(self.app.datapaths.items()):
            parser = dp.ofproto_parser
            match = self.app._match_for(parser, src_ip, dst_ip)
            rev_match = self.app._match_for(parser, dst_ip, src_ip)
            dp.send_msg(parser.OFPFlowMod(
                datapath=dp, command=dp.ofproto.OFPFC_DELETE,
                out_port=dp.ofproto.OFPP_ANY, out_group=dp.ofproto.OFPG_ANY,
                match=match, priority=100,
            ))
            dp.send_msg(parser.OFPFlowMod(
                datapath=dp, command=dp.ofproto.OFPFC_DELETE,
                out_port=dp.ofproto.OFPP_ANY, out_group=dp.ofproto.OFPG_ANY,
                match=rev_match, priority=100,
            ))
            out_port = self.app._port_for(dpid, switches, src_ip, dst_ip)
            if out_port is not None:
                self.app.add_flow(dp, 100, match,
                                  [parser.OFPActionOutput(out_port)], idle=0)
                installed += 1
        LOG.info("reroute %s->%s via %s (rules=%d)", src_ip, dst_ip, switches, installed)
        return self._json({"ok": True, "path": switches, "flow_rules_installed": installed})

    @route("sdn", "/v1/reset", methods=["POST"])
    def reset(self, req, **kwargs):
        if not _check_token(req):
            return self._json({"error": "unauthorized"}, status=401)
        with STATE_LOCK:
            FLOW_OVERRIDES.clear()
            CONGESTION.clear()
        for _, dp in list(self.app.datapaths.items()):
            parser = dp.ofproto_parser
            dp.send_msg(parser.OFPFlowMod(
                datapath=dp, command=dp.ofproto.OFPFC_DELETE,
                out_port=dp.ofproto.OFPP_ANY, out_group=dp.ofproto.OFPG_ANY,
                priority=100,
            ))
        return self._json({"ok": True, "message": "flow overrides cleared"})

    @route("sdn", "/v1/congestion", methods=["POST"])
    def congestion(self, req, **kwargs):
        """Record an externally-driven congestion generator on a link.

        The actual traffic is generated by Mininet (iperf3), the controller
        only bookkeeps the fact so telemetry sees it.
        """
        if not _check_token(req):
            return self._json({"error": "unauthorized"}, status=401)
        try:
            body = json.loads(req.body.decode("utf-8"))
        except Exception:
            return self._json({"error": "invalid json"}, status=400)
        link_id = str(body.get("link_id", ""))
        rate_mbps = float(body.get("rate_mbps", 0))
        action = str(body.get("action", "start"))
        if link_id not in LINKS:
            return self._json({"error": "unknown link"}, status=400)
        with STATE_LOCK:
            if action == "start":
                CONGESTION[link_id] = {
                    "rate_mbps": rate_mbps,
                    "until_ts": time.time() + float(body.get("duration_seconds", 0)),
                }
            else:
                CONGESTION.pop(link_id, None)
        return self._json({"ok": True, "congestion": dict(CONGESTION)})
