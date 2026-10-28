"""CRM wiring."""

from __future__ import annotations

from app.config import Settings, get_settings
from app.crm.base import CRMAdapter, NullCRMAdapter


def build_crm(settings: Settings | None = None) -> CRMAdapter:
    settings = settings or get_settings()

    if settings.crm_mode == "ghl":
        from app.crm.ghl import GoHighLevelAdapter

        return GoHighLevelAdapter(settings)
    if settings.crm_mode == "fake":
        from app.crm.fake import FakeCRMAdapter

        return FakeCRMAdapter()
    return NullCRMAdapter()
