"""Reaction routing.

The spec's routing table (spec 8.3) lives here. A sequence may override *where*
a reaction goes by naming a target step in ``graph["reactions"]``; compliance
events (opt-out, hard bounce) deliberately cannot be redirected.

If a graph declares an override, the graph author owns the whole branch - the
built-in Slack alert and CRM side effects are skipped so a sequence with its own
``notify_slack`` step does not double-notify.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app import constants as c

ACTION_IGNORE = "ignore"
ACTION_GOTO = "goto"
ACTION_EXIT = "exit"
ACTION_EXIT_SUPPRESS = "exit_suppress"
ACTION_PAUSE = "pause"
ACTION_SUBSTITUTE = "substitute"
ACTION_DELAY = "delay"

# Reaction keys the graph may point at a step.
_OVERRIDABLE = (
    "replied_positive",
    "replied_negative",
    "replied_neutral",
    "replied",
    "auto_reply",
    "meeting_booked",
    "sms_undelivered",
)


@dataclass
class ReactionPlan:
    action: str
    description: str
    target_step: str | None = None
    exit_reason: str | None = None
    suppression: tuple[str, str] | None = None  # (channel, reason)
    notify: str | None = None
    create_task: bool = False
    task_title: str = ""
    sentiment: str = ""
    delay_seconds: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def stops_automation(self) -> bool:
        return self.action in (ACTION_EXIT, ACTION_EXIT_SUPPRESS, ACTION_PAUSE)


def _default_plans() -> dict[str, ReactionPlan]:
    return {
        "replied_positive": ReactionPlan(
            action=ACTION_EXIT_SUPPRESS,
            description="Positive reply: alert the team, create a CRM task, stop automation",
            exit_reason=c.EXIT_REPLIED_POSITIVE,
            suppression=None,
            notify=c.NOTIFY_POSITIVE_REPLY,
            create_task=True,
            task_title="Follow up on positive reply",
            sentiment=c.SENTIMENT_POSITIVE,
        ),
        "replied_negative": ReactionPlan(
            action=ACTION_EXIT_SUPPRESS,
            description="Negative reply: alert the team, suppress, stop automation",
            exit_reason=c.EXIT_REPLIED_NEGATIVE,
            suppression=("all", c.SUPPRESS_OBJECTION),
            notify=c.NOTIFY_NEGATIVE_REPLY,
            sentiment=c.SENTIMENT_NEGATIVE,
        ),
        "replied_neutral": ReactionPlan(
            action=ACTION_PAUSE,
            description="Question or neutral reply: alert the team and wait for a human",
            suppression=None,
            notify=c.NOTIFY_NEUTRAL_REPLY,
            create_task=True,
            task_title="Answer the prospect's reply",
            sentiment=c.SENTIMENT_NEUTRAL,
        ),
        "replied": ReactionPlan(
            action=ACTION_EXIT,
            description="Reply received: stop automation",
            exit_reason=c.EXIT_REPLIED,
            notify=c.NOTIFY_REACTION,
        ),
        "auto_reply": ReactionPlan(
            action=ACTION_DELAY,
            description="Out-of-office detected: keep the cadence, shift past the return date",
            delay_seconds=86400,
            sentiment=c.SENTIMENT_AUTO_REPLY,
        ),
        "meeting_booked": ReactionPlan(
            action=ACTION_EXIT_SUPPRESS,
            description="Meeting booked: goal reached, suppress and exit",
            exit_reason=c.EXIT_MEETING_BOOKED,
            suppression=("all", c.SUPPRESS_MEETING_BOOKED),
            notify=c.NOTIFY_MEETING_BOOKED,
        ),
        "sms_undelivered": ReactionPlan(
            action=ACTION_SUBSTITUTE,
            description="SMS undelivered: substitute the fallback channel",
        ),
        "unsubscribed": ReactionPlan(
            action=ACTION_EXIT_SUPPRESS,
            description="Opt-out: suppress everywhere and stop automation",
            exit_reason=c.EXIT_UNSUBSCRIBED,
            suppression=("all", c.SUPPRESS_UNSUBSCRIBED),
        ),
        "bounced_hard": ReactionPlan(
            action=ACTION_EXIT_SUPPRESS,
            description="Hard bounce: suppress email and stop automation",
            exit_reason=c.EXIT_BOUNCED_HARD,
            suppression=("email", c.SUPPRESS_HARD_BOUNCE),
        ),
    }


DEFAULT_PLANS = _default_plans()

# Compliance events are never redirectable by a sequence graph.
_NOT_OVERRIDABLE = ("unsubscribed", "bounced_hard")


def reaction_key(event_type: str, sentiment: str | None = None) -> str | None:
    """Map an event type (plus sentiment) onto a reaction key."""
    # An out-of-office is not a real reply: it must be routed to the delay plan
    # rather than credited as a reply or treated as radio silence.
    if event_type == c.EVENT_EMAIL_AUTO_REPLY:
        return "auto_reply"
    if event_type in c.REPLY_EVENTS:
        if sentiment == c.SENTIMENT_AUTO_REPLY:
            return "auto_reply"
        if sentiment == c.SENTIMENT_POSITIVE:
            return "replied_positive"
        if sentiment == c.SENTIMENT_NEGATIVE:
            return "replied_negative"
        if sentiment is None:
            return "replied"
        return "replied_neutral"
    if event_type == c.EVENT_MEETING_BOOKED:
        return "meeting_booked"
    if event_type == c.EVENT_EMAIL_BOUNCED:
        return "bounced_hard"
    if event_type in (c.EVENT_CONTACT_UNSUBSCRIBED, c.EVENT_EMAIL_UNSUBSCRIBED):
        return "unsubscribed"
    if event_type in (c.EVENT_SMS_UNDELIVERED, c.EVENT_SMS_FAILED):
        return "sms_undelivered"
    return None


def plan_for_event(
    event_type: str,
    *,
    sentiment: str | None = None,
    overrides: dict[str, str] | None = None,
) -> ReactionPlan:
    key = reaction_key(event_type, sentiment)
    if key is None:
        return ReactionPlan(action=ACTION_IGNORE, description="No routing for this event")

    overrides = overrides or {}
    base = DEFAULT_PLANS.get(key) or ReactionPlan(
        action=ACTION_IGNORE, description="No default plan"
    )

    if (
        key in _OVERRIDABLE
        and key not in _NOT_OVERRIDABLE
        and overrides.get(key)
    ):
        return ReactionPlan(
            action=ACTION_GOTO,
            description=f"Sequence routes '{key}' to step '{overrides[key]}'",
            target_step=overrides[key],
            sentiment=base.sentiment,
            meta={"reaction_key": key, "override": True},
        )

    plan = ReactionPlan(**{**base.__dict__})
    plan.meta = {"reaction_key": key, "override": False}
    return plan
