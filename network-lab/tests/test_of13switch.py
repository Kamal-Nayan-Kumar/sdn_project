"""Regression tests for the pure-Python OpenFlow 1.3 datapath.

The defect these tests guard against: `_parse_flow_mod` read the flow
mod's metadata with ``struct.calcsize(ofp.OFP_FLOW_MOD_PACK_STR0)`` but then
forgot the OXM match's padding, so the instruction decoder started in the
*middle of the match padding* (a zeroed word) instead of at the instruction
header. Every priority-100 rule the controller installed was therefore
parsed with zero instructions -- and a flow with no actions never forwarded
anything, so every IPv4 data frame fell through to the controller as a
PACKET_IN. The switches looked idle in the flow table while the control
channel carried the entire data plane.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "network-lab" / "topologies"))

rf = pytest.importorskip("ryu.ofproto.ofproto_v1_3_parser",
                         reason="the vendored ryu parser is not importable")
ofp = pytest.importorskip("ryu.ofproto.ofproto_v1_3",
                          reason="the vendored ryu is not importable")

import of13switch  # noqa: E402,F401


class _FakeDP:
    """Just enough of a datapath for the parser to serialise a match."""

    ofproto = ofp
    ofproto_parser = rf
    xid = 1


def _serialise_flow(path_nodes, priority=100):
    parser = rf
    dp = _FakeDP()
    match = parser.OFPMatch(eth_type=0x0800, ipv4_src="10.0.0.1",
                           ipv4_dst="10.0.0.4")
    actions = [parser.OFPActionOutput(3)]
    mod = parser.OFPFlowMod(
        datapath=dp,
        priority=priority,
        match=match,
        instructions=[parser.OFPInstructionActions(
            ofp.OFPIT_APPLY_ACTIONS, actions)],
        idle_timeout=0,
        hard_timeout=0,
    )
    mod.serialize()
    return bytes(mod.buf)


class TestFlowModDecoding:
    def test_the_oxm_match_is_padded_on_the_wire(self):
        wire = _serialise_flow(["s1", "s3", "s4"])
        base = ofp.OFP_HEADER_SIZE + struct.calcsize(ofp.OFP_FLOW_MOD_PACK_STR0)
        m_type, m_len = struct.unpack_from("!HH", wire, base)
        assert m_type == ofp.OFPMT_OXM
        # The fields occupy 22 bytes; the *wire* pads the match to 8, so the
        # instruction decoder must not stop at `m_len`.
        assert ((m_len + 7) // 8) * 8 > m_len
        pad = wire[base + m_len:base + ((m_len + 7) // 8) * 8]
        assert pad == b"\x00" * len(pad)

    def test_the_instructions_survive_the_round_trip(self):
        wire = _serialise_flow(["s1", "s3", "s4"])
        parsed = of13switch._parse_flow_mod(wire)
        assert parsed.match.to_jsondict()["OFPMatch"]["oxm_fields"]
        assert len(parsed.instructions) == 1
        assert parsed.instructions[0].type == ofp.OFPIT_APPLY_ACTIONS
        assert [a.port for a in parsed.instructions[0].actions] == [3]

    def test_a_wildcard_rule_still_decodes(self):
        wire = _serialise_flow(["s1", "s3", "s4"], priority=0)
        parsed = of13switch._parse_flow_mod(wire)
        assert parsed.instructions

    def test_a_packet_out_still_carries_its_frame(self):
        parser = rf
        dp = _FakeDP()
        data = b"\x00\x01\x02\x03testpayload"
        mod = parser.OFPPacketOut(
            datapath=dp, in_port=ofp.OFPP_CONTROLLER, buffer_id=ofp.OFP_NO_BUFFER,
            actions=[parser.OFPActionOutput(1)], data=data)
        mod.serialize()
        parsed = of13switch._parse_packet_out(bytes(mod.buf))
        assert parsed.actions and parsed.actions[0].port == 1
        assert parsed.data == data
