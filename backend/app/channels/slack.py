"""Slack notifications for the sales team."""

from __future__ import annotations

from typing import Any

import httpx

from app.channels.base import NotifyResult
from app.config import Settings, get_settings

POST_MESSAGE = "https://slack.com/api/chat.postMessage"


class SlackChatChannel:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def notify(
        self, *, channel: str, text: str, blocks: list[dict[str, Any]] | None = None
    ) -> NotifyResult:
        token = self.settings.slack_bot_token
        if not token:
            return NotifyResult(ok=False, error="SLACK_BOT_TOKEN is not configured")

        payload: dict[str, Any] = {"channel": channel or self.settings.slack_alert_channel, "text": text}
        if blocks:
            payload["blocks"] = blocks

        try:
            response = httpx.post(
                POST_MESSAGE,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
                timeout=15.0,
            )
        except httpx.HTTPError as exc:
            return NotifyResult(ok=False, error=f"Slack transport error: {exc}", retryable=True)

        if response.status_code >= 500 or response.status_code == 429:
            return NotifyResult(ok=False, error=f"Slack {response.status_code}", retryable=True)

        try:
            body = response.json()
        except ValueError:
            return NotifyResult(ok=False, error=f"Slack returned non-JSON ({response.status_code})")

        if not body.get("ok"):
            return NotifyResult(
                ok=False,
                error=str(body.get("error", "unknown error")),
                retryable=body.get("error") == "ratelimited",
                raw=body,
            )
        return NotifyResult(ok=True, raw=body)
