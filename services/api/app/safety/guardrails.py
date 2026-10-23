"""Deterministic guardrails around the agent's inputs and outputs.

These are rules, not suggestions. They run before the graph is built (input)
and after the model writes (output), and their verdicts are published on the
run's stream so a refusal is as visible as an action.

Why a table rather than an `if` ladder: each rule is a name, a pattern and a
reason, and the operator can add one without touching the control flow. The
first matching rule wins, which keeps the reported reason stable.

The LLM never evaluates these. A model that could be talked out of a guardrail
is not a guardrail.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Iterable

from pydantic import BaseModel, Field


class GuardrailStage(str, Enum):
    INPUT = "input"
    EVIDENCE = "evidence"
    VALIDATION = "validation"
    OUTPUT = "output"


class GuardrailDecision(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"


class GuardrailVerdict(BaseModel):
    stage: GuardrailStage
    decision: GuardrailDecision
    rule: str
    reason: str

    @property
    def allowed(self) -> bool:
        return self.decision is GuardrailDecision.ALLOW


def _allow(stage: GuardrailStage, rule: str = "allowed") -> GuardrailVerdict:
    return GuardrailVerdict(
        stage=stage, decision=GuardrailDecision.ALLOW, rule=rule,
        reason="no guardrail rule matched")


def _block(stage: GuardrailStage, rule: str, reason: str) -> GuardrailVerdict:
    return GuardrailVerdict(
        stage=stage, decision=GuardrailDecision.BLOCK, rule=rule, reason=reason)


#: (rule, pattern, reason). Ordered: the first match is the one reported.
_REQUEST_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "instruction_override",
        re.compile(
            r"\b(ignore|disregard|forget)\b[^.\n]{0,40}\b(previous|prior|above|"
            r"system|your)\b[^.\n]{0,30}\b(instructions?|rules?|prompt)\b",
            re.IGNORECASE),
        "The request tries to override the agent's instructions. Operator "
        "instructions and the safety policy are not negotiable.",
    ),
    (
        "persona_switch",
        re.compile(
            r"\b(you are now|from now on you are|pretend (you are|to be)|"
            r"act as (an? )?(admin|root|superuser|unrestricted))\b",
            re.IGNORECASE),
        "The request asks the agent to adopt a different identity with more "
        "privileges. It only ever acts as this agent, under this policy.",
    ),
    (
        "safety_bypass",
        re.compile(
            r"\b(disable|turn off|switch off|bypass|circumvent|skip)\b[^.\n]{0,40}"
            r"\b(safety|guardrail|validator|validation|gate|approval|policy)\b",
            re.IGNORECASE),
        "The request asks to disable a safety gate. Gates are enforced in "
        "Python and cannot be turned off by a message.",
    ),
    (
        "destructive_operation",
        re.compile(
            r"\b(delete|drop|flush|wipe|erase)\b[^.\n]{0,40}\b(all|every)\b"
            r"[^.\n]{0,20}\b(flows?|rules?|tables?|state)\b",
            re.IGNORECASE),
        "The request asks to remove all flow state. This agent only installs "
        "a validated reroute; it has no destructive tool.",
    ),
    (
        "host_command",
        re.compile(
            r"\b(rm\s+-rf|shutdown|reboot|mkfs|dd\s+if=|kill\s+-9|curl\s+http)\b",
            re.IGNORECASE),
        "The request contains a host command. The agent's tool layer has no "
        "shell; it can only read telemetry and reroute a flow.",
    ),
    (
        "protected_flow_tamper",
        re.compile(
            r"\b(voice|protected)\b[^.\n]{0,40}\b(reroute|move|migrate|shift)\b"
            r"|\b(reroute|move|migrate|shift)\b[^.\n]{0,40}\b(voice|protected)\b",
            re.IGNORECASE),
        "The request targets the protected voice flow. Protected traffic is "
        "measured before and after every change and must not be moved.",
    ),
)

#: Patterns that must never appear in text sent to a browser tab.
_SECRET_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("api_key", re.compile(r"\b(sk-[A-Za-z0-9]{16,}|gsk_[A-Za-z0-9]{16,})\b"),
     "The text contained something shaped like an API key."),
    ("env_assignment", re.compile(
        r"\b(GROQ_API_KEY|LAB_BRIDGE_TOKEN|DATABASE_URL)\s*=\s*\S+"),
     "The text contained an environment assignment."),
    ("bearer_header", re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}"),
     "The text contained a bearer token."),
)

MAX_REQUEST_CHARS = 2000


def _first_match(
    stage: GuardrailStage,
    text: str,
    rules: Iterable[tuple[str, re.Pattern[str], str]],
) -> GuardrailVerdict | None:
    for rule, pattern, reason in rules:
        if pattern.search(text):
            return _block(stage, rule, reason)
    return None


def screen_request(message: str) -> GuardrailVerdict:
    """Screen a natural-language request before any tool can run."""
    text = (message or "").strip()
    if not text:
        return _block(GuardrailStage.INPUT, "empty_request",
                      "The message was empty, so there was nothing to act on.")
    if len(text) > MAX_REQUEST_CHARS:
        return _block(
            GuardrailStage.INPUT, "request_too_long",
            f"The request is {len(text)} characters; the limit is "
            f"{MAX_REQUEST_CHARS}. Send the intent and the constraint only.")
    return _first_match(GuardrailStage.INPUT, text, _REQUEST_RULES) or _allow(
        GuardrailStage.INPUT)


def screen_output(text: str) -> GuardrailVerdict:
    """Screen model output before it reaches the browser."""
    return _first_match(GuardrailStage.OUTPUT, text or "", _SECRET_RULES) or _allow(
        GuardrailStage.OUTPUT)


def redact(text: str) -> str:
    """Remove anything the output guardrail flags, keeping the rest readable."""
    redacted = text or ""
    for _, pattern, _ in _SECRET_RULES:
        redacted = pattern.sub("[redacted]", redacted)
    return redacted


__all__ = [
    "GuardrailDecision",
    "GuardrailStage",
    "GuardrailVerdict",
    "max_request_chars",
    "redact",
    "screen_output",
    "screen_request",
]

#: Exposed for the dashboard's policy line without leaking pattern internals.
max_request_chars = MAX_REQUEST_CHARS
