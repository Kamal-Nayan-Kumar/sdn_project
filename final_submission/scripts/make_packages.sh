#!/usr/bin/env bash
#
# Build the two submission archives.
#
#   Overleaf_Source.zip   the paper's LaTeX source, the IEEE class files and
#                         the figures -- everything Overleaf needs to build
#                         paper.pdf without any further downloads.
#
#   reproducibility.zip   the code, the pinned requirements, the reproduction
#                         scripts, the raw data and the instructions -- enough
#                         to re-run the experiment and regenerate every
#                         number and figure in the paper.
#
set -eu

SUBMISSION_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "${SUBMISSION_DIR}/.." && pwd)"
PAPER_DIR="${SUBMISSION_DIR}/paper"
DATA_DIR="${SUBMISSION_DIR}/data"
OUT_DIR="${SUBMISSION_DIR}/submission"
STAGE="$(mktemp -d)"
trap 'rm -rf "${STAGE}"' EXIT

mkdir -p "${OUT_DIR}"

# ---------------------------------------------------------------------------
# Overleaf source
# ---------------------------------------------------------------------------
OVERLEAF="${STAGE}/Overleaf_Source"
mkdir -p "${OVERLEAF}/figures"
cp "${PAPER_DIR}/paper.tex" "${OVERLEAF}/"
cp "${PAPER_DIR}/references.bib" "${OVERLEAF}/"
cp "${PAPER_DIR}/tables.tex" "${OVERLEAF}/"
cp "${PAPER_DIR}/numbers.tex" "${OVERLEAF}/"
[ -f "${PAPER_DIR}/tables_llm.tex" ] && cp "${PAPER_DIR}/tables_llm.tex" "${OVERLEAF}/"
[ -f "${PAPER_DIR}/numbers_llm.tex" ] && cp "${PAPER_DIR}/numbers_llm.tex" "${OVERLEAF}/"
[ -f "${PAPER_DIR}/tables_probe.tex" ] && cp "${PAPER_DIR}/tables_probe.tex" "${OVERLEAF}/"
[ -f "${PAPER_DIR}/numbers_probe.tex" ] && cp "${PAPER_DIR}/numbers_probe.tex" "${OVERLEAF}/"
cp "${PAPER_DIR}/IEEEtran.cls" "${OVERLEAF}/"
cp "${PAPER_DIR}/IEEEtran.bst" "${OVERLEAF}/"
cp "${PAPER_DIR}"/figures/*.pdf "${OVERLEAF}/figures/"
cp "${PAPER_DIR}/paper.pdf" "${OVERLEAF}/"

cat > "${OVERLEAF}/README.txt" <<'EOF'
Trustworthy Agentic Automation for Intent-Based Self-Healing
in Software-Defined Networks
================================================================

Files
-----
paper.tex        the paper (IEEE conference format, IEEEtran.cls)
references.bib   the bibliography
tables.tex       GENERATED: the result tables, from final_submission/data/
numbers.tex      GENERATED: the numbers quoted in the prose
tables_llm.tex   GENERATED: the LLM-active replication table (Section VIII-B)
numbers_llm.tex  GENERATED: the LLM-active replication numbers (Section VIII-B)
tables_probe.tex GENERATED: the boundary-probe and prompt-robustness tables
numbers_probe.tex
                 GENERATED: the boundary-probe numbers (Section VIII-C)
IEEEtran.cls     the IEEE conference class (and IEEEtran.bst for the
                 bibliography style)
figures/*.pdf    every figure, as PDF
paper.pdf        the compiled paper, for reference

Build
-----
On Overleaf: choose "pdfLaTeX" as the compiler and upload everything in
this directory. The two .tex files are included by paper.tex:

    \input{numbers.tex}      (in the preamble: the macros used in the abstract)
    \input{numbers_llm.tex}  (likewise, for the VIII-B replication)
    \input{numbers_probe.tex} (likewise, for the VIII-C probe)
    \input{tables.tex}       (in Section VIII: the result tables)
    \input{tables_llm.tex}   (likewise, in Section VIII-B)
    \input{tables_probe.tex} (likewise, in Section VIII-C)

To regenerate tables.tex and numbers.tex from a fresh run of the
experiment, see final_submission/scripts/make_paper_tables.py.

Locally, with tectonic:

    tectonic -X compile paper.tex --outfmt pdf
EOF

( cd "${STAGE}" && zip -qr "${OUT_DIR}/Overleaf_Source.zip" Overleaf_Source )
echo "==> Overleaf_Source.zip: $(du -h "${OUT_DIR}/Overleaf_Source.zip" | cut -f1)"

# ---------------------------------------------------------------------------
# Reproducibility package
# ---------------------------------------------------------------------------
REPRO="${STAGE}/reproducibility"
mkdir -p "${REPRO}"

copy_tree() {
  # copy_tree <source> <destination-inside-the-package>
  local src="$1" dst="$2"
  mkdir -p "${REPRO}/${dst}"
  ( cd "${src}" && tar cf - \
      --exclude='__pycache__' --exclude='*.pyc' --exclude='.pytest_cache' \
      --exclude='*.egg-info' --exclude='node_modules' --exclude='.next' \
      --exclude='.venv' --exclude='ryu-src' --exclude='*.db' \
      --exclude='*.log' --exclude='*.png' --exclude='.playwright-mcp' \
      --exclude='playwright-report' --exclude='test-results' \
      --exclude='*.bak' --exclude='.tsbuildinfo' \
      --exclude='.env' --exclude='.env.*' \
      . ) | ( cd "${REPRO}/${dst}" && tar xf - )
}

copy_tree "${REPO_ROOT}/services/api" "services/api"
copy_tree "${REPO_ROOT}/network-lab" "network-lab"
copy_tree "${REPO_ROOT}/scripts" "scripts"
copy_tree "${REPO_ROOT}/apps/web/src" "apps/web/src"
cp "${REPO_ROOT}/apps/web/package.json" "${REPRO}/apps/web/" 2>/dev/null || true

mkdir -p "${REPRO}/final_submission"
cp -r "${SUBMISSION_DIR}/scripts" "${REPRO}/final_submission/scripts"
cp "${SUBMISSION_DIR}/paper/paper.pdf" "${REPRO}/final_submission/paper.pdf" 2>/dev/null || true
cp "${SUBMISSION_DIR}/requirements.txt" "${REPRO}/final_submission/requirements.txt"
cp "${SUBMISSION_DIR}/INSTRUCTIONS.md" "${REPRO}/final_submission/INSTRUCTIONS.md"
cp "${SUBMISSION_DIR}/AUDIT.md" "${REPRO}/final_submission/AUDIT.md"
cp "${SUBMISSION_DIR}/REVISION_REPORT_TEMPLATE.md" \
   "${REPRO}/final_submission/REVISION_REPORT_TEMPLATE.md"
for doc in REVISION_REPORT.md REVISION_REPORT.tex REVISION_REPORT.pdf \
           REVISION_REPORT_ROUND2.md REVISION_REPORT_ROUND2.tex REVISION_REPORT_ROUND2.pdf \
           REVISION_REPORT_ROUND3.md REVISION_REPORT_ROUND3.tex REVISION_REPORT_ROUND3.pdf \
           GA_DEEP_REVIEW_ROUND1.md GA_DEEP_REVIEW_ROUND1.tex GA_DEEP_REVIEW_ROUND1.pdf \
           GA_DEEP_REVIEW_ROUND2.md GA_DEEP_REVIEW_ROUND2.tex GA_DEEP_REVIEW_ROUND2.pdf \
           GA_DEEP_REVIEW_ROUND3.md GA_DEEP_REVIEW_ROUND3.tex GA_DEEP_REVIEW_ROUND3.pdf; do
  [ -f "${SUBMISSION_DIR}/${doc}" ] && cp "${SUBMISSION_DIR}/${doc}" "${REPRO}/final_submission/"
done
mkdir -p "${REPRO}/final_submission/data"
cp "${DATA_DIR}"/*.csv "${DATA_DIR}"/*.json "${REPRO}/final_submission/data/" 2>/dev/null || true
# Ship the deterministic-fallback dataset as the canonical one (identical to
# data/) and the LLM-active replication as a second, labelled dataset.
for d in data-fallback data-llm; do
  if [ -d "${SUBMISSION_DIR}/${d}" ]; then
    mkdir -p "${REPRO}/final_submission/${d}"
    cp "${SUBMISSION_DIR}/${d}"/*.csv "${SUBMISSION_DIR}/${d}"/*.json \
       "${REPRO}/final_submission/${d}/" 2>/dev/null || true
  fi
done
cp "${DATA_DIR}"/pytest_*.txt "${REPRO}/final_submission/data/" 2>/dev/null || true

cp "${REPO_ROOT}/README.md" "${REPO_ROOT}/PROJECT_SPEC.md" \
   "${REPO_ROOT}/.env.example" "${REPRO}/"

cat > "${REPRO}/README.txt" <<'EOF'
Trustworthy Agentic Automation for Intent-Based Self-Healing
in Software-Defined Networks
================================================================
Reproducibility package

    bash final_submission/scripts/reproduce.sh

is the one command that reproduces every number and every figure in the
paper. It starts the lab, runs the eight scenarios, runs the test suites and
regenerates the figures. See final_submission/INSTRUCTIONS.md.

What is here
------------
services/api/          the FastAPI backend, the LangGraph agent, the safety
                       gates, the persistence layer and the test suite
network-lab/           the Mininet/Ryu lab, the topology, the telemetry
                       collector, the pure-Python OpenFlow datapath and its tests
scripts/               the one-time setup helpers (mnexec, vendored Ryu)
apps/web/src/          the dashboard source (not needed for the results)
final_submission/
  scripts/             reproduce.sh, run_experiments.py, make_figures.py,
                       make_paper_tables.py, make_llm_table.py,
                       make_probe_table.py, run_boundary_probe.py
  data/                the raw JSON and CSV produced by the last run
  data-fallback/       backup of the shipped deterministic-fallback dataset
  data-llm/            the LLM-active replication (Section VIII-E)
  paper.pdf            the compiled paper (source: Overleaf_Source.zip)
  requirements.txt     the pinned dependencies
  INSTRUCTIONS.md      how to reproduce, and what every data file is
  AUDIT.md             the truth audit: every claim checked, every defect
                       found, every fix applied
  REVISION_REPORT.pdf  the Round 1 response letter: every review comment
                       and the action taken on it
  REVISION_REPORT.tex  its source
  REVISION_REPORT_ROUND2.pdf
                       the Round 2 response letter
  REVISION_REPORT_ROUND2.tex
                       its source
  GA_DEEP_REVIEW_ROUND1.pdf
                       the Round 1 review, reproduced verbatim
  GA_DEEP_REVIEW_ROUND1.md
                       its byte-identical source for diffing
  GA_DEEP_REVIEW_ROUND2.pdf
                       the Round 2 review, reproduced verbatim
  GA_DEEP_REVIEW_ROUND2.md
                       its byte-identical source for diffing
  REVISION_REPORT_ROUND3.pdf
                       the Round 3 response letter
  REVISION_REPORT_ROUND3.tex
                       its source
  GA_DEEP_REVIEW_ROUND3.pdf
                       the Round 3 review, reproduced verbatim
  GA_DEEP_REVIEW_ROUND3.md
                       its byte-identical source for diffing
  REVISION_REPORT_TEMPLATE.md

What is NOT here
----------------
network-lab/controller/ryu-src/   the vendored Ryu checkout; re-create it
                                  with scripts/setup-ryu.sh
.venv/                             the virtualenv; create it from
                                  final_submission/requirements.txt
network-lab/bin/mnexec             built by scripts/setup-mininet.sh
EOF

( cd "${STAGE}" && zip -qr "${OUT_DIR}/reproducibility.zip" reproducibility )
echo "==> reproducibility.zip: $(du -h "${OUT_DIR}/reproducibility.zip" | cut -f1)"
echo
ls -la "${OUT_DIR}"
