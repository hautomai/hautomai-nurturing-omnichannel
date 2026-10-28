"""Shared API dependencies.

Channels and CRM adapters are process-level singletons so that in fake mode the
recorded state (sent messages, notifications) persists across requests, which
makes local development behave like a real workspace.
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.channels.registry import Channels, build_channels
from app.config import Settings, get_settings
from app.crm.base import CRMAdapter
from app.crm.registry import build_crm
from app.models import Client


@lru_cache
def get_channels() -> Channels:
    return build_channels(get_settings())


@lru_cache
def get_crm() -> CRMAdapter:
    return build_crm(get_settings())


def reset_cached_adapters() -> None:
    """Tests call this between cases to get fresh fakes."""
    get_channels.cache_clear()
    get_crm.cache_clear()


def get_settings_dep() -> Settings:
    return get_settings()


def list_clients(db: Session) -> list[Client]:
    return list(db.scalars(select(Client).order_by(Client.created_at)).all())


def resolve_client(db: Session, client_id: str | None = None) -> Client:
    if client_id:
        client = db.get(Client, client_id)
        if client is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
        return client
    client = db.scalar(select(Client).order_by(Client.created_at).limit(1))
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No client exists yet. Run the seed (python -m app.seed).",
        )
    return client


def get_or_404(db: Session, model, entity_id: str, label: str):
    entity = db.get(model, entity_id)
    if entity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} not found"
        )
    return entity
