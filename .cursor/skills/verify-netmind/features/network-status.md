# Live network telemetry and status

Provides real-time measurement of flows across the 4-switch SDN topology, reporting mean latency, jitter, packet loss, and link utilization.

## Sub-features

- `status-live-check` reports whether the network lab, switch backend, and Ryu controller are active.
- `telemetry-flows` measures real ping latencies between `h1->h4` (video) and `h3->h4` (protected voice).
- `telemetry-links` samples switch port byte counters to report link utilization in Mbps.

## How to get to it (user POV)

- Open the Next.js dashboard at `http://localhost:3030` and observe the top metric strip and topology canvas.
- Query `GET /v1/status` on `http://sdn-lab.orb.local:8000`.
- Query `GET /v1/snapshot` on `http://sdn-lab.orb.local:8000`.

## Driving it with control.sh

Preconditions:
- Lab is launched: `.cursor/skills/verify-netmind/control.sh launch`.
- `.cursor/skills/verify-netmind/control.sh doctor` reports OK.

- **Check status.** Run `.cursor/skills/verify-netmind/control.sh doctor`. Observable output contains `connected_switches: 4` and `running: true`.
- **Capture telemetry snapshot.** Run `.cursor/skills/verify-netmind/control.sh snapshot artifacts/verify-netmind/baseline-snapshot.json`. The resulting JSON file contains measured flows `h1->h4` and `h3->h4` with `ok: true`.
- **Verify baseline values.** Check `h1->h4` latency in the JSON. The latency is between 20 ms and 26 ms with 0% packet loss.
- **Frontend observation.** Run `.cursor/skills/verify-netmind/control.sh test-frontend`. All Playwright tests pass and confirm the topology and metric strip render correctly.

## Gotchas

- The first ping immediately after switch bring-up incurs an ARP resolution delay; subsequent samples stabilize.
- The lab never returns mock data. If the lab process crashes or namespace is inaccessible, the API returns HTTP 503 instead of blank data.
