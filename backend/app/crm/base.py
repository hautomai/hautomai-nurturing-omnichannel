"""CRM adapter interface.

GoHighLevel is the first implementation (D5); HubSpot and Pipedrive are stubs so
the interface is exercised by more than one shape before it ossifies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class CrmContact:
    external_id: str
    email: str = ""
    phone: str = ""
    first_name: str = ""
    last_name: str = ""
    company: str = ""
    tags: list[str] = field(default_factory=list)
    custom_fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class CrmResult:
    ok: bool
    external_id: str = ""
    error: str = ""
    skipped: bool = False


class CRMAdapter(Protocol):
    name: str

    def upsert_contact(self, contact: CrmContact) -> CrmResult: ...

    def find_contact(self, email: str = "", phone: str = "") -> CrmContact | None: ...

    def list_contacts(self, *, limit: int = 100, tag: str | None = None) -> list[CrmContact]: ...

    def add_tag(self, external_id: str, tag: str) -> CrmResult: ...

    def create_note(self, external_id: str, body: str) -> CrmResult: ...

    def create_task(self, external_id: str, title: str, body: str = "") -> CrmResult: ...

    def create_opportunity(
        self, external_id: str, name: str, *, stage: str = "new", value: float = 0.0
    ) -> CrmResult: ...

    def update_opportunity_stage(self, opportunity_id: str, stage: str) -> CrmResult: ...


class NullCRMAdapter:
    """Used when CRM_MODE=none: every call is a recorded no-op."""

    name = "none"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _skip(self, method: str, **kwargs: Any) -> CrmResult:
        self.calls.append((method, kwargs))
        return CrmResult(ok=True, skipped=True)

    def upsert_contact(self, contact: CrmContact) -> CrmResult:
        return self._skip("upsert_contact", email=contact.email)

    def find_contact(self, email: str = "", phone: str = "") -> CrmContact | None:
        self.calls.append(("find_contact", {"email": email, "phone": phone}))
        return None

    def list_contacts(self, *, limit: int = 100, tag: str | None = None) -> list[CrmContact]:
        self.calls.append(("list_contacts", {"limit": limit, "tag": tag}))
        return []

    def add_tag(self, external_id: str, tag: str) -> CrmResult:
        return self._skip("add_tag", external_id=external_id, tag=tag)

    def create_note(self, external_id: str, body: str) -> CrmResult:
        return self._skip("create_note", external_id=external_id)

    def create_task(self, external_id: str, title: str, body: str = "") -> CrmResult:
        return self._skip("create_task", external_id=external_id, title=title)

    def create_opportunity(
        self, external_id: str, name: str, *, stage: str = "new", value: float = 0.0
    ) -> CrmResult:
        return self._skip("create_opportunity", external_id=external_id, name=name)

    def update_opportunity_stage(self, opportunity_id: str, stage: str) -> CrmResult:
        return self._skip("update_opportunity_stage", opportunity_id=opportunity_id, stage=stage)
