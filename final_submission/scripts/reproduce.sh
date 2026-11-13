#!/usr/bin/env bash
#
# One-command reproduction of every result in the paper.
#
#     bash final_submission/scripts/reproduce.sh
#
# What it does, in order:
#
#   1. checks the Linux prerequisites for the lab (user namespaces);
#   2. stops any lab or backend left running by a previous session;
#   3. starts the Mininet/OpenFlow lab and waits for all four datapaths;
#   4. starts the FastAPI backend against a dedicated SQLite database;
#   5. runs every experiment scenario (with the LLM keys cleared, so the
#      agent runs its deterministic keyword-parser fallback) and writes raw
#      JSON + CSV to final_submission/data/;
#   6. runs the backend test suite and the live-lab test suite;
#   7. regenerates every figure in final_submission/paper/figures/ from the
#      recorded data;
#   8. tears the lab and the backend down again.
#
# Options:
#   --keep-running   leave the lab and backend up afterwards
#   --skip-tests     do not run the test suites
#   --skip-figures   do not regenerate the figures
#   --api URL        backend base URL (default http://127.0.0.1:8000)
#
set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUBMISSION_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${SUBMISSION_DIR}/.." && pwd)"
DATA_DIR="${SUBMISSION_DIR}/data"
FIGURE_DIR="${SUBMISSION_DIR}/paper/figures"
PY="${REPO_ROOT}/.venv/bin/python"
API="http://127.0.0.1:8000"
DB_PATH="${DATA_DIR}/repro.db"
KEEP_RUNNING=0
SKIP_TESTS=0
SKIP_FIGURES=0

while [ $# -gt 0 ]; do
  case "$1" in
    --keep-running) KEEP_RUNNING=1; shift ;;
    --skip-tests)   SKIP_TESTS=1;   shift ;;
    --skip-figures) SKIP_FIGURES=1; shift ;;
    --api)          API="$2";        shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

mkdir -p "${DATA_DIR}" "${FIGURE_DIR}"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\n!!! %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
say "0/7  environment"
# ---------------------------------------------------------------------------
if [ ! -x "${PY}" ]; then
  echo "    no virtualenv at ${REPO_ROOT}/.venv -- creating one"
  python3 -m venv "${REPO_ROOT}/.venv" || fail "could not create the virtualenv"
  "${REPO_ROOT}/.venv/bin/python" -m pip install --quiet --upgrade pip \
    || fail "could not install pip"
  "${REPO_ROOT}/.venv/bin/python" -m pip install --quiet \
    -r "${SUBMISSION_DIR}/requirements.txt" \
    || fail "could not install the dependencies in requirements.txt"
fi

"${PY}" - <<'EOF'
import importlib, sys
missing = []
for mod in ("fastapi", "uvicorn", "pydantic", "langgraph", "sqlalchemy",
            "matplotlib", "numpy"):
    try:
        importlib.import_module(mod)
    except ImportError:
        missing.append(mod)
if missing:
    sys.exit("missing python packages: " + ", ".join(missing))
print("    python dependencies present")
EOF
[ $? -eq 0 ] || fail "the python dependencies are not installed"

command -v unshare >/dev/null || fail "unshare(1) is required"
if ! unshare -Urn true 2>/dev/null; then
  fail "this kernel does not allow unprivileged user namespaces (unshare -Urn).
       The Mininet lab cannot run without them, so the results cannot be
       reproduced on this machine."
fi
echo "    unprivileged user namespaces available"

# ---------------------------------------------------------------------------
say "1/7  stopping anything left over from a previous session"
# ---------------------------------------------------------------------------
pkill -f "uvicorn app.main:app" 2>/dev/null && echo "    stopped a stray backend"
bash "${REPO_ROOT}/network-lab/scripts/stop-lab.sh" >/dev/null 2>&1 \
  && echo "    stopped a stray lab"
sleep 2

# ---------------------------------------------------------------------------
say "2/7  starting the network lab"
# ---------------------------------------------------------------------------
bash "${REPO_ROOT}/network-lab/scripts/start-lab.sh" || fail "the lab did not start"
echo "    waiting for the four datapaths to connect..."
for _ in $(seq 1 60); do
  sleep 2
  if "${PY}" - <<'EOF' 2>/dev/null
import http.client, json, socket, sys
class C(http.client.HTTPConnection):
    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout); s.connect(self.socket_path); self.sock = s
    def __init__(self, p):
        super().__init__("localhost", timeout=5); self.socket_path = p
c = C("/tmp/sdn-lab.sock")
c.request("GET", "/v1/status")
d = json.loads(c.getresponse().read().decode())
sys.exit(0 if d.get("running") and int(d.get("connected_switches", 0)) >= 4 else 1)
EOF
  then
    echo "    lab ready"
    break
  fi
done
grep -q "controller topology mirror matches" "${REPO_ROOT}/logs/lab.log" \
  || fail "the lab did not pass the controller/topology cross-check"

# ---------------------------------------------------------------------------
say "3/7  starting the backend"
# ---------------------------------------------------------------------------
cd "${REPO_ROOT}/services/api" || fail "services/api is missing"
# The headline evaluation is the DETERMINISTIC fallback: the keys are
# explicitly cleared for the backend this script starts, so the run is
# reproducible on a machine with no credentials at all. The model-active
# replication of Section VIII-B is a separate, opt-in run.
GROQ_API_KEY= OPENROUTER_API_KEY= \
DATABASE_URL="sqlite+pysqlite:///${DB_PATH}" \
  nohup "${PY}" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 \
  > "${DATA_DIR}/backend.log" 2>&1 &
BACKEND_PID=$!
cd "${REPO_ROOT}" || fail "cannot return to the repository root"

for _ in $(seq 1 40); do
  sleep 1
  if curl -sf "${API}/health" | grep -q '"status":"ok"'; then
    echo "    backend healthy (pid ${BACKEND_PID})"
    break
  fi
done
curl -sf "${API}/health" >/dev/null || fail "the backend did not become healthy"

# ---------------------------------------------------------------------------
say "4/7  running the experiments"
# ---------------------------------------------------------------------------
"${PY}" "${SCRIPT_DIR}/run_experiments.py" \
  --repo-root "${REPO_ROOT}" --data-dir "${DATA_DIR}" --api "${API}" \
  || fail "the experiment run failed"

# ---------------------------------------------------------------------------
say "5/7  running the test suites"
# ---------------------------------------------------------------------------
if [ "${SKIP_TESTS}" -eq 0 ]; then
  ( cd "${REPO_ROOT}/services/api" && "${PY}" -m pytest tests -m "not live" ) \
    > "${DATA_DIR}/pytest_notlive.txt" 2>&1
  echo "    backend unit + integration suite: $(grep -Eo '[0-9]+ passed[^ ]*' "${DATA_DIR}/pytest_notlive.txt" | tail -1)"
  ( cd "${REPO_ROOT}" && "${PY}" -m pytest network-lab/tests ) \
    > "${DATA_DIR}/pytest_network_lab.txt" 2>&1
  echo "    network-lab datapath suite: $(grep -Eo '[0-9]+ passed[^ ]*' "${DATA_DIR}/pytest_network_lab.txt" | tail -1)"
  ( cd "${REPO_ROOT}/services/api" && "${PY}" -m pytest tests -m "live" ) \
    > "${DATA_DIR}/pytest_live.txt" 2>&1
  echo "    live-lab suite: $(grep -Eo '[0-9]+ (passed|skipped)[^ ]*' "${DATA_DIR}/pytest_live.txt" | tail -1)"
  cp "${DATA_DIR}/pytest_network_lab.txt" "${SUBMISSION_DIR}/data/" 2>/dev/null || true
  cp "${DATA_DIR}/pytest_notlive.txt" "${DATA_DIR}/pytest_live.txt" \
     "${SUBMISSION_DIR}/data/" 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
say "6/7  regenerating the figures"
# ---------------------------------------------------------------------------
if [ "${SKIP_FIGURES}" -eq 0 ]; then
  "${PY}" "${SCRIPT_DIR}/make_figures.py" \
    --data-dir "${DATA_DIR}" --figure-dir "${FIGURE_DIR}" \
    || fail "the figure generation failed"
  ls -1 "${FIGURE_DIR}" | sed 's/^/    /'
  # The paper's tables and the numbers quoted in its prose are generated from
  # the same data, so they cannot drift away from it.
  "${PY}" "${SCRIPT_DIR}/make_paper_tables.py" \
    --data-dir "${DATA_DIR}" --out "${SUBMISSION_DIR}/paper/tables.tex" \
    || fail "the table generation failed"
  echo "    regenerated ${SUBMISSION_DIR}/paper/tables.tex and numbers.tex"
fi

# ---------------------------------------------------------------------------
say "7/7  tearing down"
# ---------------------------------------------------------------------------
if [ "${KEEP_RUNNING}" -eq 1 ]; then
  echo "    --keep-running: leaving the lab (pid $(cat /tmp/sdn-lab.pid 2>/dev/null))"
  echo "    and the backend (pid ${BACKEND_PID}) up"
else
  kill "${BACKEND_PID}" 2>/dev/null && echo "    stopped the backend"
  bash "${REPO_ROOT}/network-lab/scripts/stop-lab.sh" >/dev/null 2>&1 \
    && echo "    stopped the lab"
fi

say "done"
echo "    raw data : ${DATA_DIR}"
echo "    figures  : ${FIGURE_DIR}"
