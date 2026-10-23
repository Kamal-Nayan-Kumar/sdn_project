"""FastAPI entrypoint for the trustworthy-agentic SDN backend.

Responsibilities kept deliberately small: validate configuration at startup,
open the database, mount the routers, and expose `/health`. All policy lives in
`app.safety.gates` and all agent behaviour in `app.agents.graph`.

Two rules this module enforces:

*   It never falls back silently. If the LLM is unconfigured the system still
    runs (the agent has deterministic fallbacks) but `/health` reports it, and
    if the lab is unreachable the system says so rather than fabricating
    telemetry.
*   CORS is restricted to the configured `FRONTEND_ORIGIN`. This is a local
    research tool; a wildcard origin would let any page on the machine drive
    the controller.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import lab as lab_routes
from app.api.routes import runs as run_routes
from app.core.config import get_settings
from app.db.session import check_db_connection, init_engine
from app.services.lab_bridge import LabBridgeClient, LabUnavailable

LOG = logging.getLogger(__name__)

DESCRIPTION = """\
Backend for intent-based self-healing SDN over a local OpenFlow lab.

The agent proposes; a deterministic validator decides; the controller executes;
and recovery is verified against fresh telemetry before anything is reported as
fixed. The LLM is used only for intent parsing, evidence narration and
explanation -- never for the decision to change the network.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open shared resources and report their state at startup."""
    settings = get_settings()

    init_engine()
    from app.db.repository import init_db, load_runs_into_registry
    init_db()
    load_runs_into_registry()
    db_ok, db_detail = check_db_connection()
    LOG.info("database: %s (%s)", "ok" if db_ok else "UNAVAILABLE", db_detail)

    if not settings.llm_configured:
        LOG.warning(
            "GROQ_API_KEY is not configured. The agent will run in fully "
            "deterministic mode and will report that the LLM was not used "
            "rather than pretending otherwise.")

    if settings.is_postgres:
        LOG.info("database backend: PostgreSQL (%s)", settings.database_url)
    else:
        LOG.info("database backend: SQLite fallback (%s)", settings.database_url)

    app.state.lab = LabBridgeClient()
    if settings.proactive_monitor_enabled:
        from app.services.proactive_monitor import monitor
        monitor.start(app.state.lab)

    yield
    from app.services.proactive_monitor import monitor
    monitor.stop()
    LOG.info("shutting down")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Trustworthy Agentic SDN",
        description=DESCRIPTION,
        version="0.1.0",
        lifespan=lifespan,
    )

    # A local tool driven by a specific dev server, not a public API.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.frontend_origin.split(",")
                       if o.strip()],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, Any]:
        """Liveness plus an honest account of what is actually wired up.

        Reports degraded rather than hiding it: `degraded` is true whenever a
        dependency the demo needs is missing, so an operator is never told the
        system is ready when it cannot reach the lab.
        """
        db_ok, db_detail = check_db_connection()

        lab_status: dict[str, Any]
        try:
            lab_status = app.state.lab.status()
        except LabUnavailable as exc:
            lab_status = {"reachable": False, "running": False, "error": str(exc)}
        except Exception as exc:  # pragma: no cover - defensive
            lab_status = {"reachable": False, "running": False, "error": f"{type(exc).__name__}: {exc}"}

        # Normalise the lab's self-reported shape ("running"/"controller_reachable")
        # into the boolean this endpoint has always promised ("reachable"), so
        # the dashboard stops reading a perpetual "degraded".
        if "reachable" not in lab_status:
            lab_status["reachable"] = bool(
                lab_status.get("running") and lab_status.get("controller_reachable"))

        degraded = not (db_ok and lab_status.get("reachable"))

        return {
            "status": "ok" if not degraded else "degraded",
            "degraded": degraded,
            "lab_mode": settings.lab_mode,
            "database": {"ok": db_ok, "detail": db_detail,
                         "backend": "postgresql" if settings.is_postgres
                         else "sqlite"},
            "lab": lab_status,
            "llm": {"configured": settings.llm_configured,
                    "model": settings.groq_model if settings.llm_configured
                    else None},
            "safety": {
                "require_human_approval_for_risky_actions":
                    settings.require_human_approval_for_risky_actions,
                "evidence_min_confidence": settings.evidence_min_confidence,
                "congestion_utilization_threshold":
                    settings.congestion_utilization_threshold,
            },
        }

    app.include_router(lab_routes.router)
    app.include_router(run_routes.router)

    return app


app = create_app()