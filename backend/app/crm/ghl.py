"""GoHighLevel (HighLevel) CRM adapter.

Mirrors what the n8n workflow did with the HighLevel node - read contacts, and
write back notes/tasks/opportunities - but over the v2 REST API with an explicit
location scope and idempotent upserts.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.crm.base import CrmContact, CrmResult

logger = logging.getLogger(__name__)

API_VERSION = "2021-07-28"


class GoHighLevelAdapter:
    name = "ghl"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.base = self.settings.ghl_api_base.rstrip("/")
        self.location_id = self.settings.ghl_location_id

    # -- plumbing ---------------------------------------------------------
    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.ghl_api_token}",
            "Version": API_VERSION,
            "Content-Type": "application/json",
        }

    @property
    def configured(self) -> bool:
        return bool(self.settings.ghl_api_token and self.location_id)

    def _request(self, method: str, path: str, **kwargs: Any) -> CrmResult:
        if not self.configured:
            return CrmResult(ok=False, error="GoHighLevel is not configured")
        url = f"{self.base}{path}"
        try:
            response = httpx.request(
                method, url, headers=self._headers, timeout=20.0, **kwargs
            )
        except httpx.HTTPError as exc:
            return CrmResult(ok=False, error=f"GHL transport error: {exc}")

        if response.status_code >= 400:
            return CrmResult(
                ok=False, error=f"GHL {response.status_code}: {response.text[:200]}"
            )
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        external_id = ""
        if isinstance(payload, dict):
            external_id = str(
                payload.get("id")
                or (payload.get("contact") or {}).get("id")
                or (payload.get("opportunity") or {}).get("id")
                or ""
            )
        return CrmResult(ok=True, external_id=external_id)

    # -- adapter ----------------------------------------------------------
    def upsert_contact(self, contact: CrmContact) -> CrmResult:
        payload: dict[str, Any] = {
            "locationId": self.location_id,
            "firstName": contact.first_name,
            "lastName": contact.last_name,
            "companyName": contact.company,
            "tags": contact.tags,
            "customFields": contact.custom_fields,
        }
        if contact.email:
            payload["email"] = contact.email
        if contact.phone:
            payload["phone"] = contact.phone
        return self._request("POST", "/contacts/upsert", json=payload)

    def find_contact(self, email: str = "", phone: str = "") -> CrmContact | None:
        query = email or phone
        if not query:
            return None
        result = self._request(
            "GET", "/contacts/", params={"locationId": self.location_id, "query": query}
        )
        if not result.ok:
            logger.warning("GHL contact lookup failed: %s", result.error)
            return None
        # ``_request`` already consumed the body; do the lookup inline instead.
        try:
            response = httpx.get(
                f"{self.base}/contacts/",
                headers=self._headers,
                params={"locationId": self.location_id, "query": query},
                timeout=20.0,
            )
            contacts = response.json().get("contacts", [])
        except (httpx.HTTPError, ValueError):
            return None
        if not contacts:
            return None
        first = contacts[0]
        return CrmContact(
            external_id=str(first.get("id", "")),
            email=str(first.get("email", "")),
            phone=str(first.get("phone", "")),
            first_name=str(first.get("firstName", "")),
            last_name=str(first.get("lastName", "")),
            company=str(first.get("companyName", "")),
            tags=list(first.get("tags", []) or []),
        )

    def list_contacts(self, *, limit: int = 100, tag: str | None = None) -> list[CrmContact]:
        params: dict[str, Any] = {"locationId": self.location_id, "limit": limit}
        if tag:
            params["query"] = tag
        try:
            response = httpx.get(
                f"{self.base}/contacts/", headers=self._headers, params=params, timeout=25.0
            )
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("GHL contact list failed: %s", exc)
            return []
        results: list[CrmContact] = []
        for row in payload.get("contacts", []):
            results.append(
                CrmContact(
                    external_id=str(row.get("id", "")),
                    email=str(row.get("email", "")),
                    phone=str(row.get("phone", "")),
                    first_name=str(row.get("firstName", "")),
                    last_name=str(row.get("lastName", "")),
                    company=str(row.get("companyName", "")),
                    tags=list(row.get("tags", []) or []),
                )
            )
        return results

    def add_tag(self, external_id: str, tag: str) -> CrmResult:
        return self._request(
            "POST", f"/contacts/{external_id}/tags", json={"tags": [tag]}
        )

    def create_note(self, external_id: str, body: str) -> CrmResult:
        return self._request(
            "POST",
            f"/contacts/{external_id}/notes",
            json={"body": body, "userId": None},
        )

    def create_task(self, external_id: str, title: str, body: str = "") -> CrmResult:
        return self._request(
            "POST",
            f"/contacts/{external_id}/tasks",
            json={"title": title, "body": body, "completed": False},
        )

    def create_opportunity(
        self, external_id: str, name: str, *, stage: str = "new", value: float = 0.0
    ) -> CrmResult:
        return self._request(
            "POST",
            "/opportunities/",
            json={
                "locationId": self.location_id,
                "contactId": external_id,
                "name": name,
                "status": stage,
                "monetaryValue": value,
            },
        )

    def update_opportunity_stage(self, opportunity_id: str, stage: str) -> CrmResult:
        return self._request(
            "PUT", f"/opportunities/{opportunity_id}", json={"status": stage}
        )
