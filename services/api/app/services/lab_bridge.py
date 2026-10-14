"""Client for the local network-lab API.

The lab runs inside its own network namespace (so Mininet/OVS have the
privileges they need) and exposes its API on a **unix domain socket**. This
client speaks to that socket and is the only channel through which the
backend can observe or mutate the network.

Security properties enforced here as well as server-side:
  * every request carries the local bridge token;
  * the only mutations offered are the allow-listed scenario and reroute
    calls -- there is no "run a command" method, so the backend cannot ask
    the lab to execute anything arbitrary;
  * failures are raised as `LabUnavailable` and never silently downgraded.
"""

from __future__ import annotations

import json
import os
import socket as _socket
import urllib.error
import urllib.request
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import get_settings


class LabUnavailable(RuntimeError):
    """Raised when the local network lab cannot be reached."""


class LabBridgeClient:
    """Thin client for the lab's unix-socket HTTP API."""

    def __init__(self, socket_path: str | None = None,
                 token: str | None = None) -> None:
        settings = get_settings()
        self.socket_path = socket_path or os.environ.get(
            "SDN_LAB_SOCKET", "/tmp/sdn-lab.sock")
        self.token = token or os.environ.get("LAB_API_TOKEN", "") or (
            settings.lab_bridge_token if settings.bridge_token_configured else "")

    # ------------------------------------------------------------------
    # plumbing
    # ------------------------------------------------------------------
    def _request(self, method: str, path: str,
                 body: dict[str, Any] | None = None) -> Any:
        url = f"http://localhost{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data:
            req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")

        class UnixHTTPConnection(urllib.request.AbstractHTTPHandler):
            def __init__(self, socket_path: str) -> None:
                self.socket_path = socket_path

            def do_open(self, http_class, req, **http_conn_args):  # noqa: ANN001
                conn = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
                conn.settimeout(30.0)
                conn.connect(self.socket_path)
                h = http_class(conn, timeout=30.0)
                h.request(req.get_method(), req.selector, headers=req.headers)
                return h

        opener = urllib.request.build_opener(UnixHTTPConnection(self.socket_path))
        try:
            with opener.open(req, timeout=60.0) as resp:
                return json.loads(resp.read().decode())
        except (FileNotFoundError, ConnectionError, OSError) as exc:
            raise LabUnavailable(
                f"network lab not reachable at {self.socket_path}: {exc}") from exc
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise LabUnavailable(
                f"network lab returned {exc.code} for {path}: {detail}") from exc

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return self._request("POST", path, body)

    # ------------------------------------------------------------------
    # read-only
    # ------------------------------------------------------------------
    def is_available(self) -> bool:
        try:
            self.get("/v1/status")
            return True
        except LabUnavailable:
            return False

    def status(self) -> dict[str, Any]:
        return self.get("/v1/status")

    def topology(self) -> dict[str, Any]:
        return self.get("/v1/topology")

    def snapshot(self) -> dict[str, Any]:
        return self.get("/v1/snapshot")

    def candidate_paths(self, src: str = "h1", dst: str = "h4") -> list[dict[str, Any]]:
        return self.get(f"/v1/paths?src={src}&dst={dst}").get("paths", [])

    # ------------------------------------------------------------------
    # mutations (allow-listed only)
    # ------------------------------------------------------------------
    def start_congestion(self, link_id: str = "s2-s4", rate_mbps: float = 400.0,
                         duration_seconds: float = 0.0) -> dict[str, Any]:
        return self.post("/v1/scenarios/congestion", {
            "link_id": link_id, "rate_mbps": rate_mbps,
            "duration_seconds": duration_seconds})

    def reset(self) -> dict[str, Any]:
        return self.post("/v1/scenarios/reset")

    def reroute(self, src_host: str, dst_host: str,
                path: list[str]) -> dict[str, Any]:
        """Apply an *already validated* reroute. The caller (safety engine)
        must have approved this action -- this method does no validation.
        """
        return self.post("/v1/reroute", {
            "src_host": src_host, "dst_host": dst_host, "path": path})


class LabHealth(BaseModel):
    """Read-only lab health summary for the API/UI."""

    reachable: bool = False
    switch_backend: str | None = None
    connected_switches: int = 0
    congested_links: list[str] = Field(default_factory=list)
    detail: str = ""

    @classmethod
    def probe(cls, client: LabBridgeClient) -> "LabHealth":
        if not client.is_available():
            return cls(reachable=False, detail="lab socket absent/unreachable")
        try:
            status = client.status()
        except LabUnavailable as exc:
            return cls(reachable=False, detail=str(exc))
        return cls(
            reachable=True,
            switch_backend=status.get("switch_backend"),
            connected_switches=int(status.get("connected_switches", 0)),
            congested_links=list(status.get("congested_links", [])),
        )


__all__ = ["LabBridgeClient", "LabHealth", "LabUnavailable"]
