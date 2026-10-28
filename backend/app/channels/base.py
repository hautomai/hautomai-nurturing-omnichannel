"""Channel interfaces.

Every outbound side effect in the engine goes through one of these protocols, so
``CHANNEL_MODE=fake`` gives a fully working app with no external services and
the real implementations can be swapped in by configuration alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass
class SendResult:
    ok: bool
    provider_message_id: str = ""
    error: str = ""
    # Transient failures are retried with backoff; permanent ones are classified
    # as bounces or hard failures.
    retryable: bool = False
    bounce_type: str | None = None  # constants.BOUNCE_HARD | BOUNCE_SOFT
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class NotifyResult:
    ok: bool
    error: str = ""
    retryable: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class InboundEmail:
    from_address: str
    to_address: str
    subject: str
    body: str
    message_id: str = ""
    in_reply_to: str = ""
    references: list[str] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    received_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class EmailChannel(Protocol):
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
    ) -> SendResult: ...


class SMSChannel(Protocol):
    def send(self, *, from_number: str, to_address: str, body: str) -> SendResult: ...


class ChatChannel(Protocol):
    def notify(
        self, *, channel: str, text: str, blocks: list[dict[str, Any]] | None = None
    ) -> NotifyResult: ...


class InboundEmailSource(Protocol):
    def poll(self, inbox: Any) -> list[InboundEmail]: ...
