"""Thin, fail-closed wrapper around the Groq chat API.

The agent nodes call `chat_json()` when an LLM can help and fall back to
deterministic logic when it cannot. Two properties matter for trustworthiness:

*   **It never gates safety.** Nothing here decides whether an action is
    allowed; the deterministic gates in `app.safety.gates` do. This module
    produces prose and structured hints only.
*   **It fails loudly, not silently.** A timeout, a missing key, or output
    that does not parse as JSON raises `LLMUnavailable` (or a subclass) so the
    calling node records *why* the model was skipped in the audit trail instead
    of quietly substituting invented values.

`schema_hint` is a lightweight example of the shape expected. It is included in
the prompt as guidance, never used to coerce or repair the response.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

from app.core.config import get_settings

LOG = logging.getLogger(__name__)

# Providers wrap JSON in prose often enough that a fenced block or a bare
# object has to be recoverable. These are tried in order; the first that parses
# as a JSON *object* wins.
_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)

_client_lock = threading.Lock()
_client: Any = None


class LLMUnavailable(RuntimeError):
    """The LLM could not be used. The caller must fall back deterministically."""


class LLMTimeout(LLMUnavailable):
    """The request exceeded `GROQ_TIMEOUT_SECONDS`."""


class LLMOutputError(LLMUnavailable):
    """The model replied, but the reply was not usable JSON."""


def _get_client() -> Any:
    """Lazily build a shared `groq.Groq` client.

    Construction happens on first use and is cached: importing this module must
    not fail when no API key is present, because the agent has to run in a
    fully deterministic mode in that case.
    """
    global _client
    if _client is not None:
        return _client

    with _client_lock:
        if _client is not None:
            return _client

        settings = get_settings()
        if not settings.llm_configured:
            raise LLMUnavailable("GROQ_API_KEY is not configured")

        try:
            from groq import Groq
        except ImportError as exc:  # pragma: no cover - dependency missing
            raise LLMUnavailable(f"groq client is not installed: {exc}") from exc

        _client = Groq(
            api_key=settings.groq_api_key,
            timeout=settings.groq_timeout_seconds,
            max_retries=0,  # retries are the caller's decision, not ours
        )
        return _client


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
) -> dict[str, Any]:
    """Ask the model for a JSON object.

    Args:
        system:  instructions describing the task and its constraints.
        user:    the data or question, typically telemetry.
        schema_hint: illustrative object describing the expected keys and
            value types. Purely prompt material.

    Returns:
        The parsed JSON object.

    Raises:
        LLMTimeout: the request took longer than the configured timeout.
        LLMOutputError: the reply could not be parsed as a JSON object.
        LLMUnavailable: no key configured, client unusable, or the API
            returned an error.
    """
    settings = get_settings()
    client = _get_client()

    messages = [
        {"role": "system", "content": system + _render_hint(schema_hint)},
        {"role": "user", "content": user},
    ]

    try:
        completion = client.chat.completions.create(
            model=settings.groq_model,
            messages=messages,
            temperature=0.0,  # reproducible decisions matter more than variety
            response_format={"type": "json_object"},
            timeout=settings.groq_timeout_seconds,
        )
    except Exception as exc:
        name = type(exc).__name__
        if "Timeout" in name:
            raise LLMTimeout(
                f"{settings.groq_model} exceeded "
                f"{settings.groq_timeout_seconds}s") from exc
        raise LLMUnavailable(f"Groq request failed: {exc}") from exc

    try:
        content = completion.choices[0].message.content or ""
    except (AttributeError, IndexError, KeyError) as exc:
        raise LLMOutputError(f"unexpected response shape: {exc}") from exc

    return _extract_json(content)


def llm_available() -> bool:
    """True when a usable key is configured. Does not make a network call."""
    return get_settings().llm_configured


def reset_client_cache() -> None:
    """Test helper: force the next call to rebuild the client."""
    global _client
    with _client_lock:
        _client = None