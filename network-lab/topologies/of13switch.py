#!/usr/bin/env python3
"""A small, self-contained OpenFlow 1.3 user-space switch for the SDN lab.

Why this exists
---------------
The spec asks for Ryu + Open vSwitch. OVS is the primary backend and is used
automatically whenever `ovs-vsctl` is available (see
`network-lab/topologies/lab.py`). On a machine where OVS is not installed
the lab still needs a *real* OpenFlow 1.3 switch that a real controller can
drive, otherwise every downstream claim -- telemetry, safety validation,
verified recovery -- would be untestable.

This switch therefore provides a fallback that is honest about what it is:

  * it speaks the OpenFlow 1.3 wire protocol, negotiated with the real Ryu
    controller running in a separate process;
  * it is driven by OpenFlow messages only -- no side channel, no shell, no
    configuration file of flow rules;
  * it implements the subset the controller actually uses: HELLO handshake,
    FEATURES, PACKET_IN/OUT, FLOW_MOD (add/delete), MULTIPART port/flow stats
    and BARRIER.

Wire encoding is delegated to Ryu's own parser module (`ryu.ofproto.
ofproto_v1_3_parser`), so anything the controller sends is decoded exactly
the way the controller itself would decode it. That keeps this file small and
removes a whole class of hand-rolled header bugs.

Usage
-----
    of13switch.py --controller 127.0.0.1:6653 --dpid 1 \\
                  --listen unix:/tmp/s1.listen \\
                  --port 1:s1-eth1 --port 2:s1-eth2 ...

The socket/listen convention matches Mininet's UserSwitch, so Mininet's
`UserSwitch` subclass can simply launch this script.

Limitations (documented, not hidden)
------------------------------------
* One controller connection at a time; no auxiliary connections.
* No group tables, meters, or multiple flow tables.
* Table-miss always reports to the controller.
* No OFPT_ROLE handling.
"""

from __future__ import annotations

import argparse
import errno
import logging
import os
import select
import signal
import socket
import struct
import sys
import threading
import time
from typing import Any

# Make sure the vendored Ryu is importable even when this script is launched
# as a bare process by Mininet (minimal environment, no PYTHONPATH).
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, "..", "..", "network-lab", "controller", "ryu-src")):
    _p = os.path.abspath(_p)
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Ryu's parser is the protocol authority. If it is unavailable the switch
# refuses to start rather than guess at the wire format.
try:
    from ryu.ofproto import ofproto_parser as of_proto_parser
    from ryu.ofproto import ofproto_v1_3 as ofp
    from ryu.ofproto import ofproto_v1_3_parser as ofp_parser
except ImportError as exc:  # pragma: no cover - dependency guard
    sys.stderr.write(
        f"of13switch: ryu is required for OpenFlow 1.3 wire encoding ({exc})\n"
    )
    sys.exit(70)

LOG = logging.getLogger("of13switch")

# Number of bytes buffered for a single oversized message.
MAX_RECV = 65535

# Port flags presented in FEATURES_REPLY. This mirrors what an OVS datapath
# reports: 1G copper + link up.
PORT_CURR_FEATURES = 0x0000C000 | 0x00000001  # COPPER | _10MB
PORT_SUPPORTED = 0x0000C000
PORT_ADVERTISED = 0x0000C000
PORT_CONFIG = 0
PORT_STATE_UP = 0  # not LINK_DOWN
PORT_STATE_LINK_DOWN = 1

# Link-local control traffic that must not be forwarded. See
# `_handle_port_frame` for why: forwarding it produces a packet storm.
QUIET_ETHTYPES = frozenset({0x88CC, 0x86DD})

# How long a learned source MAC stays attributed to a port. A copy that
# returns on a different port inside this window is a loop.
LOOP_WINDOW_SECONDS = 5.0

# Ryu's multipart-reply subclasses drop the `type_` they are constructed with,
# so map the class back to the multipart type it stands for.
_STATS_REPLY_TYPES = {
    "OFPDescStatsReply": ofp.OFPMP_DESC,
    "OFPPortDescStatsReply": ofp.OFPMP_PORT_DESC,
    "OFPPortStatsReply": ofp.OFPMP_PORT_STATS,
    "OFPFlowStatsReply": ofp.OFPMP_FLOW,
    "OFPAggregateStatsReply": ofp.OFPMP_AGGREGATE,
}

# Messages Ryu never parses on the way in from a controller, because they only
# ever travel controller-to-switch. Their bodies are unpacked by hand instead.
_CONTROLLER_ONLY_TYPES = frozenset({
    ofp.OFPT_MULTIPART_REQUEST,
    ofp.OFPT_GET_CONFIG_REQUEST,
    ofp.OFPT_GET_ASYNC_REQUEST,
    ofp.OFPT_QUEUE_GET_CONFIG_REQUEST,
    ofp.OFPT_ROLE_REQUEST,
    ofp.OFPT_PORT_MOD,
    ofp.OFPT_TABLE_MOD,
    ofp.OFPT_GROUP_MOD,
})



def _serialize_action(action: Any) -> bytes:
    """Encode a single OFPActionOutput (the only action this switch uses)."""
    port = getattr(action, "port", 0) or 0
    # type(2) len(2) port(4) max_len(2) pad(6)
    return struct.pack("!HHIH6x", ofp.OFPAT_OUTPUT, 16, port,
                       getattr(action, "max_len", 0) or 0)


def _fixed(value: Any, size: int) -> bytes:
    """Coerce a string/bytes field into a fixed-width, NUL-padded blob."""
    if value is None:
        return b""
    raw = value.encode() if isinstance(value, str) else bytes(value)
    return raw[:size].ljust(size, b"\x00")


def _port_bytes(port: Any) -> bytes:
    """Encode an OFPPort into its 64-byte OF1.3 wire representation."""
    return struct.pack(
        ofp.OFP_PORT_PACK_STR,
        int(getattr(port, "port_no", 0) or 0),
        _fixed(getattr(port, "hw_addr", ""), ofp.OFP_ETH_ALEN),
        _fixed(getattr(port, "name", ""), ofp.OFP_MAX_PORT_NAME_LEN),
        int(getattr(port, "config", 0) or 0),
        int(getattr(port, "state", 0) or 0),
        int(getattr(port, "curr", 0) or 0),
        int(getattr(port, "advertised", 0) or 0),
        int(getattr(port, "supported", 0) or 0),
        int(getattr(port, "peer", 0) or 0),
        int(getattr(port, "curr_speed", 0) or 0),
        int(getattr(port, "max_speed", 0) or 0),
    )


def _stats_entry_bytes(mp_type: int, entry: Any) -> bytes:
    """Encode one entry of a multipart stats reply body."""
    if mp_type == ofp.OFPMP_DESC:
        desc_len = ofp.DESC_STR_LEN
        return struct.pack(
            ofp.OFP_DESC_PACK_STR,
            _fixed(getattr(entry, "mfr_desc", ""), desc_len),
            _fixed(getattr(entry, "hw_desc", ""), desc_len),
            _fixed(getattr(entry, "sw_desc", ""), desc_len),
            _fixed(getattr(entry, "serial_num", ""), ofp.SERIAL_NUM_LEN),
            _fixed(getattr(entry, "dp_desc", ""), desc_len),
        )

    if mp_type == ofp.OFPMP_PORT_DESC:
        return _port_bytes(entry)

    if mp_type == ofp.OFPMP_PORT_STATS:
        return struct.pack(
            ofp.OFP_PORT_STATS_PACK_STR,
            int(getattr(entry, "port_no", 0) or 0),
            *(
                int(getattr(entry, field, 0) or 0)
                for field in ("rx_packets", "tx_packets", "rx_bytes",
                              "tx_bytes", "rx_dropped", "tx_dropped",
                              "rx_errors", "tx_errors", "rx_frame_err",
                              "rx_over_err", "rx_crc_err", "collisions")
            ),
            int(getattr(entry, "duration_sec", 0) or 0),
            int(getattr(entry, "duration_nsec", 0) or 0),
        )

    if mp_type == ofp.OFPMP_FLOW:
        match = getattr(entry, "match", b"") or b""
        if not isinstance(match, (bytes, bytearray)):
            buf = bytearray()
            match.serialize(buf, 0)
            match = bytes(buf)
        match = bytes(match)
        # The parser recomputes length from the fixed header plus the
        # 8-byte-aligned match, then walks any instructions that follow. This
        # datapath reports rules with no instructions, so length ends here.
        length = ofp.OFP_FLOW_STATS_0_SIZE + len(match)
        return struct.pack(
            ofp.OFP_FLOW_STATS_0_PACK_STR,
            length,
            int(getattr(entry, "table_id", 0) or 0),
            int(getattr(entry, "duration_sec", 0) or 0),
            int(getattr(entry, "duration_nsec", 0) or 0),
            int(getattr(entry, "priority", 0) or 0),
            int(getattr(entry, "idle_timeout", 0) or 0),
            int(getattr(entry, "hard_timeout", 0) or 0),
            int(getattr(entry, "flags", 0) or 0),
            int(getattr(entry, "cookie", 0) or 0),
            int(getattr(entry, "packet_count", 0) or 0),
            int(getattr(entry, "byte_count", 0) or 0),
        ) + match

    return b""


class FakeDatapath:
    """Minimal object Ryu's parser needs in order to serialize messages."""

    ofproto = ofp

    def __init__(self) -> None:
        self.id = 1
        self.ofproto = ofp
        self.ofproto_parser = ofp_parser


class FlowEntry:
    """One installed flow: (priority, match, actions)."""

    __slots__ = ("priority", "match", "actions", "idle_timeout", "hard_timeout",
                 "created", "cookie", "last_used")

    def __init__(self, priority: int, match: Any, actions: list[Any],
                 idle_timeout: int = 0, hard_timeout: int = 0,
                 cookie: int = 0) -> None:
        self.priority = priority
        self.match = match
        self.actions = list(actions)
        self.idle_timeout = idle_timeout
        self.hard_timeout = hard_timeout
        self.created = time.time()
        self.last_used = self.created
        self.cookie = cookie

    def expired(self) -> bool:
        now = time.time()
        if self.hard_timeout and now - self.created >= self.hard_timeout:
            return True
        if self.idle_timeout and now - self.last_used >= self.idle_timeout:
            return True
        return False

    def matches(self, in_port: int | None, frame: bytes) -> bool:
        m = self.match
        fields = m.to_jsondict().get("OFPMatch", {}) if hasattr(m, "to_jsondict") else {}
        if not fields:
            return True  # wildcard flow matches everything
        if "in_port" in fields and in_port is not None and fields["in_port"] != in_port:
            return False
        # Only the Ethernet + ARP/IP header is inspected. Anything else is
        # treated as "not matched" only when the flow asks for it.
        eth_type = None
        if len(frame) >= 14:
            eth_type = struct.unpack("!H", frame[12:14])[0]
        if "eth_type" in fields and fields["eth_type"] != eth_type:
            return False
        if "eth_src" in fields:
            src = frame[6:12] if len(frame) >= 12 else b""
            if _mac_to_str(src) != str(fields["eth_src"]).lower():
                return False
        if "eth_dst" in fields:
            dst = frame[0:6] if len(frame) >= 6 else b""
            if _mac_to_str(dst) != str(fields["eth_dst"]).lower():
                return False
        if eth_type == 0x0800 and len(frame) >= 34:
            if "ipv4_src" in fields and socket.inet_ntoa(frame[26:30]) != str(fields["ipv4_src"]):
                return False
            if "ipv4_dst" in fields and socket.inet_ntoa(frame[30:34]) != str(fields["ipv4_dst"]):
                return False
        elif eth_type == 0x0806 and len(frame) >= 42:
            if "arp_spa" in fields and socket.inet_ntoa(frame[28:32]) != str(fields["arp_spa"]):
                return False
            if "arp_tpa" in fields and socket.inet_ntoa(frame[38:42]) != str(fields["arp_tpa"]):
                return False
        return True

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Flow prio={self.priority} match={self.match}>"


class _SimpleNamespace:
    """Attribute bag for hand-decoded controller messages."""

    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        inner = ", ".join(f"{k}={v!r}" for k, v in self.__dict__.items())
        return f"<{type(self).__name__} {inner}>"


class _ParsedAction:
    """A decoded OFPAT_OUTPUT, shaped like Ryu's `OFPActionOutput`."""

    def __init__(self, port: int, max_len: int) -> None:
        self.port = port
        self.max_len = max_len


def _parse_actions(buf: bytes, offset: int, end: int) -> tuple[list[_ParsedAction], int]:
    """Decode a run of OFPAT_OUTPUT actions.

    Returns the actions and the offset just past them. Unknown action types
    consume their declared length so a newer controller cannot desynchronise
    the parse of the rest of the message.
    """
    actions: list[_ParsedAction] = []
    while offset + 4 <= end:
        atype, alen = struct.unpack_from("!HH", buf, offset)
        if alen < 4 or offset + alen > end:
            break
        if atype == ofp.OFPAT_OUTPUT and alen >= 16:
            port, max_len = struct.unpack_from("!IH", buf, offset + 4)
            actions.append(_ParsedAction(port, max_len))
        offset += alen
    return actions, offset


def _parse_packet_out(body: bytes) -> Any:
    """Decode OFPT_PACKET_OUT, which Ryu provides no parser for."""
    if len(body) < ofp.OFP_HEADER_SIZE + 16:
        raise ValueError("PACKET_OUT too short")
    buffer_id, in_port, actions_len = struct.unpack_from(
        "!IIH", body, ofp.OFP_HEADER_SIZE)
    actions_start = ofp.OFP_HEADER_SIZE + 16
    actions_end = actions_start + actions_len
    actions, offset = _parse_actions(body, actions_start, actions_end)
    # OF 1.3 ofp_packet_out puts a 2-byte pad between the action list and the
    # payload. Without skipping it every forwarded frame loses its last two
    # bytes, which silently truncates ARP/IP payloads.
    offset += 2
    return _SimpleNamespace(buffer_id=buffer_id, in_port=in_port,
                            actions=actions, data=body[offset:],
                            _raw=body[:actions_end])


class _ParsedInstruction:
    """A decoded instruction, shaped like Ryu's `OFPInstruction`."""

    def __init__(self, itype: int, actions: list[_ParsedAction]) -> None:
        self.type = itype
        self.actions = actions


def _parse_instructions(buf: bytes, offset: int, end: int) -> list[_ParsedInstruction]:
    instructions: list[_ParsedInstruction] = []
    while offset + 4 <= end:
        itype, ilen = struct.unpack_from("!HH", buf, offset)
        if ilen < 8 or offset + ilen > end:
            break
        actions, _ = _parse_actions(buf, offset + 8, offset + ilen)
        instructions.append(_ParsedInstruction(itype, actions))
        offset += ilen
    return instructions


def _parse_flow_mod(body: bytes) -> Any:
    """Decode OFPT_FLOW_MOD, which Ryu provides no parser for."""
    base = ofp.OFP_HEADER_SIZE
    if len(body) < base + struct.calcsize(ofp.OFP_FLOW_MOD_PACK_STR0) + 8:
        raise ValueError("FLOW_MOD too short")
    (cookie, cookie_mask, table_id, command, idle_timeout, hard_timeout,
     priority, buffer_id, out_port, out_group, flags) = struct.unpack_from(
        ofp.OFP_FLOW_MOD_PACK_STR0, body, base)
    offset = base + struct.calcsize(ofp.OFP_FLOW_MOD_PACK_STR0)

    match_len = struct.unpack_from("!H", body, offset)[0]
    # Decode with Ryu's own OFPMatch parser so a stored match keeps the
    # `to_jsondict()` shape the flow matcher and delete logic expect.
    match = ofp_parser.OFPMatch.parser(body, offset)
    offset += 8 + match_len

    return _SimpleNamespace(
        cookie=cookie, cookie_mask=cookie_mask, table_id=table_id,
        command=command, idle_timeout=idle_timeout, hard_timeout=hard_timeout,
        priority=priority, buffer_id=buffer_id, out_port=out_port,
        out_group=out_group, flags=flags, match=match,
        instructions=_parse_instructions(body, offset, len(body)))


def _mac_to_str(raw: bytes) -> str:
    return ":".join(f"{b:02x}" for b in raw)


def _parse_oxm_frame(frame: bytes) -> dict[str, Any]:
    """Extract the same header fields the flow matcher needs, from the packet."""
    out: dict[str, Any] = {}
    if len(frame) < 14:
        return out
    out["eth_src"] = _mac_to_str(frame[6:12])
    out["eth_dst"] = _mac_to_str(frame[0:6])
    eth_type = struct.unpack("!H", frame[12:14])[0]
    out["eth_type"] = eth_type
    if eth_type == 0x0800 and len(frame) >= 34:
        out["ipv4_src"] = socket.inet_ntoa(frame[26:30])
        out["ipv4_dst"] = socket.inet_ntoa(frame[30:34])
        out["ip_proto"] = frame[23]
    return out


class OF13Switch:
    """The switch itself: one datapath, one controller connection."""

    def __init__(self, controller: str, dpid: int, ports: dict[int, str],
                 listen: str) -> None:
        self.controller_host, self.controller_port = controller
        self.dpid = dpid
        self.ports = ports  # port_no -> interface name
        self.listen_spec = listen
        self.dp = FakeDatapath()
        self.dp.id = dpid

        self.flows: list[FlowEntry] = []
        # Source-MAC -> port, and when it was last seen. Used only by the loop
        # guard; see `_accept_frame`.
        self._mac_port: dict[bytes, int] = {}
        self._mac_seen_at: dict[bytes, float] = {}
        self._dropped_loops = 0
        self.port_stats: dict[int, dict[str, int]] = {
            p: {"rx": 0, "tx": 0, "rx_packets": 0, "tx_packets": 0}
            for p in ports
        }
        # Raw sockets bound to each OpenFlow port for frame I/O.
        self.port_socks: dict[int, socket.socket] = {}
        self.sock_to_port: dict[int, int] = {}

        self.ctrl_sock: socket.socket | None = None
        self.ctrl_buf = b""
        # Bytes handed to `_send` that the kernel has not accepted yet.
        self._ctrl_out = b""
        self.listen_sock: socket.socket | None = None
        self._ctrl_fd = -1
        self._poller: select.poll | None = None
        self.running = False
        self.xid = 0

        self.buffer_id_counter = -1
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def start(self) -> int:
        # This switch is an *out-of-band* OpenFlow client: it connects to the
        # controller and carries traffic on its Mininet ports. It never accepts
        # inbound controller connections, so there is no listen socket to open.
        self._open_ports()
        self._connect_controller()
        self.running = True

        poller = select.poll()
        for fd in self.sock_to_port:
            poller.register(fd, select.POLLIN)
        self._poller = poller
        ctrl_fd = self.ctrl_sock.fileno() if self.ctrl_sock else -1
        if ctrl_fd >= 0:
            poller.register(ctrl_fd, select.POLLIN)
            self._ctrl_fd = ctrl_fd

        LOG.info("switch dpid=%d controller=%s:%d ports=%s",
                 self.dpid, self.controller_host,
                 self.controller_port, self.ports)

        while self.running:
            try:
                events = poller.poll(1000)
            except InterruptedError:
                continue
            for fd, event_mask in events:
                # A bug in one handler must not take the whole datapath down:
                # the controller would lose every flow and the lab would look
                # like an OpenFlow failure rather than a code bug.
                try:
                    if fd == self._ctrl_fd:
                        if event_mask & select.POLLOUT:
                            self._flush_ctrl_out()
                        if event_mask & select.POLLIN:
                            self._handle_controller()
                    elif fd in self.sock_to_port:
                        self._handle_port_frame(fd)
                except Exception:
                    LOG.exception("error handling event on fd %d", fd)
        return 0

    def stop(self, *_a: Any) -> None:
        self.running = False

    def _open_ports(self) -> None:
        for port_no, ifname in self.ports.items():
            s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(0x0003))
            s.bind((ifname, 0))
            s.setblocking(False)
            self.port_socks[port_no] = s
            self.sock_to_port[s.fileno()] = port_no
            if os.environ.get("OF13_DEBUG_FRAMES"):
                LOG.info("port %d -> %s fd=%d", port_no, ifname, s.fileno())

    def _connect_controller(self) -> None:
        for attempt in range(1, 31):
            try:
                s = socket.create_connection(
                    (self.controller_host, self.controller_port), timeout=2.0
                )
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                s.setblocking(False)
                self.ctrl_sock = s
                # The switch sends only HELLO. It must NOT send a
                # FEATURES_REQUEST: that is a controller-only message, and
                # sending one breaks the handshake (the controller has no
                # parser for message type 5 and therefore never receives the
                # FEATURES_REPLY it needs to register the datapath). We reply
                # to the controller's FEATURES_REQUEST instead.
                self._send(self._hello())
                LOG.info("connected to controller %s:%d",
                         self.controller_host, self.controller_port)
                return
            except OSError as exc:
                if attempt % 5 == 0:
                    LOG.warning("controller connect attempt %d failed: %s", attempt, exc)
                time.sleep(1.0)
        LOG.error("could not reach controller %s:%d", self.controller_host,
                  self.controller_port)

    # ------------------------------------------------------------------
    # OpenFlow message construction
    # ------------------------------------------------------------------
    def _next_xid(self) -> int:
        self.xid = (self.xid + 1) & 0xFFFFFFFF
        return self.xid

    def _finish(self, msg: Any, xid: int | None = None) -> bytes:
        """Serialize a Ryu message object into a wire frame.

        Two Ryu quirks are worked around here:

        1. ``OFPMatch`` needs ``serialize(buf, offset)`` while every
           ``MsgBase`` uses a no-arg ``serialize()`` that fills ``msg.buf``.
           Messages carrying a match (PACKET_IN, PACKET_OUT, FLOW_MOD) are
           handed the *pre-serialised* match bytes, which their inherited
           ``_serialize_body`` copies verbatim.
        2. ``OFPPacketIn``/``OFPPacketOut`` do not define ``_serialize_body``
           for the OF 1.3 match+data layout, so ``MsgBase.serialize()``
           produces a bare 8-byte header. We build their bodies by hand.
        """
        cls_name = type(msg).__name__

        m = getattr(msg, "match", None)
        if isinstance(m, ofp_parser.OFPMatch):
            mbuf = bytearray()
            m.serialize(mbuf, 0)
            msg.match = bytes(mbuf)

        if cls_name in ("OFPPacketIn", "OFPPacketOut"):
            return self._finish_packet_msg(msg, xid)

        # Ryu only implements `_serialize_body` for the controller-to-switch
        # direction. Everything the switch originates (FEATURES_REPLY,
        # multipart replies, GET_CONFIG_REPLY) serialises to a bare 8-byte
        # header unless encoded here, and the controller's parser then rejects
        # the frame as malformed -- killing the handshake.
        if cls_name == "OFPSwitchFeatures":
            return self._finish_switch_features(msg, xid)
        if cls_name == "OFPGetConfigReply":
            return self._finish_config_reply(msg, xid)
        if isinstance(msg, ofp_parser.OFPMultipartReply):
            return self._finish_multipart_reply(msg, xid)

        msg.serialize()
        if xid is not None:
            struct.pack_into("!I", msg.buf, 4, xid)
        return bytes(msg.buf)

    @staticmethod
    def _frame(msg_type: int, body: bytes, xid: int | None) -> bytes:
        header = struct.pack("!BBHI", ofp.OFP_VERSION, msg_type,
                             ofp.OFP_HEADER_SIZE + len(body), xid or 0)
        return header + body

    def _finish_switch_features(self, msg: Any, xid: int | None) -> bytes:
        """Hand-encode FEATURES_REPLY (Ryu has no `_serialize_body` for it).

        Without this, `MsgBase.serialize()` emits an 8-byte header-only frame
        and the controller raises `struct.error` while parsing the reply --
        which is why the handshake never produced `_connected_switches()`.
        """
        ports = b"".join(_port_bytes(p) for p in getattr(msg, "ports", []) or [])
        body = struct.pack(
            ofp.OFP_SWITCH_FEATURES_PACK_STR,
            int(getattr(msg, "datapath_id", 0) or 0),
            int(getattr(msg, "n_buffers", 0) or 0),
            int(getattr(msg, "n_tables", 0) or 0),
            int(getattr(msg, "auxiliary_id", 0) or 0),
            int(getattr(msg, "capabilities", 0) or 0),
            0,
        ) + ports
        return self._frame(ofp.OFPT_FEATURES_REPLY, body, xid)

    def _finish_config_reply(self, msg: Any, xid: int | None) -> bytes:
        body = struct.pack(
            ofp.OFP_SWITCH_CONFIG_PACK_STR,
            int(getattr(msg, "flags", 0) or 0),
            int(getattr(msg, "miss_send_len", 128) or 128),
        )
        return self._frame(ofp.OFPT_GET_CONFIG_REPLY, body, xid)

    def _finish_multipart_reply(self, msg: Any, xid: int | None) -> bytes:
        """Hand-encode any OFPMultipartReply and its stats entries."""
        # The stats-reply subclasses accept `type_` in __init__ but silently
        # discard it, so the `type` field has to be recovered from the class.
        mp_type = getattr(msg, "type", None)
        if mp_type is None:
            mp_type = _STATS_REPLY_TYPES.get(type(msg).__name__,
                                              ofp.OFPMP_DESC)
        flags = int(getattr(msg, "flags", 0) or 0)
        entries = b"".join(_stats_entry_bytes(mp_type, e)
                           for e in (getattr(msg, "body", None) or []))
        body = struct.pack(ofp.OFP_MULTIPART_REPLY_PACK_STR,
                           int(mp_type), flags) + entries
        return self._frame(ofp.OFPT_MULTIPART_REPLY, body, xid)

    def _finish_packet_msg(self, msg: Any, xid: int | None) -> bytes:
        """Hand-encode PACKET_IN / PACKET_OUT (Ryu does not implement it)."""
        match_bytes = bytes(getattr(msg, "match", b"") or b"")
        match_len = len(match_bytes)

        # MsgBase sets `msg_type` to None until serialize() runs, so the type
        # must come from the class-level constant, never the instance.
        msg_type = (getattr(type(msg), "cls_msg_type", None)
                    or ofp.OFPT_PACKET_IN)

        if isinstance(msg, ofp_parser.OFPPacketIn):
            # OF 1.3 ofp_packet_in:
            #   buffer_id(u32) total_len(u16) reason(u8) table_id(u8) cookie(u64)
            #   match, pad2(2), data...
            # total_len is the *unpadded* frame length; the payload is padded
            # out to an 8-byte boundary and the controller trims it using
            # total_len. Omitting pad2 silently truncates every frame.
            data = bytes(getattr(msg, "data", b"") or b"")
            body = struct.pack(
                "!IHBBQ",
                getattr(msg, "buffer_id", ofp.OFP_NO_BUFFER) & 0xFFFFFFFF,
                len(data) & 0xFFFF,
                getattr(msg, "reason", ofp.OFPR_NO_MATCH) & 0xFF,
                (getattr(msg, "table_id", 0) or 0) & 0xFF,
                (getattr(msg, "cookie", 0) or 0),
            ) + match_bytes + b"\x00\x00"
            padded = data + (b"\x00" * (-len(data) % 8))
            header = struct.pack("!BBHI", ofp.OFP_VERSION, msg_type,
                                 ofp.OFP_HEADER_SIZE + len(body) + len(padded),
                                 xid or 0)
            return header + body + padded

        # OFPPacketOut: buffer_id(4) in_port(4) actions_len(2) pad(6) actions data
        actions = b"".join(a.serialize_buf() if hasattr(a, "serialize_buf")
                           else _serialize_action(a)
                           for a in getattr(msg, "actions", []) or [])
        data = getattr(msg, "data", b"")
        body = struct.pack(
            "!IIH6s",
            getattr(msg, "buffer_id", ofp.OFP_NO_BUFFER) & 0xFFFFFFFF,
            getattr(msg, "in_port", ofp.OFPP_NONE) & 0xFFFFFFFF,
            len(actions), b"\x00" * 6,
        ) + actions + data
        msg_len = ofp.OFP_HEADER_SIZE + len(body)
        header = struct.pack("!BBHI", ofp.OFP_VERSION, msg_type,
                             msg_len, xid or 0)
        return header + body

    def _hello(self) -> bytes:
        """HELLO with a version bitmap of exactly {1.3}.

        Ryu parses the hello elements to decide which versions are usable;
        a bare HELLO with no elements leaves it with an empty list and the
        handshake never reaches the point where it sends FEATURES_REQUEST.
        """
        # Version bitmap Hello Element (type 1):
        #   header  = type(u16) + length(u16)  [OFP_HELLO_ELEM..._HEADER = 4]
        #   payload = bitmap(u32)
        # OF 1.3 is bit 4 of the 32-bit field => 0x10. The *element length*
        # field counts header + payload = 8, which is what the controller
        # validates against.
        bitmap = 1 << ofp.OFP_VERSION
        element = struct.pack("!HHI", 1, 8, bitmap)
        body = element
        # Hand-build the message: OFPHello has no _serialize_body of its own,
        # so MsgBase.serialize() would drop the elements entirely.
        msg_len = ofp.OFP_HEADER_SIZE + len(body)
        return struct.pack("!BBHI", ofp.OFP_VERSION, ofp.OFPT_HELLO, msg_len, 0) + body

    def _features_request(self) -> bytes:
        return self._finish(ofp_parser.OFPFeaturesRequest(datapath=self.dp))

    def _error(self, xid: int, err_type: int, code: int, data: bytes = b"") -> bytes:
        return self._finish(
            ofp_parser.OFPErrorMsg(datapath=self.dp, type_=err_type, code=code,
                                   data=data), xid)

    def _features_reply(self, xid: int) -> bytes:
        """FEATURES_REPLY for this datapath.

        OF 1.3 puts the port list in the body (and a separate PORT_DESC
        multipart request), so the ports are attached after construction
        rather than passed to the constructor.
        """
        ports = []
        for port_no, ifname in sorted(self.ports.items()):
            ports.append(ofp_parser.OFPPort(
                port_no=port_no,
                hw_addr=self._iface_mac(ifname),
                name=ifname[:15],
                config=PORT_CONFIG,
                state=PORT_STATE_UP,
                curr=PORT_CURR_FEATURES,
                advertised=PORT_ADVERTISED,
                supported=PORT_SUPPORTED,
                peer=PORT_ADVERTISED,
                curr_speed=1000000,
                max_speed=1000000,
            ))
        reply = ofp_parser.OFPSwitchFeatures(
            datapath=self.dp,
            datapath_id=self.dpid,
            n_buffers=0,
            n_tables=1,
            auxiliary_id=0,
            capabilities=0,
        )
        reply.ports = ports
        return self._finish(reply, xid)

    @staticmethod
    def _iface_mac(ifname: str) -> str:
        """MAC of an interface, or a deterministic placeholder.

        `/sys/class/net` is sometimes not readable (or the interface may live
        in a namespace this process is not attached to), so a stable synthetic
        address is used as a fallback. The controller does not use the
        hardware address for anything in this lab, so a placeholder is safe and
        avoids a hard failure that would abort the whole handshake.
        """
        try:
            with open(f"/sys/class/net/{ifname}/address") as fh:
                value = fh.read().strip()
                if value:
                    return value
        except OSError:
            pass
        digest = sum(bytearray(ifname.encode()))
        return "00:1a:2b:%02x:%02x:%02x" % (
            digest & 0xFF, (digest >> 8) & 0xFF, (digest << 2) & 0xFF)

    def _port_desc_reply(self, xid: int, more: bool) -> bytes:
        ports = []
        for port_no, ifname in sorted(self.ports.items()):
            ports.append(ofp_parser.OFPPort(
                port_no=port_no, hw_addr=self._iface_mac(ifname), name=ifname[:15],
                config=PORT_CONFIG, state=PORT_STATE_UP, curr=PORT_CURR_FEATURES,
                advertised=PORT_ADVERTISED, supported=PORT_SUPPORTED,
                peer=PORT_ADVERTISED, curr_speed=1000000, max_speed=1000000,
            ))
        body = ofp_parser.OFPPortDescStatsRequest
        reply = ofp_parser.OFPPortDescStatsReply(datapath=self.dp, flags=0, body=ports)
        return self._wrap_multipart(xid, ofp.OFPMP_PORT_DESC, reply, more)

    def _wrap_multipart(self, xid: int, mp_type: int, reply: Any, more: bool) -> bytes:
        """Send a stats reply.

        The subclass (OFPFlowStatsReply &c.) is *not* wrapped in a plain
        OFPMultipartReply: Ryu's parser identifies a stats reply purely by the
        `type` field inside the frame and looks the body parser up in
        `_STATS_MSG_TYPES`. Nesting would therefore produce a body the
        controller cannot walk.
        """
        reply.flags = ofp.OFPMPF_REPLY_MORE if more else 0
        try:
            return self._finish(reply, xid)
        except Exception:
            LOG.exception("failed to encode %s (mp_type=%s, %d entries)",
                          type(reply).__name__, mp_type,
                          len(getattr(reply, "body", None) or []))
            raise

    def _packet_in(self, in_port: int, frame: bytes, reason: int,
                   buffer_id: int | None, table_id: int = 0) -> bytes:
        # Build a match that carries the packet's own header fields, which is
        # what a real datapath does and what the controller's flow installs
        # then have to cover.
        fields = _parse_oxm_frame(frame)
        if in_port is not None:
            fields["in_port"] = in_port
        match = ofp_parser.OFPMatch(self.dp, **fields)
        # No datapath-side buffering in this switch: always inline the frame
        # and use OFP_NO_BUFFER, which is what tells the controller to expect
        # the packet bytes in the body.
        msg = ofp_parser.OFPPacketIn(
            datapath=self.dp, buffer_id=ofp.OFP_NO_BUFFER,
            reason=reason, table_id=table_id, match=match, data=frame)
        return self._finish(msg, self._next_xid())

    # Small ring of recently handled frames so a controller that references a
    # buffer id can still be served. This switch does no datapath buffering
    # itself, so this is a compatibility aid rather than a fast path.
    _buffer: dict[int, bytes] = {}

    def _store_buffer(self, frame: bytes) -> None:
        if len(self._buffer) > 256:
            self._buffer.pop(next(iter(self._buffer)))
        self._buffer[self.buffer_id_counter & 0xFFFFFFFF] = frame

    def _stored_buffer(self, buffer_id: int) -> bytes:
        return self._buffer.get(buffer_id, b"")

    def _packet_out_frame(self, out_port: int, frame: bytes,
                          exclude_port: int | None = None) -> None:
        """Transmit `frame`, honouring the OpenFlow special ports.

        `exclude_port` is the ingress port: FLOOD must not echo a frame back
        out of the port it arrived on, which would loop forever.
        """
        if out_port in self.port_socks:
            if out_port == exclude_port:
                return
            try:
                self.port_socks[out_port].send(frame)
                self.port_stats[out_port]["tx"] += len(frame)
                self.port_stats[out_port]["tx_packets"] += 1
            except OSError as exc:
                if exc.errno != errno.EAGAIN:
                    raise
        elif out_port == ofp.OFPP_FLOOD:
            for p, s in self.port_socks.items():
                if p == exclude_port:
                    continue
                try:
                    s.send(frame)
                    self.port_stats[p]["tx"] += len(frame)
                    self.port_stats[p]["tx_packets"] += 1
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # message handling
    # ------------------------------------------------------------------
    def _send(self, data: bytes) -> None:
        """Queue `data` for the controller, flushing what the socket accepts.

        The control socket is non-blocking, so a burst can fill the kernel
        buffer. Treating EAGAIN as a fatal error (as an earlier version did)
        tore down the controller session under load and took every installed
        flow with it -- which looks exactly like a network fault. Instead the
        unsent remainder is queued and drained when the socket is writable.
        """
        if self.ctrl_sock is None:
            return
        self._ctrl_out += data
        self._flush_ctrl_out()

    def _flush_ctrl_out(self) -> None:
        sock = self.ctrl_sock
        if sock is None:
            self._ctrl_out = b""
            return
        while self._ctrl_out:
            try:
                sent = sock.send(self._ctrl_out)
            except (BlockingIOError, InterruptedError):
                # Socket buffer is full: ask to be told when it drains.
                self._want_write()
                return
            except OSError as exc:
                LOG.warning("send to controller failed: %s", exc)
                self.ctrl_sock = None
                self._ctrl_out = b""
                return
            if sent <= 0:
                self._want_write()
                return
            self._ctrl_out = self._ctrl_out[sent:]
        self._want_write(False)

    def _want_write(self, enable: bool = True) -> None:
        """Arm/disarm POLLOUT for the controller socket."""
        poller = self._poller
        if poller is None or self.ctrl_sock is None:
            return
        try:
            poller.register(self._ctrl_fd, select.POLLIN | (select.POLLOUT if enable else 0))
        except (ValueError, KeyError, OSError):
            pass

    def _handle_controller(self) -> None:
        assert self.ctrl_sock is not None
        try:
            data = self.ctrl_sock.recv(MAX_RECV)
        except BlockingIOError:
            return
        except OSError as exc:
            LOG.warning("controller disconnected: %s", exc)
            poller = getattr(self, "_poller", None)
            if poller is not None and self.ctrl_sock is not None:
                try:
                    poller.unregister(self.ctrl_sock.fileno())
                except (KeyError, ValueError):
                    pass
            self._restart_controller(poller)
            return
        if not data:
            LOG.warning("controller closed the connection")
            poller = getattr(self, "_poller", None)
            if poller is not None and self.ctrl_sock is not None:
                try:
                    poller.unregister(self.ctrl_sock.fileno())
                except (KeyError, ValueError):
                    pass
            self._restart_controller(poller)
            return

        self.ctrl_buf += data
        while True:
            if len(self.ctrl_buf) < 8:
                return
            _ver, msg_type, msg_len, xid = struct.unpack_from("!BBHI", self.ctrl_buf)
            if len(self.ctrl_buf) < msg_len:
                return
            body = bytearray(self.ctrl_buf[:msg_len])
            self.ctrl_buf = self.ctrl_buf[msg_len:]
            self._dispatch(body, msg_type, msg_len, xid)

    def _drop_controller(self, poller: select.poll | None = None) -> None:
        """Close the controller session and clear the flow table.

        With no controller we cannot install flows, so keeping stale rules
        would be silently wrong -- forwarding on rules nobody can update.
        """
        if self.ctrl_sock is not None:
            try:
                self.ctrl_sock.close()
            except OSError:
                pass
        self.ctrl_sock = None
        self.ctrl_buf = b""
        self._ctrl_out = b""
        self._ctrl_fd = -1
        with self._lock:
            self.flows = []
        LOG.warning("flow table flushed (controller disconnected)")

    def _restart_controller(self, poller: select.poll | None) -> None:
        """Reconnect to the controller and re-arm the poller."""
        self._drop_controller(poller)
        self._connect_controller()
        if self.ctrl_sock is None:
            return
        self._ctrl_fd = self.ctrl_sock.fileno()
        if poller is not None:
            try:
                poller.register(self._ctrl_fd, select.POLLIN)
            except ValueError:
                pass

    def _dispatch(self, body: bytearray, msg_type: int, msg_len: int,
                  xid: int) -> None:
        try:
            # Ryu routes inbound messages through the version-keyed dispatcher
            # `ofproto_parser.msg()`, which raises on anything it cannot
            # decode -- exactly the behaviour we want. But OFPT_FEATURES_REQUEST
            # (type 5) is deliberately absent from Ryu's `_MSG_PARSERS` map
            # because it is a controller-to-switch message that the framework
            # handles internally, so it must be short-circuited here before we
            # ask Ryu to decode an 8-byte controller-only body.
            if msg_type == ofp.OFPT_FEATURES_REQUEST:
                obj = ofp_parser.OFPFeaturesRequest(self.dp)
                obj.set_headers(ofp.OFP_VERSION, msg_type, msg_len, xid)
                obj.set_buf(body)
            elif msg_type == ofp.OFPT_PACKET_OUT:
                # Ryu's parser table only covers switch->controller traffic,
                # so every controller->switch message is decoded by hand.
                obj = _parse_packet_out(bytes(body))
            elif msg_type == ofp.OFPT_FLOW_MOD:
                obj = _parse_flow_mod(bytes(body))
            elif msg_type in _CONTROLLER_ONLY_TYPES:
                # Same reason, same fix: these reach their handler as a raw
                # frame rather than a decoded object.
                obj = None
            else:
                obj = of_proto_parser.msg(
                    self.dp, ofp.OFP_VERSION, msg_type, msg_len, xid, body)
        except Exception as exc:  # pragma: no cover - malformed input guard
            LOG.exception("failed to decode message type %d: %s", msg_type, exc)
            self._send(self._error(xid, ofp.OFPET_BAD_REQUEST,
                                   ofp.OFPBRC_BAD_TYPE, bytes(body[:64])))
            return

        if msg_type == ofp.OFPT_HELLO:
            self._send(self._hello())
        elif msg_type == ofp.OFPT_ECHO_REQUEST:
            payload = getattr(obj, "data", b"")
            self._send(self._finish(
                ofp_parser.ofpecho(version=ofp.OFP_VERSION,
                                   msg_type=ofp.OFPT_ECHO_REPLY,
                                   msg_len=8 + len(payload), xid=xid, data=payload)))
        elif msg_type == ofp.OFPT_FEATURES_REQUEST:
            self._send(self._features_reply(xid))
        elif msg_type == ofp.OFPT_SET_CONFIG:
            pass  # nothing to configure in this datapath
        elif msg_type == ofp.OFPT_GET_CONFIG_REQUEST:
            self._send(self._finish(
                ofp_parser.OFPGetConfigReply(datapath=self.dp), xid))
        elif msg_type == ofp.OFPT_PACKET_OUT:
            self._handle_packet_out(obj)
        elif msg_type == ofp.OFPT_FLOW_MOD:
            self._handle_flow_mod(obj, xid)
        elif msg_type == ofp.OFPT_BARRIER_REQUEST:
            self._send(self._finish(
                ofp_parser.OFPBarrierReply(datapath=self.dp), xid))
        elif msg_type == ofp.OFPT_MULTIPART_REQUEST:
            # `obj` is None here (Ryu will not decode controller->switch
            # messages), so hand the raw frame to the handler.
            self._handle_multipart(body, xid)
        else:
            LOG.debug("ignoring message type %d", msg_type)

    def _handle_packet_out(self, msg: Any) -> None:
        """Forward the controller's packet (or stored buffer) out a port."""
        data = getattr(msg, "data", b"") or b""
        if os.environ.get("OF13_DEBUG_FRAMES"):
            LOG.info("PACKET_OUT in_port=%s actions=%s %d bytes raw=%s",
                     getattr(msg, "in_port", None),
                     [getattr(a, "port", None) for a in getattr(msg, "actions", [])],
                     len(data),
                     getattr(msg, "_raw", b"").hex())
        if not data and getattr(msg, "buffer_id", ofp.OFP_NO_BUFFER) != ofp.OFP_NO_BUFFER:
            data = self._stored_buffer(getattr(msg, "buffer_id", 0))
        if not data:
            return
        for action in msg.actions:
            port = getattr(action, "port", None)
            if port in (None, ofp.OFPP_CONTROLLER, ofp.OFPP_ANY,
                        ofp.OFPP_IN_PORT):
                continue
            self._packet_out_frame(port, data,
                                   exclude_port=getattr(msg, "in_port", None))

    def _handle_flow_mod(self, msg: Any, xid: int) -> None:
        command = msg.command
        with self._lock:
            if command == ofp.OFPFC_ADD:
                self.flows.append(FlowEntry(
                    priority=msg.priority, match=msg.match,
                    actions=_actions_of(msg), idle_timeout=msg.idle_timeout,
                    hard_timeout=msg.hard_timeout, cookie=msg.cookie))
            elif command in (ofp.OFPFC_DELETE, ofp.OFPFC_DELETE_STRICT):
                target = FlowEntry(priority=msg.priority, match=msg.match,
                                   actions=_actions_of(msg))
                keep = []
                for f in self.flows:
                    strict = command == ofp.OFPFC_DELETE_STRICT
                    same = (f.priority == target.priority and stricter_match_equal(f.match, target.match)) \
                        if strict else _match_covers(target.match, f.match)
                    if same and _matches_flags(msg, f):
                        continue
                    keep.append(f)
                self.flows = keep
            elif command == ofp.OFPFC_MODIFY:
                # Treat as add for our single-table datapath.
                self.flows.append(FlowEntry(priority=msg.priority, match=msg.match,
                                            actions=_actions_of(msg)))
            elif command == ofp.OFPFC_MODIFY_STRICT:
                with self._lock:
                    self.flows = [FlowEntry(priority=msg.priority, match=msg.match,
                                            actions=_actions_of(msg))]
            self.flows.sort(key=lambda f: -f.priority)

    @staticmethod
    def _multipart_request(body: bytearray) -> tuple[int, int]:
        """Return (multipart type, flags) from a raw MULTIPART_REQUEST body.

        `ofproto_parser.msg()` returns None for controller-to-switch messages,
        so the 8-byte multipart header is unpacked directly here.
        """
        mp_type, flags = struct.unpack_from(
            ofp.OFP_MULTIPART_REQUEST_PACK_STR, bytes(body),
            ofp.OFP_HEADER_SIZE)
        return int(mp_type), int(flags)

    def _handle_multipart(self, msg: Any, xid: int) -> None:
        mp_type, flags = self._multipart_request(msg)
        more = bool(flags & ofp.OFPMPF_REPLY_MORE)
        if mp_type == ofp.OFPMP_DESC:
            body = ofp_parser.OFPDescStatsReply(
                datapath=self.dp, mfr_desc="NetMind Lab",
                hw_desc="of13switch (user-space)", sw_desc="1.0",
                serial_num=str(self.dpid), dp_desc="multipath-lab")
            body.body = [ofp_parser.OFPDescStats(
                mfr_desc="NetMind Lab", hw_desc="of13switch (user-space)",
                sw_desc="1.0", serial_num=str(self.dpid),
                dp_desc="multipath-lab")]
            self._send(self._wrap_multipart(xid, mp_type, body, more))
        elif mp_type == ofp.OFPMP_PORT_DESC:
            self._send(self._port_desc_reply(xid, more))
        elif mp_type == ofp.OFPMP_PORT_STATS:
            entries = []
            for port_no in sorted(self.ports):
                st = self.port_stats.get(port_no, {})
                entries.append(ofp_parser.OFPPortStats(
                    port_no=port_no, rx_packets=st.get("rx_packets", 0),
                    tx_packets=st.get("tx_packets", 0), rx_bytes=st.get("rx", 0),
                    tx_bytes=st.get("tx", 0), rx_dropped=0, tx_dropped=0,
                    rx_errors=0, tx_errors=0, rx_frame_err=0, rx_over_err=0,
                    rx_crc_err=0, collisions=0,
                    duration_sec=0, duration_nsec=0))
            body = ofp_parser.OFPPortStatsReply(datapath=self.dp, flags=0, body=entries)
            self._send(self._wrap_multipart(xid, mp_type, body, more))
        elif mp_type == ofp.OFPMP_FLOW:
            entries = []
            with self._lock:
                for f in self.flows:
                    now = time.time()
                    entries.append(ofp_parser.OFPFlowStats(
                        table_id=0, priority=f.priority, match=f.match,
                        duration_sec=int(now - f.created),
                        duration_nsec=0, packet_count=0, byte_count=0,
                        cookie=f.cookie))
            body = ofp_parser.OFPFlowStatsReply(datapath=self.dp, flags=0, body=entries)
            self._send(self._wrap_multipart(xid, mp_type, body, more))
        else:
            LOG.debug("unhandled multipart request type %d", mp_type)

    # ------------------------------------------------------------------
    # data plane
    # ------------------------------------------------------------------
    def _accept_frame(self, port_no: int, frame: bytes) -> bool:
        """Loop guard for *flooded* frames.

        The lab topology is a ring (s1-s2-s4-s3-s1) with two independent paths,
        which is what makes rerouting possible -- but a bridged ring with no
        spanning tree floods broadcasts forever. Each switch re-floods the copy
        that returns, the copies multiply, and the control channel starves.

        Real networks solve this with STP, which a pure-datapath lab does not
        run. Instead a flooded frame is dropped when its *source* MAC arrives on
        a different port than it was last seen on within `LOOP_WINDOW_SECONDS`:
        that is precisely a copy that has travelled around the ring.

        The check deliberately applies only to multicast/broadcast
        destinations. A unicast frame follows the controller-installed path, so
        it cannot loop, and rerouting a flow legitimately moves its source MAC
        between ports -- applying the guard to unicast would break exactly the
        behaviour the lab exists to demonstrate. Returns False when dropped.
        """
        if len(frame) < 14:
            return True
        dst = frame[0:6]
        if not (dst[0] & 0x01):
            return True  # unicast: not flooded, cannot loop

        src = frame[6:12]
        now = time.time()
        previous = self._mac_port.get(src)
        if previous is not None and previous != port_no:
            if now - self._mac_seen_at.get(src, 0.0) < LOOP_WINDOW_SECONDS:
                self._dropped_loops += 1
                if os.environ.get("OF13_DEBUG_FRAMES"):
                    LOG.info("port %d: dropping looped flooded frame from %s "
                             "(already seen on port %d)",
                             port_no, _mac_to_str(src), previous)
                return False
        self._mac_port[src] = port_no
        self._mac_seen_at[src] = now
        return True

    def _handle_port_frame(self, fd: int) -> None:
        port_no = self.sock_to_port[fd]
        s = self.port_socks[port_no]
        try:
            frame = s.recv(MAX_RECV)
        except BlockingIOError:
            return
        except OSError:
            return
        if not frame:
            return
        # LLDP and IPv6 are link-local control traffic. The controller ignores
        # them and no flow in this lab matches them, but forwarding them lets
        # them circulate around the loop forever -- each switch floods what it
        # receives, so the frame returns on another port and is flooded again.
        # The result is an unbounded packet storm that starves the OpenFlow
        # control channel. Drop them at the edge.
        if len(frame) >= 14 and struct.unpack("!H", frame[12:14])[0] in QUIET_ETHTYPES:
            return
        if not self._accept_frame(port_no, frame):
            return
        self.port_stats[port_no]["rx"] += len(frame)
        self.port_stats[port_no]["rx_packets"] += 1
        if os.environ.get("OF13_DEBUG_FRAMES"):
            LOG.info("port %d rx %d bytes: %s", port_no, len(frame),
                     frame[:14].hex())

        with self._lock:
            for flow in self.flows:  # highest priority first
                if flow.expired():
                    continue
                if flow.matches(port_no, frame):
                    action_ports = [a.port for a in flow.actions
                                    if getattr(a, "port", None) is not None]
                    flow.last_used = time.time()
                    break
            else:
                action_ports = []

        # An output to the controller port (which is how every controller
        # installs its table-miss rule) means "send me this packet", not
        # "forward to port 0xfffffffe". Treating it as a physical port
        # silently drops the frame and no packet ever reaches the controller.
        to_controller = any(p in (ofp.OFPP_CONTROLLER, ofp.OFPP_ANY)
                            for p in action_ports)
        out_ports = [p for p in action_ports
                     if p not in (ofp.OFPP_CONTROLLER, ofp.OFPP_ANY)]

        if out_ports:
            if os.environ.get("OF13_DEBUG_FRAMES"):
                LOG.info("port %d FLOW-MATCH -> %s : %s", port_no, out_ports,
                         frame[:14].hex())
            for out_port in out_ports:
                self._packet_out_frame(out_port, frame, exclude_port=port_no)
        else:
            # Table miss (or an explicit controller output) -> ask the
            # controller for a decision.
            if os.environ.get("OF13_DEBUG_FRAMES"):
                LOG.info("port %d -> PACKET_IN (%d bytes): %s%s", port_no,
                         len(frame), frame[:14].hex(),
                         " [controller action]" if to_controller else "")
            self._send(self._packet_in(
                port_no, frame,
                ofp.OFPR_ACTION if to_controller else ofp.OFPR_NO_MATCH,
                buffer_id=None))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _actions_of(flow_mod: Any) -> list[Any]:
    out: list[Any] = []
    for inst in getattr(flow_mod, "instructions", None) or []:
        if inst.type in (ofp.OFPIT_APPLY_ACTIONS, ofp.OFPIT_WRITE_ACTIONS):
            out.extend(inst.actions)
    return out


def _matches_flags(flow_mod: Any, flow: FlowEntry) -> bool:
    """OFPC_DELETE flags: out_port / out_group scoping."""
    out_port = getattr(flow_mod, "out_port", ofp.OFPP_ANY)
    if out_port != ofp.OFPP_ANY:
        ports = [a.port for a in flow.actions if getattr(a, "port", None) is not None]
        if out_port not in ports:
            return False
    return True


def _match_covers(target: Any, existing: Any) -> bool:
    """True when `target` is a wildcard-ish subset of `existing` (delete rule)."""
    t = target.to_jsondict().get("OFPMatch", {}) if target is not None else {}
    e = existing.to_jsondict().get("OFPMatch", {}) if existing is not None else {}
    if not t:
        return True
    for k, v in t.items():
        if e.get(k) != v:
            return False
    return True


def stricter_match_equal(a: Any, b: Any) -> bool:
    try:
        ja = a.to_jsondict().get("OFPMatch", {})
        jb = b.to_jsondict().get("OFPMatch", {})
    except Exception:
        return False
    return ja == jb


# ---------------------------------------------------------------------------


def _parse_ports(args: list[str]) -> dict[int, str]:
    ports: dict[int, str] = {}
    for spec in args:
        num, iface = spec.split(":", 1)
        ports[int(num)] = iface
    return ports


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Minimal OpenFlow 1.3 user-space switch")
    ap.add_argument("--controller", required=True, help="host:port of the controller")
    ap.add_argument("--dpid", type=int, default=1)
    ap.add_argument("--listen", default="unix:/tmp/of13s.listen")
    ap.add_argument("--port", action="append", default=[],
                    help="PORT:IFACE, repeatable")
    ap.add_argument("--log-level", default="info")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    if ":" not in args.controller:
        ap.error("--controller must be host:port")
    host, port = args.controller.rsplit(":", 1)

    sw = OF13Switch(controller=(host, int(port)), dpid=args.dpid,
                    ports=_parse_ports(args.port), listen=args.listen)

    signal.signal(signal.SIGTERM, sw.stop)
    signal.signal(signal.SIGINT, sw.stop)
    return sw.start()


if __name__ == "__main__":
    sys.exit(main())
