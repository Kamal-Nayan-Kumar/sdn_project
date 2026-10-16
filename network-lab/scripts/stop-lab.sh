#!/usr/bin/env bash
#
# Stop the SDN network lab and every process it owns.
#
# Repeated launcher runs can leave orphaned Ryu processes (each `unshare` gets
# its own network namespace, so an old Ryu can still hold port 6653 inside a
# dead namespace and silently absorb the new switches). This script finds and
# kills *all* of them, so there is exactly one controller for the switches.

set -u

echo "==> stopping sdn lab"

# Kill the lab runner first so it does not restart its children.
pkill -f "network-lab/run_lab.py" 2>/dev/null && echo "    stopped run_lab" || echo "    no run_lab running"

# Then the switches, then the controller.
for pattern in "topologies/of13switch.py" "ryu.cmd.manager" "network-lab/controller/ryu_controller.py"; do
  pkill -f "$pattern" 2>/dev/null && echo "    stopped $pattern" || true
done

# Leftover lab traffic generators.
pkill -f "iperf3" 2>/dev/null && echo "    stopped iperf3" || true

# Mininet's own cleanup, in a user namespace.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "${SCRIPT_DIR}/../network-lab/bin/mnexec" ]; then
  export PATH="${SCRIPT_DIR}/../network-lab/bin:${PATH}"
  timeout 30 unshare -Urn "${SCRIPT_DIR}/../.venv/bin/python" -m mininet.clean >/dev/null 2>&1 \
    && echo "    mininet.clean ok" || echo "    mininet.clean skipped/failed"
fi

# Remove the lab socket, token file, pid file, and per-switch logs.
rm -f /tmp/sdn-lab.sock /tmp/sdn-lab.token /tmp/sdn-lab.pid
rm -f /tmp/s[0-9]-of13.log /tmp/s[0-9].listen
echo "    removed lab socket + token + switch logs"

# Anything still alive?
sleep 1
LEFT=$(pgrep -f "run_lab.py|of13switch.py|ryu.cmd.manager" 2>/dev/null | wc -l)
if [ "$LEFT" -gt 0 ]; then
  echo "==> WARNING: $LEFT lab process(es) still alive; killing harder"
  pkill -9 -f "run_lab.py|of13switch.py|ryu.cmd.manager" 2>/dev/null || true
  sleep 1
fi

FINAL=$(pgrep -f "run_lab.py|of13switch.py|ryu.cmd.manager" 2>/dev/null | wc -l)
echo "==> lab stopped (${FINAL} lab processes remaining)"
