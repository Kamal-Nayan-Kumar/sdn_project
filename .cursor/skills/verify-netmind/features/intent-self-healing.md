# Intent-based self-healing and safety gates

Translates human-stated network intents into deterministic diagnostic workflows, evaluates the three trustworthiness gates (Evidence, Safety, Verification), and reroutes affected flows across congestion-free paths.

## Sub-features

- `intent-parse` extracts latency SLO targets and flow identifiers from natural language.
- `gate1-evidence` checks measurement freshness and sanity; refuses action if evidence is inconclusive.
- `gate2-safety` verifies candidate paths and ensures protected flows (`h3->h4`) are never degraded.
- `gate3-verification` re-measures the network after flow modification; reports `recovery_failed` if latency does not improve.

## How to get to it (user POV)

- Enter intent prompt into the assistant chat input on the dashboard (e.g., `optimise latency so that it comes under 50 ms`).
- View the **Agent Activity** timeline and **Run Details** tabs in the sidebar.
- Send a POST request to `http://sdn-lab.orb.local:8000/api/runs` with JSON body `{"message": "optimise latency so that it comes under 50 ms"}`.

## Driving it with control.sh

Preconditions:
- Lab is running and congested on link `s2-s4`.
- Video flow `h1->h4` latency exceeds target SLO (> 50 ms).

- **Submit optimization intent.** Run:
  `RUN_ID=$(curl -s -X POST http://sdn-lab.orb.local:8000/api/runs -H "Content-Type: application/json" -d '{"message": "optimise latency so that it comes under 50 ms"}' | jq -r .run_id)`
- **Inspect run outcome.** Query `GET /api/runs/${RUN_ID}` after graph execution:
  `curl -s http://sdn-lab.orb.local:8000/api/runs/${RUN_ID} > artifacts/verify-netmind/agent-run.json`
- **Inspect gate audits.** Check `outcome`, `validation`, and `verification` in `agent-run.json`. Confirms Gate 1 (Evidence satisfied), Gate 2 (Safety validation approved), and Gate 3 (Verification intent satisfied).
- **Verify route state.** Run `.cursor/skills/verify-netmind/control.sh snapshot artifacts/verify-netmind/post-heal-snapshot.json`. Path for `h1->h4` reports `["s1", "s3", "s4"]`.

## Gotchas

- If video latency is already below 50 ms when the intent is stated, the agent correctly reports `intent_satisfied` (a confident no-op) rather than rerouting.
- Without a Groq API key configured, the LangGraph agent runs in deterministic mode and explicitly logs that the LLM was not used.
