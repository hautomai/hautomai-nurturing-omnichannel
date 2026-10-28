"""Fake channels: nothing leaves the process.

These are the default (``CHANNEL_MODE=fake``). They record everything they were
asked to do and support scripted failures, so engine behaviour - retries, bounce
handling, substitutions, inbox pausing - is fully testable with no network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app import constants as c
from app.channels.base import InboundEmail, NotifyResult, SendResult


@dataclass
class RecordedSend:
    channel: str
    from_address: str
    to_address: str
    subject: str
    body: str
    mailbox: str
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    provider_message_id: str = ""
    is_substitution: bool = False


class FakeEmailChannel:
    """Records sends; ``failures`` scripts the next N results."""

    def __init__(self) -> None:
        self.sent: list[RecordedSend] = []
        self.failures: list[SendResult] = []
        self.counter = 0

    def send(
        self,
        *,
        inbox: Any,
        to_address: str,
        from_name: str,
        from_email: str,
        reply_to: str,
        subject: str,
        body: str,
        headers: dict[str, str] | None = None,
    ) -> SendResult:
        if self.failures:
            return self.failures.pop(0)
        self.counter += 1
        message_id = f"<fake-email-{self.counter}@{from_email.split('@')[-1] or 'example.com'}>"
        self.sent.append(
            RecordedSend(
                channel=c.CHANNEL_EMAIL,
                from_address=from_email,
                to_address=to_address,
                subject=subject,
                body=body,
                mailbox=getattr(inbox, "email", str(inbox)),
                provider_message_id=message_id,
            )
        )
        return SendResult(ok=True, provider_message_id=message_id)


class FakeSMSChannel:
    def __init__(self) -> None:
        self.sent: list[RecordedSend] = []
        self.failures: list[SendResult] = []
        self.counter = 0

    def send(self, *, from_number: str, to_address: str, body: str) -> SendResult:
        if self.failures:
            return self.failures.pop(0)
        self.counter += 1
        sid = f"SM{self.counter:08d}fake"
        self.sent.append(
            RecordedSend(
                channel=c.CHANNEL_SMS,
                from_address=from_number,
                to_address=to_address,
                subject="",
                body=body,
                mailbox=from_number,
                provider_message_id=sid,
            )
        )
        return SendResult(ok=True, provider_message_id=sid)


class FakeSlackChannel:
    def __init__(self) -> None:
        self.notifications: list[dict[str, Any]] = []
        self.failures: list[NotifyResult] = []

    def notify(
        self, *, channel: str, text: str, blocks: list[dict[str, Any]] | None = None
    ) -> NotifyResult:
        if self.failures:
            return self.failures.pop(0)
        self.notifications.append({"channel": channel, "text": text, "blocks": blocks})
        return NotifyResult(ok=True)

    # Convenience for assertions in tests.
    def contains(self, needle: str) -> bool:
        return any(needle.lower() in item["text"].lower() for item in self.notifications)


class FakeInboundEmailSource:
    """Returns queued inbound messages; the poller consumes them one at a time."""

    def __init__(self) -> None:
        self.pending: list[InboundEmail] = []

    def queue(self, message: InboundEmail) -> None:
        self.pending.append(message)

    def poll(self, inbox: Any) -> list[InboundEmail]:
        messages, self.pending = self.pending, []
        return messages
