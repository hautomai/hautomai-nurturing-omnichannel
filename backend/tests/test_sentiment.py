"""Rule-based reply classification (no LLM)."""

from __future__ import annotations

from app import constants as c
from app.engine import sentiment


def test_affirmative_reply_is_positive():
    result = sentiment.classify("Re: intro", "Yes, happy to chat next week.")
    assert result.sentiment == c.SENTIMENT_POSITIVE
    assert result.matched_rule in {"affirmative", "interested"}


def test_negative_language_wins_over_a_positive_word():
    """'No thanks, sounds good for someone else' must not read as interest."""
    result = sentiment.classify("", "No thanks — sounds good but we're not interested.")
    assert result.sentiment == c.SENTIMENT_NEGATIVE


def test_opt_out_is_negative_with_its_own_rule():
    result = sentiment.classify("", "Please unsubscribe me from this list.")
    assert result.sentiment == c.SENTIMENT_NEGATIVE
    assert result.matched_rule == "opt_out"


def test_question_is_neutral():
    result = sentiment.classify("", "How much does this cost for a production run?")
    assert result.sentiment == c.SENTIMENT_NEUTRAL
    assert result.matched_rule == "question"


def test_unmatched_reply_is_neutral():
    result = sentiment.classify("", "Noted, thanks.")
    assert result.sentiment == c.SENTIMENT_NEUTRAL
    assert result.matched_rule == "unmatched"


def test_auto_reply_headers_are_detected():
    result = sentiment.classify(
        "Automatic reply: out of office",
        "I am currently out of the office until 2026-10-01.",
        {"Auto-Submitted": "auto-replied"},
    )
    assert result.sentiment == c.SENTIMENT_AUTO_REPLY
    assert result.is_auto_reply


def test_auto_reply_beats_positive_language():
    """An out-of-office must never be read as a yes just because it says 'will follow up'."""
    result = sentiment.classify(
        "Out of office",
        "I will be back in the office and happy to chat yes soon.",
        {"Precedence": "bulk"},
    )
    assert result.sentiment == c.SENTIMENT_AUTO_REPLY


def test_auto_submitted_no_is_not_an_auto_reply():
    assert not sentiment.is_auto_reply("Re: hi", "Yes please.", {"Auto-Submitted": "no"})


def test_return_date_extraction():
    assert sentiment.extract_return_date("I am away until 2026-10-01.") == "2026-10-01"
    assert sentiment.extract_return_date("Back on 12/10/2026.") == "12/10/2026"
    assert sentiment.extract_return_date("No date here.") is None


def test_meeting_intent_is_positive():
    result = sentiment.classify("", "Can we schedule a call for Thursday?")
    assert result.sentiment == c.SENTIMENT_POSITIVE
