"""Background proactive monitoring watchdog for intent SLO violations.

Periodically samples live telemetry from the lab. When an SLO violation
persists across a consecutive-violation threshold (to rule out single-packet
transient spikes), it automatically initiates an autonomous self-healing run
through the LangGraph workflow without requiring manual operator intervention.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.core.config import get_settings
from app.schemas.domain import FlowTelemetry, NetworkSnapshot
from app.services.lab_bridge import LabBridgeClient, LabUnavailable
from app.streaming.registry import registry

LOG = logging.getLogger(__name__)


class ProactiveMonitor:
    def __init__(self, lab_client: LabBridgeClient | None = None) -> None:
        self.lab_client = lab_client
        self.enabled = False
        self.task: asyncio.Task[None] | None = None
        self.consecutive_violations = 0
        self.last_triggered_at: float = 0.0
        self.target_latency_ms = 50.0
        self.src_host = "h1"
        self.dst_host = "h4"

    def start(self, lab_client: LabBridgeClient | None = None) -> dict[str, Any]:
        if lab_client is not None:
            self.lab_client = lab_client
        self.enabled = True
        try:
            loop = asyncio.get_running_loop()
            if self.task is None or self.task.done():
                self.task = loop.create_task(self._run_loop())
        except RuntimeError:
            self.task = None
        LOG.info("proactive monitor started (target=%sms, src=%s, dst=%s)",
                 self.target_latency_ms, self.src_host, self.dst_host)
        return self.status()

    def stop(self) -> dict[str, Any]:
        self.enabled = False
        if self.task is not None and not self.task.done():
            self.task.cancel()
        self.task = None
        self.consecutive_violations = 0
        LOG.info("proactive monitor stopped")
        return self.status()

    def status(self) -> dict[str, Any]:
        settings = get_settings()
        cooldown = max(0.0, (self.last_triggered_at + settings.proactive_cooldown_seconds) - time.time())
        return {
            "enabled": self.enabled,
            "running": self.task is not None and not self.task.done(),
            "target_latency_ms": self.target_latency_ms,
            "consecutive_violations": self.consecutive_violations,
            "required_consecutive_violations": settings.proactive_violation_count,
            "cooldown_remaining_seconds": round(cooldown, 1),
            "flow": f"{self.src_host}->{self.dst_host}",
        }

    async def _run_loop(self) -> None:
        settings = get_settings()
        interval = settings.proactive_sample_interval_seconds
        required_violations = settings.proactive_violation_count
        cooldown_sec = settings.proactive_cooldown_seconds

        LOG.info("proactive monitor background loop active")
        while self.enabled:
            try:
                await asyncio.sleep(interval)
                if not self.enabled or self.lab_client is None:
                    continue

                try:
                    raw_snap = self.lab_client.snapshot(latency_count=3)
                    snap = NetworkSnapshot.model_validate(raw_snap)
                except LabUnavailable:
                    continue
                except Exception as exc:
                    LOG.debug("proactive monitor snapshot error: %s", exc)
                    continue

                # Check targeted flow (video flow h1->h4)
                flow: FlowTelemetry | None = None
                for f in snap.flows:
                    if f.src_host == self.src_host and f.dst_host == self.dst_host:
                        flow = f
                        break

                if flow is None or flow.latency_ms is None or flow.latency_ms <= 0:
                    continue

                is_violation = flow.latency_ms > self.target_latency_ms
                if is_violation:
                    self.consecutive_violations += 1
                    LOG.info(
                        "proactive monitor: detected violation on %s->%s: %.1fms > %.1fms (count %d/%d)",
                        self.src_host, self.dst_host, flow.latency_ms,
                        self.target_latency_ms, self.consecutive_violations, required_violations
                    )
                else:
                    self.consecutive_violations = 0

                now = time.time()
                in_cooldown = (now - self.last_triggered_at) < cooldown_sec

                if self.consecutive_violations >= required_violations and not in_cooldown:
                    LOG.warning(
                        "proactive monitor: sustained violation on %s->%s! Triggering autonomous self-healing.",
                        self.src_host, self.dst_host
                    )
                    self.last_triggered_at = now
                    self.consecutive_violations = 0

                    # Trigger autonomous agent run
                    from app.api.routes.runs import _new_run_id, _run_graph
                    run_id = _new_run_id()
                    trigger_msg = (
                        f"Proactive self-healing triggered: {self.src_host}->{self.dst_host} "
                        f"latency measured at {flow.latency_ms:.1f} ms, exceeding the "
                        f"{self.target_latency_ms:.0f} ms SLO target."
                    )
                    registry.create(run_id, trigger_msg, trigger="proactive")
                    asyncio.create_task(_run_graph(run_id, trigger_msg))

            except asyncio.CancelledError:
                break
            except Exception as exc:
                LOG.exception("unexpected error in proactive monitor: %s", exc)


monitor = ProactiveMonitor()
