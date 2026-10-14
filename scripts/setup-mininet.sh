#!/usr/bin/env bash
#
# Build the `mnexec` helper that Mininet needs.
#
# Mininet shells out to `mnexec` to move a process into a network namespace,
# but the PyPI distribution ships no C sources, so it has to be compiled from a
# checkout of the mininet repository. `network-lab/bin/mnexec` is the build
# output and is therefore not committed.
#
# `network-lab/bin/ifconfig` (a bash shim translating `ifconfig` to `ip`,
# because Arch has no net-tools) *is* committed -- it is source, not a binary.
#
# Usage:  bash scripts/setup-mininet.sh
#
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN_DIR="${PROJECT_ROOT}/network-lab/bin"
OUT="${BIN_DIR}/mnexec"
MININET_REPO="${MININET_REPO:-https://github.com/mininet/mininet.git}"

if [ -x "${OUT}" ] && [ "${1:-}" != "--force" ]; then
  echo "==> ${OUT} already exists (use --force to rebuild)"
  exit 0
fi

WORKDIR="$(mktemp -d)"
trap 'rm -rf "${WORKDIR}"' EXIT

echo "==> cloning ${MININET_REPO}"
git clone --depth 1 --branch mininet-2.3.0 "${MININET_REPO}" "${WORKDIR}/mininet" 2>/dev/null \
  || git clone --depth 1 "${MININET_REPO}" "${WORKDIR}/mininet"

# Prefer the 2.3 release tag; fall back to whatever branch cloned cleanly.
if [ ! -d "${WORKDIR}/mininet/mnexec" ]; then
  echo "!!! mnexec sources not found in the checkout." >&2
  echo "    Mininet reorganised its layout; locate mnexec.c and build it with:" >&2
  echo "      gcc -o network-lab/bin/mnexec -DVERSION=\"2.3.0\" <path>/mnexec.c" >&2
  exit 1
fi

mkdir -p "${BIN_DIR}"
echo "==> building mnexec"
( cd "${WORKDIR}/mininet" && make mnexec )
install -m 0755 "${WORKDIR}/mininet/mnexec" "${OUT}"

echo "==> installed ${OUT}"
"${OUT}" 2>&1 | head -3 || true