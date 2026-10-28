"""Rule-based reply classification (D13: no LLM in the runtime path).

The original n8n flow looked for the literal string "yes" in a Gmail snippet.
This replaces it with an ordered rule set that is tunable without a deploy, and
that always records *which* rule fired so a false positive can be diagnosed from
the contact timeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app import constants as c

# --------------------------------------------------------------------------
# Rule tables. Order matters: the first matching rule wins, so negative and
# opt-out language must be checked before affirmatives.
# --------------------------------------------------------------------------
NEGATIVE_RULES: list[tuple[str, str]] = [
    (r"\b(unsubscribe|opt[\s-]?out|remove me|take me off|stop emailing)\b", "opt_out"),
    (r"\b(not interested|no thanks|no thank you|pass on this|we'?re good|not a fit)\b", "not_interested"),
    (r"\b(already (have|work with)|currently (have|work with) a (vendor|partner))\b", "has_vendor"),
    (r"\b(do not contact|don'?t contact|never contact)\b", "do_not_contact"),
]

POSITIVE_RULES: list[tuple[str, str]] = [
    (r"\b(yes|yep|yeah|sure|sounds good|let'?s do it|book it|sign me up)\b", "affirmative"),
    (r"\b(interested|curious|keen|happy to (chat|talk|meet))\b", "interested"),
    (r"\b(send (it |me )?(over|through|the)|share (the )?(deck|case stud|info)|more info)\b", "send_info"),
    (r"\b(book|schedule|calendar|availab\w*|meeting|call)\b", "meeting_intent"),
]

QUESTION_RULES: list[tuple[str, str]] = [
    # Keyword interrogatives are checked before the bare question mark so a
    # pricing question is labelled as a question, not merely as "ends with ?".
    (r"\b(how much|pricing|cost|quote|timeline|lead time|what do you|can you|would you|do you)\b", "question"),
    (r"\?\s*$", "question_mark"),
]

AUTO_REPLY_SUBJECT_RULES: list[tuple[str, str]] = [
    (r"\b(out of office|automatic reply|auto[\s-]?reply|autoreply|on leave|away from)\b", "auto_reply_subject"),
]

AUTO_REPLY_BODY_RULES: list[tuple[str, str]] = [
    (r"\b(i am (currently )?(out of|away)|i'?m (currently )?(out of|away))\b", "auto_reply_body"),
    (r"\b(will be (back|returning)|back in the office|limited access to email)\b", "auto_reply_return"),
]

# "until 5 May 2026", "on 12/05/2026", "returning 2026-05-05"
RETURN_DATE_PATTERNS = [
    re.compile(r"until\s+(\d{4}-\d{2}-\d{2})", re.IGNORECASE),
    re.compile(r"until\s+(\d{1,2}\s+\w+\s+\d{4})", re.IGNORECASE),
    re.compile(r"returning\s+(?:on\s+)?(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})", re.IGNORECASE),
    re.compile(r"back\s+(?:on\s+)?(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})", re.IGNORECASE),
]


@dataclass(frozen=True)
class Classification:
    sentiment: str
    matched_rule: str
    is_auto_reply: bool = False


def _first_match(text: str, rules: list[tuple[str, str]]) -> str | None:
    for pattern, name in rules:
        if re.search(pattern, text, re.IGNORECASE):
            return name
    return None


def is_auto_reply(subject: str = "", body: str = "", headers: dict[str, str] | None = None) -> bool:
    """Detect out-of-office / auto-responders.

    Header signals are the reliable ones (``Auto-Submitted: auto-replied``,
    ``X-Autoreply``, ``Precedence: bulk``); the text rules are the fallback.
    """
    headers = {key.lower(): value for key, value in (headers or {}).items()}
    auto_submitted = headers.get("auto-submitted", "").lower()
    if auto_submitted and auto_submitted != "no":
        return True
    if headers.get("x-autoreply") or headers.get("x-autorespond"):
        return True
    if headers.get("precedence", "").lower() in {"auto_reply", "junk", "bulk"}:
        return True
    if _first_match(subject or "", AUTO_REPLY_SUBJECT_RULES):
        return True
    return bool(_first_match(body or "", AUTO_REPLY_BODY_RULES))


def extract_return_date(text: str) -> str | None:
    """Best-effort return date from an out-of-office body. Returns raw string."""
    for pattern in RETURN_DATE_PATTERNS:
        match = pattern.search(text or "")
        if match:
            return match.group(1)
    return None


def classify(
    subject: str = "",
    body: str = "",
    headers: dict[str, str] | None = None,
) -> Classification:
    """Classify a reply.

    Order: opt-out/negative before positive, because "no thanks, sounds good for
    a colleague" must not be read as interest. Questions are checked after
    positives so "yes, but how much?" counts as a positive that a human should
    still read.
    """
    text = f"{subject or ''}\n{body or ''}"

    if is_auto_reply(subject, body, headers):
        return Classification(c.SENTIMENT_AUTO_REPLY, "auto_reply", is_auto_reply=True)

    negative = _first_match(text, NEGATIVE_RULES)
    if negative:
        return Classification(c.SENTIMENT_NEGATIVE, negative)

    positive = _first_match(text, POSITIVE_RULES)
    if positive:
        return Classification(c.SENTIMENT_POSITIVE, positive)

    question = _first_match(text, QUESTION_RULES)
    if question:
        return Classification(c.SENTIMENT_NEUTRAL, question)

    return Classification(c.SENTIMENT_NEUTRAL, "unmatched")
