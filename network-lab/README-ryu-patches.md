# Local patches to the vendored Ryu

`network-lab/controller/ryu-src` is a vendored copy of Ryu. It is patched in
place rather than patched at runtime so the lab's behaviour is reproducible and
reviewable. Upstream is never installed with `pip install ryu`.

## 1. `OFPPacketOut._serialize_body` (OF 1.3)

**File:** `ryu/ofproto/ofproto_v1_3_parser.py`

**Upstream behaviour.** The action list is written at
`OFP_PACKET_OUT_SIZE`, then `self.buf += self.data` appends the payload
directly after it.

Two things are wrong for OF 1.3:

* The struct is
  `buffer_id(4) in_port(4) actions_len(2) pad(6) actions pad2(2) data`.
  Upstream never writes `pad2`, so the payload starts two bytes too early.
* The payload is never padded out to the 8-byte message alignment.

**Why it matters.** Every spec-correct switch mis-parses the frame. Our own
`of13switch.py` lost the last two bytes of every forwarded packet, which
truncated ARP and IP payloads and produced 100% packet loss. The bug is also
visible inside Ryu itself: `OFPPacketIn.parser` *does* skip the matching 2
bytes, so upstream's PACKET_IN and PACKET_OUT encoders disagree with each
other.

**Fix.** Build the body in a correctly sized buffer and write `pad2`.

The payload is deliberately *not* padded out to an 8-byte boundary, even though
the spec says it "should" be. `ofp_packet_out` has no length field for the
payload, so a receiver cannot tell padding from data: padding a 42-byte ARP
frame to 48 bytes makes the switch transmit a 46-byte frame that the receiving
host silently discards. `ofp_packet_in` *does* carry `total_len`, so the switch
pads there instead and the controller trims using that field.

---

## 2. Everything else in this tree

`of13switch.py` works around the remaining Ryu gaps without patching Ryu,
because they are *missing features* rather than bugs:

* Ryu only implements `_serialize_body` for the controller→switch direction,
  so `OFPSwitchFeatures`, `OFPMultipartReply` (and its stats subclasses) and
  `OFPGetConfigReply` serialise to a bare 8-byte header. The switch hand-encodes
  them using Ryu's own `OFP_*_PACK_STR` constants.
* `ofproto_parser.msg()` has no parser for any controller→switch message, so
  `PACKET_OUT`, `FLOW_MOD` and `MULTIPART_REQUEST` are decoded by hand in the
  switch.