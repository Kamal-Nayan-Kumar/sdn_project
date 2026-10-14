# Trustworthy Agentic AI for Intent-Based Self-Healing SDN

## Project Direction, Architecture, Implementation Plan, and OpenCode Build Specification

> **Purpose of this document:** This is the implementation brief for an
> AI coding agent such as OpenCode. Build a real, locally running SDN
> self-healing system with a polished dashboard, a trustworthy Agentic
> AI workflow, live network telemetry, streamed agent activity,
> persistent conversation history, and repeatable end-to-end tests.

------------------------------------------------------------------------

# 1. Final Project Title and Research Direction

## Project title

**Trustworthy Agentic AI for Intent-Based Self-Healing SDN**

## Core research question

How can an AI agent safely recover an SDN network when a user-defined
performance goal is violated, while ensuring that its decisions are
supported by sufficient evidence, its actions satisfy safety
constraints, and its recovery claims are verified against actual network
measurements?

## Specific problem to demonstrate

A network administrator wants to maintain video traffic latency below
**50 ms**. The network is initially healthy, but a link becomes
congested and video latency rises---for example, to **120 ms**.

The administrator opens the local dashboard and asks:

> Optimize the video traffic latency and keep it below 50 ms. Do not
> disrupt other traffic.

The system must:

1.  Detect the violated intent using real network measurements.
2.  Gather trustworthy evidence and identify the likely bottleneck.
3.  Determine whether it has enough evidence to propose a recovery
    action.
4.  Select a permitted alternative path.
5.  Validate that the action satisfies defined safety constraints.
6.  Execute the approved action through the SDN controller.
7.  Measure the network again and verify whether latency is below 50 ms.
8.  Explain what happened and retain an auditable record of the
    decision.

If the alternative route is unsafe, reject it. If telemetry is
unreliable, collect more evidence or abstain. If recovery fails, report
failure rather than inventing a successful result.

The central research contribution is **trustworthy recovery**, not
merely using an LLM to reroute traffic.

------------------------------------------------------------------------

# 2. Technology Stack

Everything except the external Groq model API will run locally on the
development machine.

## Frontend

-   Next.js
-   React
-   TypeScript
-   CSS/Tailwind CSS as appropriate
-   React Flow or a suitable SVG/canvas solution for interactive network
    topology
-   Server-Sent Events (SSE) for streamed agent activity

Responsibilities:

-   Interactive network topology
-   Live latency, packet loss, utilization, and protected-flow metrics
-   Chat interface and conversation history
-   Streamed agent activity timeline
-   Expandable tool-call cards
-   Evidence and safety validation results
-   Before/after measurements
-   Audit history
-   Network lab connection status
-   Scenario start and reset controls

## Backend

-   Python
-   FastAPI
-   Pydantic
-   LangGraph
-   Groq API client or compatible OpenAI-style client
-   Model: `openai/gpt-oss-120b`
-   NetworkX for path analysis
-   pandas where useful for metric analysis
-   SQLAlchemy
-   Alembic for database migrations
-   pytest for backend, safety, and integration tests

The Groq API key must be read from an environment variable. Do not
hardcode or commit it. The backend calls Groq; the browser must never
receive the secret API key.

## Network lab

-   Mininet
-   Open vSwitch (OVS)
-   Ryu SDN controller
-   OpenFlow
-   Linux traffic and measurement tools such as `iperf3`, `ping`, and
    other appropriate utilities

Use **Ryu + OpenFlow + Open vSwitch** as the primary controller setup.
Do not run Ryu and POX as simultaneous controllers for the same
switches. Existing POX work may be useful as a reference, but the new
project should have one clearly defined controller.

## Persistence

**Recommended default: local PostgreSQL.**

Use PostgreSQL locally if it is already installed or can be run through
Docker/Podman. Neon is not required because this project is not being
deployed. A local PostgreSQL database provides durable conversations,
agent events, run state, and audit history without sending this data to
a hosted database.

SQLite is an acceptable fallback for an early prototype if PostgreSQL is
unavailable, but PostgreSQL is preferred for the complete project
because it supports concurrent requests and LangGraph checkpoint
persistence well.

Possible local PostgreSQL options:

1.  Use an existing local PostgreSQL installation.
2.  Run PostgreSQL using Docker or Podman, if available.
3.  If neither is practical, use SQLite temporarily and document the
    limitations.

Do not create a Neon account or use a hosted database unless there is a
specific later requirement.

## Local development and orchestration

-   Run Next.js locally.
-   Run FastAPI and LangGraph locally.
-   Run PostgreSQL locally.
-   Run Mininet, Open vSwitch, and Ryu locally on the Omarchy Linux
    machine.
-   Use a local authenticated bridge only if the backend and network lab
    need to be isolated into separate processes.
-   Use a root-owned or appropriately privileged network-lab launcher
    only for the specific Mininet/OVS operations that require elevated
    privileges. Do not run the whole web application as root.

Suggested local addresses (configurable):

-   Frontend: `http://localhost:3000`
-   FastAPI: `http://127.0.0.1:8000`
-   FastAPI docs: `http://127.0.0.1:8000/docs`
-   Local PostgreSQL: `localhost:5432`
-   Ryu API/controller endpoints: bind to localhost or a trusted local
    interface where practical.

Do not expose the Ryu API or switch-control ports to an untrusted
network.

------------------------------------------------------------------------

# 3. Local Architecture

The project runs locally. The only expected external runtime dependency
is the Groq API used by the LLM.

``` mermaid
flowchart TD
    U[Network Administrator] --> UI[Next.js Dashboard<br/>localhost:3000]
    UI <-->|HTTP + SSE| API[FastAPI Backend<br/>localhost:8000]
    API --> LG[LangGraph Agent]
    LG --> GROQ[Groq API<br/>openai/gpt-oss-120b]
    LG --> SAFE[Deterministic Safety Engine]
    API <--> DB[(Local PostgreSQL)]
    API <--> BRIDGE[Local Network Tool Layer / Bridge]
    BRIDGE <--> RYU[Ryu SDN Controller]
    RYU <-->|OpenFlow| OVS[Open vSwitch]
    OVS <--> MN[Mininet Hosts and Links]
    MN --> TEL[Traffic and Network Telemetry]
    TEL --> BRIDGE
```

The LLM helps interpret intents, summarize evidence, diagnose likely
causes, and propose structured recovery plans. It does not have
unrestricted control over switches or the shell.

The safety engine and verification logic are ordinary deterministic
Python code. They must work even if the LLM returns an incorrect,
malformed, or overconfident answer.

## Local-only requirements

-   Do not configure Vercel, Render, Neon, or another hosted deployment.
-   Do not require a public domain or public webhook.
-   Do not expose local network-control services publicly.
-   Keep secrets in a local `.env` file ignored by Git.
-   Include `.env.example` with placeholders only.
-   Make the entire demo startable through documented local commands or
    a carefully designed local development script.
-   Clearly report when the Groq API or the local network lab is
    unavailable.
-   The network lab must not be replaced silently by mocked data.

------------------------------------------------------------------------

# 4. Core Problem Statement

Modern Software Defined Networks can be programmed to recover from
failures and Quality of Service (QoS) violations. However, integrating
LLMs and Agentic AI into network control introduces additional
challenges. An agent may diagnose a violation using incomplete or
unreliable telemetry, propose a recovery action that violates network
constraints, or declare success without confirming that the intended
network condition has been restored.

This project investigates a trustworthy, intent-based self-healing SDN
framework that:

1.  Evaluates the sufficiency of network evidence before action
    selection.
2.  Validates proposed actions against deterministic safety constraints
    before execution.
3.  Verifies recovery using fresh post-action telemetry.
4.  Records decisions, tool results, validation outcomes, and recovery
    measurements in an auditable history.

The framework aims to automate network recovery while maintaining
explicit safety conditions and distinguishing successful recovery from
partial improvement, failed recovery, and unverified outcomes.

## Example scenario

The administrator defines:

-   Traffic class: video
-   Source: H1
-   Destination: H4
-   Maximum latency: 50 ms
-   Protected traffic: voice or other explicitly protected flows

A controlled background load congests a link. Video latency rises above
50 ms and packet loss may increase.

The agent observes the network and identifies the likely bottleneck. It
checks that the telemetry is fresh and relevant, evaluates candidate
routes, creates a structured reroute action, and submits the action to
the deterministic safety validator. Only an approved action reaches the
Ryu controller.

After the route changes, the agent collects new telemetry and checks
whether the video intent is satisfied and protected traffic remains
within its allowed constraints.

The example values are targets or illustrative starting points. Actual
measurements must come from the running network lab; do not hardcode
expected outcomes.

------------------------------------------------------------------------

# 5. The Three Trustworthiness Gates

## Gate 1 --- Evidence sufficiency

Before selecting a recovery action, require:

-   Required measurements are present.
-   Measurements are recent enough for the configured policy.
-   Values pass sanity checks.
-   The affected flow and suspected bottleneck are identifiable.
-   The diagnosis is supported by topology and telemetry evidence.

If evidence is missing, stale, contradictory, or insufficient, refresh
the measurements or abstain. Do not let the LLM override the evidence
gate.

Example response:

> I detected a latency violation, but the telemetry for the suspected
> link is stale. I will refresh the measurements before selecting a
> recovery action.

Implement evidence assessment using deterministic checks wherever
possible. The LLM may help explain the evidence but must not be the sole
authority deciding whether the required measurements exist or are fresh.

## Gate 2 --- Deterministic action safety

Every proposed mutation must pass checks such as:

-   The operation is on an allow-list.
-   The source, destination, and path are valid.
-   All required links exist and are operational.
-   Capacity and configured network policies are respected.
-   Protected flows are not unintentionally modified.
-   The action is scoped to the intended flow.
-   The action parameters match a Pydantic schema.
-   The request is not expired, duplicated, or associated with the wrong
    run.

An LLM-generated statement that an action is safe is not authorization.
Only the deterministic validator may approve it.

For a semester project, demonstrate safety against an explicit set of
constraints. Do not claim to mathematically guarantee the absence of
every possible network failure.

## Gate 3 --- Recovery verification

After an approved action executes:

-   Collect fresh telemetry.
-   Compare measured latency against the intent.
-   Check packet loss and protected-flow performance.
-   Distinguish successful command execution from successful network
    recovery.
-   Retry only within strict limits.
-   Report a failed or unverified outcome when appropriate.

Example outcomes:

-   Latency = 35 ms and protected-flow checks pass: intent satisfied.
-   Latency = 65 ms: intent still violated.
-   Telemetry unavailable: recovery unverified.
-   Latency improves but remains above 50 ms: partial improvement, not
    successful recovery.

A missing measurement must never be interpreted as success.

------------------------------------------------------------------------

# 6. Agent Workflow Using LangGraph

Use explicit LangGraph nodes and conditional routing. The LLM must not
control the entire workflow without deterministic safeguards.

``` mermaid
flowchart TD
    A[User Intent or Proactive Alert] --> B[Parse Intent]
    B --> C[Observe Network]
    C --> D{Evidence Sufficient?}
    D -- No --> E[Refresh or Collect Evidence]
    E --> F{Evidence Retry Limit Reached?}
    F -- No --> C
    F -- Yes --> G[Abstain and Explain]
    D -- Yes --> H[Diagnose and Plan]
    H --> I[Generate Structured Action]
    I --> J[Deterministic Safety Validator]
    J --> K{Action Approved?}
    K -- No --> L[Reject and Record Reason]
    L --> M{Alternative Available?}
    M -- Yes --> H
    M -- No --> N[Report Safe Failure]
    K -- Yes --> O[Execute Through Ryu]
    O --> P[Collect Fresh Telemetry]
    P --> Q{Intent Satisfied?}
    Q -- Yes --> R[Record Verified Recovery]
    Q -- No --> S{Recovery Retry Limit Reached?}
    S -- No --> H
    S -- Yes --> T[Report Recovery Failure]
```

## Required nodes

1.  Intent detection and structured intent creation.
2.  Network observation.
3.  Evidence sufficiency assessment.
4.  Diagnosis and recovery planning.
5.  Structured action generation.
6.  Deterministic safety validation.
7.  Approved action execution.
8.  Fresh post-action observation.
9.  Intent verification.
10. Final explanation and audit recording.

Support conditional paths for:

-   Missing or stale evidence
-   Rejected actions
-   Bounded retries
-   Cancellation
-   Lab disconnection
-   LLM/API errors
-   Failed execution
-   Unsuccessful recovery

Use typed LangGraph state. Keep state fields explicit, such as `run_id`,
`conversation_id`, `intent`, `observations`, `evidence_status`,
`diagnosis_summary`, `candidate_actions`, `selected_action`,
`validation_result`, `execution_result`, `verification_result`,
`retry_count`, and `final_outcome`.

Use a PostgreSQL-backed LangGraph checkpointer if practical. Persist
chat messages and audit events separately so conversation history
remains available independently of graph state.

------------------------------------------------------------------------

# 7. LLM Integration: Groq

Use the Groq API with the model:

`openai/gpt-oss-120b`

## Requirements

-   Read the API key from `GROQ_API_KEY`.
-   Keep the model name configurable through `GROQ_MODEL`, defaulting to
    `openai/gpt-oss-120b`.
-   Call the model only from the Python backend.
-   Never expose the API key to frontend code.
-   Use structured output or validate model responses against Pydantic
    schemas.
-   Handle rate limits, network failures, timeouts, and malformed model
    output.
-   Do not allow an LLM failure to bypass safety validation.
-   Make model integration replaceable so another supported model can be
    configured later.
-   Provide a clear error state when the API is unavailable; do not
    silently claim that AI reasoning happened.
-   Avoid sending unnecessary conversation or network data to the
    external model. Send only the context required for the current task.

The Groq API is an external dependency even though the rest of the
system is local. Internet connectivity and API availability are
therefore required for LLM-assisted operations. Network telemetry, the
frontend, the backend, validation, and the network lab should remain
local.

If the Groq API is unavailable, the system may allow read-only network
inspection and deterministic diagnostic checks, but it must not pretend
that an LLM-powered workflow completed.

------------------------------------------------------------------------

# 8. Proactive Monitoring

Support two modes.

## Reactive mode

The administrator sends a request in the chat, such as:

> Optimize video latency below 50 ms without disrupting other traffic.

The agent investigates and runs the guarded recovery workflow.

## Proactive mode

A background monitor periodically checks configured intents and detects
sustained violations without waiting for a chat message.

Implement:

1.  Configurable sampling interval.
2.  Consecutive-violation threshold to avoid reacting to one brief
    spike.
3.  Fresh evidence collection.
4.  Deduplicated alerts and cooldowns.
5.  Evidence assessment before planning.
6.  The same deterministic action validation used by reactive mode.
7.  Bounded retries and execution timeouts.
8.  Post-action verification.
9.  Notifications and audit records.

For the initial demo, autonomous diagnosis and planning are acceptable,
but every network mutation must pass deterministic validation. Provide
an optional approval mode for higher-risk actions. Unattended execution
should be restricted to explicitly permitted, tested, low-risk actions.

The monitor must not repeatedly trigger the same recovery on every
polling cycle.

------------------------------------------------------------------------

# 9. Frontend Design

Build a light-themed, technical dashboard inspired by Cloudflare's clean
grid-based visual direction without copying a product interface.

## Visual design

-   Main canvas: warm white or light grey.
-   Primary blue: approximately `#2563EB`.
-   Cyan accent: approximately `#0891B2`.
-   Main text: approximately `#111827`.
-   Subtle blue-grey grid lines.
-   Thin neutral borders and modest corner radii.
-   Red for actual violations.
-   Amber for warnings and uncertain states.
-   Green for verified recovery.
-   Avoid an entirely dark interface.

Use CSS or SVG for the grid and topology. The live network itself should
be the main visual; decorative images are not required.

## Dashboard layout

### Header

-   Project name and logo/mark
-   Local lab connection status
-   Agent status
-   Current scenario
-   Reset lab control

### Main network area

-   Interactive topology generated from actual network state
-   Hosts, switches, and links
-   Current path and alternative path
-   Link utilization
-   Congested or failed link indicators
-   Flow direction
-   Latency, packet loss, and protected-flow metrics

### Chat sidebar

-   Conversation list/history
-   User and assistant messages
-   Streaming response
-   Message input
-   Stop/cancel action when safe
-   Expandable activity groups
-   Tool-call inputs and outputs
-   Evidence assessment summary
-   Safety validation results
-   Recovery verification results

### Metrics and audit panels

-   Before/after latency
-   Before/after packet loss
-   Link utilization
-   Protected-flow performance
-   Recovery time
-   Intent status
-   Action and validation history

## Agent activity groups

Show structured, expandable groups such as:

-   `intent_detection`
-   `evidence_collection`
-   `diagnosis_summary`
-   `recovery_planning`
-   `tool_calls`
-   `safety_validation`
-   `execution`
-   `recovery_verification`

Show concise reasoning summaries and observable work---not raw private
chain-of-thought. Users should see the evidence used, the measurements
returned, the alternatives considered at a high level, which policy
checks passed or failed, and the actual result.

Never fabricate tool calls, measurements, validation results, or success
indicators.

## Required status distinctions

Use distinct UI states for:

-   Running
-   Waiting for clarification
-   Gathering evidence
-   Insufficient evidence
-   Action proposed
-   Awaiting approval, if approval mode is enabled
-   Approved
-   Rejected
-   Executing
-   Verified recovery
-   Partial improvement
-   Failed recovery
-   Unverified
-   Lab offline
-   LLM/API unavailable
-   Cancelled

------------------------------------------------------------------------

# 10. Streaming Agent Activity

Use Server-Sent Events (SSE) from FastAPI to the browser for the initial
implementation.

The backend should emit typed events as LangGraph progresses. The
frontend must render the events incrementally rather than waiting for
the entire workflow to finish.

Example event payloads:

``` json
{"type":"step_started","step":"intent_detection","run_id":"run_123"}
{"type":"tool_started","tool":"get_network_snapshot","run_id":"run_123"}
{"type":"tool_completed","tool":"get_network_snapshot","status":"success"}
{"type":"evidence_assessed","status":"sufficient","summary":"Fresh measurements support the diagnosis."}
{"type":"validation_completed","status":"approved","checks_passed":4}
{"type":"verification_completed","status":"success","latency_ms":38}
{"type":"run_completed","outcome":"intent_satisfied"}
```

These are example payloads, not hardcoded outcomes. Implement matching
Pydantic event models and use real runtime values.

Requirements:

-   Every event has a run ID and timestamp.
-   Events have a consistent ordering or sequence number.
-   Persist events so the frontend can recover after reconnecting.
-   Handle duplicate events.
-   Handle interrupted streams and reconnects.
-   Handle tool failures and cancellations.
-   Include a terminal event for completed, failed, or cancelled runs.
-   Do not leak secrets or sensitive environment values into events.

SSE is for browser-facing activity. If a separate local bridge is
needed, use an authenticated local WebSocket or another secure local IPC
mechanism for that bridge. Do not use one public-facing channel for both
purposes without a clear security model.

------------------------------------------------------------------------

# 11. Conversation History and Persistence

Use local PostgreSQL as the recommended persistent database.

## Recommended tables

  -----------------------------------------------------------------------
  Table                               Purpose
  ----------------------------------- -----------------------------------
  `conversations`                     Conversation ID, title,
                                      creation/update timestamps

  `messages`                          User and assistant messages

  `agent_runs`                        Run ID, status, outcome, intent,
                                      timing

  `run_events`                        Ordered activity events, tool
                                      calls, and results

  `intents`                           Latency targets, protected flows,
                                      policy settings

  `action_records`                    Proposed actions, validation
                                      results, execution status

  `verification_results`              Before/after metrics and recovery
                                      outcome

  `lab_sessions`                      Local lab connection state and
                                      heartbeat information
  -----------------------------------------------------------------------

Use UUIDs for identifiers and UTC timestamps.

## Conversation behavior

Example:

1.  Administrator: "Keep video latency below 50 ms."
2.  Agent: "I detected congestion. Would you like me to investigate?"
3.  Administrator: "Yes, but do not modify voice traffic."
4.  Agent: "I will investigate the video flow and protect voice
    traffic."
5.  Administrator: "What happened to the alternative path you
    considered?"

The agent should use the stored conversation context and recorded
actions to answer the last question.

Do not rely only on the LLM context window as permanent memory.

Use a PostgreSQL-backed LangGraph checkpointer if practical. Store chat
messages independently so they can be loaded without replaying the whole
graph.

Do not add vector-database memory unless a concrete requirement appears.
PostgreSQL conversation history is enough for the initial project.

------------------------------------------------------------------------

# 12. Backend API Design

Implement typed FastAPI endpoints.

  -----------------------------------------------------------------------------
  Endpoint                                  Purpose
  ----------------------------------------- -----------------------------------
  `POST /api/conversations`                 Create a conversation

  `GET /api/conversations`                  List conversations

  `GET /api/conversations/{id}`             Load conversation details and
                                            messages

  `POST /api/conversations/{id}/messages`   Submit a message and start an agent
                                            run

  `GET /api/runs/{id}/events`               Stream agent activity over SSE

  `GET /api/runs/{id}`                      Retrieve run status and final
                                            outcome

  `POST /api/runs/{id}/cancel`              Request safe cancellation

  `GET /api/network/snapshot`               Retrieve latest real network state

  `GET /api/network/metrics`                Retrieve telemetry history

  `POST /api/lab/scenarios/congestion`      Start a controlled congestion
                                            scenario

  `POST /api/lab/scenarios/reset`           Restore the lab baseline

  `GET /api/lab/status`                     Check local lab status

  `POST /api/intents`                       Create or update an intent

  `GET /api/audit`                          View action and verification
                                            records
  -----------------------------------------------------------------------------

Use Pydantic models for all request and response bodies.

Validate an intent against the actual topology. A syntactically valid
request is not necessarily feasible.

For cancellation, do not abruptly terminate a network mutation halfway
through. Define safe cancellation points, record the state, and verify
the network if cancellation happens after a change was executed.

------------------------------------------------------------------------

# 13. Network Lab and Live Demo

## Proposed topology

Create a multi-path Mininet topology with:

-   Video source host H1
-   Video receiver host H4
-   Multiple Open vSwitch switches
-   A congestible route
-   At least one alternative route
-   A protected voice or other traffic flow

Generate the topology visualization from the actual network state rather
than hardcoding the diagram.

## Controlled congestion

Use an appropriate traffic generator such as `iperf3` and configure link
bandwidth/delay through Mininet. Generate background traffic on the
bottleneck link.

Measure:

-   End-to-end latency for the selected flow
-   Packet loss
-   Link utilization
-   Per-flow packet and byte counters
-   Link availability and topology
-   Protected-flow latency and packet loss

Calibrate the scenario on the local machine. Do not assume that a
particular traffic load will automatically produce 120 ms latency or 95%
utilization. Use measured results.

## Live demo sequence

1.  Start PostgreSQL, the backend, frontend, Ryu, and the network lab.
2.  Show the actual topology and healthy baseline metrics.
3.  Start controlled background traffic on the bottleneck link.
4.  Show latency and utilization increasing in the dashboard.
5.  Let proactive monitoring detect a sustained violation, or submit an
    optimization request through chat.
6.  Show the telemetry tool calls and collected evidence in the activity
    timeline.
7.  Show the diagnosis summary and proposed alternative route.
8.  Show deterministic validation results, including protected-flow
    constraints.
9.  Execute the approved reroute through Ryu/OpenFlow.
10. Collect fresh telemetry.
11. Show actual before/after latency, packet loss, link utilization, and
    protected-flow performance.
12. Record whether the intent was satisfied, partially improved, failed,
    or unverified.
13. Show the audit record.
14. Reset the lab and demonstrate that the baseline can be reproduced.

The demonstration must show a real forwarding change and measured
network results. Animation alone is not sufficient.

------------------------------------------------------------------------

# 14. Local Network Bridge and Privileges

Mininet and Open vSwitch require Linux networking facilities and
sometimes elevated privileges. Do not run the entire Next.js or FastAPI
application as root.

Create a narrowly scoped network tool layer. If needed, use a local
bridge process or restricted launcher that exposes only allow-listed
operations.

Rules:

-   Never execute arbitrary shell commands generated by the LLM.
-   Do not accept arbitrary filesystem paths or command strings from the
    frontend.
-   Validate all tool inputs.
-   Keep the bridge local and authenticated.
-   Bind local services to loopback where possible.
-   Do not expose Ryu or switch-control ports to untrusted networks.
-   Use narrowly scoped privileges for the network-lab operations that
    require them.
-   Log mutating operations.
-   Provide an explicit reset operation limited to the project lab.
-   If the bridge disconnects, stop new mutations and mark recovery as
    unverified.
-   Handle stale or repeated requests safely.

Inspect the current Omarchy environment before deciding exactly how
Mininet and Ryu should be launched. Do not assume all components are
installed or working.

------------------------------------------------------------------------

# 15. Security and Validation Rules

The backend must enforce these rules:

1.  The LLM never executes arbitrary shell commands.
2.  Only allow-listed tools can interact with the lab.
3.  Every network mutation passes deterministic validation.
4.  Every action is scoped to an authorized flow and permitted
    operation.
5.  Tool schemas are validated using Pydantic.
6.  Requests carry a run ID and action ID; use idempotency protection
    for mutations where appropriate.
7.  Expired or duplicated requests cannot silently execute twice.
8.  Every action and rejection is recorded.
9.  Recovery retries have strict limits.
10. Lab disconnection prevents new mutations.
11. Missing post-action telemetry cannot produce a success outcome.
12. The safety validator cannot be bypassed by an LLM response.
13. API secrets are not logged or exposed in SSE events.
14. Local APIs must not be exposed to untrusted networks.
15. Reset operations are explicit and restricted to the lab.

For the initial implementation, limit mutations to a small set such as
rerouting a specified flow through an allowed path. A small, thoroughly
tested action space is more useful than many untested actions.

------------------------------------------------------------------------

# 16. Repository Structure

Use a monorepo for the frontend, backend, local bridge, network lab, and
tests.

``` text
trustworthy-agentic-sdn/
├── apps/
│   └── web/
│       ├── src/
│       │   ├── app/
│       │   ├── components/
│       │   │   ├── dashboard/
│       │   │   ├── topology/
│       │   │   ├── metrics/
│       │   │   ├── chat/
│       │   │   └── agent-activity/
│       │   ├── hooks/
│       │   ├── lib/
│       │   └── types/
│       └── package.json
├── services/
│   ├── api/
│   │   ├── app/
│   │   │   ├── api/routes/
│   │   │   ├── schemas/
│   │   │   ├── models/
│   │   │   ├── db/
│   │   │   ├── agents/
│   │   │   │   ├── graph.py
│   │   │   │   ├── state.py
│   │   │   │   ├── nodes/
│   │   │   │   └── routing.py
│   │   │   ├── safety/
│   │   │   ├── tools/
│   │   │   ├── services/
│   │   │   ├── streaming/
│   │   │   └── main.py
│   │   ├── migrations/
│   │   ├── tests/
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   └── lab-bridge/
│       ├── bridge/
│       │   ├── client.py
│       │   ├── commands.py
│       │   ├── telemetry.py
│       │   └── ryu_client.py
│       ├── tests/
│       └── requirements.txt
├── network-lab/
│   ├── topologies/
│   ├── scenarios/
│   ├── traffic/
│   ├── controller/
│   └── scripts/
├── docs/
│   ├── architecture.md
│   ├── safety-model.md
│   ├── api-contracts.md
│   └── demo-runbook.md
├── docker-compose.yml
├── .env.example
└── README.md
```

This is a target structure, not a requirement to create every file
before coding. Keep the initial implementation small and add modules
when they are needed.

------------------------------------------------------------------------

# 17. Environment Configuration

Create a `.env.example` similar to:

``` dotenv
GROQ_API_KEY=replace_with_your_key
GROQ_MODEL=openai/gpt-oss-120b

DATABASE_URL=postgresql+psycopg://sdn_user:replace_password@localhost:5432/sdn_db

BACKEND_HOST=127.0.0.1
BACKEND_PORT=8000
FRONTEND_ORIGIN=http://localhost:3000

LAB_MODE=real
LAB_BRIDGE_URL=ws://127.0.0.1:8765
LAB_BRIDGE_TOKEN=replace_with_a_long_random_local_secret

MAX_EVIDENCE_RETRIES=2
MAX_RECOVERY_RETRIES=2
TELEMETRY_MAX_AGE_SECONDS=5
PROACTIVE_MONITOR_ENABLED=false
PROACTIVE_SAMPLE_INTERVAL_SECONDS=5
PROACTIVE_VIOLATION_COUNT=3
PROACTIVE_COOLDOWN_SECONDS=60
```

These values are illustrative starting defaults. Validate and tune them
during implementation.

Important:

-   `.env` must be in `.gitignore`.
-   Never commit a real Groq key or bridge token.
-   Generate a strong local bridge token.
-   Validate configuration at startup.
-   Fail safely when required secrets are missing.
-   Use a separate test configuration.
-   Never silently change `LAB_MODE=real` into a mocked mode when the
    lab is unavailable.

------------------------------------------------------------------------

# 18. Implementation Plan for the Coding Agent

Work incrementally. Do not generate the entire codebase in one pass and
assume it works.

## Phase 1 --- Inspect the environment

Inspect the current Omarchy machine, existing repository, and installed
dependencies.

Check:

-   Python and virtual-environment support
-   Node.js and npm/pnpm
-   Mininet
-   Open vSwitch
-   Ryu or POX
-   OpenFlow connectivity
-   `iperf3` and relevant measurement tools
-   PostgreSQL
-   Docker or Podman, if available
-   Available disk space and ports

Report what is installed, what works, what is missing, and which
installation steps need permission. Do not reinstall working components
or modify networking without permission.

## Phase 2 --- Build the network lab

Create the multi-path topology, video and protected flows, telemetry
collection, controlled congestion scenario, and reset script.

Prove that:

-   The baseline is healthy.
-   The congestion scenario creates a measurable violation.
-   A valid reroute changes forwarding behavior.
-   The post-action measurements reflect the actual result.
-   The reset restores a known baseline.

Do this before adding the LLM.

## Phase 3 --- Build the safe tool layer

Implement tools for topology discovery, link telemetry, flow telemetry,
network snapshots, path evaluation, action validation, flow rerouting,
verification, and lab reset.

Define Pydantic input/output schemas. Add deterministic checks for path
existence, link availability, capacity, flow scope, permitted
operations, and protected-flow constraints.

Test invalid actions before allowing agent-driven mutations.

## Phase 4 --- Build persistence and FastAPI

Implement the API, local database, migrations, conversation storage,
agent-run records, event history, and audit trail.

Prefer local PostgreSQL. If it is not available, document the issue and
use SQLite temporarily rather than blocking every other phase.

Implement the local lab bridge if the network tools need a separate
process or privilege boundary. Test connectivity and disconnection
handling.

## Phase 5 --- Implement the LangGraph workflow

Implement explicit nodes and conditional routes for intent detection,
observation, evidence assessment, diagnosis, planning, validation,
execution, and verification.

Add bounded retries, abstention, rejection, failure handling, and final
audit recording.

Integrate Groq using `openai/gpt-oss-120b`. Validate structured outputs
and handle model/API failures.

## Phase 6 --- Implement streaming and history

Add SSE events, durable event storage, run status, reconnect support,
and conversation history.

Test with the real backend before building the complete UI around it.

## Phase 7 --- Build the frontend

Implement the light grid-based dashboard, live topology, metrics, chat
sidebar, activity timeline, tool-call cards, safety results, and
before/after comparison.

Use real API data. Clearly label any test fixture or mock data.

## Phase 8 --- Add proactive monitoring

Add configurable polling, consecutive-violation thresholds, cooldowns,
deduplication, evidence checks, and bounded recovery.

Every mutation must use the same validator as chat-triggered actions.

## Phase 9 --- Test end-to-end

Run unit, integration, safety, and end-to-end tests.

Demonstrate a full scenario from congestion through validation and
rerouting to measured verification.

## Phase 10 --- Documentation and local launch

Document setup, environment configuration, database initialization,
network-lab startup, frontend/backend startup, testing, cleanup, and the
live demo sequence.

Provide local commands or a development launcher that clearly reports
which services started and which failed. Do not hide errors.

At the end of each phase, report the exact commands, test results,
failures, and unresolved issues. Do not claim a phase is complete unless
its acceptance tests have passed.

------------------------------------------------------------------------

# 19. Testing and Acceptance Checklist

## Network

-   [ ] Baseline metrics come from Mininet and actual traffic.
-   [ ] Congestion reliably creates a measurable latency violation.
-   [ ] A validated reroute changes the actual forwarding path.
-   [ ] Reset returns the lab to a known baseline.
-   [ ] Protected traffic is measured before and after recovery.

## Trustworthiness

-   [ ] Stale or missing telemetry prevents unsupported actions.
-   [ ] Invalid or prohibited paths are rejected.
-   [ ] Capacity and flow-scope constraints are enforced.
-   [ ] Protected-flow constraints are checked before execution and
    after recovery.
-   [ ] Failed validation prevents network mutation.
-   [ ] Failed recovery is never reported as verified success.
-   [ ] Missing post-action telemetry produces an unverified outcome.
-   [ ] Retries are bounded.

## Agent and API

-   [ ] LangGraph routes correctly for success, rejection, and failure.
-   [ ] Pydantic rejects malformed or out-of-range requests.
-   [ ] SSE reports tool results and terminal run status.
-   [ ] Conversation history survives a backend restart.
-   [ ] Groq API failures are handled explicitly.
-   [ ] The LLM cannot bypass the deterministic validator.
-   [ ] Tool calls and decisions are recorded in the audit trail.

## Frontend

-   [ ] The topology is generated from real lab state.
-   [ ] Metrics are obtained from backend telemetry.
-   [ ] The activity timeline updates during a live run.
-   [ ] Tool results and safety checks are expandable.
-   [ ] Before/after measurements match the backend records.
-   [ ] Offline, failed, unverified, and successful states are distinct.
-   [ ] No fabricated success indicators are shown.

## Local operation

-   [ ] Frontend runs locally.
-   [ ] FastAPI runs locally.
-   [ ] Database runs locally.
-   [ ] Network lab and controller run locally.
-   [ ] No deployment to Vercel, Render, or Neon is required.
-   [ ] Secrets are excluded from Git.
-   [ ] Local control APIs are not exposed to untrusted networks.
-   [ ] The documented startup and reset procedures work.

Do not count a test as passed because the LLM says it passed. Use actual
test output, network measurements, and persisted records.

------------------------------------------------------------------------

# 20. Live Demo Runbook

The final demonstration should follow this sequence:

1.  Start the local database.
2.  Start Ryu and the Mininet network lab.
3.  Start FastAPI and the local agent backend.
4.  Start the Next.js frontend.
5.  Confirm that the lab and telemetry are connected.
6.  Show the healthy baseline and topology.
7.  Start the controlled congestion scenario.
8.  Show the latency violation and bottleneck utilization.
9.  Submit a chat request to optimize video latency, or let proactive
    monitoring detect the violation.
10. Show evidence collection and the diagnosis summary.
11. Show the candidate route and deterministic safety checks.
12. Demonstrate rejection if an intentionally invalid route is proposed.
13. Execute a valid reroute.
14. Show fresh post-action measurements.
15. Show verified recovery, partial improvement, or failure based on the
    actual results.
16. Show the audit record and conversation history.
17. Reset the lab and confirm that the scenario can be reproduced.

Use measured results in the presentation. If the target is not reached,
explain that honestly and show how the system reports the outcome.

------------------------------------------------------------------------

# 21. Final Decisions

  -----------------------------------------------------------------------
  Decision                            Final choice
  ----------------------------------- -----------------------------------
  Project title                       Trustworthy Agentic AI for
                                      Intent-Based Self-Healing SDN

  Execution                           Entire application and network lab
                                      run locally

  Frontend                            Next.js + React + TypeScript

  Backend                             Python + FastAPI + Pydantic

  Agent                               LangGraph

  LLM                                 Groq API, `openai/gpt-oss-120b`

  Controller                          Ryu + OpenFlow

  Network                             Mininet + Open vSwitch

  Database                            Local PostgreSQL preferred; SQLite
                                      temporary fallback

  Conversation history                PostgreSQL-backed messages and run
                                      events

  Streaming                           SSE from FastAPI to the browser

  Safety                              Deterministic evidence,
                                      action-validation, and verification
                                      gates

  Monitoring                          Proactive monitoring with cooldowns
                                      and bounded recovery

  Theme                               Light grid, blue/cyan accents,
                                      near-black text

  Deployment                          No cloud deployment required

  Main demo                           Congestion → evidence → diagnosis →
                                      validation → reroute → measured
                                      verification
  -----------------------------------------------------------------------

------------------------------------------------------------------------

# 22. Final Instruction to OpenCode

Start by inspecting the current project directory and the Omarchy
machine. Report the environment and dependency status before making
changes.

Then implement the project in the phases above. Prioritize a real,
reproducible network lab and deterministic safety checks before building
advanced UI features.

Do not create a frontend-only simulation. Do not invent network metrics.
Do not allow an LLM to bypass validation. Do not treat a successful
command as proof of successful recovery.

The final result must be a locally runnable, end-to-end project in which
the administrator can observe a real network violation, interact with an
Agentic AI system, inspect the evidence and tool calls, see safety
checks, observe a real network change, and verify the result using fresh
telemetry.
