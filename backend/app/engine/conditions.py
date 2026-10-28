"""Branch predicates.

v1 predicates are deliberately small and observable. There is no
``opened``/``clicked`` predicate because tracking is off (D15) - branching is on
replies, sentiment, silence and meetings, which are all things the engine can
actually see.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


@dataclass
class ConditionContext:
    """Everything a predicate may look at, assembled once per evaluation."""

    has_reply: bool = False
    last_reply_sentiment: str | None = None
    last_reply_at: datetime | None = None
    meeting_booked: bool = False
    available_channels: set[str] = field(default_factory=set)
    tags: set[str] = field(default_factory=set)
    custom_fields: dict[str, object] = field(default_factory=dict)
    # When the previous touch fired. Anchors "no reply for N days" to elapsed
    # time rather than wall-clock time (D17).
    reference_at: datetime | None = None


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def evaluate(
    predicate: dict[str, object] | None,
    ctx: ConditionContext,
    *,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Return ``(result, human-readable rule description)``."""
    now = now or datetime.now(timezone.utc)
    if not isinstance(predicate, dict):
        return False, "no predicate"

    kind = predicate.get("type")

    if kind == "replied":
        return ctx.has_reply, "replied?" if ctx.has_reply else "no reply"

    if kind == "reply_sentiment":
        wanted = str(predicate.get("value", ""))
        matched = ctx.has_reply and ctx.last_reply_sentiment == wanted
        return matched, f"reply sentiment is {wanted}"

    if kind == "no_reply_for":
        try:
            days = float(predicate.get("days", 0) or 0)
        except (TypeError, ValueError):
            days = 0.0
        if ctx.has_reply:
            return False, "already replied"
        anchor = _as_utc(ctx.reference_at) or _as_utc(ctx.last_reply_at)
        if anchor is None:
            # Nothing has been sent yet, so silence cannot be established.
            return False, "no reference point"
        elapsed = now - anchor
        due = elapsed >= timedelta(days=days)
        return due, f"{elapsed.total_seconds() / 3600:.1f}h of silence (need {days:.0f}d)"

    if kind == "meeting_booked":
        return ctx.meeting_booked, "meeting booked?" if ctx.meeting_booked else "no meeting"

    if kind == "channel_available":
        channel = str(predicate.get("channel", ""))
        available = channel in ctx.available_channels
        return available, f"{channel} available" if available else f"{channel} unavailable"

    if kind == "has_tag":
        tag = str(predicate.get("tag", ""))
        matched = tag in ctx.tags
        return matched, f"tag {tag}={'present' if matched else 'absent'}"

    if kind == "custom_field_equals":
        field_name = str(predicate.get("field", ""))
        expected = predicate.get("value")
        actual = ctx.custom_fields.get(field_name)
        matched = str(actual) == str(expected)
        return matched, f"{field_name}={actual!r} (want {expected!r})"

    return False, f"unknown predicate {kind!r}"
