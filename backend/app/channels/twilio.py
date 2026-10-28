"""Twilio SMS."""

from __future__ import annotations

import httpx

from app.channels.base import SendResult
from app.config import Settings, get_settings

API_TEMPLATE = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"


class TwilioSMSChannel:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def send(self, *, from_number: str, to_address: str, body: str) -> SendResult:
        sid = self.settings.twilio_account_sid
        token = self.settings.twilio_auth_token
        if not sid or not token:
            return SendResult(
                ok=False,
                error="Twilio credentials are not configured",
                retryable=False,
            )
        if not from_number:
            return SendResult(ok=False, error="No sending number was provided", retryable=False)

        try:
            response = httpx.post(
                API_TEMPLATE.format(sid=sid),
                data={"From": from_number, "To": to_address, "Body": body},
                auth=(sid, token),
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            return SendResult(ok=False, error=f"Twilio transport error: {exc}", retryable=True)

        if response.status_code >= 500 or response.status_code == 429:
            return SendResult(
                ok=False,
                error=f"Twilio {response.status_code}",
                retryable=True,
                raw={"status_code": response.status_code},
            )

        if response.status_code >= 400:
            try:
                detail = response.json()
            except ValueError:
                detail = {"message": response.text[:200]}
            message = str(detail.get("message", detail))
            # 21610 is "unsubscribed recipient", 21211 is an invalid number -
            # both are permanent for this contact.
            permanent = any(code in message for code in ("21610", "21211", "21614"))
            return SendResult(
                ok=False,
                error=f"Twilio {response.status_code}: {message}",
                retryable=not permanent,
                bounce_type=None if permanent else None,
                raw={"status_code": response.status_code, "detail": detail},
            )

        payload = response.json()
        return SendResult(ok=True, provider_message_id=str(payload.get("sid", "")), raw=payload)


# Twilio status callback values (delivered / undelivered / failed / queued ...)
TWILIO_DELIVERED = ("delivered", "sent")
TWILIO_UNDELIVERED = ("undelivered",)
TWILIO_FAILED = ("failed",)
