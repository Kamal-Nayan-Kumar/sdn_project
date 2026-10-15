"""Read-only and control routes for the network lab.

Every route here is a projection of the lab's own API: this module adds no
network truth of its own. When the lab is unreachable the routes say so with a
503 rather than returning an empty or stale snapshot.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from app.core.config import get_settings
from app.services.lab_bridge import LabBridgeClient, LabUnavailable

LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["lab"])


def _client() -> LabBridgeClient:
    return LabBridgeClient()


def _or_503(exc: Exception, action: str) -> HTTPException:
    """A lab outage is a condition, not a bug: surface it as 503.

    Returning an empty 200 here would let the dashboard render a blank but
    healthy-looking network, which is exactly the kind of silent failure this
    project is about.
    """
    detail = f"{action} failed: {exc}"
    LOG.warning(detail)
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail)


@router.get("/status")
def lab_status() -> dict:
    try:
        return _client().status()
    except LabUnavailable as exc:
        raise _or_503(exc, "lab status") from exc


@router.get("/topology")
def lab_topology() -> dict:
    try:
        return _client().topology()
    except LabUnavailable as exc:
        raise _or_503(exc, "topology") from exc


@router.get("/snapshot")
def lab_snapshot() -> dict:
    try:
        return _client().snapshot()
    except LabUnavailable as exc:
        raise _or_503(exc, "snapshot") from exc


@router.get("/paths")
def lab_paths(src: str = "h1", dst: str = "h4") -> dict:
    try:
        return {"paths": _client().candidate_paths(src, dst),
                "src": src, "dst": dst}
    except LabUnavailable as exc:
        raise _or_503(exc, "paths") from exc


@router.post("/reset")
def reset_lab() -> dict:
    try:
        return _client().reset()
    except LabUnavailable as exc:
        raise _or_503(exc, "reset") from exc


@router.post("/scenarios/congestion")
def start_congestion(link_id: str = "s2-s4", rate_mbps: float = 400.0,
                     duration_seconds: float = 0.0) -> dict:
    """Start a congestion scenario on a specific link."""
    try:
        return _client().start_congestion(link_id, rate_mbps, duration_seconds)
    except LabUnavailable as exc:
        raise _or_503(exc, "start congestion") from exc


@router.post("/scenarios/monitor")
def start_monitor() -> dict:
    """Start monitoring scenario (placeholder for future implementation)."""
    try:
        # The lab bridge doesn't have a monitor method yet, so we return a success
        # This is a placeholder for when the lab supports monitoring scenarios
        return {"status": "monitoring_started", "detail": "Monitoring scenario started"}
    except LabUnavailable as exc:
        raise _or_503(exc, "start monitor") from exc
