"""Structural smoke tests.

These do not run a lab. They catch the failure mode that cost the project the
most time: a module that has never been imported. If a route, node or schema
cannot even load, no amount of testing below it helps.
"""

from __future__ import annotations

import importlib

import pytest

BACKEND_MODULES = [
    "app.core.config",
    "app.db.session",
    "app.models.orm",
    "app.schemas.domain",
    "app.services.lab_bridge",
    "app.services.llm",
    "app.tools",
    "app.safety.gates",
    "app.agents.state",
    "app.agents.workflow",
    "app.agents.graph",
    "app.main",
]


@pytest.mark.parametrize("module", BACKEND_MODULES)
def test_module_imports(module: str) -> None:
    """Every backend module must import. Individually, because a single
    failure otherwise hides the rest."""
    try:
        importlib.import_module(module)
    except Exception as exc:  # pragma: no cover - reporting aid
        pytest.fail(f"{module} failed to import: {type(exc).__name__}: {exc}")


def test_app_is_a_fastapi_instance() -> None:
    from fastapi import FastAPI

    from app.main import app

    assert isinstance(app, FastAPI)
    paths = {route.path for route in app.routes}
    assert "/health" in paths


def test_every_tool_has_a_valid_input_schema() -> None:
    """The tool layer is an allow-list: a tool whose input cannot be validated
    is not callable. Zero-argument tools (get_topology, reset_lab) are fine;
    a tool declaring no *validators* is not."""
    from pydantic import BaseModel

    from app.tools import TOOL_REGISTRY

    assert TOOL_REGISTRY
    for name, schema in TOOL_REGISTRY.items():
        assert issubclass(schema, BaseModel), name
        try:
            schema.model_json_schema()
        except Exception as exc:  # pragma: no cover - reporting aid
            pytest.fail(f"{name} has an unusable schema: {exc}")


def test_no_tool_accepts_arbitrary_extra_fields() -> None:
    """`extra` must stay forbidden: an unvalidated passthrough would let a
    caller smuggle anything into the tool layer."""
    from app.tools import TOOL_REGISTRY

    for name, schema in TOOL_REGISTRY.items():
        assert schema.model_config.get("extra") != "allow", (
            f"{name} allows extra fields; the allow-list is no longer a "
            "boundary")


def test_graph_compiles() -> None:
    """The LangGraph topology must be constructable and connected."""
    from app.agents.graph import build_graph

    compiled = build_graph(_StubLabClient())
    assert compiled is not None
    assert set(compiled.get_graph().nodes)


def test_graph_includes_all_ten_steps() -> None:
    from app.agents.graph import build_graph

    compiled = build_graph(_StubLabClient())
    nodes = set(compiled.get_graph().nodes)
    expected = {
        "intent", "observe", "evidence", "diagnose", "validate",
        "execute", "observe_after", "verify", "abstain", "satisfied",
        "stop", "hold", "finish",
    }
    missing = expected - nodes
    assert not missing, f"graph is missing nodes: {sorted(missing)}"


def test_safety_policy_defaults_are_conservative() -> None:
    """Approval-by-default for anything above low risk. If this ever flips,
    the trustworthiness claim of the project is gone."""
    from app.schemas.domain import RiskLevel

    from app.safety.gates import SafetyPolicy

    policy = SafetyPolicy()
    assert RiskLevel.MEDIUM in policy.require_approval_for
    assert RiskLevel.HIGH in policy.require_approval_for


def test_llm_module_fails_closed_without_a_key() -> None:
    """With no API key the module raises a typed error rather than guessing."""
    from app.services.llm import LLMUnavailable, chat_json, llm_available

    if llm_available():
        pytest.skip("GROQ_API_KEY is configured; cannot test the no-key path")
    with pytest.raises(LLMUnavailable):
        chat_json(system="s", user="u")


class _StubLabClient:
    """Minimum surface the graph needs; it never makes a request."""

    def __init__(self) -> None:
        self.token = ""
        self.socket_path = "/tmp/does-not-exist.sock"

    def _request(self, *args, **kwargs):  # pragma: no cover - never called
        raise AssertionError("the graph must not talk to a lab just to compile")
