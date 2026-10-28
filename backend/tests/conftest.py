"""Test configuration.

The environment is set *before* any application module is imported, because
settings, the engine and the session factory are all process-level singletons.

Tests run against a real SQLite file (not ``:memory:``) so that the API's own
sessions and the test's session see the same database - the same code path the
Postgres deployment uses, just a different dialect.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="nurture-tests-"))

os.environ.update(
    {
        "APP_ENV": "test",
        "DATABASE_URL": f"sqlite+pysqlite:///{_TMP / 'test.db'}",
        "CHANNEL_MODE": "fake",
        "CRM_MODE": "fake",
        "AUTO_CREATE_TABLES": "true",
        "SEED_ON_STARTUP": "false",
        "SECRET_KEY": "test-secret-key",
        "BOOTSTRAP_ADMIN_EMAIL": "ops@test.example",
        "BOOTSTRAP_ADMIN_PASSWORD": "test-password",
        "SLACK_ALERT_CHANNEL": "#test-alerts",
        # Most engine tests assert exact send behaviour, so the SMS window is off
        # by default and switched on only by the tests that cover it.
        "SMS_QUIET_HOURS_ENABLED": "false",
        "DEFAULT_CONTACT_TIMEZONE": "UTC",
        "MIN_HOURS_BETWEEN_CONTACT_TOUCHES": "0",
        "REDIS_URL": "redis://localhost:6379/15",
    }
)

import pytest  # noqa: E402

from app import db as db_module  # noqa: E402
from app.channels.registry import build_channels  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.crm.fake import FakeCRMAdapter  # noqa: E402
from app.db import Base, create_all, get_engine, get_session_factory, reset_engine  # noqa: E402
from app.security import ensure_bootstrap_admin  # noqa: E402
from tests import factories  # noqa: E402


@pytest.fixture()
def settings():
    """Fresh settings object per test so mutations cannot leak."""
    get_settings.cache_clear()
    settings = get_settings()
    yield settings
    get_settings.cache_clear()


@pytest.fixture()
def db(settings):
    reset_engine()
    create_all(settings)
    session = get_session_factory(settings)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=get_engine(settings))
        reset_engine()


@pytest.fixture()
def channels():
    """Fresh fake channels for each test (nothing leaves the process)."""
    bundle = build_channels(get_settings())
    yield bundle


@pytest.fixture()
def crm():
    return FakeCRMAdapter()


@pytest.fixture()
def workspace(db, settings, channels, crm):
    """A ready-to-send workspace: client, persona, inbox, number, templates, sequence."""
    from app.api import deps

    deps.reset_cached_adapters()

    client = factories.make_client(db)
    persona = factories.make_persona(db, client)
    inbox = factories.make_inbox(db, client, email="outbox1@test.example")
    number = factories.make_phone(db, client)
    factories.default_templates(db, client)
    sequence = factories.make_sequence(db, client, graph=factories.three_touch_graph())
    contact = factories.make_contact(db, client)
    db.commit()

    return {
        "client": client,
        "persona": persona,
        "inbox": inbox,
        "number": number,
        "sequence": sequence,
        "contact": contact,
        "channels": channels,
        "crm": crm,
        "settings": settings,
        "db": db,
    }


@pytest.fixture()
def api_client(db, settings):
    """A TestClient with the seeded admin user and a bearer token."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    ensure_bootstrap_admin(db, settings)
    db.commit()

    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/auth/login",
            json={
                "email": settings.bootstrap_admin_email,
                "password": settings.bootstrap_admin_password,
            },
        )
        assert response.status_code == 200, response.text
        client.headers.update({"Authorization": f"Bearer {response.json()['access_token']}"})
        yield client
