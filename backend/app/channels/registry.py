"""Channel wiring.

``CHANNEL_MODE=fake`` (the default) returns the in-process fakes;
``CHANNEL_MODE=real`` returns the SMTP/IMAP/Twilio/Slack implementations. The
engine only ever sees the ``Channels`` bundle, so switching modes is a config
change and never a code change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.channels.base import ChatChannel, EmailChannel, InboundEmailSource, SMSChannel
from app.config import Settings, get_settings


@dataclass
class Channels:
    email: EmailChannel
    sms: SMSChannel
    slack: ChatChannel
    inbound: InboundEmailSource
    mode: str = "fake"


def build_channels(settings: Settings | None = None) -> Channels:
    settings = settings or get_settings()

    if settings.channel_mode == "fake":
        from app.channels.fake import (
            FakeEmailChannel,
            FakeInboundEmailSource,
            FakeSlackChannel,
            FakeSMSChannel,
        )

        return Channels(
            email=FakeEmailChannel(),
            sms=FakeSMSChannel(),
            slack=FakeSlackChannel(),
            inbound=FakeInboundEmailSource(),
            mode="fake",
        )

    from app.channels.imap import IMAPEmailSource
    from app.channels.slack import SlackChatChannel
    from app.channels.smtp import SMTPEmailChannel
    from app.channels.twilio import TwilioSMSChannel

    return Channels(
        email=SMTPEmailChannel(settings),
        sms=TwilioSMSChannel(settings),
        slack=SlackChatChannel(settings),
        inbound=IMAPEmailSource(settings),
        mode="real",
    )


def describe_channels(channels: Channels) -> dict[str, Any]:
    return {"mode": channels.mode}
