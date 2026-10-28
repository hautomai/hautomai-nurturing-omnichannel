"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import constants as c
from app.api import api_router
from app.channels.registry import describe_channels, build_channels
from app.config import Settings, get_settings
from app.db import create_all, session_scope
from app.security import ensure_bootstrap_admin
from app.seed import seed_all

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.auto_create_tables:
        create_all(settings)
        logger.info("Tables ensured for %s", settings.database_url.split("@")[-1])

    # The bootstrap admin must exist regardless of seeding: without it there is
    # no way to authenticate into a fresh deployment. Idempotent.
    try:
        with session_scope() as db:
            ensure_bootstrap_admin(db, settings)
    except Exception:  # noqa: BLE001 - never block boot on the bootstrap admin
        logger.exception("Could not ensure the bootstrap admin user")

    if settings.seed_on_startup:
        try:
            with session_scope() as db:
                ids = seed_all(db, settings)
            logger.info("Seeded workspace: client=%s sequence=%s", ids["client_id"], ids["sequence_id"])
        except Exception:  # noqa: BLE001 - seeding must never block boot
            logger.exception("Seeding failed; continuing without seed data")
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="Cross-Channel Nurture Sequences",
        version="0.1.0",
        description=(
            "Omnichannel nurture: coordinated email, SMS and Slack, where the next "
            "touchpoint is chosen by how the prospect reacted to the last one."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)

    @app.get("/healthz", tags=["meta"])
    def healthz() -> dict[str, str]:
        return {"status": "ok", "env": settings.app_env}

    @app.get("/api/meta", tags=["meta"])
    def meta() -> dict[str, object]:
        """Tells the UI whether it is looking at fake channels or the real thing."""
        channels = build_channels(settings)
        return {
            "app_env": settings.app_env,
            "channels": describe_channels(channels),
            "channel_mode": settings.channel_mode,
            "crm_mode": settings.crm_mode,
            "policy": {
                "min_hours_between_touches": settings.min_hours_between_contact_touches,
                "max_touches": settings.max_touches_per_enrollment,
                "max_sms": settings.max_sms_per_enrollment,
                "sms_quiet_hours": settings.sms_quiet_hours_enabled,
                "sms_window": [settings.sms_window_start_hour, settings.sms_window_end_hour],
                "sms_allow_weekends": settings.sms_allow_weekends,
            },
            "reaction_policy": c.REACTION_POLICY_CANCEL_AND_REBRANCH,
            "tracking": {"open_pixel": False, "link_rewriting": False, "signals": ["reply", "silence", "meeting"]},
        }

    return app


app = create_app()
