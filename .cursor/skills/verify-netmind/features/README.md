# NetMind verification map

This directory is the maintained source for verifying user-facing features of the NetMind SDN self-healing system. Read this index before driving scenarios, then follow the corresponding feature recipe.

## Baseline preconditions

- OrbStack VM `sdn-lab` is up with `.cursor/skills/verify-netmind/control.sh launch`.
- Doctor check passes: `.cursor/skills/verify-netmind/control.sh doctor`.
- All 4 OpenFlow switches (`s1`, `s2`, `s3`, `s4`) are connected to Ryu on 127.0.0.1:6653.
- Baseline video latency `h1->h4` sits around 20–25 ms (clean shortest path `s1-s2-s4`).
- Protected voice flow `h3->h4` reports 0% packet loss.

## Driving conventions

- Run verification recipes using `.cursor/skills/verify-netmind/control.sh` or direct HTTP commands against `http://sdn-lab.orb.local:8000`.
- Preserve proof artifacts under `artifacts/verify-netmind/`.
- Do not run two concurrent lab instances: Mininet kernel interfaces are single-tenant.

## Features

- [Live network telemetry and status](./network-status.md) covers viewing live flow latencies, link utilization, and switch status on the dashboard or API.
- [Bottleneck congestion injection](./congestion-scenario.md) covers loading link `s2-s4` with UDP traffic, observing latency inflation past 50 ms, and packet loss.
- [Intent-based self-healing and safety gates](./intent-self-healing.md) covers submitting natural language optimization goals, evidence checks, safety verification, and automatic rerouting via `s1-s3-s4`.
