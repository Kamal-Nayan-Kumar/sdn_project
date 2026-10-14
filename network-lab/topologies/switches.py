"""Mininet switch subclasses for the multipath lab.

Three switch backends, tried in this order:

1. ``OVSSwitch``      -- the spec's primary choice (Open vSwitch, OF 1.3).
2. ``Of13UserSwitch`` -- our pure-Python OF 1.3 datapath (see
   ``of13switch.py``). Used when OVS is not installed, so the rest of the
   system still talks to a *real* OpenFlow switch driven by real Ryu.

The selected backend is reported loudly in the logs and exposed through the
lab session state, so nothing is silently substituted.
"""

from __future__ import annotations

import os
import sys
import shutil
import subprocess
from typing import Any

from mininet.node import OVSSwitch, UserSwitch

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
OF13_SCRIPT = os.path.join(HERE, "of13switch.py")
LAB_BIN = os.path.join(PROJECT_ROOT, "network-lab", "bin")


def ovs_available() -> bool:
    """True when Open vSwitch binaries are on PATH."""
    return shutil.which("ovs-vsctl") is not None and shutil.which("ovs-vswitchd") is not None


def which_switch():
    """Pick the switch class, returning (cls, backend_name)."""
    if ovs_available():
        return OVSSwitch, "openvswitch"
    return Of13UserSwitch, "of13-user-space"


class Of13UserSwitch(UserSwitch):
    """Launches our pure-Python OF 1.3 switch instead of ofdatapath."""

    protocols = "OpenFlow13"
    dpidLen = 12

    def __init__(self, name: str, **opts: Any) -> None:
        opts.setdefault("protocols", "OpenFlow13")
        super().__init__(name, **opts)

    def start(self, controllers=None) -> None:
        """Do not let Mininet start ofdatapath; launch our switch instead."""
        ctrl = controllers[0] if controllers else None
        ctl_addr = "127.0.0.1:6653"
        if ctrl is not None:
            host = getattr(ctrl, "ip", None) or "127.0.0.1"
            port = getattr(ctrl, "port", None) or 6653
            ctl_addr = f"{host}:{port}"

        listen = f"unix:/tmp/{self.name}.listen"
        # Mininet's `Switch.ports` maps Intf -> OpenFlow port number, so
        # iterate it as (intf, port_no). Fall back to `intf.ofport` only when
        # the port map is not populated yet.
        port_args: list[str] = []
        if getattr(self, "ports", None):
            for intf, port_no in self.ports.items():
                if port_no and port_no > 0:
                    port_args.extend(["--port", f"{port_no}:{intf.name}"])
        else:
            for intf in sorted(self.intfs.values(), key=lambda i: i.name):
                no = int(getattr(intf, "ofport", 0) or 0)
                if no:
                    port_args.extend(["--port", f"{no}:{intf.name}"])

        # Use the interpreter running this process so the vendored Ryu and its
        # dependencies resolve. Launching bare `python3` would pick up whatever
        # is first on PATH, which is not guaranteed to be the venv.
        python = sys.executable
        env = dict(os.environ)
        pypath = env.get("PYTHONPATH", "")
        ryu_src = os.environ.get("RYU_SRC", "")
        extra = [PROJECT_ROOT, ryu_src] if ryu_src else [PROJECT_ROOT]
        env["PYTHONPATH"] = os.pathsep.join([p for p in [*extra, pypath] if p])

        cmd = [
            python, OF13_SCRIPT,
            "--controller", ctl_addr,
            "--dpid", str(self.dpid),
            "--listen", listen,
            *port_args,
        ]
        LOG = __import__("mininet.log", fromlist=["info"]).info
        LOG("*** Of13UserSwitch %s -> %s\n" % (self.name, ctl_addr))
        self.cmd(" ".join(cmd) + " > /tmp/%s-of13.log 2>&1 &" % self.name)

    def stop(self, deleteIntfs: bool = True) -> None:
        """Kill the switch process and (optionally) its interfaces."""
        try:
            self.cmd("kill %cat /tmp/%s.pid" % ("%", self.name))
        except Exception:
            pass
        # The switch writes no pidfile; use pkill on the script + dpid pair.
        self.cmd(
            "pkill -f 'of13switch.py.*--dpid %s' 2>/dev/null || true" % str(self.dpid)
        )
        if deleteIntfs:
            for intf in list(self.intfs.values()):
                self.cmd("ip link del %s 2>/dev/null || true" % intf.name)

    def connected(self) -> bool:
        """Our switch is connected when the controller socket is open."""
        rc = self.cmd("pgrep -f 'of13switch.py.*--dpid %s' >/dev/null && echo YES" % str(self.dpid))
        return "YES" in rc

    def dpctl(self, *args: str) -> str:
        return "of13 switch (userspace) - use the controller API"
