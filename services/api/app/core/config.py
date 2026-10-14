"""Runtime configuration for the trustworthy-agentic-sdn backend.

Everything is read from the environment (see `.env.example`). The module
validates the configuration at import/first-use time and *fails safely*:
if a required secret is missing we raise a clear error rather than
silently pretending the LLM is available.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load nearest .env (project root) without overriding real env vars.
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Strongly-typed application settings."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # --- LLM ---------------------------------------------------------------
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    groq_model: str = Field(default="openai/gpt-oss-120b", alias="GROQ_MODEL")
    groq_timeout_seconds: float = Field(default=30.0, alias="GROQ_TIMEOUT_SECONDS")

    # --- Database ----------------------------------------------------------
    database_url: str = Field(
        default="sqlite+pysqlite:///./sdn_dev.db", alias="DATABASE_URL"
    )

    # --- API ---------------------------------------------------------------
    backend_host: str = Field(default="127.0.0.1", alias="BACKEND_HOST")
    backend_port: int = Field(default=8000, alias="BACKEND_PORT")
    frontend_origin: str = Field(default="http://localhost:3000", alias="FRONTEND_ORIGIN")

    # --- Network lab -------------------------------------------------------
    lab_mode: Literal["real", "mock"] = Field(default="real", alias="LAB_MODE")
    lab_bridge_url: str = Field(default="ws://127.0.0.1:8765", alias="LAB_BRIDGE_URL")
    lab_bridge_token: str = Field(default="", alias="LAB_BRIDGE_TOKEN")

    # --- Trustworthiness policy -------------------------------------------
    max_evidence_retries: int = Field(default=2, alias="MAX_EVIDENCE_RETRIES")
    max_recovery_retries: int = Field(default=2, alias="MAX_RECOVERY_RETRIES")
    telemetry_max_age_seconds: float = Field(default=5.0, alias="TELEMETRY_MAX_AGE_SECONDS")
    evidence_min_confidence: float = Field(default=0.6, alias="EVIDENCE_MIN_CONFIDENCE")
    congestion_utilization_threshold: float = Field(
        default=0.8, alias="CONGESTION_UTILIZATION_THRESHOLD"
    )

    # --- Proactive monitor -------------------------------------------------
    proactive_monitor_enabled: bool = Field(
        default=False, alias="PROACTIVE_MONITOR_ENABLED"
    )
    proactive_sample_interval_seconds: float = Field(
        default=5.0, alias="PROACTIVE_SAMPLE_INTERVAL_SECONDS"
    )
    proactive_violation_count: int = Field(default=3, alias="PROACTIVE_VIOLATION_COUNT")
    proactive_cooldown_seconds: float = Field(
        default=60.0, alias="PROACTIVE_COOLDOWN_SECONDS"
    )

    # --- Safety ------------------------------------------------------------
    require_human_approval_for_risky_actions: bool = Field(
        default=True, alias="REQUIRE_HUMAN_APPROVAL_FOR_RISKY_ACTIONS"
    )

    # ---------------------------------------------------------------------
    @field_validator("max_evidence_retries", "max_recovery_retries")
    @classmethod
    def _non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("retry limits must be >= 0")
        return v

    @field_validator("evidence_min_confidence", "congestion_utilization_threshold")
    @classmethod
    def _fraction(cls, v: float) -> float:
        if not 0.0 < v <= 1.0:
            raise ValueError("value must be in (0, 1]")
        return v

    # ---------------------------------------------------------------------
    @property
    def llm_configured(self) -> bool:
        """True only when a non-placeholder Groq key is present."""
        key = (self.groq_api_key or "").strip()
        return bool(key) and not key.startswith("replace_with")

    @property
    def bridge_token_configured(self) -> bool:
        tok = (self.lab_bridge_token or "").strip()
        return bool(tok) and not tok.startswith("replace_with")

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()


def reset_settings_cache() -> None:
    """Test helper: drop the cached settings."""
    get_settings.cache_clear()
