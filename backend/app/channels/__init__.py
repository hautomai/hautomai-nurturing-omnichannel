"""Channel adapters: email, SMS and Slack, in fake and real flavours."""

from app.channels.base import (
    ChatChannel,
    EmailChannel,
    InboundEmail,
    InboundEmailSource,
    NotifyResult,
    SendResult,
    SMSChannel,
)
from app.channels.registry import Channels, build_channels

__all__ = [
    "ChatChannel",
    "Channels",
    "EmailChannel",
    "InboundEmail",
    "InboundEmailSource",
    "NotifyResult",
    "SendResult",
    "SMSChannel",
    "build_channels",
]
