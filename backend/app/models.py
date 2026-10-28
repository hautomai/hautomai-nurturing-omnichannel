"""ORM models.

Design notes that matter:

* IDs are 32-char hex strings rather than database-native UUIDs so the schema is
  portable between Postgres (production) and SQLite (tests, single-file local
  runs).
* JSON columns use ``JSON`` with a ``JSONB`` variant on Postgres.
* Timestamps are timezone-aware UTC. The engine compares in UTC and converts to
  a contact's local timezone only for the SMS quiet-hours window.
* ``SequenceVersion.graph`` is immutable once published; ``Sequence.current_version_id``
  deliberately has no foreign key so the sequences/sequence_versions pair does not
  need a circular constraint to bootstrap.
* Deliberately no ORM relationships: the engine loads what it needs with explicit
  queries, which keeps mapper configuration trivial and avoids lazy-load surprises
  inside worker transactions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app import constants as c
from app.db import Base, UTCDateTime

JSONType = JSON().with_variant(JSONB, "postgresql")


def new_id() -> str:
    return uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------------
class Client(TimestampMixin, Base):
    """A brand we send on behalf of. Seeded with Acme; more can be added later."""

    __tablename__ = "clients"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    default_timezone: Mapped[str] = mapped_column(String(64), default="Asia/Singapore")
    sending_halted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    halt_reason: Mapped[str | None] = mapped_column(String(400))


class User(TimestampMixin, Base):
    """Internal Hautomai operator."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(400), nullable=False)
    role: Mapped[str] = mapped_column(String(40), default="operator")
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


# ---------------------------------------------------------------------------
# Sending assets
# ---------------------------------------------------------------------------
class SenderPersona(TimestampMixin, Base):
    """One fixed sending identity per client (D23)."""

    __tablename__ = "sender_personas"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    from_name: Mapped[str] = mapped_column(String(200), nullable=False)
    from_email: Mapped[str] = mapped_column(String(320), nullable=False)
    reply_to: Mapped[str] = mapped_column(String(320), default="")
    signature_html: Mapped[str] = mapped_column(Text, default="")
    calendar_link: Mapped[str] = mapped_column(String(500), default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=True)


class SenderInbox(TimestampMixin, Base):
    """A warmed mailbox in the rotation pool."""

    __tablename__ = "sender_inboxes"
    __table_args__ = (
        UniqueConstraint("client_id", "email", name="uq_sender_inbox_client_email"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), default="")

    smtp_host: Mapped[str] = mapped_column(String(255), default="")
    smtp_port: Mapped[int] = mapped_column(Integer, default=587)
    smtp_username: Mapped[str] = mapped_column(String(320), default="")
    # Name of the secret holding the password (env var or secret store key).
    smtp_password_ref: Mapped[str] = mapped_column(String(200), default="")
    imap_host: Mapped[str] = mapped_column(String(255), default="")
    imap_port: Mapped[int] = mapped_column(Integer, default=993)
    use_tls: Mapped[bool] = mapped_column(Boolean, default=True)

    daily_cap: Mapped[int] = mapped_column(Integer, default=25)
    sends_today: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sends_today_date: Mapped[str] = mapped_column(String(10), default="")

    status: Mapped[str] = mapped_column(String(20), default=c.INBOX_WARMING)
    total_sends: Mapped[int] = mapped_column(Integer, default=0)
    hard_bounces: Mapped[int] = mapped_column(Integer, default=0)
    soft_bounces: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    bounce_rate: Mapped[float] = mapped_column(Float, default=0.0)
    last_send_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    paused_reason: Mapped[str | None] = mapped_column(String(400))
    warmup_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class PhoneNumber(TimestampMixin, Base):
    """A Twilio sending number in the rotation pool."""

    __tablename__ = "phone_numbers"
    __table_args__ = (
        UniqueConstraint("client_id", "e164", name="uq_phone_client_e164"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False, index=True)
    e164: Mapped[str] = mapped_column(String(24), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), default="twilio")
    daily_cap: Mapped[int] = mapped_column(Integer, default=200)
    sends_today: Mapped[int] = mapped_column(Integer, default=0)
    sends_today_date: Mapped[str] = mapped_column(String(10), default="")
    status: Mapped[str] = mapped_column(String(20), default=c.INBOX_ACTIVE)
    total_sends: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    last_send_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Template(TimestampMixin, Base):
    """Static, authored message copy with merge fields (no runtime LLM)."""

    __tablename__ = "templates"
    __table_args__ = (UniqueConstraint("client_id", "key", name="uq_template_client_key"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), default="")
    subject: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------
class Contact(TimestampMixin, Base):
    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint("client_id", "email", name="uq_contact_client_email"),
        Index("ix_contacts_client_phone", "client_id", "phone_e164"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String(320))
    phone_e164: Mapped[str | None] = mapped_column(String(24))
    first_name: Mapped[str] = mapped_column(String(120), default="")
    last_name: Mapped[str] = mapped_column(String(120), default="")
    company: Mapped[str] = mapped_column(String(200), default="")
    job_title: Mapped[str] = mapped_column(String(200), default="")
    timezone: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(20), default="manual")
    ghl_contact_id: Mapped[str | None] = mapped_column(String(64))
    tags: Mapped[list[str]] = mapped_column(JSONType, default=list)
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )


class Suppression(TimestampMixin, Base):
    """Cross-channel suppression, scoped to a client (D12/D29 defaults)."""

    __tablename__ = "suppressions"
    __table_args__ = (
        UniqueConstraint("client_id", "contact_id", "channel", name="uq_suppression_scope"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False, index=True)
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(20), default="all")
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    note: Mapped[str] = mapped_column(Text, default="")


# ---------------------------------------------------------------------------
# Sequences
# ---------------------------------------------------------------------------
class Sequence(TimestampMixin, Base):
    __tablename__ = "sequences"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=c.SEQUENCE_DRAFT, index=True)
    # No FK: avoids a circular constraint with sequence_versions.
    current_version_id: Mapped[str | None] = mapped_column(String(32))
    reaction_policy: Mapped[str] = mapped_column(
        String(40), default=c.REACTION_POLICY_CANCEL_AND_REBRANCH
    )
    allow_cadence_override: Mapped[bool] = mapped_column(Boolean, default=False)
    cadence_preset: Mapped[str] = mapped_column(String(60), default="")
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )


class SequenceVersion(Base):
    """Immutable published graph. Editing produces a new version (D21)."""

    __tablename__ = "sequence_versions"
    __table_args__ = (
        UniqueConstraint("sequence_id", "version", name="uq_sequence_version"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    sequence_id: Mapped[str] = mapped_column(ForeignKey("sequences.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    graph: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


# ---------------------------------------------------------------------------
# Enrollments and scheduling
# ---------------------------------------------------------------------------
class Enrollment(Base):
    __tablename__ = "enrollments"
    __table_args__ = (
        Index("ix_enrollments_status_next_run", "status", "next_run_at"),
        Index("ix_enrollments_contact_lane", "contact_id", "channel_lane", "status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"), nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False, index=True)
    sequence_id: Mapped[str] = mapped_column(ForeignKey("sequences.id"), nullable=False, index=True)
    sequence_version_id: Mapped[str] = mapped_column(
        ForeignKey("sequence_versions.id"), nullable=False
    )

    status: Mapped[str] = mapped_column(String(20), default=c.ENROLLMENT_ACTIVE, index=True)
    # Lanes the enrollment owns; a comma-free single value keeps the collision
    # rule cheap. Multi-channel sequences claim "email" then "sms" as they go.
    channel_lane: Mapped[str] = mapped_column(String(20), default=c.CHANNEL_EMAIL)
    current_step_key: Mapped[str | None] = mapped_column(String(120))
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    exit_reason: Mapped[str | None] = mapped_column(String(40))
    pause_reason: Mapped[str | None] = mapped_column(String(120))

    touches: Mapped[int] = mapped_column(Integer, default=0)
    sms_touches: Mapped[int] = mapped_column(Integer, default=0)

    entered_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    last_event_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )


class Timer(Base):
    """The engine's work queue. One row per pending step execution."""

    __tablename__ = "timers"
    __table_args__ = (Index("ix_timers_status_run_at", "status", "run_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    enrollment_id: Mapped[str] = mapped_column(
        ForeignKey("enrollments.id"), nullable=False, index=True
    )
    step_key: Mapped[str] = mapped_column(String(120), nullable=False)
    run_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=c.TIMER_SCHEDULED)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    claimed_by: Mapped[str | None] = mapped_column(String(80))
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


class Message(Base):
    """Outbound log - one row per attempted send, including skips."""

    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_contact_channel_sent", "contact_id", "channel", "sent_at"),
        Index("ix_messages_inbox_sent", "sender_inbox_id", "sent_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False, index=True)
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"), nullable=False, index=True)
    enrollment_id: Mapped[str | None] = mapped_column(ForeignKey("enrollments.id"), index=True)
    step_key: Mapped[str] = mapped_column(String(120), default="")

    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), default="outbound")
    status: Mapped[str] = mapped_column(String(20), default=c.MESSAGE_QUEUED, index=True)

    sender_inbox_id: Mapped[str | None] = mapped_column(ForeignKey("sender_inboxes.id"))
    phone_number_id: Mapped[str | None] = mapped_column(ForeignKey("phone_numbers.id"))
    from_address: Mapped[str] = mapped_column(String(320), default="")
    to_address: Mapped[str] = mapped_column(String(320), default="")

    subject: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    provider_message_id: Mapped[str] = mapped_column(String(200), default="")
    thread_key: Mapped[str] = mapped_column(String(200), default="", index=True)

    skip_reason: Mapped[str | None] = mapped_column(String(40))
    bounce_type: Mapped[str | None] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(Text)
    is_substitution: Mapped[bool] = mapped_column(Boolean, default=False)

    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


class InboundMessage(Base):
    __tablename__ = "inbound_messages"
    __table_args__ = (Index("ix_inbound_contact_received", "contact_id", "received_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False, index=True)
    contact_id: Mapped[str | None] = mapped_column(ForeignKey("contacts.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    from_address: Mapped[str] = mapped_column(String(320), default="")
    to_address: Mapped[str] = mapped_column(String(320), default="")
    subject: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    thread_key: Mapped[str] = mapped_column(String(200), default="", index=True)
    in_reply_to: Mapped[str] = mapped_column(String(200), default="")
    sentiment: Mapped[str] = mapped_column(String(20), default=c.SENTIMENT_NEUTRAL)
    matched_rule: Mapped[str] = mapped_column(String(120), default="")
    is_auto_reply: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_reply_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    received_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


class Event(Base):
    """Append-only spine. Every send, reaction, decision and failure is one row."""

    __tablename__ = "events"
    __table_args__ = (Index("ix_events_contact_occurred", "contact_id", "occurred_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False, index=True)
    contact_id: Mapped[str | None] = mapped_column(ForeignKey("contacts.id"), index=True)
    enrollment_id: Mapped[str | None] = mapped_column(ForeignKey("enrollments.id"), index=True)
    type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(20), default="")
    occurred_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    dedupe_key: Mapped[str] = mapped_column(String(200), default="", unique=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


class BranchDecision(Base):
    """Why a contact went left. Rendered by the timeline."""

    __tablename__ = "branch_decisions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    enrollment_id: Mapped[str] = mapped_column(
        ForeignKey("enrollments.id"), nullable=False, index=True
    )
    event_id: Mapped[str | None] = mapped_column(ForeignKey("events.id"))
    step_key: Mapped[str] = mapped_column(String(120), default="")
    rule: Mapped[str] = mapped_column(String(160), default="")
    outcome: Mapped[str] = mapped_column(String(160), default="")
    detail: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


class SequenceStat(Base):
    """Pre-aggregated counters. The dashboard never scans raw events."""

    __tablename__ = "sequence_stats"
    __table_args__ = (
        UniqueConstraint(
            "sequence_version_id", "step_key", "channel", name="uq_sequence_stat_scope"
        ),
        Index("ix_sequence_stats_sequence", "sequence_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False)
    sequence_id: Mapped[str] = mapped_column(ForeignKey("sequences.id"), nullable=False)
    sequence_version_id: Mapped[str] = mapped_column(
        ForeignKey("sequence_versions.id"), nullable=False
    )
    step_key: Mapped[str] = mapped_column(String(120), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), default="")

    sent: Mapped[int] = mapped_column(Integer, default=0)
    delivered: Mapped[int] = mapped_column(Integer, default=0)
    bounced: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    substituted: Mapped[int] = mapped_column(Integer, default=0)
    replied: Mapped[int] = mapped_column(Integer, default=0)
    positive_replies: Mapped[int] = mapped_column(Integer, default=0)
    meetings: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow
    )


class HumanTask(TimestampMixin, Base):
    """A step that waits for a person."""

    __tablename__ = "human_tasks"
    __table_args__ = (Index("ix_human_tasks_status_due", "status", "due_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), nullable=False, index=True)
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"), nullable=False, index=True)
    enrollment_id: Mapped[str | None] = mapped_column(ForeignKey("enrollments.id"), index=True)
    step_key: Mapped[str] = mapped_column(String(120), default="")
    title: Mapped[str] = mapped_column(String(200), default="")
    instructions: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=c.TASK_OPEN, index=True)
    assignee: Mapped[str] = mapped_column(String(200), default="")
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    timeout_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    on_timeout: Mapped[str] = mapped_column(String(20), default=c.TASK_TIMEOUT_EXIT)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    outcome: Mapped[str] = mapped_column(String(120), default="")
