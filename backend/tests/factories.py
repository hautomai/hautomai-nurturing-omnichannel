"""Factories shared by the test suite.

Deliberately small and explicit: each test builds the exact graph it needs, so a
failure points at one behaviour rather than at a fixture.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app import constants as c
from app.models import (
    Client,
    Contact,
    PhoneNumber,
    SenderInbox,
    SenderPersona,
    Sequence,
    SequenceVersion,
    Template,
)
from app.models import utcnow

BASE_SUBJECT = "Quick question about {{contact.company}}"
BASE_BODY = "Hi {{contact.first_name}}, from {{persona.from_name}}. {{client.name}} here."


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------
def make_client(db: Session, *, slug: str = "test-client", tz: str = "UTC", name: str = "Test Client") -> Client:
    client = Client(name=name, slug=slug, default_timezone=tz)
    db.add(client)
    db.flush()
    return client


def make_persona(db: Session, client: Client, *, email: str | None = None) -> SenderPersona:
    persona = SenderPersona(
        client_id=client.id,
        display_name="Test Persona",
        from_name="Dana Tan",
        from_email=email or f"dana@{client.slug}.example",
        reply_to=email or f"dana@{client.slug}.example",
        signature_html="Dana Tan — Test Client",
        calendar_link="https://cal.example.com/dana",
        is_default=True,
    )
    db.add(persona)
    db.flush()
    return persona


def make_inbox(
    db: Session,
    client: Client,
    *,
    email: str = "outbox1@test.example",
    daily_cap: int = 25,
    status: str = c.INBOX_ACTIVE,
) -> SenderInbox:
    inbox = SenderInbox(
        client_id=client.id,
        email=email,
        display_name=email,
        smtp_host="smtp.test.example",
        smtp_port=587,
        smtp_username=email,
        smtp_password_ref="TEST_SMTP_PASSWORD",
        imap_host="imap.test.example",
        imap_port=993,
        daily_cap=daily_cap,
        status=status,
        warmup_started_at=utcnow() - timedelta(days=30),
    )
    db.add(inbox)
    db.flush()
    return inbox


def make_phone(db: Session, client: Client, *, e164: str = "+6590000001") -> PhoneNumber:
    number = PhoneNumber(
        client_id=client.id, e164=e164, provider="twilio", daily_cap=200, status=c.INBOX_ACTIVE
    )
    db.add(number)
    db.flush()
    return number


def make_template(
    db: Session,
    client: Client,
    *,
    key: str,
    channel: str = c.CHANNEL_EMAIL,
    subject: str = BASE_SUBJECT,
    body: str = BASE_BODY,
) -> Template:
    template = Template(
        client_id=client.id,
        key=key,
        channel=channel,
        name=key,
        subject=subject if channel == c.CHANNEL_EMAIL else "",
        body=body,
    )
    db.add(template)
    db.flush()
    return template


def make_contact(
    db: Session,
    client: Client,
    *,
    email: str | None = "prospect@test.example",
    phone: str | None = "+6591110000",
    **kwargs: Any,
) -> Contact:
    contact = Contact(
        client_id=client.id,
        email=email,
        phone_e164=phone,
        first_name=kwargs.pop("first_name", "Alex"),
        last_name=kwargs.pop("last_name", "Ng"),
        company=kwargs.pop("company", "Orbit Hardware"),
        job_title=kwargs.pop("job_title", "Head of Engineering"),
        timezone=kwargs.pop("timezone", "UTC"),
        source=kwargs.pop("source", "manual"),
        tags=kwargs.pop("tags", []),
        custom_fields=kwargs.pop("custom_fields", {}),
    )
    db.add(contact)
    db.flush()
    return contact


def make_sequence(
    db: Session,
    client: Client,
    *,
    graph: dict[str, Any],
    name: str = "Test Sequence",
    publish: bool = True,
    allow_cadence_override: bool = False,
    reaction_policy: str = c.REACTION_POLICY_CANCEL_AND_REBRANCH,
) -> Sequence:
    sequence = Sequence(
        client_id=client.id,
        name=name,
        description="test",
        status=c.SEQUENCE_ACTIVE if publish else c.SEQUENCE_DRAFT,
        allow_cadence_override=allow_cadence_override,
        reaction_policy=reaction_policy,
    )
    db.add(sequence)
    db.flush()
    version = SequenceVersion(sequence_id=sequence.id, version=1, graph=graph)
    db.add(version)
    db.flush()
    if publish:
        version.published_at = utcnow()
        sequence.current_version_id = version.id
    db.flush()
    return sequence


def add_version(
    db: Session,
    sequence: Sequence,
    *,
    graph: dict[str, Any],
    version: int,
    publish: bool = False,
) -> SequenceVersion:
    row = SequenceVersion(sequence_id=sequence.id, version=version, graph=graph)
    db.add(row)
    db.flush()
    if publish:
        row.published_at = utcnow()
        sequence.current_version_id = row.id
        db.flush()
    return row


# ---------------------------------------------------------------------------
# Graphs
# ---------------------------------------------------------------------------
def email_step(key: str, next_key: str, *, template: str, fallback: str = "skip", fallback_template: str = "") -> dict[str, Any]:
    step: dict[str, Any] = {
        "key": key,
        "type": "send_email",
        "name": key,
        "template_key": template,
        "fallback": fallback,
        "next": next_key,
    }
    if fallback_template:
        step["fallback_template_key"] = fallback_template
    return step


def sms_step(key: str, next_key: str, *, template: str, fallback: str = "skip", fallback_template: str = "") -> dict[str, Any]:
    step: dict[str, Any] = {
        "key": key,
        "type": "send_sms",
        "name": key,
        "template_key": template,
        "fallback": fallback,
        "next": next_key,
    }
    if fallback_template:
        step["fallback_template_key"] = fallback_template
    return step


def delay_step(key: str, next_key: str, *, days: float = 1) -> dict[str, Any]:
    return {
        "key": key,
        "type": "delay",
        "name": key,
        "amount": days,
        "unit": "days",
        "next": next_key,
    }


def condition_step(key: str, true_key: str, false_key: str, predicate: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": key,
        "type": "condition",
        "name": key,
        "predicate": predicate,
        "on_true": true_key,
        "on_false": false_key,
    }


def exit_step(key: str, reason: str = c.EXIT_COMPLETED) -> dict[str, Any]:
    return {"key": key, "type": "exit", "name": key, "reason": reason}


def three_touch_graph(*, delay_days: float = 1) -> dict[str, Any]:
    """email -> delay -> sms -> delay -> email -> exit."""
    return {
        "entry_step_keys": ["step_email_1"],
        "steps": [
            email_step(
                "step_email_1",
                "step_wait_1",
                template="email_1",
                fallback="send_sms",
                fallback_template="email_1_sms",
            ),
            delay_step("step_wait_1", "step_sms_1", days=delay_days),
            sms_step(
                "step_sms_1",
                "step_wait_2",
                template="sms_1",
                fallback="send_email",
                fallback_template="sms_1_email",
            ),
            delay_step("step_wait_2", "step_email_2", days=delay_days),
            email_step("step_email_2", "step_exit", template="email_2"),
            exit_step("step_exit"),
        ],
    }


def reply_branch_graph() -> dict[str, Any]:
    """A graph that routes a positive reply into its own step."""
    return {
        "entry_step_keys": ["step_email_1"],
        "reactions": {"replied_positive": "step_positive"},
        "steps": [
            email_step("step_email_1", "step_wait_1", template="email_1"),
            delay_step("step_wait_1", "step_exit", days=3),
            {
                "key": "step_positive",
                "type": "notify_slack",
                "name": "Positive handoff",
                "text": "Positive reply - take over",
                "next": "step_exit",
            },
            exit_step("step_exit"),
        ],
    }


def default_templates(db: Session, client: Client) -> None:
    make_template(db, client, key="email_1", channel=c.CHANNEL_EMAIL, subject="First {{contact.company}}", body="Hi {{contact.first_name}}")
    make_template(db, client, key="email_2", channel=c.CHANNEL_EMAIL, subject="Second", body="Following up, {{contact.first_name}}")
    make_template(db, client, key="email_1_sms", channel=c.CHANNEL_SMS, subject="", body="Hi {{contact.first_name}}, quick nudge")
    make_template(db, client, key="sms_1", channel=c.CHANNEL_SMS, subject="", body="Worth 10 minutes, {{contact.first_name}}?")
    make_template(db, client, key="sms_1_email", channel=c.CHANNEL_EMAIL, subject="Worth 10 minutes?", body="Hi {{contact.first_name}}")
