"""Settings parsing.

Regression tests for a startup crash: ``cors_origins`` is a list field, so
pydantic-settings JSON-decoded the raw env value and a plain
``CORS_ORIGINS=http://localhost:5173`` killed the process with

    SettingsError: error parsing value for field "cors_origins"

The field is now ``Annotated[list[str], NoDecode]``. These tests pin both the
comma-separated form documented in ``.env.example`` and the explicit JSON form.
"""

from __future__ import annotations

import json

import pytest

from app.config import Settings


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    # ``_env_file=None`` so a developer's local ``.env`` cannot change the result.
    return Settings(_env_file=None)  # type: ignore[arg-type]


def test_cors_origins_parses_a_bare_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # The exact value shipped in .env.example - the one that used to crash boot.
    settings = _settings(monkeypatch, CORS_ORIGINS="http://localhost:5173")
    assert settings.cors_origins == ["http://localhost:5173"]


def test_cors_origins_parses_a_comma_separated_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        monkeypatch,
        CORS_ORIGINS="http://localhost:5173, http://localhost:4173 ,https://app.example.com",
    )
    assert settings.cors_origins == [
        "http://localhost:5173",
        "http://localhost:4173",
        "https://app.example.com",
    ]


def test_cors_origins_ignores_blank_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, CORS_ORIGINS="http://localhost:5173,")
    assert settings.cors_origins == ["http://localhost:5173"]


def test_cors_origins_still_accepts_json(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(
        monkeypatch, CORS_ORIGINS=json.dumps(["http://a.test", "http://b.test"])
    )
    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_cors_origins_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    settings = Settings(_env_file=None)  # type: ignore[arg-type]
    assert settings.cors_origins == ["http://localhost:5173"]


def test_booleans_and_numbers_parse_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other list/typed fields keep working alongside the NoDecode fix."""
    settings = _settings(
        monkeypatch,
        AUTO_CREATE_TABLES="false",
        SEED_ON_STARTUP="true",
        CHANNEL_MODE="fake",
        CRM_MODE="ghl",
        MAX_TOUCHES_PER_ENROLLMENT="9",
        INBOX_BOUNCE_RATE_LIMIT="0.05",
    )
    assert settings.auto_create_tables is False
    assert settings.seed_on_startup is True
    assert settings.channel_mode == "fake"
    assert settings.crm_mode == "ghl"
    assert settings.max_touches_per_enrollment == 9
    assert settings.inbox_bounce_rate_limit == pytest.approx(0.05)
    assert settings.is_fake_channels is True
