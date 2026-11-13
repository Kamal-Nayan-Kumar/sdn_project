# Reproducing this submission

Everything in `final_submission/` was produced by one command on one Linux
machine. This file says exactly what that command is, what it needs, and
what every artefact in `final_submission/data/` contains.

---

## How this maps to the course's submission instructions

The course's final instructions (Step 1–3 of the professor's email) are
answered artefact-by-artefact:

**Step 1 — the paper (Overleaf).** The paper is `final_submission/paper/paper.tex`
(IEEEtran, compiled with Tectonic; source in `Overleaf_Source.zip`). It
contains every section the instructions name: abstract, introduction,
related work, problem scenario, system model, mathematical formulation,
proposed algorithm, baseline algorithms and experimental setup, results,
graphs and performance analysis, conclusion and IEEE-format references. The
implementation is the source of truth for every number in it: all tabular
values and every number quoted in prose are generated from the recorded
data by `final_submission/scripts/make_paper_tables.py`
(`tables.tex` and `numbers.tex`), which is why the prose and the tables
cannot drift apart.

**Step 2 — Google Deep Review (minimum two rounds).** Round 1 and Round 2
of the Google Deep Review were run against this paper. Both reviews are
reproduced verbatim (`final_submission/GA_DEEP_REVIEW_ROUND1.pdf`,
`final_submission/GA_DEEP_REVIEW_ROUND2.pdf`, with byte-identical
Markdown sources); the revision reports that were submitted with them are
`final_submission/REVISION_REPORT.pdf` and
`final_submission/REVISION_REPORT_ROUND2.pdf`. Every item in each review
is either fixed in the paper/artifact or explicitly declined with a
reason; the code defects found were fixed with regression tests.
**Note added after Round 2:** the final paper additionally contains
Section VIII-B (the model in the loop: `qwen/qwen3.8-27b` active in
intent parsing and narration; outcomes identical to the fallback run, full
details in §4.5 of `AUDIT.md`) and the figure `fig_llm.pdf`.

**Note added after Round 3:** the Round 3 review's items were addressed in
full (§4.6 of `AUDIT.md`): Section VIII-C (the boundary probe and the
prompt-robustness experiment, both run on the live lab and shipped with
their raw data), three further code defects that our own probe found (a
request to disable a *named* check now refused; the clarification branch
now records its own outcome; Gate 1 now counts the loss half of the
intent), a formally specified evidence-sufficiency predicate, the
improvement predicate as Eq. (5a), the protected-flow tuple and the
provenance of the effective delay, and a rewritten related-work
positioning. These changes were made after the Round 3 upload and are
documented in `REVISION_REPORT_ROUND3.pdf`.

**Step 3 — reproducibility.** `Overleaf_Source.zip` (paper only) and
`reproducibility.zip` (the whole artifact) are both in
`final_submission/submission/`. The zip is well under the 100 MB cap and
contains: all implementation files, `requirements.txt`, the install and
execution instructions (this file), the recorded data and the
parameters/seeds/interfaces used, the exact commands to regenerate every
reported result and figure (`bash final_submission/scripts/reproduce.sh`),
and the honest caveat section (`AUDIT.md`). Nothing in the submission needs
an API key, a network connection, or root.

**Deadline.** Thu 19 Nov 2026, 5 PM IST (hard), via the Google Form
(the human submitter enters it; nothing here uploads anything).

---

## 1. Requirements

| Requirement | Why | How to check |
| --- | --- | --- |
| Linux with **unprivileged user namespaces** | Mininet and the network namespace need them | `unshare -Urn true` |
| Python **3.12+** | the backend and the lab | `python3 --version` |
| A virtualenv with the pinned dependencies | backend, lab and figures | `final_submission/requirements.txt` |
| `network-lab/bin/mnexec` | Mininet shells out to it; it is not on PyPI | `bash scripts/setup-mininet.sh` |
| `network-lab/controller/ryu-src` | the vendored Ryu the lab imports | `bash scripts/setup-ryu.sh` |
| `tectonic` | to build the paper | `tectonic --version` |

No root, no Docker, no cloud service. The lab runs inside its own network
namespace (`unshare -Urn`) and exposes its API on a **unix domain socket**, so
no TCP port is opened for network control.

The LLM is **optional**. With no `GROQ_API_KEY` the agent runs fully
deterministically and says so in the run record. Every result in this
submission was produced in that mode, so nothing here depends on an external
API.

### Install

```bash
cd <repository root>
python3 -m venv .venv
.venv/bin/pip install -r final_submission/requirements.txt
bash scripts/setup-mininet.sh          # builds network-lab/bin/mnexec
bash scripts/setup-ryu.sh              # clones network-lab/controller/ryu-src
cp .env.example .env                   # optional; leave GROQ_API_KEY empty
```

> `scripts/setup-mininet.sh` and `scripts/setup-ryu.sh` need network access
> and, for Ryu, a `network-lab/patches/` directory that this checkout does
> **not** contain — see `AUDIT.md` §3.1 (U4). A vendored Ryu checkout already
> exists in this working tree, so the script is not needed to reproduce the
> results here.

### Reproducing the LLM-active run (Section VIII-B)

The headline evaluation in this submission was produced **without** a model
(the deterministic fallback; the gates make every decision). The
`data-llm/` replication used `qwen/qwen3.8-27b` on Groq. To reproduce it
yourself you need a Groq key in `.env` (never commit it; the zips ship only
`.env.example`):

```bash
cp .env.example .env
# set the provider in .env (any OpenAI-compatible endpoint works):
#   LLM_API_KEY=<your key>
#   LLM_BASE_URL=https://api.groq.com/openai/v1   (or any compatible gateway)
#   LLM_MODEL=qwen/qwen3.8-27b
# (or keep the historic GROQ_API_KEY/GROQ_MODEL pair for Groq defaults)
bash network-lab/scripts/start-lab.sh           # or unshare -Ur ...
cd services/api && ../../.venv/bin/python -m uvicorn app.main:app \
    --host 127.0.0.1 --port 8000 &
cd <repo root>
.venv/bin/python final_submission/scripts/run_experiments.py \
    --repo-root <repo root> --data-dir final_submission/data-llm \
    --api http://127.0.0.1:8000
.venv/bin/python final_submission/scripts/make_llm_table.py \
    --data-dir final_submission/data-llm \
    --fallback-dir final_submission/data-fallback \
    --out final_submission/paper/tables_llm.tex \
    --macro-out final_submission/paper/numbers_llm.tex
```

Without a key, the same commands run the deterministic fallback; nothing in
this submission depends on the model being present.

### Reproducing the boundary probe (Section VIII-C)

The boundary probe puts candidate paths in front of Gate 2 with the
planner bypassed, and records what the agent does with paraphrased,
underspecified, out-of-topology and protected-flow requests. It needs the
lab and the backend running (the commands above start them):

```bash
.venv/bin/python final_submission/scripts/run_boundary_probe.py \
    --api http://127.0.0.1:8000 --data-dir final_submission/data \
    --proposals 8
.venv/bin/python final_submission/scripts/make_probe_table.py \
    --data-dir final_submission/data \
    --out final_submission/paper/tables_probe.tex \
    --macro-out final_submission/paper/numbers_probe.tex
```

It writes `boundary_probe.csv`, `prompt_robustness.csv` and
`boundary_probe.json` into the data directory, and the tables of
Section VIII-C are generated from them (no number in that section is
typed by hand). Without a key the model-proposal part is skipped and only
the hand-crafted proposals and the prompt cases are recorded.

---

## 2. The one command

```bash
bash final_submission/scripts/reproduce.sh
```

It performs eight steps, in this order:

| Step | What it does |
| --- | --- |
| 0 | checks the virtualenv, the dependencies and `unshare -Urn` |
| 1 | stops any lab or backend left over from a previous session |
| 2 | starts the Mininet/OpenFlow lab and waits for all four datapaths |
| 3 | starts the FastAPI backend against `final_submission/data/repro.db` |
| 4 | runs the eight experiment scenarios and writes the raw data |
| 5 | runs the backend test suite, the network-lab datapath suite and the live-lab test suite |
| 6 | regenerates every figure from the recorded data |
| 7 | stops the backend and the lab |

Options: `--keep-running` (leave the lab and backend up), `--skip-tests`,
`--skip-figures`, `--api URL`.

**Expected wall-clock time:** about 20–25 minutes, of which roughly 15 is
the lab being restarted between scenarios and trials.

**Expected output:** eight scenarios with these outcomes, in this order.
The four active-recovery scenarios are repeated as **five independent
trials** each, each from a freshly built lab, so their outcomes are
distributions rather than single draws:

```
baseline_healthy          intent_satisfied
congestion_recovery       intent_satisfied        (x5 trials)
congestion_partial        partial_improvement     (x5 trials)
healthy_impossible_target no_better_path
guardrail_blocked         guardrail_blocked
lab_offline               lab_offline
offered_load_congestion   intent_satisfied        (x5 trials; bottleneck at 60 Mbit/s)
degraded_no_oracle        intent_satisfied        (x5 trials)
```

followed by `134 passed` for the unit/integration suite, `4 passed` for the
network-lab datapath suite and `5 passed` for the live-lab suite.

---

## 3. Running the pieces individually

```bash
# the lab alone (leave it running in a terminal)
bash network-lab/scripts/start-lab.sh
tail -f logs/lab.log

# the backend alone
cd services/api && ../../.venv/bin/python -m uvicorn app.main:app \
    --host 127.0.0.1 --port 8000

# the dashboard (optional)
cd apps/web && npm install && npm run dev      # http://localhost:3000
```

Useful endpoints on the running backend:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | liveness, lab reachability, switch count, safety policy |
| `GET` | `/v1/status` | the lab's own status |
| `GET` | `/v1/topology` | the live topology |
| `GET` | `/v1/snapshot` | a raw telemetry snapshot |
| `GET` | `/v1/paths?src=h1&dst=h4` | the candidate paths |
| `GET` | `/v1/flows` | the datapaths' installed flow tables |
| `GET` | `/v1/port-stats` | the datapaths' port counters |
| `POST` | `/v1/scenarios/congestion` | start the congestion scenario |
| `POST` | `/v1/reset` | reset the lab |
| `POST` | `/api/runs` | start an agent run |
| `GET` | `/api/runs/{id}` | the run's outcome, gates and audit trail |
| `GET` | `/api/runs/{id}/stream` | the run's SSE event stream |

Example:

```bash
curl -s -X POST http://127.0.0.1:8000/api/runs \
     -H 'Content-Type: application/json' \
     -d '{"message":"optimise latency so that it comes under 50 ms"}'
```

---

## 4. Running the tests

```bash
# backend unit + integration (no lab needed)
cd services/api && ../../.venv/bin/python -m pytest tests -m "not live"

# live-lab tests (need the lab running)
cd services/api && ../../.venv/bin/python -m pytest tests -m "live"

# frontend end-to-end (starts its own dev server)
cd apps/web && npm run test:e2e
```

Expected: **133 passed, 5 deselected** and **5 passed**.

The 133 are 78 pre-existing tests, 55 audit-round tests — 44 from the truth
audit (`tests/test_regressions.py`, `tests/test_paper_equations.py`) and 11
from the peer-review round (`tests/test_review_round1.py`).

Running `pytest network-lab/tests` separately exercises the pure-Python
OpenFlow 1.3 datapath (4 tests), including the flow-mod decoder class of
bug that the review round found.

The live-lab suite skips itself if the lab is not reachable, so check that it
reports `5 passed` rather than `5 skipped` before quoting the number.

---

## 5. Regenerating the figures

```bash
.venv/bin/python final_submission/scripts/make_figures.py \
    --data-dir final_submission/data \
    --figure-dir final_submission/paper/figures
```

The script reads **only** `final_submission/data/`. It never talks to the lab
and never re-measures anything, so every figure can be traced back to the
JSON/CSV that produced it. Each figure is written as both `.pdf` (for the
paper) and `.png`.

| Figure | What it shows | Source |
| --- | --- | --- |
| `fig_topology` | the lab topology, drawn from the lab's own description | `topology.json` |
| `fig_workflow` | the 13-node LangGraph graph and its three gates | hard-coded from `agents/graph.py` |
| `fig_latency_phases` | measured video latency per phase, with the 50 ms intent | `measurements.csv` |
| `fig_sample_distribution` | every individual ping sample, not just the mean | `measurements.csv` |
| `fig_ranking_ablation` | which path each ranking rule selects, and what it measured | `ablations.csv` |
| `fig_outcomes` | the outcome and the measured effect of every scenario | `scenario_summary.csv` |
| `fig_link_utilisation` | measured link utilisation per phase | `link_utilisation.csv` |

---

## 6. Why the lab is restarted between scenarios

The lab's reset restores the link delays and clears the controller's override
table, and **with the fixed datapath it also restores the baseline forwarding
path** (measured: 25.4 ms before reset, 17.3 ms after). The harness nevertheless
restarts the lab before every scenario, so that each scenario starts from a
bit-identical, freshly built baseline: that costs a few seconds per scenario and
removes any chance of state leaking between scenarios, and it means the
experiment needs no reset semantics at all.

Historically this was not true: in the tree shipped for the first review, the
pure-Python datapath never matched any priority-100 rule (see `AUDIT.md` §3.1
U2 and §4.3 F17), so a reset after a reroute left the flow on the old path.
Two fixes were attempted at the time (flushing the whole flow table; deleting
the learning rules individually) and **both caused a broadcast storm around the
ring**, because the ring has no spanning tree and a datapath that cannot reach
the controller floods forever. The harness's restart-between-scenarios protocol
was the workaround for that defect; with F17 the workaround is no longer
*necessary*, but it is kept for determinism.

---

## 7. What is in `final_submission/data/`, and in `final_submission/`
### The top-level documents

| File | What it is |
| --- | --- |
| `paper/paper.pdf` | the paper |
| `paper/paper.tex` | its LaTeX source |
| `AUDIT.md` | the truth audit: every claim checked, every defect found, every fix applied |
| `GA_DEEP_REVIEW_ROUND1.pdf` | the Round 1 Google Deep Review, reproduced verbatim (all wording preserved; inline math typeset) |
| `GA_DEEP_REVIEW_ROUND1.md` | the byte-identical source of that review, for diffing |
| `REVISION_REPORT.pdf` | the Round 1 response letter: every review comment from Round 1 and the action taken on it |
| `REVISION_REPORT.tex` | its source |
| `GA_DEEP_REVIEW_ROUND2.pdf` | the Round 2 Google Deep Review, reproduced verbatim |
| `GA_DEEP_REVIEW_ROUND2.md` | the byte-identical source of that review, for diffing |
| `REVISION_REPORT_ROUND2.pdf` | the Round 2 response letter: every review comment from Round 2 and the action taken on it |
| `REVISION_REPORT_ROUND2.tex` | its source |
| `submission/Overleaf_Source.zip` | everything Overleaf needs to build the paper standalone |
| `submission/reproducibility.zip` | the reproducibility package (this zip's contents) |

---

| File | Contents |
| --- | --- |
| `data/` | the canonical run used for the paper's tables and figures: the deterministic keyword-parser fallback evaluation. **Do not regenerate it in place with an `.env` that contains a key** — re-running `reproduce.sh` with a configured key overwrites these files with the LLM-active run; back it up first or pass a different directory |
| `data-fallback/` | byte-level backup of the deterministic-fallback dataset that was shipped with this submission (identical to `data/` as shipped) |
| `data-llm/` | the same protocol run with the model active (`qwen/qwen3.8-27b` on Groq; intent parsing at temperature 0), as reported in Section VIII-B of the paper |
| `boundary_probe.csv` | every candidate path put in front of Gate 2 with the planner bypassed: the model's own proposals (with and without telemetry) and hand-crafted unsafe ones (Section VIII-C) |
| `prompt_robustness.csv` | the recorded outcome of six request shapes: canonical, paraphrase, underspecified, out-of-topology, protected-flow, gate-override (Section VIII-C) |
| `runs.json` | every agent run: the API record **and** its complete SSE event transcript |
| `snapshots.json` | the raw lab snapshots, one per sampled phase (60 snapshots) |
| `scenario_summary.csv` | one row per scenario **and trial**: outcome, before/after latency and loss, installed path, gates passed, risk level, diagnosis and answer |
| `scenario_aggregates.csv` | one row per scenario: the number of trials, the distribution of outcomes, and the min/mean/max of the post-action latency |
| `measurements.csv` | one row per **individual flow measurement**: scenario, phase, sample index, flow, mean/min/max latency, jitter, loss, path, and the full list of ping samples as JSON |
| `link_utilisation.csv` | one row per sampled link per phase: utilisation, capacity, effective delay, status, congested flag |
| `ablations.csv` | the ablation results (ranking rule, violation rule, always-act versus abstain) |
| `candidate_paths.json` | the lab's candidate paths with their link sets and configured delays, captured **while the bottleneck was degraded** |
| `topology.json` | the live topology |
| `pytest_notlive.txt`, `pytest_live.txt` | the raw test output |
| `backend.log` | the backend's log for the run |

The run also writes `repro.db`, the SQLite database the backend used; it is a
runtime artefact rather than a shipped data file, and is not included in
`reproducibility.zip`.

### Regenerating the paper's tables

The paper's tables and the numbers quoted in its prose are generated from the
same data, so they cannot drift away from it:

```bash
.venv/bin/python final_submission/scripts/make_paper_tables.py \
    --data-dir final_submission/data \
    --out final_submission/paper/tables.tex
```

This writes `final_submission/paper/tables.tex` (the result tables) and
`final_submission/paper/numbers.tex` (the macros used in the abstract and in
the prose). `reproduce.sh` runs it automatically as part of step 6.

**No data in this directory is synthetic.** Every value was measured on the
running lab. The one exception is the *ablation* rows, which are
**re-computations of recorded measurements** under a different decision rule —
they are labelled as such in the `ablation` and `policy` columns, and they
quote the measured latencies they are based on.

---

## 8. Parameters, seeds and configuration

There is no randomness in the measurement path: the traffic is ICMP ping with
a fixed packet count, the lab is deterministic, and the agent's intent
parsing is a deterministic keyword parser when no LLM is configured.

| Parameter | Value | Where |
| --- | --- | --- |
| Latency samples per measurement | 10 ping packets, 0.2 s apart | `topologies/telemetry.py::measure_flow` |
| Flows measured | `h1→h4` (video), `h3→h4` (voice) | `topologies/telemetry.py::snapshot` |
| Samples per phase | 5 | `final_submission/scripts/run_experiments.py` |
| Latency intent | 50 ms | the agent's canonical intent |
| Congestion link | `s2-s4` | `run_lab.py::start_congestion` |
| Congestion severity | +40 ms netem delay on both interfaces | `run_lab.py::_apply_link_congestion_delay` |
| `rate_mbps` | recorded; used only by the offered-load scenario (S7/S8), which starts a real background UDP stream from `h2` to `h4` | `run_lab.py` |
| `CONGESTION_UTILIZATION_THRESHOLD` | 0.8 | `.env` / `core/config.py` |
| `TELEMETRY_MAX_AGE_SECONDS` | 5 | `.env` / `core/config.py` |
| `EVIDENCE_MIN_CONFIDENCE` | 0.6 | `.env` / `core/config.py` |
| `MAX_PATH_HOPS` | 6 | `safety/gates.py` |
| `VIOLATION_SAMPLE_FRACTION` | 0.25 | `agents/workflow.py` |
| `REQUIRE_HUMAN_APPROVAL_FOR_RISKY_ACTIONS` | true | `.env` / `core/config.py` |
| `PROACTIVE_MONITOR_ENABLED` | false (the monitor is exercised by a unit test instead) | `.env` |
| Database | `sqlite+pysqlite:///<data>/repro.db` | set by `reproduce.sh` |
| Backend bind | `127.0.0.1:8000` | set by `reproduce.sh` |
| Lab socket | `/tmp/sdn-lab.sock` | `network-lab/scripts/start-lab.sh` |

### Baseline configurations compared in the ablations

| Ablation | System rule | Ablated rule |
| --- | --- | --- |
| Ranking | `PathCandidate.score`: a path that crosses a congested link is disqualified (`inf`); among clean paths the accumulated delay decides | rank on the **configured** delay alone, as a static shortest-path table would |
| Violation | violated when **≥ 25 %** of the individual ping samples exceed the target | violated when the **mean** exceeds the target |
| Acting | a healthy network ends at `intent_satisfied` with **no** flow rules installed | always proceed to plan and execute |

---

## 9. Building the paper

```bash
cd final_submission/paper
tectonic -X compile paper.tex --outfmt pdf
```

The build needs `tectonic` (0.17.0 was used) and fetches `IEEEtran.cls` from
the Tectonic bundle on first use, so the first build needs network access.
`Overleaf_Source.zip` contains everything the build needs, so it compiles
there without network access.

---

## 10. Cleaning up

```bash
bash network-lab/scripts/stop-lab.sh     # the lab, the switches and the controller
pkill -f "uvicorn app.main:app"          # the backend
```

The lab's socket, token file and per-switch logs are removed by
`stop-lab.sh`. The controller binds to `127.0.0.1` only.
