"""Runtime settings. Every value comes from the environment (prefix ``LEVIGATE_``) or a local ``.env``.

Secrets are never stored here in plain text for production: ``secret_key`` and the AI endpoint key are expected
to be injected from the vault (Azure Key Vault) into the process environment by the deployment.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LEVIGATE_", env_file=".env", extra="ignore")

    env: Literal["dev", "demo", "test", "prod"] = "dev"
    tenant_id: str = "demo"
    pharmacy_name: str = "Maple Street Compounding (demo)"
    timezone: str = "America/Chicago"

    # Databases. The app database is read + write; the warehouse is reached only through the read-only connector.
    app_db_url: str = "sqlite+aiosqlite:///./data/levigate_app.db"
    warehouse_url: str = "sqlite:///./data/demo_warehouse.db"
    connector_allowed_hosts: list[str] = Field(default_factory=list)
    connector_query_timeout_s: float = 20.0

    # Sessions (C-04): idle timeout, cookie hardening.
    secret_key: SecretStr = Field(default_factory=lambda: SecretStr(secrets.token_urlsafe(48)))
    session_idle_minutes: int = 15
    session_cookie_secure: bool = False
    allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # Sync model.
    sync_enabled: bool = True
    sync_interval_minutes: int = 15

    # API4 / LLM unbounded consumption.
    rate_limit_per_minute: int = 240
    max_page_size: int = 200
    ai_daily_tokens_per_user: int = 60_000
    ai_timeout_s: float = 8.0

    # AI provider: "offline" uses deterministic templates and sends nothing anywhere.
    ai_provider: Literal["offline", "azure_openai"] = "offline"
    azure_openai_endpoint: str | None = None
    azure_openai_deployment: str | None = None
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_api_key: SecretStr | None = None

    # C-11: mask PHI for users outside the US unless the client approves that user.
    offshore_phi_masking: bool = True

    # Business-hours window used for off-hours export alerts (C-13 / CC-05).
    business_hours_start: int = 7
    business_hours_end: int = 19

    @property
    def demo_login_enabled(self) -> bool:
        return self.env in ("dev", "demo", "test")

    @model_validator(mode="after")
    def _prod_guards(self) -> Settings:
        if self.env == "prod":
            if self.app_db_url.startswith("sqlite"):
                raise ValueError("prod requires PostgreSQL (LEVIGATE_APP_DB_URL)")
            if not self.session_cookie_secure:
                raise ValueError("prod requires LEVIGATE_SESSION_COOKIE_SECURE=true")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
