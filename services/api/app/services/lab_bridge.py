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
        # An explicitly supplied token always wins; otherwise the token is
        # resolved from the environment or from the token file the lab writes
        # at startup.
        self._explicit_token = token
        self.token = self._resolve_token(settings)
        self.timeout = float(getattr(settings, "lab_bridge_timeout_seconds", 30.0))

    # ------------------------------------------------------------------
    # token handling
    # ------------------------------------------------------------------
    @staticmethod
    def _token_files() -> list[str]:
        user_token_file = f"/tmp/sdn-lab-{os.environ.get('USER', os.getuid())}.token"
        return ["/tmp/sdn-lab.token", user_token_file]

    def _resolve_token(self, settings: Any = None) -> str:
        """Read the lab's current token from wherever it is published.

        The lab generates a fresh token every time it starts and writes it to
        a token file. A client that captured the token once went stale after
        a lab restart, and every *mutation* then failed with HTTP 401 while
        reads kept working (the lab only requires the token on POST). That
        looked like "the agent tried to reroute and the network refused",
        which is not what happened. The token is therefore re-read whenever
        the lab rejects a request.
        """
        if self._explicit_token:
            return self._explicit_token
        env_token = os.environ.get("LAB_API_TOKEN", "")
        if env_token:
            return env_token
        for tf in self._token_files():
            if os.path.exists(tf):
                try:
                    with open(tf, "r") as f:
                        content = f.read().strip()
                    if content:
                        return content
                except OSError:
                    pass
        if settings is not None and settings.bridge_token_configured:
            return settings.lab_bridge_token
        return ""  # No token configured - lab will be open

    # ------------------------------------------------------------------
    # plumbing
    # ------------------------------------------------------------------
    def _request(self, method: str, path: str,
                 body: dict[str, Any] | None = None) -> Any:
        """Send one request, retrying once if the lab rejects our token.

        The lab regenerates its token on every start. A long-lived backend
        that kept the token it read at startup would start getting HTTP 401
        on every mutation after a lab restart -- reads still worked, because
        the lab only guards POST -- so the failure looked like the network
        refusing a reroute. Re-reading the token file and retrying makes the
        backend survive a lab restart without being restarted itself.
        """
        for attempt in (0, 1):
            status, raw = self._send(method, path, body)
            if status == 401 and attempt == 0:
                refreshed = self._resolve_token()
                if refreshed and refreshed != self.token:
                    self.token = refreshed
                    continue
            if status >= 400:
                raise LabUnavailable(
                    f"network lab returned {status} for {path}: {raw[:200]}")
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                raise LabUnavailable(
                    f"network lab returned non-JSON for {path}: {raw[:200]}") from exc
        raise LabUnavailable(f"network lab returned {status} for {path}")

    def _send(self, method: str, path: str,
              body: dict[str, Any] | None) -> tuple[int, str]:
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
            status = response.status
        except (FileNotFoundError, ConnectionRefusedError, ConnectionError,
                TimeoutError, OSError) as exc:
            raise LabUnavailable(
                f"network lab not reachable at {self.socket_path}: {exc}") from exc
        finally:
            conn.close()
        return status, raw

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
                         duration_seconds: float = 0.0,
                         report_degraded: bool = True) -> dict[str, Any]:
        return self.post("/v1/scenarios/congestion", {
            "link_id": link_id, "rate_mbps": rate_mbps,
            "duration_seconds": duration_seconds,
            "report_degraded": bool(report_degraded)})

    def reset(self) -> dict[str, Any]:
        return self.post("/v1/scenarios/reset")

    def start_background_load(self, src_host: str = "h2",
                               dst_host: str = "h4",
                               rate_mbps: float = 5.0,
                               duration_seconds: float = 0.0) -> dict[str, Any]:
        return self.post("/v1/scenarios/background_load", {
            "src_host": src_host, "dst_host": dst_host,
            "rate_mbps": rate_mbps,
            "duration_seconds": duration_seconds})

    def stop_background_load(self) -> dict[str, Any]:
        return self.post("/v1/scenarios/background_load/stop", {})

    def set_link_capacity(self, link_id: str = "s2-s4",
                          capacity_mbps: float = 100.0) -> dict[str, Any]:
        return self.post("/v1/scenarios/link_capacity", {
            "link_id": link_id, "capacity_mbps": float(capacity_mbps)})

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
