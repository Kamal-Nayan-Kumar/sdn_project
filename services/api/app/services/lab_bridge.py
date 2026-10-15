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

import http.client
import json
import os
import socket as _socket
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import get_settings


class LabUnavailable(RuntimeError):
    """Raised when the local network lab cannot be reached."""


class _UnixHTTPConnection(http.client.HTTPConnection):
    """HTTP/1.1 over a unix domain socket.

    `urllib` has no supported way to talk HTTP over an AF_UNIX socket -- the
    usual trick of subclassing `AbstractHTTPHandler` does not work, because that
    class defines no `http_open` and the handler is therefore never invoked.
    Subclassing the connection is the supported route.
    """

    def __init__(self, socket_path: str, timeout: float = 30.0) -> None:
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:  # noqa: D102 - http.client contract
        sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self.socket_path)
        self.sock = sock


class LabBridgeClient:
    """Thin client for the lab's unix-socket HTTP API."""

    def __init__(self, socket_path: str | None = None,
                 token: str | None = None) -> None:
        settings = get_settings()
        self.socket_path = socket_path or os.environ.get(
            "SDN_LAB_SOCKET", "/tmp/sdn-lab.sock")
        # Prefer explicit token, then env var, then /tmp/sdn-lab.token, then settings
        env_token = os.environ.get("LAB_API_TOKEN", "")
        token_file = "/tmp/sdn-lab.token"
        file_token = ""
        if not env_token and os.path.exists(token_file):
            try:
                with open(token_file, "r") as f:
                    file_token = f.read().strip()
            except OSError:
                pass

        if token:
            self.token = token
        elif env_token:
            self.token = env_token
        elif file_token:
            self.token = file_token
        elif settings.bridge_token_configured:
            self.token = settings.lab_bridge_token
        else:
            self.token = ""  # No token configured - lab will be open
        self.timeout = float(getattr(settings, "lab_bridge_timeout_seconds", 30.0))

    # ------------------------------------------------------------------
    # plumbing
    # ------------------------------------------------------------------
    def _request(self, method: str, path: str,
                 body: dict[str, Any] | None = None) -> Any:
        payload = json.dumps(body).encode() if body is not None else None
        headers = {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        # Only send Authorization header if we have a non-empty token
        # If token is empty, don't send the header at all (lab will allow open access)
        if self.token and self.token.strip():
            headers["Authorization"] = f"Bearer {self.token.strip()}"

        conn = _UnixHTTPConnection(self.socket_path, timeout=self.timeout)
        try:
            conn.request(method, path, body=payload, headers=headers)
            response = conn.getresponse()
            raw = response.read().decode(errors="replace")
        except (FileNotFoundError, ConnectionRefusedError, ConnectionError,
                TimeoutError, OSError) as exc:
            raise LabUnavailable(
                f"network lab not reachable at {self.socket_path}: {exc}") from exc
        finally:
            conn.close()

        if response.status >= 400:
            raise LabUnavailable(
                f"network lab returned {response.status} for {path}: {raw[:200]}")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LabUnavailable(
                f"network lab returned non-JSON for {path}: {raw[:200]}") from exc

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
