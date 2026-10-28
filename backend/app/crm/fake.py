"""In-memory CRM adapter (CRM_MODE=fake).

Records every call so tests can assert that a positive reply created a task, and
keeps a small contact store so flows that need a lookup still work.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.crm.base import CrmContact, CrmResult


class FakeCRMAdapter:
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.contacts: dict[str, CrmContact] = {}
        self.notes: list[dict[str, str]] = []
        self.tasks: list[dict[str, str]] = []
        self.opportunities: list[dict[str, Any]] = []
        self.tags: list[dict[str, str]] = []

    # -- helpers ----------------------------------------------------------
    def calls_of(self, method: str) -> list[dict[str, Any]]:
        return [payload for name, payload in self.calls if name == method]

    # -- adapter ----------------------------------------------------------
    def upsert_contact(self, contact: CrmContact) -> CrmResult:
        self.calls.append(("upsert_contact", {"email": contact.email, "phone": contact.phone}))
        key = contact.email or contact.phone or contact.external_id
        external_id = contact.external_id or f"fake-{uuid4().hex[:8]}"
        contact.external_id = external_id
        self.contacts[key] = contact
        return CrmResult(ok=True, external_id=external_id)

    def find_contact(self, email: str = "", phone: str = "") -> CrmContact | None:
        self.calls.append(("find_contact", {"email": email, "phone": phone}))
        return self.contacts.get(email) or self.contacts.get(phone)

    def list_contacts(self, *, limit: int = 100, tag: str | None = None) -> list[CrmContact]:
        self.calls.append(("list_contacts", {"limit": limit, "tag": tag}))
        contacts = list(self.contacts.values())
        if tag:
            contacts = [contact for contact in contacts if tag in contact.tags]
        return contacts[:limit]

    def add_tag(self, external_id: str, tag: str) -> CrmResult:
        self.calls.append(("add_tag", {"external_id": external_id, "tag": tag}))
        self.tags.append({"external_id": external_id, "tag": tag})
        return CrmResult(ok=True, external_id=external_id)

    def create_note(self, external_id: str, body: str) -> CrmResult:
        self.calls.append(("create_note", {"external_id": external_id}))
        self.notes.append({"external_id": external_id, "body": body})
        return CrmResult(ok=True, external_id=external_id)

    def create_task(self, external_id: str, title: str, body: str = "") -> CrmResult:
        self.calls.append(("create_task", {"external_id": external_id, "title": title}))
        self.tasks.append({"external_id": external_id, "title": title, "body": body})
        return CrmResult(ok=True, external_id=external_id)

    def create_opportunity(
        self, external_id: str, name: str, *, stage: str = "new", value: float = 0.0
    ) -> CrmResult:
        self.calls.append(("create_opportunity", {"external_id": external_id, "name": name}))
        opportunity_id = f"opp-{uuid4().hex[:8]}"
        self.opportunities.append(
            {"id": opportunity_id, "external_id": external_id, "name": name, "stage": stage}
        )
        return CrmResult(ok=True, external_id=opportunity_id)

    def update_opportunity_stage(self, opportunity_id: str, stage: str) -> CrmResult:
        self.calls.append(
            ("update_opportunity_stage", {"opportunity_id": opportunity_id, "stage": stage})
        )
        for opportunity in self.opportunities:
            if opportunity["id"] == opportunity_id:
                opportunity["stage"] = stage
        return CrmResult(ok=True, external_id=opportunity_id)
