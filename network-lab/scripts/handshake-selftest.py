"""Minimal end-to-end check of the OpenFlow 1.3 handshake.

Runs Ryu and one instance of the pure-Python switch inside a single shared
network namespace (so both can talk over 127.0.0.1) and asserts that the
handshake reaches MAIN dispatcher, i.e. Ryu received FEATURES_REPLY.

    python network-lab/scripts/handshake-selftest.py

Exit codes:
    0  handshake reached the main dispatcher on every switch
    1  handshake failed (see stdout)
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))


def _bring_up_loopback() -> None:
    """`lo` must be up for 127.0.0.1 to work inside a fresh netns."""
    for cmd in ("ip link set lo up",):
        subprocess.run(cmd, shell=True, capture_output=True)


def _wait_for_port(port: float, timeout: float = 15.0) -> bool:
    import socket

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=0.5)
            s.close()
            return True
        except OSError:
            time.sleep(0.3)
    return False


def _ryu_cmd(of_port: int, rest_port: int) -> list[str]:
    return [
        sys.executable, "-m", "ryu.cmd.manager",
        "--ofp-tcp-listen-port", str(of_port),
        "--wsapi-host", "127.0.0.1",
        "--wsapi-port", str(rest_port),
        "--verbose",
        os.path.join(PROJECT_ROOT, "network-lab", "controller", "ryu_controller.py"),
    ]


def main() -> int:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [
        os.path.join(PROJECT_ROOT, "network-lab", "controller", "ryu-src"),
        PROJECT_ROOT,
        os.path.join(PROJECT_ROOT, "network-lab"),
        os.path.join(PROJECT_ROOT, "network-lab", "topologies"),
        env.get("PYTHONPATH", ""),
    ]))

    of_port, rest_port = 36653, 38080
    log = os.path.join(PROJECT_ROOT, "logs")
    os.makedirs(log, exist_ok=True)
    log_fh = open(os.path.join(log, "handshake-selftest-ryu.log"), "w")

    print("==> starting ryu")
    _bring_up_loopback()
    ryu = subprocess.Popen(_ryu_cmd(of_port, rest_port), env=env,
                           stdout=log_fh, stderr=subprocess.STDOUT)
    if not _wait_for_port(rest_port, timeout=30.0):
        print("FAIL: ryu REST did not come up")
        ryu.terminate()
        return 1

    print("==> starting one of13switch against it")
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "network-lab", "topologies"))
    import of13switch as of13

    sw = of13.OF13Switch(
        controller=("127.0.0.1", of_port),
        dpid=99,
        ports={},
        listen="unix:/tmp/selftest-s99.listen",
    )
    thread = threading.Thread(target=sw.start, daemon=True)
    thread.start()

    # Give the handshake time: HELLO -> FEATURES_REQUEST -> FEATURES_REPLY.
    deadline = time.time() + 25
    connected = False
    while time.time() < deadline:
        with open(os.path.join(log, "handshake-selftest-ryu.log"),
                  errors="replace") as fh:
            text = fh.read()
        if "features ev" in text:
            connected = True
            break
        if not thread.is_alive():
            print("FAIL: switch thread exited during handshake")
            break
        time.sleep(0.5)

    ok = connected and sw.ctrl_sock is not None
    print(f"    switch alive={thread.is_alive()} "
          f"controller_socket={sw.ctrl_sock is not None} "
          f"features_reply_seen={connected}")

    ryu.terminate()
    try:
        ryu.wait(timeout=5)
    except subprocess.TimeoutExpired:
        ryu.kill()
    log_fh.close()

    print("==> handshake " + ("OK" if ok else "FAILED"))
    if not ok:
        print("    see logs/handshake-selftest-ryu.log for the controller side")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
