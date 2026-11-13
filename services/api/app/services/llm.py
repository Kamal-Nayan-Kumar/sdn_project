"""Thin, fail-closed wrapper around an OpenAI-compatible chat API.

The agent nodes call `chat_json()` when an LLM can help and fall back to
deterministic logic when it cannot. Two properties matter for trustworthiness:

*   **It never gates safety.** Nothing here decides whether an action is
    allowed; the deterministic gates in `app.safety.gates` do. This module
    produces prose and structured hints only.
*   **It fails loudly, not silently.** A timeout, a missing key, or output
    that does not parse as JSON raises `LLMUnavailable` (or a subclass) so the
    calling node records *why* the model was skipped in the audit trail instead
    of quietly substituting invented values.

The client is provider-agnostic. It talks to any OpenAI-compatible
``chat/completions`` endpoint: Groq's default (``https://api.groq.com/openai/v1``),
NVIDIA NIM (``https://integrate.api.nvidia.com/v1``), OpenRouter, or a local
gateway. The endpoint, key and model come from settings
(``LLM_BASE_URL`` / ``LLM_API_KEY`` / ``LLM_MODEL``; or ``GROQ_*`` for Groq's
historic defaults), so switching providers needs no code change.

`schema_hint` is a lightweight example of the shape expected. It is included in
the prompt as guidance, never used to coerce or repair the response.
"""

from __future__ import annotations

import json
import logging
import re
import socket
import threading
import urllib.error
import urllib.request
from typing import Any, Callable

from app.core.config import get_settings

LOG = logging.getLogger(__name__)

# Providers wrap JSON in prose often enough that a fenced block or a bare
# object has to be recoverable. These are tried in order; the first that parses
# as a JSON *object* wins.
_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


class LLMUnavailable(RuntimeError):
    """The LLM could not be used. The caller must fall back deterministically."""


class LLMTimeout(LLMUnavailable):
    """The request exceeded the configured timeout."""


class LLMOutputError(LLMUnavailable):
    """The model replied, but the reply was not usable JSON."""


def _endpoint() -> str:
    settings = get_settings()
    base = (settings.llm_endpoint or "https://api.groq.com/openai/v1").rstrip("/")
    return f"{base}/chat/completions"


def _headers(*, stream: bool = False) -> dict[str, str]:
    settings = get_settings()
    return {
        "Authorization": f"Bearer {settings.llm_key}",
        "Content-Type": "application/json",
        # Some provider gateways drop urllib's default user agent.
        "User-Agent": "curl/8.0.1",
        "Accept": "text/event-stream" if stream else "application/json",
    }


def _post_json(endpoint: str, headers: dict[str, str], payload: dict[str, Any],
               timeout: float) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(endpoint, data=data, method="POST",
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            body = str(exc)
        raise LLMUnavailable(f"provider returned HTTP {exc.code}: {body}") from exc
    except (socket.timeout, TimeoutError) as exc:
        raise LLMTimeout(f"request exceeded {timeout}s") from exc
    except urllib.error.URLError as exc:
        raise LLMUnavailable(f"provider not reachable: {exc.reason}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMOutputError(f"provider returned non-JSON: {raw[:200]!r}") from exc


def _extract_json(text: str) -> dict[str, Any]:
    """Pull a JSON object out of a model reply, or raise `LLMOutputError`."""
    candidates: list[str] = []
    fenced = _JSON_FENCE.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(text)
    objectish = _JSON_OBJECT.search(text)
    if objectish:
        candidates.append(objectish.group(0))

    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
        raise LLMOutputError(
            f"model returned a JSON {type(parsed).__name__}, expected an object")

    raise LLMOutputError(f"model reply is not JSON: {text[:200]!r}")


def _render_hint(schema_hint: dict[str, Any] | None) -> str:
    if not schema_hint:
        return ""
    rendered = json.dumps(schema_hint, indent=2, sort_keys=True)
    return ("\nRespond with a single JSON object and nothing else. "
            f"It must have exactly these keys:\n{rendered}\n")


def chat_json(
    *,
    system: str,
    user: str,
    schema_hint: dict[str, Any] | None = None,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """Ask the model for a JSON object.

    Args:
        system:  instructions describing the task and its constraints.
        user:    the data or question, typically telemetry.
        schema_hint: illustrative object describing the expected keys and
            value types. Purely prompt material.
        temperature: decoding temperature. The default (0) is what the
            agent's own calls use, because reproducible decisions matter more
            than variety; the boundary-probe experiment raises it to draw
            several distinct proposals from the same prompt.

    Returns:
        The parsed JSON object.

    Raises:
        LLMTimeout: the request took longer than the configured timeout.
        LLMOutputError: the reply could not be parsed as a JSON object.
        LLMUnavailable: no key configured, or the provider returned an error.
    """
    settings = get_settings()
    if not settings.llm_configured:
        raise LLMUnavailable("no LLM key is configured")

    payload = {
        "model": settings.llm_model_name,
        "messages": [
            {"role": "system", "content": system + _render_hint(schema_hint)},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": 2000,
        "response_format": {"type": "json_object"},
    }

    try:
        data = _post_json(_endpoint(), _headers(), payload,
                          settings.groq_timeout_seconds)
    except LLMUnavailable as exc:
        raise LLMUnavailable(str(exc)) from exc

    try:
        content = data["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMOutputError(f"unexpected response shape: {data!r:.200}") from exc

    return _extract_json(content)


def chat_stream(
    *,
    system: str,
    user: str,
    on_delta: Callable[[str], None],
    should_stop: Callable[[], bool] | None = None,
    temperature: float = 0.2,
) -> str:
    """Stream a prose answer, passing each delta to `on_delta` as it arrives.

    Used where the operator watches the model think (diagnosis) and where the
    final answer types itself out. The full text is returned so the caller can
    store it; the deltas are for display only. Cancellation is cooperative:
    `should_stop` is checked between chunks, and a stopped stream returns the
    text produced so far rather than raising.

    Raises the same errors as `chat_json` when the model cannot be used, so
    callers keep one fallback path.
    """
    settings = get_settings()
    if not settings.llm_configured:
        raise LLMUnavailable("no LLM key is configured")

    payload = {
        "model": settings.llm_model_name,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": 2000,
        "stream": True,
    }

    req = urllib.request.Request(_endpoint(),
                                 data=json.dumps(payload).encode("utf-8"),
                                 method="POST",
                                 headers=_headers(stream=True))
    try:
        resp = urllib.request.urlopen(req, timeout=settings.groq_timeout_seconds)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            body = str(exc)
        raise LLMUnavailable(f"provider returned HTTP {exc.code}: {body}") from exc
    except (socket.timeout, TimeoutError) as exc:
        raise LLMTimeout(f"request exceeded {settings.groq_timeout_seconds}s") from exc
    except urllib.error.URLError as exc:
        raise LLMUnavailable(f"provider not reachable: {exc.reason}") from exc

    parts: list[str] = []
    saw_any_byte = False
    try:
        with resp:
            for raw in resp:
                if should_stop is not None and should_stop():
                    break
                line = raw.decode(errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                payload_line = line[5:].strip()
                if payload_line in ("[DONE]", ""):
                    break
                try:
                    chunk = json.loads(payload_line)
                except json.JSONDecodeError:
                    continue
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta") or {}
                    # Providers differ on where reasoning vs content lives;
                    # both are carried through to the operator stream.
                    text = (delta.get("content") or "")
                    if not text:
                        text = delta.get("reasoning_content") or ""
                    if not text:
                        continue
                    saw_any_byte = True
                    parts.append(text)
                    on_delta(text)
    except Exception as exc:  # a broken stream is still a usable prefix
        if not parts:
            raise LLMUnavailable(f"stream failed: {exc}") from exc
        LOG.warning("LLM stream ended early: %s", exc)

    if not saw_any_byte:
        raise LLMUnavailable("provider returned an empty stream")
    return "".join(parts).strip()


def llm_available() -> bool:
    """True when a usable key is configured. Does not make a network call."""
    return get_settings().llm_configured


def reset_client_cache() -> None:
    """Test helper: force the next call to rebuild the client."""
    # The urllib-based client holds no persistent state; kept for API
    # compatibility with the former SDK-based implementation.
    globals().pop("_client", None)


__all__ = [
    "LLMUnavailable",
    "LLMTimeout",
    "LLMOutputError",
    "chat_json",
    "chat_stream",
    "llm_available",
    "reset_client_cache",
]
