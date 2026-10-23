#!/usr/bin/env bash
#
# Verification harness control script for NetMind SDN Lab.
# Manages the OrbStack Linux environment (sdn-lab), backend, and verification tests.
#
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
VM_NAME="sdn-lab"
API_HOST="sdn-lab.orb.local"
API_URL="http://${API_HOST}:8000"
ARTIFACT_DIR="${PROJECT_ROOT}/artifacts/verify-netmind"

mkdir -p "${ARTIFACT_DIR}"

cmd="${1:-help}"

case "${cmd}" in
  launch)
    echo "==> Ensuring OrbStack VM (${VM_NAME}) is running..."
    if ! orbctl list | grep -q "${VM_NAME}.*running"; then
      orbctl start "${VM_NAME}"
    fi

    echo "==> Ensuring clean lab state inside ${VM_NAME}..."
    orb -m "${VM_NAME}" bash "${PROJECT_ROOT}/network-lab/scripts/stop-lab.sh" || true
    orb -m "${VM_NAME}" pkill -f 'uvicorn app.main:app' || true

    echo "==> Starting SDN network lab inside ${VM_NAME}..."
    orb -m "${VM_NAME}" bash -c "
      export SDN_SWITCH_BACKEND=user
      cd '${PROJECT_ROOT}'
      bash network-lab/scripts/start-lab.sh
    "

    echo "==> Waiting for lab to initialize (switches + telemetry)..."
    sleep 8

    echo "==> Starting FastAPI backend inside ${VM_NAME}..."
    orb -m "${VM_NAME}" pkill -f 'uvicorn app.main:app' || true
    orb -m "${VM_NAME}" bash -c "nohup '${PROJECT_ROOT}/.venv/bin/python' -m uvicorn app.main:app --app-dir '${PROJECT_ROOT}/services/api' --host 0.0.0.0 --port 8000 > '${PROJECT_ROOT}/logs/api.log' 2>&1 < /dev/null &" || true

    echo "==> Waiting for backend health check..."
    for i in {1..20}; do
      if curl -s "${API_URL}/health" | grep -q '"status":"ok"'; then
        echo "==> NetMind backend is healthy at ${API_URL}"
        exit 0
      fi
      sleep 1
    done
    echo "!!! Backend failed to become healthy within 20s" >&2
    exit 1
    ;;

  doctor)
    echo "==> Checking VM status..."
    if ! orbctl list | grep -q "${VM_NAME}.*running"; then
      echo "FAIL: OrbStack VM ${VM_NAME} is not running." >&2
      exit 1
    fi

    echo "==> Checking Backend and Lab Health..."
    health=$(curl -sf "${API_URL}/health" || echo "")
    if [ -z "${health}" ]; then
      echo "FAIL: Cannot reach backend at ${API_URL}/health" >&2
      exit 1
    fi

    echo "Health response: ${health}"
    running=$(echo "${health}" | grep -o '"running":[^,}]*' | cut -d: -f2)
    switches=$(echo "${health}" | grep -o '"connected_switches":[^,}]*' | cut -d: -f2)

    if [ "${running}" != "true" ]; then
      echo "FAIL: Network lab reports not running." >&2
      exit 1
    fi
    if [ "${switches}" -lt 4 ]; then
      echo "FAIL: Expected 4 switches connected, got ${switches}" >&2
      exit 1
    fi

    echo "DOCTOR OK: All 4 OpenFlow switches connected and telemetry running."
    ;;

  snapshot)
    OUT_FILE="${2:-${ARTIFACT_DIR}/telemetry-snapshot.json}"
    mkdir -p "$(dirname "${OUT_FILE}")"
    echo "==> Capturing telemetry snapshot -> ${OUT_FILE}..."
    curl -sf "${API_URL}/v1/snapshot" | python3 -m json.tool > "${OUT_FILE}"
    echo "Saved $(wc -l < "${OUT_FILE}") lines to ${OUT_FILE}"
    ;;

  test-backend)
    echo "==> Running backend test suite (unit + live lab) inside ${VM_NAME}..."
    orb -m "${VM_NAME}" bash -c "
      cd '${PROJECT_ROOT}/services/api'
      '${PROJECT_ROOT}/.venv/bin/python' -m pytest tests
    "
    ;;

  test-frontend)
    echo "==> Running Playwright frontend E2E tests on macOS..."
    cd "${PROJECT_ROOT}/apps/web"
    NEXT_PUBLIC_API_ORIGIN="http://127.0.0.1:8000" npx playwright test
    ;;

  cleanup)
    echo "==> Stopping SDN lab and backend..."
    orb -m "${VM_NAME}" pkill -f 'uvicorn app.main:app' || true
    orb -m "${VM_NAME}" bash "${PROJECT_ROOT}/network-lab/scripts/stop-lab.sh" || true
    echo "==> Cleanup complete. Artifacts in ${ARTIFACT_DIR} are preserved."
    ;;

  *)
    echo "Usage: $0 {launch|doctor|snapshot [outfile]|test-backend|test-frontend|cleanup}"
    exit 1
    ;;
esac
