"""Branch predicates."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import constants as c
from app.engine.conditions import ConditionContext, evaluate

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)


def test_replied():
    assert evaluate({"type": "replied"}, ConditionContext(has_reply=True), now=NOW)[0]
    assert not evaluate({"type": "replied"}, ConditionContext(has_reply=False), now=NOW)[0]


def test_reply_sentiment():
    ctx = ConditionContext(has_reply=True, last_reply_sentiment=c.SENTIMENT_POSITIVE)
    assert evaluate({"type": "reply_sentiment", "value": "positive"}, ctx, now=NOW)[0]
    assert not evaluate({"type": "reply_sentiment", "value": "negative"}, ctx, now=NOW)[0]


def test_reply_sentiment_needs_an_actual_reply():
    ctx = ConditionContext(has_reply=False, last_reply_sentiment=c.SENTIMENT_POSITIVE)
    assert not evaluate({"type": "reply_sentiment", "value": "positive"}, ctx, now=NOW)[0]


def test_no_reply_for_uses_the_reference_point():
    ctx = ConditionContext(reference_at=NOW - timedelta(days=4))
    assert evaluate({"type": "no_reply_for", "days": 3}, ctx, now=NOW)[0]
    assert not evaluate({"type": "no_reply_for", "days": 5}, ctx, now=NOW)[0]


def test_no_reply_for_is_false_once_they_replied():
    ctx = ConditionContext(
        has_reply=True,
        last_reply_sentiment=c.SENTIMENT_POSITIVE,
        reference_at=NOW - timedelta(days=10),
    )
    assert not evaluate({"type": "no_reply_for", "days": 1}, ctx, now=NOW)[0]


def test_no_reply_for_without_a_reference_point_is_false():
    assert not evaluate({"type": "no_reply_for", "days": 1}, ConditionContext(), now=NOW)[0]


def test_meeting_booked():
    assert evaluate({"type": "meeting_booked"}, ConditionContext(meeting_booked=True), now=NOW)[0]


def test_channel_available():
    ctx = ConditionContext(available_channels={c.CHANNEL_EMAIL})
    assert evaluate({"type": "channel_available", "channel": "email"}, ctx, now=NOW)[0]
    assert not evaluate({"type": "channel_available", "channel": "sms"}, ctx, now=NOW)[0]


def test_has_tag():
    ctx = ConditionContext(tags={"enterprise"})
    assert evaluate({"type": "has_tag", "tag": "enterprise"}, ctx, now=NOW)[0]
    assert not evaluate({"type": "has_tag", "tag": "smb"}, ctx, now=NOW)[0]


def test_custom_field_equals_coerces_to_string():
    ctx = ConditionContext(custom_fields={"role_title": "Mechanical Engineer"})
    assert evaluate(
        {"type": "custom_field_equals", "field": "role_title", "value": "Mechanical Engineer"},
        ctx,
        now=NOW,
    )[0]
    assert not evaluate(
        {"type": "custom_field_equals", "field": "role_title", "value": "Other"}, ctx, now=NOW
    )[0]


def test_unknown_predicate_is_false_with_a_readable_rule():
    result, description = evaluate({"type": "opened_email"}, ConditionContext(), now=NOW)
    assert result is False
    assert "unknown predicate" in description


def test_missing_predicate_is_false():
    assert evaluate(None, ConditionContext(), now=NOW)[0] is False
