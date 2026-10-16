"""Pure-Python UDP traffic generator for the lab's congestion scenario.

The congestion scenario needs to put a *known* rate on a link so that the
switch port counters show real utilisation. The obvious tool is ``iperf3``,
but the lab runs inside a user namespace (``unshare -Urn``) where no package
may be installed, and iperf3 is not guaranteed to exist on the host. Guessing
at a rate instead of generating traffic would make the whole scenario a lie:
``start_congestion`` would report success while every counter read zero.

So the generator is two socket loops in this file. It has no dependency
beyond the standard library, and it is launched inside the Mininet host
namespaces with ``Node.popen``, which is the only exec path that works here
(``Node.cmd`` drives an interactive pty and is not safe to use concurrently).

Usage, from inside a host namespace::

    python3 traffic_gen.py sink --port 5201
    python3 traffic_gen.py source --dst 10.0.0.3 --rate-mbps 400 --port 5201

``--duration`` of 0 means "until terminated", which is what the congestion
scenario uses; a finite duration is useful for smoke tests.
"""

from __future__ import annotations

import argparse
import select
import socket
import sys
import time

# Big enough to amortise per-packet syscall cost, small enough that a 400 Mbps
# stream still keeps the send loop responsive to termination.
DEFAULT_PAYLOAD = 1400

# Never send faster than this. Without a floor the pacing loop spins at
# millions of iterations a second for low rates.
MIN_SLEEP = 0.001

# The pacing loop sends one batch per tick. 20 ms is long enough that the sleep
# is not dominated by scheduler granularity, and short enough that a SIGTERM
# is handled promptly.
TICK_SECONDS = 0.02

# Upper bound on datagrams per tick, so an extreme rate cannot turn one tick
# into an unbounded syscall spree. A batch this size at the default payload is
# well over 40 Gbps, far above anything this lab can carry.
MAX_BATCH = 20000


def run_sink(port: int, ready_fd: int | None) -> int:
    """Receive until killed, discarding packets as fast as they arrive.

    The socket is non-blocking and drained with ``select`` so that a SIGTERM
    from the lab is handled promptly instead of being stuck in ``recvfrom``.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", port))
    sock.setblocking(False)

    if ready_fd is not None:
        # Tell the lab the listener is bound before it starts the sender, so
        # the first packets of a short-lived scenario are not lost.
        os_write(ready_fd, b"ready\n")

    received = 0
    last_report = time.time()
    while True:
        try:
            readable, _, _ = select.select([sock], [], [], 0.25)
        except InterruptedError:
            continue
        if readable:
            while True:
                try:
                    sock.recv(65535)
                except BlockingIOError:
                    break
                except OSError:
                    break
                received += 1
        now = time.time()
        if now - last_report >= 5.0:
            last_report = now
            print(f"received {received} packets", flush=True)


def os_write(fd: int, payload: bytes) -> None:
    import os

    os.write(fd, payload)


def run_source(dst: str, rate_mbps: float, port: int, payload_size: int,
               duration: float, ready_fd: int | None) -> int:
    """Send UDP at `rate_mbps` until `duration` expires (0 = forever).

    The loop is batched rather than per-packet. Sleeping between individual
    packets cannot express anything above a few tens of Mbps: `time.sleep`
    granularity is around a millisecond, so one packet per wake-up caps the
    stream near 11 Mbps no matter what rate was asked for. Instead each tick
    sends a whole batch of datagrams, which keeps the long-run average on
    target while bounding how often the loop has to wake up.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    destination = (dst, port)
    payload = b"\0" * payload_size

    bytes_per_sec = rate_mbps * 1e6 / 8.0
    # Batch size for one tick: enough to express the requested rate, capped so
    # a single tick cannot stall for an unbounded number of syscalls.
    per_tick = bytes_per_sec * TICK_SECONDS
    batch = int(per_tick // payload_size)
    batch = max(1, min(batch, MAX_BATCH))

    if ready_fd is not None:
        os_write(ready_fd, b"ready\n")

    sent = 0
    started = next_tick = time.monotonic()
    deadline = started + duration if duration else None
    while True:
        if deadline is not None and time.monotonic() >= deadline:
            break
        for _ in range(batch):
            try:
                sock.sendto(payload, destination)
                sent += 1
            except OSError as exc:
                # A full socket buffer means we are offering more than the
                # link can carry -- which is the point of a congestion
                # scenario. Dropping is correct; counting the drops is not
                # this process's job.
                if exc.errno not in (101, 103):  # ENETUNREACH, ECONNREFUSED
                    print(f"sendto failed: {exc}", file=sys.stderr, flush=True)
                    return 1

        next_tick += TICK_SECONDS
        sleep_for = next_tick - time.monotonic()
        if sleep_for > 0:
            time.sleep(sleep_for)
        else:
            # Behind schedule: give up the deficit rather than accumulating an
            # unbounded burst, and yield so a SIGTERM is still delivered.
            next_tick = time.monotonic()
            time.sleep(MIN_SLEEP)

    elapsed = time.monotonic() - started
    achieved = (sent * payload_size * 8 / elapsed / 1e6) if elapsed > 0 else 0.0
    print(f"sent {sent} packets, {achieved:.1f} Mbps over {elapsed:.1f}s", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("sink", "source"))
    parser.add_argument("--dst", default="")
    parser.add_argument("--port", type=int, default=5201)
    parser.add_argument("--rate-mbps", type=float, default=400.0)
    parser.add_argument("--payload", type=int, default=DEFAULT_PAYLOAD)
    parser.add_argument("--duration", type=float, default=0.0,
                        help="seconds; 0 means run until terminated")
    parser.add_argument("--ready-fd", type=int, default=None,
                        help="write 'ready' here once bound/sending has started")
    args = parser.parse_args(argv)

    if args.mode == "sink":
        return run_sink(args.port, args.ready_fd)
    if not args.dst:
        parser.error("--dst is required in source mode")
    return run_source(args.dst, args.rate_mbps, args.port, args.payload,
                      args.duration, args.ready_fd)


if __name__ == "__main__":
    sys.exit(main())