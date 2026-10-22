---
name: verify-netmind
description: Verify the trustworthy-agentic SDN self-healing network lab, live telemetry, and Next.js operations dashboard. Reach for this skill to start or test the Mininet topology, Ryu controller, FastAPI backend, or to prove intent-based self-healing and safety gate decisions.
---

# Verify NetMind SDN Lab

NetMind is an intent-based self-healing SDN platform. Network emulation (Mininet, user-space OpenFlow 1.3 switch, Ryu controller) requires a Linux network stack (`netns`, `veth`, `AF_PACKET`, `tc`), which runs in an OrbStack Linux machine (`sdn-lab`) while the Next.js dashboard runs locally on macOS.

## Launch

Start the Linux lab environment and the FastAPI backend:

```bash
.cursor/skills/verify-netmind/control.sh launch
```

Ready when `control.sh` prints:
`==> NetMind backend is healthy at http://sdn-lab.orb.local:8000`

To start the Next.js dashboard UI (on port 3030 to avoid conflicts):

```bash
cd apps/web && NEXT_PUBLIC_API_ORIGIN=http://sdn-lab.orb.local:8000 npm run dev -- -p 3030
```

Ready when Next.js logs: `✓ Ready in ...` and `http://localhost:3030` responds.

## Doctor

Run the health check whenever connectivity looks degraded or before driving a scenario:

```bash
.cursor/skills/verify-netmind/control.sh doctor
```

Confirms:
1. OrbStack Linux VM `sdn-lab` is running.
2. FastAPI backend responds with status `ok` at `/health`.
3. Exactly 4 switches (`s1`, `s2`, `s3`, `s4`) are connected to the Ryu OpenFlow 1.3 controller.
4. Telemetry collection is active and not returning empty metrics.

## Drive

### 1. Automated test suites

- **Backend (unit & live OpenFlow network):**
  ```bash
  .cursor/skills/verify-netmind/control.sh test-backend
  ```
  Runs pytest inside `sdn-lab`, testing safety gates, deterministic planning, live link measurements, and candidate path resolution.

- **Frontend (Playwright E2E on macOS):**
  ```bash
  .cursor/skills/verify-netmind/control.sh test-frontend
  ```
  Launches Chromium, navigates the dashboard at `http://localhost:3030`, and verifies metrics strips, topology canvas, and lab controls.

### 2. Direct HTTP API driving

- **Capture live network telemetry:**
  ```bash
  curl -s http://sdn-lab.orb.local:8000/v1/snapshot | jq .
  ```
- **Trigger link congestion:**
  ```bash
  curl -s -X POST http://sdn-lab.orb.local:8000/v1/scenarios/congestion \
    -H "Content-Type: application/json" \
    -d '{"link_id": "s2-s4", "rate_mbps": 400.0}' | jq .
  ```
- **Submit a self-healing recovery intent:**
  ```bash
  curl -s -X POST http://sdn-lab.orb.local:8000/api/runs \
    -H "Content-Type: application/json" \
    -d '{"message": "optimise latency so that it comes under 50 ms"}' | jq .
  ```

## Evidence

Store all run proofs under `artifacts/verify-netmind/`:
- `artifacts/verify-netmind/telemetry-snapshot.json`: Raw telemetry JSON (flow latencies, packet loss, switch link utilization).
- `artifacts/verify-netmind/playwright-report/`: HTML report and screenshots from frontend driving.
- `logs/lab.log` and `logs/ryu.log`: Controller OpenFlow events and Mininet link status logs.

Evidence must capture both initial state and post-action state. Never report mock data as live telemetry.

## Cleanup

Tear down running lab processes and backend server:

```bash
.cursor/skills/verify-netmind/control.sh cleanup
```

Stops Mininet network namespaces, Ryu controller, and uvicorn. Artifacts in `artifacts/verify-netmind/` are preserved.

## Helpers

- `.cursor/skills/verify-netmind/control.sh`: Executable orchestrator supporting `launch`, `doctor`, `snapshot`, `test-backend`, `test-frontend`, and `cleanup`.
