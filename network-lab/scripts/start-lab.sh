#!/usr/bin/env bash
#
# Launch the local SDN network lab.
#
# The lab is deliberately isolated in its own *network* namespace
# (`unshare -Urn`), for two reasons:
#
#   1. Mininet/OVS need privileges. Inside a user namespace owned by us we
#      have them; on the host network namespace we do not.
#   2. Isolating the lab's netns means the demo traffic can never touch the
#      machine's real interfaces.
#
# Because a fresh netns is unreachable over TCP, the lab exposes its API on a
# **unix domain socket** (a filesystem object, so it crosses namespaces).
# The FastAPI backend connects to that socket; no TCP port is opened.
#
# Usage:  bash network-lab/scripts/start-lab.sh [--foreground]
#
set -u

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LAB_SOCK="${SDN_LAB_SOCKET:-/tmp/sdn-lab.sock}"
LOG_DIR="${PROJECT_ROOT}/logs"
RYU_SRC="${SDN_RYU_SRC:-${PROJECT_ROOT}/network-lab/controller/ryu-src}"

mkdir -p "${LOG_DIR}"

# Refuse to start a second lab: an orphaned controller inside a dead network
# namespace would silently absorb the new switches and the lab would appear
# healthy while never actually connecting.
if pgrep -f "network-lab/run_lab.py" >/dev/null 2>&1; then
  echo "A network lab is already running (pid $(pgrep -f 'network-lab/run_lab.py' | head -1))."
  echo "Stop it first:  bash ${PROJECT_ROOT}/network-lab/scripts/stop-lab.sh"
  exit 1
fi


# `mnexec` + the `ifconfig` shim must be on PATH for Mininet to work.
export PATH="${PROJECT_ROOT}/network-lab/bin:${PATH}"
# Never cache bytecode: the lab's modules are edited and relaunched during
# development, and a stale .pyc has already cost more time than it saved.
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="${RYU_SRC}:${PROJECT_ROOT}:${PROJECT_ROOT}/network-lab:${PROJECT_ROOT}/network-lab/topologies:${PYTHONPATH:-}"

# If LAB_API_TOKEN is explicitly set to empty string, don't generate a random one
if [ -z "${LAB_API_TOKEN+x}" ]; then
  # Variable is not set at all - generate a random token
  export LAB_API_TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
fi
# Write token to a known file so local backend can authenticate automatically
echo -n "${LAB_API_TOKEN}" > /tmp/sdn-lab.token
chmod 600 /tmp/sdn-lab.token


echo "==> SDN network lab"
echo "    project : ${PROJECT_ROOT}"
echo "    socket  : ${LAB_SOCK}"
echo "    backend : ${SDN_RYU_SRC:+ryu-src} ${SDN_RYU_SRC:-vendored ryu}"
echo "    logs    : ${LOG_DIR}/lab.log, ${LOG_DIR}/ryu.log"
echo "    (Ctrl-C to stop)"
echo

if [ "${1:-}" = "--foreground" ]; then
  exec unshare -Urn "${PROJECT_ROOT}/.venv/bin/python" \
    "${PROJECT_ROOT}/network-lab/run_lab.py" \
      --socket "${LAB_SOCK}" --log-level info
else
  unshare -Urn "${PROJECT_ROOT}/.venv/bin/python" \
    "${PROJECT_ROOT}/network-lab/run_lab.py" \
      --socket "${LAB_SOCK}" --log-level info >> "${LOG_DIR}/lab.log" 2>&1 &
  echo $! > /tmp/sdn-lab.pid
  echo "==> lab started (pid $(cat /tmp/sdn-lab.pid)); follow with:"
  echo "      tail -f ${LOG_DIR}/lab.log"
fi
