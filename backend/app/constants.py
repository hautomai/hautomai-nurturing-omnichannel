"""Shared vocabulary: statuses, channels, event types.

Kept as plain strings (rather than Python enums) so they round-trip through the
database, JSON graph documents and the REST API without conversion shims.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Channels
# --------------------------------------------------------------------------
CHANNEL_EMAIL = "email"
CHANNEL_SMS = "sms"
CHANNEL_SLACK = "slack"
CHANNELS = (CHANNEL_EMAIL, CHANNEL_SMS, CHANNEL_SLACK)

# Lanes are what the collision rule (one active enrollment per contact per lane)
# is keyed on. Slack is internal, so it never occupies a lane.
LANES = (CHANNEL_EMAIL, CHANNEL_SMS)


# --------------------------------------------------------------------------
# Sequence lifecycle
# --------------------------------------------------------------------------
SEQUENCE_DRAFT = "draft"
SEQUENCE_ACTIVE = "active"
SEQUENCE_PAUSED = "paused"
SEQUENCE_ARCHIVED = "archived"

REACTION_POLICY_CANCEL_AND_REBRANCH = "cancel_and_rebranch"
REACTION_POLICY_KEEP_PENDING = "keep_pending"
REACTION_POLICY_PAUSE_FOR_HUMAN = "pause_for_human"


# --------------------------------------------------------------------------
# Enrollment lifecycle
# --------------------------------------------------------------------------
ENROLLMENT_ACTIVE = "active"
ENROLLMENT_WAITING = "waiting"
ENROLLMENT_PAUSED = "paused"
ENROLLMENT_COMPLETED = "completed"
ENROLLMENT_EXITED = "exited"

ENROLLMENT_OPEN_STATES = (ENROLLMENT_ACTIVE, ENROLLMENT_WAITING)


# --------------------------------------------------------------------------
# Timers
# --------------------------------------------------------------------------
TIMER_SCHEDULED = "scheduled"
TIMER_CLAIMED = "claimed"
TIMER_DONE = "done"
TIMER_CANCELLED = "cancelled"
TIMER_FAILED = "failed"


# --------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------
MESSAGE_QUEUED = "queued"
MESSAGE_SENT = "sent"
MESSAGE_DELIVERED = "delivered"
MESSAGE_BOUNCED = "bounced"
MESSAGE_FAILED = "failed"
MESSAGE_SKIPPED = "skipped"
MESSAGE_SUBSTITUTED = "substituted"

BOUNCE_HARD = "hard"
BOUNCE_SOFT = "soft"

SKIP_REASONS = (
    "no_phone",
    "no_email",
    "suppressed",
    "cap_reached",
    "inbox_paused",
    "collision",
    "halted",
    "missing_template",
)


# --------------------------------------------------------------------------
# Inboxes / phone numbers
# --------------------------------------------------------------------------
INBOX_WARMING = "warming"
INBOX_ACTIVE = "active"
INBOX_PAUSED = "paused"


# --------------------------------------------------------------------------
# Event types (the engine's input vocabulary)
# --------------------------------------------------------------------------
EVENT_EMAIL_SENT = "email.sent"
EVENT_EMAIL_DELIVERED = "email.delivered"
EVENT_EMAIL_REPLIED = "email.replied"
EVENT_EMAIL_AUTO_REPLY = "email.auto_reply"
EVENT_EMAIL_BOUNCED = "email.bounced"
EVENT_EMAIL_UNSUBSCRIBED = "email.unsubscribed"

EVENT_SMS_SENT = "sms.sent"
EVENT_SMS_DELIVERED = "sms.delivered"
EVENT_SMS_UNDELIVERED = "sms.undelivered"
EVENT_SMS_FAILED = "sms.failed"
EVENT_SMS_REPLIED = "sms.replied"

EVENT_MEETING_BOOKED = "meeting.booked"
EVENT_CONTACT_UNSUBSCRIBED = "contact.unsubscribed"
EVENT_SEQUENCE_ENROLLED = "sequence.enrolled"
EVENT_SEQUENCE_STEP_FIRED = "sequence.step_fired"
EVENT_SEQUENCE_EXITED = "sequence.exited"
EVENT_SEQUENCE_COMPLETED = "sequence.completed"
EVENT_SEQUENCE_PAUSED = "sequence.paused"
EVENT_SEQUENCE_SUBSTITUTED = "sequence.substituted"
EVENT_SEQUENCE_SKIPPED = "sequence.skipped"
EVENT_SEQUENCE_COLLISION = "sequence.collision"
EVENT_SEQUENCE_SUPPRESSED = "sequence.suppressed"
EVENT_SLACK_ACTION = "slack.action"
EVENT_SIMULATION = "simulation.dry_run"

REPLY_EVENTS = (EVENT_EMAIL_REPLIED, EVENT_SMS_REPLIED)


# --------------------------------------------------------------------------
# Sentiment
# --------------------------------------------------------------------------
SENTIMENT_POSITIVE = "positive"
SENTIMENT_NEGATIVE = "negative"
SENTIMENT_NEUTRAL = "neutral"
SENTIMENT_AUTO_REPLY = "auto_reply"


# --------------------------------------------------------------------------
# Suppression reasons
# --------------------------------------------------------------------------
SUPPRESS_UNSUBSCRIBED = "unsubscribe"
SUPPRESS_OPT_OUT = "opt_out"
SUPPRESS_HARD_BOUNCE = "hard_bounce"
SUPPRESS_COMPLAINT = "complaint"
SUPPRESS_MANUAL = "manual"
SUPPRESS_MEETING_BOOKED = "meeting_booked"
SUPPRESS_OBJECTION = "objection"


# --------------------------------------------------------------------------
# Exit reasons
# --------------------------------------------------------------------------
EXIT_REPLIED_POSITIVE = "replied_positive"
EXIT_REPLIED_NEGATIVE = "replied_negative"
EXIT_REPLIED = "replied"
EXIT_MEETING_BOOKED = "meeting_booked"
EXIT_UNSUBSCRIBED = "unsubscribed"
EXIT_BOUNCED_HARD = "bounced_hard"
EXIT_MAX_TOUCHES = "max_touches"
EXIT_NO_REPLY_TIMEOUT = "no_reply_timeout"
EXIT_COMPLETED = "completed"
EXIT_ORPHANED_BY_VERSION = "orphaned_by_version"
EXIT_TASK_TIMEOUT = "task_timeout"
EXIT_CADENCE_CAP = "cadence_cap"
EXIT_CLIENT_HALTED = "client_halted"


# --------------------------------------------------------------------------
# Human tasks
# --------------------------------------------------------------------------
TASK_OPEN = "open"
TASK_COMPLETED = "completed"
TASK_CANCELLED = "cancelled"
TASK_TIMEOUT_EXIT = "exit"
TASK_TIMEOUT_CONTINUE = "continue"


# --------------------------------------------------------------------------
# CRM
# --------------------------------------------------------------------------
CRM_NONE = "none"
CRM_FAKE = "fake"
CRM_GHL = "ghl"


# --------------------------------------------------------------------------
# Notification kinds (Slack)
# --------------------------------------------------------------------------
NOTIFY_POSITIVE_REPLY = "positive_reply"
NOTIFY_NEUTRAL_REPLY = "neutral_reply"
NOTIFY_NEGATIVE_REPLY = "negative_reply"
NOTIFY_MEETING_BOOKED = "meeting_booked"
NOTIFY_FAILURE = "failure"
NOTIFY_INBOX_DEGRADED = "inbox_degraded"
NOTIFY_POOL_HALTED = "pool_halted"
NOTIFY_HUMAN_TASK = "human_task"
NOTIFY_REACTION = "reaction"
