"""Application settings.

Every value the engine, channels and CRM adapters need comes from here so that
behaviour can be changed without touching code, and so tests can override it
with ``Settings(...)`` directly.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ChannelMode = Literal["fake", "real"]
CrmMode = Literal["none", "fake", "ghl"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- core -------------------------------------------------------------
    app_env: str = "development"
    # ``NoDecode`` stops pydantic-settings JSON-decoding the raw env value, so
    # the comma-separated form in .env (``CORS_ORIGINS=http://a,http://b``) works
    # exactly like the default list. Without it a bare URL raises SettingsError.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )
    auto_create_tables: bool = True
    seed_on_startup: bool = True
    database_url: str = "sqlite+pysqlite:///./nurture.db"
    redis_url: str = "redis://localhost:6379/0"

    # --- auth -------------------------------------------------------------
    secret_key: str = "insecure-development-key"
    access_token_expire_minutes: int = 720
    bootstrap_admin_email: str = "ops@hautomai.com"
    bootstrap_admin_password: str = "change-me"

    # --- channels ---------------------------------------------------------
    channel_mode: ChannelMode = "fake"
    imap_poll_interval_seconds: int = 120
    default_inbox_daily_cap: int = 25
    smtp_timeout_seconds: int = 30
    smtp_test_inbox: str = ""

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_webhook_signing_secret: str = ""

    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_alert_channel: str = "#nurture-alerts"

    # Shared secret for the generic inbound email webhook. Empty means the
    # endpoint is open, which is the local-development default; set it whenever
    # the endpoint is reachable from the internet.
    webhook_secret: str = ""

    # --- crm --------------------------------------------------------------
    crm_mode: CrmMode = "fake"
    ghl_api_base: str = "https://services.leadconnectorhq.com"
    ghl_api_token: str = ""
    ghl_location_id: str = ""
    ghl_webhook_secret: str = ""

    # --- engine policy (v1 confirmed defaults) ----------------------------
    min_hours_between_contact_touches: int = 4
    max_touches_per_enrollment: int = 6
    max_sms_per_enrollment: int = 2
    sms_quiet_hours_enabled: bool = True
    sms_window_start_hour: int = 9
    sms_window_end_hour: int = 18
    sms_allow_weekends: bool = False
    default_contact_timezone: str = "Asia/Singapore"
    inbox_bounce_rate_limit: float = 0.03
    inbox_bounce_sample: int = 100
    inbox_max_consecutive_failures: int = 3
    timer_max_attempts: int = 3

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a real list, a JSON array, or a comma-separated string."""
        if not isinstance(value, str):
            return value
        text = value.strip()
        if text.startswith("["):
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, list):
                return decoded
        return [item.strip() for item in text.split(",") if item.strip()]

    @property
    def is_fake_channels(self) -> bool:
        return self.channel_mode == "fake"

    @property
    def is_testing(self) -> bool:
        return self.app_env == "test"


@lru_cache
def get_settings() -> Settings:
    return Settings()
