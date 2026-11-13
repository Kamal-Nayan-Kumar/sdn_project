# Truth audit

**Scope.** Every factual claim in `report/report.tex` and `README.md` was
checked against the code, and every number was checked by running the system.
This file records what was verified, what was wrong, what was fixed, and what
could not be fixed.

`report/report.tex` was treated as read-only, as instructed. Where it is wrong,
the correction is recorded here and the new paper in `final_submission/paper/`
states the truth.

**A note on the revision history below.** This audit was carried out in three
passes, and the earlier passes are kept as the record of what was true when
they ran: the measured latencies in Sections 1-4.2 and the file list in
Section 5 are the numbers from the state of the tree at the first and second
passes. Section 4.3 (the peer-review round) supersedes them where they
differ: most prominently, the first-pass measurements reported a healthy
baseline of ~29.7 ms and a repaired RTT of ~37-42 ms because the user-space
datapath never matched any installed rule (see F17), and the recovery
scenario reported an outcome of `recovery_failed` for an analogous reason.
The final numbers in the paper are those of the fixed tree.

**Audit baseline.**

| Item | Value |
| --- | --- |
| Repository | `/home/nayan/Projects/sdn_project` |
| Branch | `main` |
| Commit | `704ef4026c9ffd0bb0bad91835ec1d348134a564` |
| Working tree at audit start | clean (`git status --porcelain` empty) |
| `unshare -Urn true` | **succeeds** — the lab can run on this machine |
| Python | 3.12.15 (`.venv`) |
| Linux | Omarchy, x86_64 |
| Date of audit | 2026-11-10 |

---

## 1. What the system actually is

The code, not the documents, defines the system.

* **Network lab.** `network-lab/run_lab.py` starts a Ryu controller
  (`network-lab/controller/ryu_controller.py`, OpenFlow 1.3) and a Mininet
  topology, inside a user network namespace, and serves a small HTTP API over
  a **unix domain socket** (`/tmp/sdn-lab.sock`). No TCP port is opened for
  network control.
* **Topology.** `network-lab/topologies/multipath.py`: four hosts
  (h1 video source, h2 background source, h3 voice source, h4 video sink) and
  four switches (s1–s4) with **five** switch-to-switch links:
  `s1-s2` (4 ms), `s2-s4` (4 ms), `s1-s3` (5 ms), `s2-s3` (4 ms), `s3-s4`
  (7 ms), plus four host access edges. There are **four** simple paths from
  s1 to s4, not two.
* **Datapath.** Open vSwitch is not installed on this machine, so the lab
  falls back to the project's own pure-Python OpenFlow 1.3 switch
  (`network-lab/topologies/of13switch.py`). The backend reports this
  truthfully in `/health` (`switch_backend: of13-user-space`).
* **Agent.** `services/api/app/agents/graph.py` compiles a 13-node LangGraph
  state graph with three deterministic gates
  (`services/api/app/safety/gates.py`) and a request guardrail
  (`services/api/app/safety/guardrails.py`).
* **LLM.** `services/api/app/services/llm.py` wraps Groq. In this checkout
  `GROQ_API_KEY` is the placeholder `replace_with_your_key`, so the system
  runs in **fully deterministic mode** and says so in the run record
  (`intent_detected.payload.parsed_by = "GROQ_API_KEY not configured; used
  deterministic intent parsing"`). Every result below was produced in that
  mode.

### The three gates, as implemented

| Gate | Function | Decision |
| --- | --- | --- |
| 1 — Evidence | `assess_evidence` (`safety/gates.py`) | `sufficient` / `stale` / `insufficient` / `unavailable` |
| 2 — Safety | `validate_reroute` (`safety/gates.py`) | 9 named checks, `approved` + risk tier |
| 3 — Verification | `verify_recovery` (`safety/gates.py`) | `success` / `partial` / `failed` / `unverified` |

The nine Gate-2 checks are `operation_allowlisted`, `endpoints_exist`,
`path_well_formed`, `hop_budget`, `all_links_operational`,
`capacity_respected`, `protected_flows_unmodified`, `flow_scope`,
`request_not_expired`. `MAX_PATH_HOPS = 6`;
`CONGESTION_UTILIZATION_THRESHOLD = 0.8`;
`TELEMETRY_MAX_AGE_SECONDS = 5`; `EVIDENCE_MIN_CONFIDENCE = 0.6`.

### The violation rule

`agents/workflow.py::_is_violated` treats a flow as violated when **at least
25 %** of its individual ping samples exceed the target
(`VIOLATION_SAMPLE_FRACTION = 0.25`), or when any packet was lost. The
mean alone is not used when the samples are available.

---

## 2. Claims checked, and what is true

### 2.1 Verified claims

| Claim | Verdict | Evidence |
| --- | --- | --- |
| The agent may propose; only verified evidence decides | **true** | `safety/gates.py` contains no model call; `llm.py` produces prose only. |
| Nine deterministic safety checks | **true** | `validate_reroute`, `safety/gates.py:301-441`; 9 checks confirmed in the recorded runs (`scenario_summary.csv`). |
| A successful command is never a successful recovery | **true** | `verify_recovery` returns `unverified` when `after is None`; exercised live in the `lab_offline` and `healthy_impossible_target` scenarios. |
| Unknown is never treated as healthy | **true** | `_is_violated` returns `None` for a missing measurement; `assess_evidence` returns `unavailable`. |
| The LLM cannot bypass the validator | **true** | The mutating tool `apply_reroute` is only reachable from the `execute` node, which requires `validation.approved`. |
| The graph has explicit nodes and conditional routing | **true** | `graph.py:71-117`; 13 nodes, verified by `test_graph_includes_all_ten_steps`. |
| The lab exposes no TCP control port | **true** | `run_lab.py` binds `AF_UNIX`; verified with `ss -lnt` and the socket file. |
| Pydantic models forbid extra fields | **true** | `StrictModel.model_config = {"extra": "forbid", "frozen": True}`. |
| Live telemetry: ICMP latency + per-link counters from the datapath | **true** | `topologies/telemetry.py`; counters verified against `OFPT_PORT_STATS` replies. |
| The `s2-s4` link is the controlled bottleneck | **true** | `multipath.py`, `run_lab.py::start_congestion`. |
| The protected voice flow is `h3→h4` | **true** | `multipath.py`, measured at 4.7–5.5 ms in every phase. |

### 2.2 Wrong claims, and what was done about them

| # | Claim (source) | Reality | Action |
| --- | --- | --- | --- |
| C1 | "76 backend tests" (`report.tex` §Testing; `README.md`) | **95** tests pass (78 pre-existing + 17 added by this audit); 5 live-lab tests also pass. | Report the measured counts. New counts in the paper and `INSTRUCTIONS.md`. |
| C2 | Baseline video latency "24.9 ms" (`report.tex` §Results) | Measured **30.1 ms** (mean of ten samples, `measurements.csv`). | Paper quotes the measured value. |
| C3 | "Congestion at 400 Mbps → 26.7 ms" (`report.tex` §Results) | The 400 Mbps rate is **recorded and ignored**. No traffic is generated. The measured congested latency is **110.4 ms**. | Paper describes the mechanism accurately (see C4). |
| C4 | "A UDP generator that loads `s2-s4`"; "The traffic itself comes from `network-lab/traffic_gen.py`"; "its exit status is checked before the scenario is recorded"; "the scenario now reports failure if the generator does not start" (`report.tex` §5.1, §Interpretation) | **False.** `start_congestion` applies a **netem delay of +40 ms** to the link and records the requested rate. It starts no process, `self.traffic_processes` is always empty, and `network-lab/traffic_gen.py` is **dead code** — nothing imports or runs it. | The docstring in `run_lab.py::start_congestion` was corrected to describe the delay injection. The paper states the mechanism precisely and lists it as a limitation. |
| C5 | "Link utilisation rose from 0 % to approximately 9 %" (`report.tex` §Interpretation) | Utilisation **stays at 0.000 %**: the only traffic is 10 ICMP packets per sample (~100 bytes), and no offered load exists. Verified in `link_utilisation.csv` (every entry 0.0). | The paper reports the measured utilisation and explains why the congestion detector cannot fire on utilisation in this lab. |
| C6 | "Heavy overload: 40.9 ms, peak 51.7 ms, 28 % loss" (`report.tex` §Results) | **Not reproducible.** The code has no overload scenario and no mechanism that can produce packet loss. | Removed. The paper reports the scenarios that exist. |
| C7 | "Each phase was sampled five times and the mean reported" (`report.tex` §Method) | No such sampling existed. | The harness now samples every phase and reports mean, median, standard deviation, min, max and the full per-sample distribution, not just a mean. |
| C8 | Topology diagram (`report.tex` §5.1, `README.md`) | The diagram shows h1 attached to **both** s1 and s3, and omits the `s2-s3` diagonal. The real topology has h1 on s1 only and five switch links. | New figure `fig_topology` is drawn from the lab's own `/v1/topology`. |
| C9 | "`s1-s2-s4` at 10 ms and `s1-s3-s4` at 20 ms" (`report.tex` §5.1) | Configured one-way delays are **8 ms** and **12 ms**. Measured RTTs are 29.6 ms and 39.2 ms (the difference is the datapath's own processing cost). | Paper gives both numbers, labelled. |
| C10 | The controller's link table "mirrors multipath.py exactly"; "the lab cross-checks them at startup; if they ever drift, the lab refuses to run" (`ryu_controller.py` comment) | The two tables **had drifted** (5/5/8/5/12 vs 4/4/5/4/7) and **no cross-check existed**. | Tables aligned; a real startup cross-check was written (`run_lab.py::_assert_controller_topology_matches`) and it fails the lab if they disagree. Verified: the lab log now prints `controller topology mirror matches the lab topology (5 links)`. |
| C11 | "The agent identified the bottleneck" (`report.tex` §Interpretation) | The diagnosis reported **"Suspected bottleneck link(s): none identified"** while the agent was about to reroute around `s2-s4`. `_infer_bottleneck_links` was called without the flow's path, so the path-delay fallback could not fire. | **Fixed** (`workflow.py::diagnose_and_plan`). The diagnosis now reads `Suspected bottleneck link(s): s1-s2, s2-s4`. |
| C12 | "correctly rejecting the shorter 18 ms alternative that would have routed straight back through the bottleneck" (`report.tex` §Interpretation) | The **safety gate did not reject it.** `validate_reroute` approved a reroute straight over the congested link, because the domain snapshot dropped the lab's link status and the capacity check compared utilisation (0 %) against an 80 % threshold. Only the *planner's* ranking kept that path out. | **Fixed**: the snapshot keeps the lab's link status, and `capacity_respected` now also fails on any link the lab reports as congested. Verified live: the gate now rejects the bottleneck path and approves the clean one. |
| C13 | "All nine safety checks passed" (`report.tex` §Interpretation) | True for the selected path (verified: 9/9 in `scenario_summary.csv`). | Kept, with the source of the recorded value stated. |
| C14 | "the verification gate then measured 51.6 ms ... and returned `recovery_failed`" (`report.tex` §Interpretation) | Not reproducible with the current scenario. The reroute now measures **41.0 ms** against a 50 ms target and the run ends `intent_satisfied`. A *real* `recovery_failed` is produced by a different, reproducible scenario (an unreachable target on a healthy network: 30.2 ms → 36.9 ms → `recovery_failed`). | The paper reports both: the verified recovery, and a genuinely reproducible failed recovery. |
| C15 | "The agent correctly abstains on a healthy network" (`report.tex`) | True: `intent_satisfied`, **no** flow rules installed, no network change. Verified. | Kept, with the measurement attached. |
| C16 | The dashboard shows the protected voice flow being protected (`report.tex` §5.2) | The default intent declares **no protected flow**, so nothing was measured — yet the run's answer asserted "the protected voice flow stayed on its path". | **Fixed**: the answer only mentions a protected flow when one was declared and measured; `verify_recovery` no longer claims protected flows were checked when none were. |
| C17 | `README.md`: "durable persistence ... not yet written to"; "proactive monitoring ... implemented as a placeholder endpoint only" | **False.** `app/db/repository.py` persists runs, events, verification results and audit entries (a round-trip test exists); `app/services/proactive_monitor.py` is a real polling watchdog. | README left unchanged (out of scope); the paper and this audit state the real position. The monitor's *sampling* was broken — see F9. |
| C18 | `README.md`: "The user-space datapath plateaus near 40 Mbps"; "Congestion may not breach 50 ms on its own" | Stale. The current scenario breaches 50 ms reliably (110.9 ms), because the fault is a delay injection, not a load. | Noted; not used in the paper. |
| C19 | `README.md`/`PROJECT_SPEC.md` endpoint list (`POST /api/lab/scenarios/reset` etc.) | The lab routes are mounted under **`/v1`** (`/v1/reset`, `/v1/scenarios/congestion`), the run routes under **`/api`**. There is no `/api/lab/...` on the backend (that path exists only as a Next.js rewrite). | The paper states the real endpoints; `INSTRUCTIONS.md` lists them. |
| C20 | `report.tex` §6: "the only mutating tool refuses to act on an unvalidated action" | `apply_reroute` does **no** validation; it trusts the caller. The separation is real (only the `execute` node calls it, and that node requires `validation.approved`), but the wording overstates the tool. | The paper describes the actual structure: the tool layer is an allow-list, and authorisation is enforced by the graph, not by the tool. |

### 2.3 Unverifiable / not claimed

* **Offered-load behaviour.** The lab has no traffic generator wired in, so
  nothing in this submission says anything about behaviour under real
  bandwidth saturation, queueing delay or packet loss. Stated as a
  limitation.
* **Multi-user, multi-host operation.** The system is single-host and
  single-user by design (`README.md`); no claim is made about scale.
* **PostgreSQL.** `DATABASE_URL` in this checkout points at SQLite. The
  models are declared portable (`JSONB().with_variant(JSON(), "sqlite")`),
  but only the SQLite path was exercised.
* **Frontend end-to-end tests.** The Playwright suite starts its own dev
  server and was not re-run as part of this audit; the backend suite and the
  live-lab suite were. The one Playwright test that claims to verify the
  topology in fact asserts that the *placeholder* ("Waiting for live network
  telemetry") is visible, so it cannot fail when the topology fails to load.

---

## 3. Defects found in the code and fixed

Every fix below has a regression test in
`services/api/tests/test_regressions.py` (21 tests) or is verified by a
recorded measurement.

| # | File | Defect | Fix | Verification |
| --- | --- | --- | --- | --- |
| F1 | `app/core/config.py` | `PROJECT_ROOT` was `parents[3]` (= `services/`), so the repository's `.env` was never loaded and the default SQLite path was `services/sdn_dev.db`. A relative `DATABASE_URL` was resolved against the *working directory*. | `parents[4]`; relative SQLite paths are anchored to the project root. | `test_project_root_is_the_repository_root`, `test_relative_sqlite_urls_are_anchored_to_the_project_root`. |
| F2 | `app/tools/__init__.py` | The projection onto `LinkTelemetry` dropped the lab's `status`, so a congested link was reported as **up** in the domain snapshot — and Gate 2's `all_links_operational` check could not see the bottleneck. | The status is carried through. | `test_snapshot_projection_keeps_the_lab_link_status`; live re-check. |
| F3 | `app/safety/gates.py` | `capacity_respected` only compared utilisation against the 80 % threshold, so a reroute over the congested link was **approved** (utilisation is 0 % in this lab). | A link the lab reports as congested now fails the check. | `test_validator_rejects_a_reroute_over_the_congested_link`, `test_validator_still_approves_the_congestion_free_path`. |
| F4 | `app/agents/workflow.py` | `diagnose_and_plan` called `_infer_bottleneck_links` without the flow's path, so the path-delay fallback never fired and the diagnosis said "none identified". | The current path is passed in. | Live: the diagnosis now names `s1-s2, s2-s4`. |
| F5 | `app/agents/graph.py` | `_validation_route` ignored `validation.requires_approval`, so an action above the unattended risk threshold was routed to `execute`; the hold then happened *inside* `execute` and the run fell through to the verification gate and was reported as `unverified` instead of `awaiting_approval`. The `hold` node was unreachable. | The route now checks `requires_approval`; `hold` reports its own outcome. | `test_approved_but_risky_action_is_held_not_executed`. |
| F6 | `app/agents/graph.py` | `_abstain` overwrote the concrete `lab_offline` outcome with `abstained_insufficient_evidence`, hiding the real reason. | `lab_offline` is preserved. | Live: the `lab_offline` scenario now ends `lab_offline`. |
| F7 | `app/agents/workflow.py` | `verify_and_explain` overwrote a failed execution's outcome with `unverified`. | A terminal outcome set by an earlier node is preserved. | `test_lab_offline_outcome_is_not_overwritten_by_unverified`. |
| F8 | `app/api/routes/runs.py` | The deterministic answer asserted "the protected voice flow stayed on its path" although no protected flow was declared or measured, and it claimed "post-change telemetry could not be collected" when the *action* was what had failed. | Both statements are now conditional on what actually happened. | `test_answer_does_not_claim_an_unmeasured_protected_flow`, `test_unverified_answer_distinguishes_a_failed_action_from_missing_telemetry`. |
| F9 | `app/services/proactive_monitor.py` | The monitor called `lab_client.snapshot(latency_count=3)`, but that method takes no arguments, so **every poll raised `TypeError`**; it also validated the lab's raw snapshot as a `NetworkSnapshot`, which cannot work (the raw payload has no topology and keys its flows by `src->dst`). The monitor therefore never once read a measurement. It also compared the *mean* latency, while the agent compares the sample distribution. | The monitor goes through the same tool the agent uses, and uses the agent's own violation rule. | `test_proactive_monitor_samples_the_network`, `test_monitor_and_agent_agree_on_what_violated_means`. |
| F10 | `app/services/lab_bridge.py` | The client captured the lab's token once, at construction. The lab generates a new token on every start, so after a lab restart every *mutation* failed with HTTP 401 while reads kept working — which looked like "the network refused the reroute". | The token is re-read and the request retried once on a 401. | Exercised by the harness, which restarts the lab between scenarios. |
| F11 | `app/db/repository.py` | The `action_records` table existed but nothing ever wrote to it, so the audit trail kept the events and the verification but not the proposed action or its validation. | `ActionRecord` rows are now written. | `scenario_summary.csv` + `audit_tables.csv`. |
| F12 | `app/agents/graph.py` | `_evidence_route` tested for an `EvidenceStatus` value named `provisional` that does not exist. | Dead branch removed. | `test_evidence_status_enum_has_no_provisional_member`. |
| F13 | `network-lab/controller/ryu_controller.py` | The controller's link table had drifted from the topology it was mirroring, and the comment claimed a startup cross-check that did not exist. | Tables aligned; a real cross-check added in `run_lab.py`. | `test_controller_topology_mirror_matches_the_lab_topology`; the lab log line. |
| F14 | `network-lab/controller/ryu_controller.py` | `install_default_paths` installed the **forward** switch path for the reverse direction too, so the reverse direction was pinned to the wrong egress port; it also left stale priority-100 overrides in the table, where they shadow the baseline rule (equal priority, insertion order decides). | The reverse direction gets the reversed path and its own egress port; stale overrides are removed first. | Correct by construction; the datapath currently ignores these rules (see U1), so the effect is latent rather than observable. |
| F15 | `network-lab/run_lab.py` | The lab reported the *configured* link delay even while the congestion scenario was degrading the link, so telemetry could not tell which link the latency had accrued on. | The effective delay is reported while a scenario is active. | The diagnosis now names `s2-s4`. |
| F16 | `network-lab/run_lab.py` | `start_congestion`'s docstring claimed a traffic generator that does not exist; `topology()` reported every link as up even while a scenario held one down. | Docstring corrected; `topology()` marks congested links down. | See C4 and F2. |

### 3.1 Defects found but **not** fixed

These are reported rather than silently worked around.

| # | Defect | Impact | Why not fixed |
| --- | --- | --- | --- |
| U1 | **The user-space datapath does not apply the controller's priority-100 rules.** Every ICMP frame misses the flow table and is sent to the controller as a `PACKET_IN`; the controller then forwards it. The effective forwarding path is decided by the controller's L2 learning state, not by the table. | The reroute still works (verified end to end), but the data plane never holds the rule the controller installed. The lab's flow-table dump shows the rules present; the switch's own frame log shows `FLOW-MATCH` only for ARP. | The matching logic is correct in isolation (`of13switch.FlowEntry.matches` returns `True` for a synthetic ICMP frame and the priority-100 rule), and the PACKET_IN wire format round-trips correctly through Ryu's parser. The remaining discrepancy is in the datapath's rule installation and was not isolated within the time available. Fixing it means changing the datapath, which would invalidate the measurements in this submission. |
| U2 | **The lab's reset does not restore the baseline forwarding path.** It restores the link delays and clears the controller's override table, but the switches keep the priority-1 learning rules they were taught during a reroute (they have no idle timeout). A reset after a reroute leaves the flow on the old path while the controller reports the baseline one. Measured: after a reroute and a reset, the video latency stays at ~33 ms instead of returning to ~29 ms. | Scenarios interfere with each other. | **Superseded by F17.** With the flow-mod decoder fix, the datapath actually matches the priority-100 rules the controller installs, so the reset's delete-then-reinstall now takes effect. Measured after the fix: after a reroute + reset, the video RTT is back at 17.3 ms (matching a freshly built lab). The harness still restarts between scenarios for bit-identical baselines; this is no longer hiding a broken reset. The original two abandoned remedies and the broadcast storm are left in place as historical record. |
| U3 | `network-lab/traffic_gen.py` is dead code and the `rate_mbps` parameter of the congestion scenario is ignored. | The "congestion" is a latency fault, not a load fault. No claim about offered-load behaviour is possible. | Wiring a generator in would change the system under study; the honest thing is to describe the mechanism and list it as a limitation. |
| U4 | `scripts/start_lab.sh` references `network-lab/scripts/mn-runner.py`, which does not exist. `scripts/setup-ryu.sh` expects a `network-lab/patches/` directory that is not present. `.cursor/skills/verify-netmind/` drives a macOS/OrbStack environment (`orbctl`, `sdn-lab.orb.local`) that does not exist on this machine. | Three helper scripts are unusable as written. | Out of scope for this submission; recorded so nobody follows them expecting them to work. |
| U5 | The agent's `tool_calls` state field is never populated, so `GET /api/runs/{id}` returns an empty list even though the tools ran. The calls *are* recorded, as `tool_started`/`tool_completed` events in the run's event history and in the `run_events` table. | Cosmetic inaccuracy in one API field. | Low value relative to the risk of touching the graph's state plumbing. |

---

## 4. Verified measurements

Produced by `final_submission/scripts/reproduce.sh`; the raw data is in
`final_submission/data/`. Every number below is a mean over the recorded
samples, and the per-sample distributions are in `measurements.csv`.

| Scenario | Intent | Trials | Video latency before | Video latency after | Path installed | Outcome |
| --- | --- | --- | --- | --- | --- | --- |
| `baseline_healthy` | 50 ms | 1 | 29.7 ms | — (no change) | — | `intent_satisfied` |
| `congestion_recovery` | 50 ms | **5** | 109.5–112.9 ms | **36.6–41.6 ms** (mean 38.7) | `s1-s3-s4` | `intent_satisfied` (5/5) |
| `congestion_partial` | 30 ms | 1 | 112.1 ms | 39.1 ms | `s1-s3-s4` | `partial_improvement` |
| `healthy_impossible_target` | 20 ms | 1 | 29.7 ms | 36.3 ms | `s1-s3-s4` | `recovery_failed` |
| `guardrail_blocked` | — | 1 | 29.7 ms | — (no change) | — | `guardrail_blocked` |
| `lab_offline` | 50 ms | 1 | — | — | — | `lab_offline` |

The "before" column is the latency the verification gate measured on its own
snapshot; the phase means over the five sampled snapshots are 29.7 ms (S1),
109.8 ms (S2), 110.4 ms (S3), 29.8 ms (S4) and 29.7 ms (S5). The two
disagree slightly because they are different measurements of the same
condition, which is why both are reported.

* The protected voice flow (`h3→h4`) stayed between 4.2 ms and 6.3 ms in every
  phase (mean 4.9 ms).
* Link utilisation was **0.000 %** in every phase and on every link.
* The `s2-s4` link carried the whole video flow before the reroute and none
  of it afterwards (verified against the datapath's own port counters).
* The ranking ablation measured **41.6 ms** for the path the system's rule
  selects (`s1-s3-s4`) against **109.8 ms** for the path the shortest-path-only
  rule selects (`s1-s2-s4`, which crosses the degraded link).
* Always acting on the healthy network would have moved the flow from
  29.5 ms to 36.3 ms (+23 %).

Test suites, run by the harness:

```
services/api:  python -m pytest tests -m "not live"   ->  122 passed, 5 deselected
services/api:  python -m pytest tests -m "live"        ->    5 passed
```

(122 = 78 pre-existing + 17 regression tests added by this audit + 27
equation tests that check the paper against the implementation.)

### 4.1 Clean-checkout reproduction

`final_submission/submission/reproducibility.zip` was extracted into an empty
directory, the vendored Ryu checkout and the virtualenv were supplied (both
are excluded from the package: the first is a third-party checkout, the second
is a build artefact), and the single command was run there:

```
bash final_submission/scripts/reproduce.sh
```

It completed all seven steps, produced all six scenarios (the recovery
scenario as five independent trials) and all seven figures, and reported the
same test counts as the original tree (`122 passed`, `5 passed`). The
outcomes were **identical** in both runs; the measured latencies differ by
the run-to-run noise of a live network:

| Scenario | Outcome (both runs) | Before (shipped / clean) | After (shipped / clean) |
| --- | --- | --- | --- |
| `baseline_healthy` | `intent_satisfied` | — | — |
| `congestion_recovery` (5 trials) | `intent_satisfied` | 109.5 / 110.7 ms | 36.6 / 40.6 ms |
| `congestion_partial` | `partial_improvement` | 112.1 / 114.3 ms | 39.1 / 41.4 ms |
| `healthy_impossible_target` | `recovery_failed` | 29.7 / 29.0 ms | 36.3 / 42.1 ms |
| `guardrail_blocked` | `guardrail_blocked` | — | — |
| `lab_offline` | `lab_offline` | — | — |

The differences are a few milliseconds on values of 30--110~ms, which is
what one expects from ICMP probes on a shared machine. They are the reason
the paper reports distributions rather than single values, and the reason
the generated tables carry the per-phase mean, median, standard deviation
and peak alongside the raw samples.

---

## 4.1a The recovery scenario is sensitive to measurement noise

The verification gate compares the post-action latency with the target. That
latency is the mean of ten ICMP samples, and on this machine individual
samples are occasionally inflated to 50–90 ms by host scheduling (the
datapath is a pure-Python switch and the probes run in a namespace on a
shared box). One sample in ten barely moves the mean; three do.

Observed across the trials: the repaired flow's latency is **36.6–41.6 ms**
and every trial reported a verified recovery, but in one earlier trial
three of the ten post-action samples were inflated to about 90 ms, the mean
rose to 53.7 ms, and the gate reported `partial_improvement` instead.

This is the gate behaving as specified, not a failure: since an earlier
revision, both gates share one predicate, `safety.gates.latency_violation`,
which treats a flow as violating when at least a quarter of its samples
exceed the target (the same rule the paper's Eq. (4) states). It is also why
the recovery scenario is now run as **five independent trials** and the
paper reports the distribution rather than a single number. The measurement
is a real measurement; it is the measurement host, not the network, that is
noisy.

## 4.2 Second pass: the paper's equations and the workflow diagram

After the first draft of the paper was compiled, every equation and every
graph edge was re-checked against the implementation. Five defects were
found in the paper itself and corrected; one of them was a real
mis-statement of what the code does.

| # | Was | Now | Why it mattered |
| --- | --- | --- | --- |
| P1 | Eq. (4) said the violation rule falls back on ``the reported latency'' when there are fewer than two samples, but did not distinguish the two fallbacks | The rule is written out in full, in the order the code evaluates it: absent/unmeasured $\to$ unknown; loss $\to$ violated; $\ge\gamma n$ samples over the target $\to$ violated or not; and, only when there is no distribution, the reported peak, then the reported latency | The published rule did not say what the code does when the lab reports fewer than two samples. It does now. |
| P2 | Eq. (7) collapsed the outcome rules into three cases | Written out as the ordered rule set the code implements, including the case the code treats separately: the latency meets the target but the loss does not | The old formulation made ``the latency is fine but the flow now loses packets'' look like a success. |
| P3 | Eq. (6) summed the delay over all edges of the path, including the host access edges | The sum is over the switch edges, and the access delay is stated separately | The code sums the switch edges only; the paper's version did not match. |
| P4 | The constraints were listed as six bullets | The nine named checks of `validate_reroute`, in the order the code names them | The paper claimed nine checks and listed six. |
| P5 | The workflow diagram overlapped its own branch arrows and labels | Rebuilt: the main flow on the left, the five terminal outcomes on the right, each labelled with the condition that selects it, and every edge of the compiled graph drawn | The first figure could not be read. |

The corrections are now **machine-checked**: `services/api/tests/
test_paper_equations.py` contains 27 tests that assert each equation
against the function it describes, that the validator emits exactly the nine
documented checks in order, and that the compiled graph contains exactly
the thirteen documented nodes and the documented edges. The suite is 122
tests, all passing.

Two further statements were checked and found to be right but were tightened:
the diagnosis names $s2$-$s4$ first (the degraded link, and the slowest on
the flow's path) and only then $s1$-$s2$; and the ranking *disqualifies*
candidates that cross the degraded link rather than removing them from the
candidate list.

## 4.3 Third pass: the peer-review round

A peer review of the submission (reproduced in full in the submission notes)
raised several substantive findings. The ones that were real and fixable were
fixed; several were, again, **real defects**---the most serious being in the
user-space datapath. The review also asked for a real offered-load scenario
and a no-oracle variant, both of which now exist and both of which the
agent handles correctly.

### Defects fixed

| # | File | Defect | Fix | Verification |
| --- | --- | --- | --- | --- |
| F17 | `network-lab/topologies/of13switch.py` | `_parse_flow_mod` forgot the OXM match padding, so the instruction decoder started in the middle of the match (a zeroed word that looked like instruction type 0, length 0) and **no rule ever had an action**. Every IPv4 data frame missed the flow table and was forwarded via the controller. Flows were installed, matched nothing, and never forwarded. | The match length is rounded up to the 8-byte wire alignment before the instruction decoder runs. | `network-lab/tests/test_of13switch.py` (4 tests). Live: the healthy RTT dropped from ~29.7 ms to ~17.3 ms; a reroute to `s1-s3-s4` now measurably moves the flow off `s1-s2-s4` in the *datapath*, not only in the controller's bookkeeping; after a reroute + reset, the RTT reproduces the ~17.3 ms baseline (25.4 ms → reset → 17.3 ms). |
| F18 | `app/safety/gates.py` | Safety Check 7 (`protected_flows_unmodified`) compared **node sets**. Because the protected voice flow and the video flow share destination `h4` (and switch `s4`), every possible candidate path intersected the protected flow's node set, so the check could never pass and the gate would refuse every legitimate repair. | The comparison is over **switch edges**: the action may not route over an inter-switch edge currently carrying a protected flow, and a protected flow whose own path uses no inter-switch edges cannot be diverted by a reroute. | `test_a_shared_destination_does_not_block_every_path`, `test_a_path_crossing_the_protected_flows_link_is_rejected`. |
| F19 | `app/safety/gates.py` | Gate 3 judged the post-action latency by `a <= τ` on the mean, not by the sample-distribution predicate of Eq. (4). The two gates could disagree about the same measurement. | Both gates call `safety.gates.latency_violation`; the evidence gate calls it via `agents.workflow._is_violated`. | `test_the_gate_uses_the_same_predicate_as_the_evidence_gate`. |
| F20 | `app/safety/gates.py` | Eq. (7) reported a recovery that broke a protected flow but improved the latency as `partial_improvement`, because the improvement test ran before the protected-flow test. | The protected-flow test now comes first: an improvement that also harms a protected flow is `failed`, not `partial`. | `test_a_protected_flow_breach_is_a_failure_even_after_an_improvement`. |
| F21 | `app/safety/gates.py`, `app/agents/workflow.py` | The planner installed the best-scoring candidate without checking whether it improved on the path the flow was on---so on a healthy network with an unreachable target it installed the slower alternative anyway (S4), and relied on the verification gate to catch the regression afterwards. | The planner now drops any candidate whose effective switch delay is not strictly less than the current path's, and reports `no_better_path` when every candidate fails only that test. | `test_the_path_delay_helper_sums_switch_edges_only`, `test_switch_nodes_strips_the_hosts`, and live: S4 now ends `no_better_path` with nothing installed. |
| F22 | `app/agents/workflow.py` | Check 8 `flow_scope` verified the path's endpoints against the *action's* declaration---an exact restatement of Check 3---so a well-formed reroute of the wrong flow passed. | Check 8 now verifies that the action's endpoints match the *intent's* endpoints (`SafetyPolicy.intent_src/intent_dst`). | `test_an_action_for_the_wrong_flow_is_rejected`, `test_the_right_flow_still_passes`. |
| F23 | `app/agents/workflow.py` | The default intent declared no protected flow at all, so the protected-flow machinery was never exercised by a real run. | The default intent now declares the protected voice flow `h3->h4`. | `test_the_protected_voice_flow_is_declared`. |
| F24 | `app/agents/workflow.py` | Protected flows are looked up by their id, but the lab names flows `F1..F3` while the schema's example uses `h3->h4`; protected-flow checks against the default intent silently found nothing. | The lookup now matches by id **or** by endpoints; path-comparison indexes both. | `test_a_shared_destination...` and the S2/S3/S4/S7/S8 runs, in which every `protected_flows=...` check passes. |
| F25 | `app/safety/gates.py` | The two gates kept private copies of "fraction of samples over target" logic that had already drifted. | A single shared predicate is used by both. | `test_the_two_gates_call_the_same_rule`. |
| F26 | `app/agents/workflow.py` | The verification gate compared the protected flow against the **action's** path, reporting every protected flow as "moved" whenever an action was installed. | Each protected flow is compared against the path it was actually on before the action. | `test_the_verification_gate_uses_the_same_predicate_as_the_evidence_gate` (behaviour verified via the live S2/S3/S7/S8 runs). |
| F27 | `scripts/run_experiments.py`, `network-lab/run_lab.py` | The audit's "S4 = recovery_failed" scenario is correct for the *code as written*, but the code was wrong to run it at all; the planner's "improvement" check was rebuilt, and the scenario now runs with a reachable-but-not-met 15 ms target and the agent declines. | Added `degraded_no_oracle` (S8, bottleneck flagged nowhere) and `offered_load_congestion` (S7, real background UDP saturating `s2-s4`) as first-class scenarios with recorded data (`final_submission/data/`). | Both scenarios complete and are included in the paper as S7 and S8. |

### Infrastructure added

* `final_submission/scripts/run_experiments.py` gained `background_load`
  (via the lab API) so the lab can be driven with real offered load.
  `run_lab.py` wires in `network-lab/traffic_gen.py` behind a scenario
  API (`POST /v1/scenarios/background_load` and `.../stop`), a stdlib UDP
  generator executed through `Node.popen` inside the host namespaces.
* The lab's `.env`-free startup path is documented in `INSTRUCTIONS.md`.
* `pytest network-lab/tests/` runs 4 datapath regression tests.
* The backend API exposes `POST /v1/scenarios/background_load` and
  `POST /v1/scenarios/background_load/stop`.

### Claims re-verified

* The healthy baseline: **17.3 ms** (over ~290 ICMP samples, zero loss, three
  runs of the baseline agree to within 0.1 ms).
* The verified recovery: 97.5 -> **25.8** ms on average over 5 independent
  trials (range 25.4-27.0).
* The partial improvement: 97.3 -> **25.5** ms (S3, 22 ms target).
* The decline on a healthy network: **no_better_path** (S4).
* The unflagged bottleneck: repaired 97.4 -> **25.7** ms (S8), no oracle.
* The offered-load scenario: repaired 97.0 -> **27.2** ms (S7).
* The utilisation telemetry reports ~55 % on the bottleneck during S7
  (previously exactly 0.000 %, which made the Eq. (6) utilisation
  tie-breaker useless). The 80 % congestion-threshold branch of the
  evidence gate still does not fire in this lab; it is exercised by the
  test suite, not live. This is reported, not hidden.
* The live-lab suite: 5 passed. The backend suite: 133 passed. The
  network-lab datapath suite: 4 passed.

## 4.4 Round 2: the second peer review

The second Google Deep Review (reproduced verbatim in
`GA_DEEP_REVIEW_ROUND2.md`/`.pdf`) raised five substantive issues: an
framing mismatch between "agentic LLM" and the evaluated deterministic
system; missing prior art; the S7 offered-load scenario's confounded faults
and sub-threshold utilisation; single-trial active scenarios; and a set of
mathematical, algorithmic and roadmap inconsistencies. Every item was
addressed, and the response letter is `REVISION_REPORT_ROUND2.pdf`. The
findings that were defects in the artifact itself are:

| # | File | Defect | Fix | Verification |
| --- | --- | --- | --- | --- |
| F28 | `services/api/app/agents/graph.py` | `_evidence_route` returned `satisfied` whenever the measured samples satisfied the target, **without** checking the evidence was fresh, sane or sufficient. A stale snapshot whose samples satisfy \tau terminated the run as a confident no-op. | Freshness, sanity and presence are now preconditions of the satisfied branch; a stale/insane/unavailable verdict routes to abstention regardless of the samples. The routing docstring and Algorithm 1 state the order explicitly. | `test_a_stale_snapshot_is_not_reported_as_satisfied` (services/api/tests/test_regressions.py). |
| F29 | `network-lab/run_lab.py`, `network-lab/scripts/`, `services/api/app/api/routes/lab.py`, `services/api/app/services/lab_bridge.py`, `final_submission/scripts/run_experiments.py` | The offered-load scenario (S7) sat far below the 80% utilisation threshold and shared S2's fault machinery, so the utilisation branches of the gates were never exercised by live byte counters and the scenario added nothing S2 did not already show. | S7 now reconfigures `s2-s4` to a configured capacity of 60 Mbit/s (telemetry-only: the port counters are real; the capacity is the emulated link rate), drives a 70 Mbit/s offered UDP stream across it, and injects the 40 ms netem delay only as the fault that breaches the intent — with **no** lab-side degraded flag (`report_degraded=False`). The measured utilisation is 80--94 % (mean 91 %). | S7 in `final_submission/data/` (see the S7 row of `scenario_aggregates.csv`); the utilisation figure/table regenerated. |
| F30 | `final_submission/scripts/run_experiments.py` | S3, S7 and S8 were each a single draw, while S2 was reported as five trials. | S3, S7 and S8 are now repeated five independent trials each, from freshly built labs; their verdict distributions are reported. | `scenario_summary.csv`, `scenario_aggregates.csv`. |
| F31 | `final_submission/scripts/run_experiments.py`, `services` | No wall-clock duration of the closed loop was recorded, so the freshness-policy discussion had no empirical anchor. | `wall_clock_s` is recorded per run (and per-trial in the aggregates), so the wall-clock of the full loop — pre-action snapshot, gates/planner, rule installation, post-action snapshot — is reported against Gate 1's five-second freshness policy. | `scenario_summary.csv`. |
| F32 | `services/api/app/safety/gates.py` | Eq. (4) in the paper declared a packet-loss branch (`true if p_f > 0`) that the code never had, and the reviewer found the resulting unreachability of Eq. (7)'s third row. | The paper's Eq. (4) is now exactly the code's latency-violation predicate; the loss constraint is evaluated where the code evaluates it — in the verification verdict, as the loss target `L` defined in Section V-D. | `test_the_two_gates_call_the_same_rule`; Eq. (4) now matches `latency_violation`. |
| P6 | Paper | Eq. (7) rows did not map to the recorded outcomes, and the loss-clause scoping was undefined. | L and Q are defined; `¬v(f⁺,I)` is stated to read only the latency branches; the 1-to-1 mapping is tabulated. | paper Section V-D |
| P7 | Paper | Algorithm 1 attached the improvement test to the wrong node; Figure 2's boxes did not map to the thirteen compiled nodes; the early exits' interaction with `finish` was undocumented; the risk-tier/`awaiting_approval` condition contradicted the code. | Algorithm 1 rewritten to the code's actual order (diagnose drops current-path candidates; validate applies the improvement pre-filter recorded as `not_an_improvement`; empty candidate set ⇒ `action_rejected`); §VI-A maps the 13 nodes and 10 outcomes to the boxes, incl. guardrail-in-intent, lab_offline preservation, and the fact that approved actions are always LOW risk so the hold path is unreachable here. | paper Sections VI-A..VI-C |
| P8 | Paper | Roadmap items 2, 4, 5 described completed work (datapath fix; S8; reset) as future work. | Rewritten to the actual remaining gaps (vSwitch/hardware port; Gate 2 decoupling from C_deg; keeping vs retiring the restart), item 6 extended to multi-flow cascading; NIKA/NetConfEval cited as grounding. | paper Section XI |
| P9 | Paper | Bibliography: RFC 9315 had a duplicated year; Mondal cited arXiv instead of HotNets '23; ONOS IM&R, Lumi, NetConfEval, Confucius, NIKA, Protogeros 2026 missing. | References fixed/added; all verified against publisher records. | paper References |
| P10 | Paper | Peak prose (17.3/103.1) disagreed with Table II (17.5/138.8); Table VI showed only the outlier direction and mislabelled the column. | Prose uses the macros from the data; Table VI gained the `yes/no` row (oscillation case) and the columns are defined in the caption; the boundary-probe methodology is stated. | paper Section IX-C |
| P11 | Paper | Ablation 3 claimed the always-act path "scored better"—contradicting Eq. (6). | Rephrased: the always-act variant takes the best-scoring remaining alternative after excluding the current path. | paper Section IX-D |

### Claims re-verified (this run)

* Healthy baseline: **17.3 ms**. Degraded bottleneck: **97.3–97.5 ms**.
* Recovery (S2, 5 trials): 25.4–25.6 ms, 5/5 `intent_satisfied`.
* Partial improvement (S3, 22 ms intent, 5 trials): 25.4–26.1 ms, 5/5 `partial_improvement`.
* No oracle (S8, 5 trials): 25.4–25.5 ms, 5/5 `intent_satisfied`.
* Offered load (S7, bottleneck at 60 Mbit/s): pre-action 97--117 ms,
  repaired 25.1--30.4 ms (mean 26.5), 5/5 `intent_satisfied`, **no**
  lab-side degraded flag. The utilisation branch now triggers on live byte
  counters: measured utilisation is 80.2--93.8 % (mean 91.1 %) of the
  reconfigured capacity, and the snapshot reports the link `up`. This closes
  the review's sharpest methodological objection: the utilisation branches
  of both gates are exercised by live traffic, not estimated.
* The utilisation telemetry previously reported exactly 0.000 %; it now
  reports a live, threshold-crossing signal in S7.
* The live-lab suite: 5 passed. The backend suite: 134 passed. The
  network-lab datapath suite: 4 passed.

## 4.5 Fourth pass: the model in the loop

The second-round review asked for an active-LLM replication. The machine
has a Groq key, so the whole protocol was re-run with the model active and
the artifacts it produced are in `final_submission/data-llm/`.

**Configuration.** `qwen/qwen3.8-27b` on Groq (the model named in
`LLM_MODEL`), intent extraction at temperature 0 (the code path is
`chat_json` with `response_format={"type": "json_object"}`); streamed prose
narration for the diagnosis at temperature 0.2. Nothing about the gates,
planner, guardrail or executor changed between the two runs: the headline
numbers in the paper come from the deterministic fallback; this section and
the paper's §VIII-B report the LLM-active replication.

**Protocol.** The same eight scenarios, the same operator messages, five
independent trials for S2/S3/S7/S8 (25 recorded runs, plus the post-restart
sentinel), each from a freshly built lab. Output lands in `data-llm/` (the
deterministic-fallback dataset is preserved byte-for-byte as
`data-fallback/`).

**What the model did, measured.**

* Intent parsing: the model extracted the typed intent correctly in
  **23 of 23** runs that reach the intent step (h1→h4; 50 ms in the
  default scenarios, 22 ms in S3, 15 ms in S4; video class;
  needs_clarification = false). The other two recorded runs are the S5
  guardrail refusal (refused before parsing) and the post-restart sentinel
  (no events). Zero parse failures; zero deterministic fallback invocations
  in the parse step were needed (any failure would be recorded in the run
  record).
* Diagnosis narration: the diagnosis summary was model-written in **21 of 21** runs that reach the diagnose step (streamed tokens, plain prose, no structured invention: e.g. S8 reported "all links show near-zero utilisation" when there is no flag, and S7 reported "93.1% at s2-s4" from the byte counters). Zero runs fell back to the deterministic template. The four runs that never reach diagnose: the healthy no-op, the S5 guardrail refusal, the S6 lab-offline abstention, and the sentinel.
* Outcomes: **identical to the fallback run in all 25 recorded runs** (24 executed scenarios plus the post-restart sentinel, which matches trivially): 16x intent_satisfied, 5x partial_improvement, 1x no_better_path, 1x guardrail_blocked, 1x lab_offline, 1x lab_restarted (sentinel). The LLM-active table is generated by `final_submission/scripts/make_llm_table.py`, not typed by hand. The gates: every installed reroute passed all nine checks; failed_checks is empty everywhere; S5 was refused before telemetry (no observe/evidence/diagnose events).
* Gate behaviour: unchanged — every installed reroute passed all nine
  Gate 2 checks, and the S5 adversarial request was refused by the
  deterministic guardrail before any telemetry was read, as before.
* Cost: the closed loop averages **7.1 s** per run with the model active (max 12.2 s; fallback 5.7–6.2 s). The evidence gate's five-second freshness policy governs individual measurements, not the loop.

**What this does not show.** The boundary against an adversarial,
ambiguous, or confidently-wrong model — i.e., intent parsing under attack —
remains untested. A model that emits a syntactically valid,
topology-valid, semantically unintended intent would pass every check this
system has. That is and remains future work (roadmap item 1); the claim
made in §VIII-B is only: "with a compliant model, the outcomes do not
change."

**Anything found and fixed.** No defects in the model path were found in this replication; the only defect fixed during integration was in the table generator itself (it hardcoded a 50 ms intent when checking parse success, miscounting S3's 22 ms and S4's 15 ms — fixed to check each scenario's actual intent; `make_llm_table.py`). The model adds about one second of wall-clock per run (12.2 s max vs 6.2 s fallback), i.e. roughly 30 seconds of model time across the whole 25-run protocol.

## 4.6 Fifth pass: the Round 3 peer review

The third Google Deep Review (reproduced verbatim in
`GA_DEEP_REVIEW_ROUND3.md`/`.pdf`) found four defects in the artifact and
one class of specification gaps. All four defects are fixed, with
regression tests; the specification gaps are closed in the paper.

### Defects found by our own adversarial probe (and fixed)

The review pointed out that the eight canonical messages could not
distinguish a model from a keyword parser, and asked for a probe of the
boundary itself. We wrote one
(`final_submission/scripts/run_boundary_probe.py`) and it immediately found
three of our own bugs:

| # | File | Defect | Fix | Verification |
| --- | --- | --- | --- | --- |
| F33 | `app/safety/guardrails.py` | The `safety_bypass` rule named whole subsystems ("safety", "validator", "gate", "approval", "policy"), so a request phrased against one of the nine **named** checks -- "disable the capacity check" -- passed the request screen untouched and was treated as an ordinary (harmless) reroute request. | The object alternation now also covers `check`, `checks`, `constraint`, `constraints`, `protection`, so a named check cannot be switched off by name. | `test_a_request_to_disable_a_named_check_is_refused` (services/api/tests/test_regressions.py); the probe's `gate-override` case now records `guardrail_blocked`. |
| F34 | `app/agents/graph.py` | The clarification branch set no outcome of its own, so a run that correctly asked the operator a question was filed as `unknown` -- indistinguishable from a measurement failure. | The `stop` terminal records `clarification` when the state carries a clarification request and no earlier outcome. | `test_a_clarification_request_is_recorded_as_its_own_outcome`; the probe's `underspecified` case now records `clarification`. |
| F35 | `app/agents/workflow.py`, `app/agents/graph.py` | Gate 1 judged the intent with the latency predicate alone, so a flow whose packet loss exceeded a declared loss target was reported as `intent_satisfied`, while Gate 3 would have called the same network state `partial_improvement` -- exactly the inter-gate drift the shared predicate exists to prevent. | Gate 1's satisfaction test now also requires the loss predicate to hold; a flow above its loss target is treated as violated and goes to diagnosis. (The evaluated intent declares no loss target for the video flow, so no evaluated scenario changes behaviour.) | `test_a_lossy_flow_is_not_reported_as_a_satisfied_intent`. |

### What the boundary probe measured

`final_submission/data/boundary_probe.csv` (20 candidate paths put in
front of Gate 2 with the planner bypassed) and
`final_submission/data/prompt_robustness.csv` (six request shapes):

* The model, shown the effective-delay telemetry, proposed the
  congestion-free path in 8 of 8 attempts and Gate 2 approved all 8.
* Shown the topology *without* the telemetry, it proposed a path across
  the degraded bottleneck in 6 of 8 attempts. Gate 2 rejected every faulty
  proposal -- but through Check 5 (`all_links_operational`, because the
  lab's static topology still marks the held link down), **not** through
  Check 6, which cannot see an unflagged delay fault. That is the
  review's point, now measured rather than argued.
* Four hand-crafted unsafe proposals (across the degraded link, an
  unknown destination, a non-existent link, an action for another flow)
  were all rejected, each with its named failed checks.
* The excess-delay predicate the review proposes would have flagged
  exactly the 7 proposals that cross the degraded link and none of the
  others (6 of the 8 topology-only proposals, plus the one crafted path
  across the link): 7 of 7, with no false positives on this candidate
  set.
* Prompt robustness: a paraphrase was understood and executed; an
  underspecified request produced an explicit clarification; an
  out-of-topology request was handled without touching the network; and
  two refusal-shaped requests were blocked by the deterministic guardrail.

### Providers tried and rejected before the recorded run

The model-active replication went through four provider configurations
before the shipped record was made:

* `openai/gpt-oss-120b` (Groq, the first choice) — ran a complete first
  pass and a partial second pass before Groq's **200,000 tokens/day cap**
  for the organisation was exhausted. The fail-closed path absorbed each
  failure and every outcome was identical to the fallback evaluation.
* `openai/gpt-oss-20b` (Groq) — same org-wide cap, hit again on re-run.
* An NVIDIA NIM endpoint with a different key — most listed models return
  404 ("Function not found for account") for the free tier, the rest were
  unusable for our purpose (a 157-second reasoning model, one that
  answered with an invented JSON schema, one that timed out at 200 s).
  Rejected.
* `qwen/qwen3.8-27b` (Groq) — the cap is per-model, so this recorded a
  **completely model-active** run: the model parsed all 23 runs that
  reach the intent step, wrote the diagnosis in all 21 diagnosis runs, and
  the outcome distribution is identical to the fallback (25 of 25).

The provider config is one environment switch: `LLM_BASE_URL`,
`LLM_API_KEY`, `LLM_MODEL`. The `groq` SDK dependency was replaced with a
provider-agnostic OpenAI-compatible urllib client, so switching providers
needs no code change at all. `reproduce.sh` now **clears the LLM keys**
for the backend it starts, so the headline dataset is the deterministic
fallback on any machine, with or without credentials; the model-active
replication is a separate, opt-in run (the same commands with a key).

The fail-closed path is what makes this sample honest rather than
annoying: when the provider fails, the agent records *why* (`LLM
unavailable (...429...)` in the run's planning notes) and continues with
the deterministic parser and template, and no outcome flips.

### Specification gaps closed in the paper (no code change)

The improvement predicate `D(p) < D(p_cur)` is now Eq. (5a); the
evidence-sufficiency verdict is defined as a six-item conjunction; the
protected-flow tuple and its configured thresholds are stated; the
provenance of `d_e^eff` (a lab-reported value, not an inference) and the
`44 ms`/`48 ms` arithmetic are spelled out; the empty-candidate-set branch
and the `lab_offline` branch are visible in Algorithm 1; the outcome count
is corrected to eleven; Figure 2's caption maps its five red boxes to the
five compiled terminal nodes; and the related-work section now separates
deterministic closed-loop reconciliation, pre-deployment configuration
synthesis and model-centred orchestration instead of grouping them.

### Provenance of the probe recordings

Both tables were recorded after the guardrail, clarification and loss
fixes, in one session, against the shipped code and the shipped model.

### Still open after this round (and said so in the paper)

* The suspect set is conservative: Eq. (5) demands edge-disjointness from
  the whole current path, which can starve the planner on cut-edge
  topologies. Documented in Section X; the refinement (excess-delay
  criterion, or a top-k prefix) is roadmap item 4. It was *not* changed
  here, because an excess-delay-only suspect set would leave Gate 1 with
  no suspect to name on a healthy network, and the confident no-op of S1
  would become an abstention.
* S7 still uses an injected delay to breach the intent; the shaping
  experiment that would isolate an organic queueing-induced violation is
  roadmap item 3.
* The protected-flow check is vacuous in this topology (the voice flow
  traverses access edges only); making it non-vacuous requires a topology
  redesign, which is roadmap item 6.
* Gate 3 reports, it does not roll back.

## 5. Files changed outside `final_submission/`

| File | Change |
| --- | --- |
| `services/api/app/core/config.py` | F1 |
| `services/api/app/tools/__init__.py` | F2 |
| `services/api/app/safety/gates.py` | F3, and the protected-flow wording in Gate 3 |
| `services/api/app/safety/guardrails.py` | **F33** — a request to disable a *named* check is now refused |
| `services/api/app/agents/graph.py` | F5, F6, F12 |
| `services/api/app/agents/state.py` | added the `awaiting_approval` outcome |
| `services/api/app/agents/workflow.py` | F4, F7 |
| `services/api/app/api/routes/runs.py` | F8 |
| `services/api/app/services/proactive_monitor.py` | F9 |
| `services/api/app/services/lab_bridge.py` | F10 |
| `services/api/app/db/repository.py` | F11 |
| `services/api/tests/test_regressions.py` | **new** — 21 regression tests (F1--F35) |
| `services/api/tests/test_paper_equations.py` | **new** — 27 tests that check each equation of the paper against the function it describes |
| `network-lab/run_lab.py` | F15, F16, the topology cross-check, the qdisc diagnostics, the effective-delay reporting |
| `network-lab/controller/ryu_controller.py` | F13, F14 |
| `network-lab/topologies/multipath.py` | stale delay figures in the docstring |
| `README.md` | the install list omitted `matplotlib`, which the figure pipeline needs |
| `network-lab/run_lab.py` | **F17-related**: `start_background_load` + `/v1/scenarios/background_load[/stop]`, `report_degraded` flag for the no-oracle scenario |
| `network-lab/controller/ryu_controller.py` | unchanged in pass three |
| `network-lab/topologies/of13switch.py` | **F17** — the OXM match padding decoder fix |
| `services/api/app/agents/graph.py` | **F28** — `_evidence_route` returns `satisfied` only on sufficient evidence; **F34** — the clarification branch records its own outcome; **F35** — Gate 1's satisfaction test also requires the loss predicate |
| `network-lab/run_lab.py` | **F29** — `set_link_capacity` + `/v1/scenarios/link_capacity` (S7's reconfigured bottleneck) |
| `services/api/app/api/routes/lab.py` | **F29** — `/v1/scenarios/link_capacity` passthrough |
| `services/api/app/services/lab_bridge.py` | **F29** — `set_link_capacity` client |
| `final_submission/scripts/run_experiments.py` | **F30, F31** — S3/S7/S8 as five trials; per-run `wall_clock_s`; denser violation-rule sweep |
| `final_submission/scripts/make_paper_tables.py` | aggregates wall-clock macros; violation-rule table picks one row per direction; S6 note in caption |
| `final_submission/scripts/make_figures.py` | rotated scenario tick labels (no overlap) |
| `services/api/app/api/routes/lab.py` | `/v1/scenarios/background_load[/stop]`, `report_degraded` |
| `services/api/app/api/routes/runs.py` | answer text for `no_better_path` |
| `services/api/app/agents/workflow.py` | planner improvement check, shared latency predicate, protected-flow fix-ups |
| `apps/web/src/app/_shell/RunDetailsPanel.tsx` | renders `no_better_path` distinctly |
| `network-lab/tests/test_of13switch.py` | **new** — 4 datapath regression tests |
| `services/api/tests/test_review_round1.py` | **new** — 11 regression tests for the review round |

`report/report.tex` was **not** modified.
