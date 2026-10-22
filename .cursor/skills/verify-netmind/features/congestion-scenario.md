# Bottleneck congestion injection

Simulates link degradation by pumping high-rate UDP background traffic across link `s2-s4`, inflating video latency and producing packet loss while preserving the protected voice flow.

## Sub-features

- `congestion-start` triggers UDP traffic generator on `h2` targeting `h3` over link `s2-s4`.
- `congestion-observe` marks link `s2-s4` as congested in telemetry snapshots and dashboard topology.
- `congestion-reset` terminates traffic generation and restores clean link operation.

## How to get to it (user POV)

- Click **Create Congestion** in the Lab Controls panel on the Next.js dashboard.
- Send a POST request to `POST /v1/scenarios/congestion` with payload `{"link_id": "s2-s4", "rate_mbps": 400.0}`.
- Click **Reset Network** on the dashboard or send `POST /v1/reset`.

## Driving it with control.sh

Preconditions:
- Lab is healthy (`.cursor/skills/verify-netmind/control.sh doctor`).
- Clean baseline snapshot captured.

- **Trigger bottleneck load.** Run:
  `curl -s -X POST http://sdn-lab.orb.local:8000/v1/scenarios/congestion -H "Content-Type: application/json" -d '{"link_id": "s2-s4", "rate_mbps": 400.0}'`
  Output confirms: `{"ok": true, "scenario": "congestion", "link": "s2-s4"}`.
- **Observe degraded metrics.** Run:
  `.cursor/skills/verify-netmind/control.sh snapshot artifacts/verify-netmind/congested-snapshot.json`
  Output shows `congested_links: ["s2-s4"]` and `s2-s4` link status `down`.
- **Reset to baseline.** Run:
  `curl -s -X POST http://sdn-lab.orb.local:8000/v1/reset`
  Output confirms scenario reset and all links return to normal.

## Gotchas

- The pure Python OpenFlow switch (`of13switch.py`) throughput saturates near 40 Mbps, so offered load above that generates immediate queuing and packet drops, which is intended congestion behavior.
- Resetting the lab drops existing OpenFlow flow overrides and lets learning switches re-converge.
