"""Pydantic request/response schemas for the REST API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(ORMModel):
    id: str
    email: str
    name: str
    role: str


# ---------------------------------------------------------------------------
# Clients, personas, inboxes, numbers, templates
# ---------------------------------------------------------------------------
class ClientOut(ORMModel):
    id: str
    name: str
    slug: str
    default_timezone: str
    sending_halted: bool
    halt_reason: str | None = None


class ClientUpdate(BaseModel):
    sending_halted: bool | None = None
    halt_reason: str | None = None
    default_timezone: str | None = None


class PersonaOut(ORMModel):
    id: str
    client_id: str
    display_name: str
    from_name: str
    from_email: str
    reply_to: str
    signature_html: str
    calendar_link: str
    is_default: bool


class PersonaCreate(BaseModel):
    display_name: str
    from_name: str
    from_email: EmailStr
    reply_to: str = ""
    signature_html: str = ""
    calendar_link: str = ""
    is_default: bool = True


class InboxOut(ORMModel):
    id: str
    client_id: str
    email: str
    display_name: str
    status: str
    daily_cap: int
    sends_today: int
    sends_today_date: str
    total_sends: int
    hard_bounces: int
    soft_bounces: int
    consecutive_failures: int
    bounce_rate: float
    last_send_at: datetime | None = None
    last_error: str | None = None
    paused_reason: str | None = None
    smtp_host: str
    imap_host: str


class InboxCreate(BaseModel):
    email: EmailStr
    display_name: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password_ref: str = ""
    imap_host: str = ""
    imap_port: int = 993
    use_tls: bool = True
    daily_cap: int | None = None


class InboxUpdate(BaseModel):
    status: Literal["warming", "active", "paused"] | None = None
    daily_cap: int | None = None
    paused_reason: str | None = None


class PhoneNumberOut(ORMModel):
    id: str
    client_id: str
    e164: str
    provider: str
    status: str
    daily_cap: int
    sends_today: int
    total_sends: int


class PhoneNumberCreate(BaseModel):
    e164: str
    daily_cap: int = 200
    provider: str = "twilio"


class TemplateOut(ORMModel):
    id: str
    client_id: str
    key: str
    channel: str
    name: str
    subject: str
    body: str


class TemplateCreate(BaseModel):
    key: str
    channel: Literal["email", "sms"]
    name: str = ""
    subject: str = ""
    body: str = ""


class TemplateUpdate(BaseModel):
    name: str | None = None
    subject: str | None = None
    body: str | None = None


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------
class ContactOut(ORMModel):
    id: str
    client_id: str
    email: str | None = None
    phone_e164: str | None = None
    first_name: str
    last_name: str
    company: str
    job_title: str
    timezone: str
    source: str
    tags: list[str] = Field(default_factory=list)
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ContactCreate(BaseModel):
    email: EmailStr | None = None
    phone_e164: str | None = None
    first_name: str = ""
    last_name: str = ""
    company: str = ""
    job_title: str = ""
    timezone: str = ""
    tags: list[str] = Field(default_factory=list)
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    source: str = "manual"
    enroll_in_sequence_id: str | None = None


class ContactUpdate(BaseModel):
    email: EmailStr | None = None
    phone_e164: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    company: str | None = None
    job_title: str | None = None
    timezone: str | None = None
    tags: list[str] | None = None
    custom_fields: dict[str, Any] | None = None


class ImportResult(BaseModel):
    created: int
    updated: int
    rejected: list[dict[str, str]] = Field(default_factory=list)
    enrolled: int = 0


class SuppressionOut(ORMModel):
    id: str
    contact_id: str
    channel: str
    reason: str
    note: str
    created_at: datetime


class SuppressionCreate(BaseModel):
    channel: Literal["email", "sms", "all"] = "all"
    reason: str = "manual"
    note: str = ""


# ---------------------------------------------------------------------------
# Sequences
# ---------------------------------------------------------------------------
class SequenceOut(ORMModel):
    id: str
    client_id: str
    name: str
    description: str
    status: str
    current_version_id: str | None = None
    reaction_policy: str
    allow_cadence_override: bool
    cadence_preset: str
    created_at: datetime
    updated_at: datetime


class SequenceCreate(BaseModel):
    name: str
    description: str = ""
    cadence_preset: str = ""
    reaction_policy: str = "cancel_and_rebranch"
    allow_cadence_override: bool = False
    graph: dict[str, Any] | None = None


class SequenceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: Literal["draft", "active", "paused", "archived"] | None = None
    reaction_policy: Literal[
        "cancel_and_rebranch", "keep_pending", "pause_for_human"
    ] | None = None
    allow_cadence_override: bool | None = None


class SequenceVersionOut(ORMModel):
    id: str
    sequence_id: str
    version: int
    graph: dict[str, Any]
    notes: str
    created_at: datetime
    published_at: datetime | None = None


class SequenceDetail(BaseModel):
    sequence: SequenceOut
    current_version: SequenceVersionOut | None = None
    draft_version: SequenceVersionOut | None = None
    versions: list[SequenceVersionOut] = Field(default_factory=list)


class GraphUpdate(BaseModel):
    graph: dict[str, Any]
    notes: str = ""


class ValidationIssue(BaseModel):
    level: Literal["error", "warning"]
    step_key: str | None = None
    code: str
    message: str


class PublishResult(BaseModel):
    ok: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    version: SequenceVersionOut | None = None
    migrated_enrollments: int = 0
    orphaned_enrollments: int = 0


class ValidateResult(BaseModel):
    ok: bool
    issues: list[ValidationIssue] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Enrollments
# ---------------------------------------------------------------------------
class EnrollmentOut(ORMModel):
    id: str
    contact_id: str
    client_id: str
    sequence_id: str
    sequence_version_id: str
    status: str
    channel_lane: str
    current_step_key: str | None = None
    next_run_at: datetime | None = None
    exit_reason: str | None = None
    pause_reason: str | None = None
    touches: int
    sms_touches: int
    entered_at: datetime
    last_event_at: datetime | None = None


class EnrollmentCreate(BaseModel):
    contact_id: str
    sequence_id: str
    start_at: datetime | None = None


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------
class SimulationRequest(BaseModel):
    contact_id: str
    hypotheticals: dict[str, bool] = Field(default_factory=dict)
    max_steps: int = 40


class SimulatedStep(BaseModel):
    step_key: str
    step_type: str
    channel: str = ""
    run_at: datetime | None = None
    detail: str = ""
    rendered_subject: str = ""
    rendered_body: str = ""
    outcome: str = ""


class SimulationResult(BaseModel):
    timeline: list[SimulatedStep]
    exited: bool
    exit_reason: str | None = None
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Timeline & analytics
# ---------------------------------------------------------------------------
class TimelineEntry(BaseModel):
    kind: Literal["event", "message", "inbound", "decision", "task"]
    at: datetime
    title: str
    channel: str = ""
    status: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)


class FunnelStep(BaseModel):
    step_key: str
    name: str
    channel: str
    index: int
    sent: int = 0
    delivered: int = 0
    bounced: int = 0
    failed: int = 0
    skipped: int = 0
    substituted: int = 0
    replied: int = 0
    positive_replies: int = 0
    meetings: int = 0
    reply_rate: float = 0.0
    drop_off: float | None = None


class OverviewMetrics(BaseModel):
    active_enrollments: int
    sent_today: int
    daily_capacity: int
    reply_rate: float
    replies: int
    positive_replies: int
    meetings: int
    live_versions: int
    deltas: dict[str, float] = Field(default_factory=dict)


class ChannelComparisonRow(BaseModel):
    channel: str
    sent: int
    delivered: int
    replies: int
    reply_rate: float
    positive_rate: float
    meetings: int


class InboxHealthRow(BaseModel):
    id: str
    email: str
    display_name: str
    status: str
    sends_today: int
    daily_cap: int
    bounce_rate: float
    warmup_days: int | None = None
    suspended: bool = False
    paused_reason: str | None = None


class FailureRow(BaseModel):
    reason: str
    count: int
    channel: str = ""


class NeedsAttentionRow(BaseModel):
    kind: Literal["reply", "task", "stalled"]
    at: datetime
    contact_id: str
    contact_label: str
    summary: str
    sentiment: str = ""
    severity: Literal["info", "warn", "danger"] = "info"


class SequenceAnalytics(BaseModel):
    sequence_id: str
    sequence_name: str
    version_id: str
    version: int
    funnel: list[FunnelStep]
    channels: list[ChannelComparisonRow]
    totals: dict[str, float] = Field(default_factory=dict)


class VersionComparisonRow(BaseModel):
    version: int
    version_id: str
    sent: int
    replies: int
    reply_rate: float
    meetings: int
    positive_replies: int
