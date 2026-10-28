"""Seed the workspace.

Creates the ``Acme`` client with its sending assets, the ten templates from the
spec's §11 copy, and the published ``Acme - Hiring-Signal Nurture`` sequence
(6 touches over 14 days: 4 email + 2 SMS), so the dashboard and the canvas have
real shape on first run.

Idempotent: running it twice changes nothing.

Template substitution note: the two SMS steps declare an email fallback (with a
short email variant) because an SMS with no phone number should still reach the
prospect. The four email steps declare ``skip`` rather than a synthesized SMS,
because flattening a multi-paragraph email into a text message reads like spam.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import constants as c
from app.config import Settings, get_settings
from app.db import create_all, session_scope
from app.models import (
    Client,
    Contact,
    PhoneNumber,
    SenderInbox,
    SenderPersona,
    Sequence,
    SequenceVersion,
    Template,
    utcnow,
)
from app.security import ensure_bootstrap_admin

logger = logging.getLogger(__name__)

Acme_SLUG = "Acme-innovation"
SEQUENCE_NAME = "Acme - Hiring-Signal Nurture"


# ---------------------------------------------------------------------------
# Copy
# ---------------------------------------------------------------------------
TEMPLATES: list[dict[str, str]] = [
    {
        "key": "Acme_email_1",
        "channel": c.CHANNEL_EMAIL,
        "name": "01 · Signal opener",
        "subject": "Re: your {{signal.role_title}} opening at {{contact.company}}",
        "body": (
            "<p>Hi {{contact.first_name}},</p>"
            "<p>Saw {{contact.company}} is hiring a {{signal.role_title}} — that usually means "
            "a product is moving from concept toward production.</p>"
            "<p>That's the stage where {{client.name}} helps: design, prototyping and "
            "manufacturing under one roof, so nothing gets lost between vendors.</p>"
            "<p>Worth a short call, or should I send over the closest case study first?</p>"
            "<p>{{persona.signature}}</p>"
        ),
    },
    {
        "key": "Acme_email_1_sms",
        "channel": c.CHANNEL_SMS,
        "name": "01b · Signal opener (SMS fallback)",
        "subject": "",
        "body": (
            "Hi {{contact.first_name}}, {{persona.first_name}} from {{client.name}} — saw "
            "{{contact.company}} hiring a {{signal.role_title}}. We do design through "
            "manufacturing under one roof. Worth a short call?"
        ),
    },
    {
        "key": "Acme_email_2",
        "channel": c.CHANNEL_EMAIL,
        "name": "02 · Proof point",
        "subject": "Following up on {{contact.company}}",
        "body": (
            "<p>Hi {{contact.first_name}},</p>"
            "<p>Quick follow-up — we recently took a hardware product from CAD to working "
            "prototypes to a small production run, which is usually where the handoffs between "
            "design and manufacturing houses hurt most.</p>"
            "<p>Happy to walk you through how that ran.</p>"
            "<p>{{persona.signature}}</p>"
        ),
    },
    {
        "key": "Acme_sms_1",
        "channel": c.CHANNEL_SMS,
        "name": "03 · SMS nudge",
        "subject": "",
        "body": (
            "Hi {{contact.first_name}}, {{persona.first_name}} from {{client.name}} — left you "
            "a note about {{contact.company}}'s {{signal.role_title}} opening. Worth 10 minutes?"
        ),
    },
    {
        "key": "Acme_sms_1_email",
        "channel": c.CHANNEL_EMAIL,
        "name": "03b · SMS nudge (email fallback)",
        "subject": "Quick question about {{contact.company}}",
        "body": (
            "<p>Hi {{contact.first_name}},</p>"
            "<p>I left you a note about {{contact.company}}'s {{signal.role_title}} opening — "
            "worth 10 minutes this week?</p>"
            "<p>{{persona.signature}}</p>"
        ),
    },
    {
        "key": "Acme_email_3",
        "channel": c.CHANNEL_EMAIL,
        "name": "04 · Different angle",
        "subject": "The hidden cost of splitting design and manufacturing",
        "body": (
            "<p>Hi {{contact.first_name}},</p>"
            "<p>One thing we see a lot: teams split design from prototyping and manufacturing "
            "to save time, then pay for it in rework cycles and misaligned tolerances.</p>"
            "<p>Keeping all three with one team is usually cheaper by the time the first "
            "prototype run is done.</p>"
            "<p>{{persona.signature}}</p>"
        ),
    },
    {
        "key": "Acme_sms_2",
        "channel": c.CHANNEL_SMS,
        "name": "05 · Soft close",
        "subject": "",
        "body": (
            "Last one from me, {{contact.first_name}} — should I close your file, or is a later "
            "quarter better?"
        ),
    },
    {
        "key": "Acme_sms_2_email",
        "channel": c.CHANNEL_EMAIL,
        "name": "05b · Soft close (email fallback)",
        "subject": "Should I close your file?",
        "body": (
            "<p>Hi {{contact.first_name}},</p>"
            "<p>Last note from me — should I close your file, or is a later quarter better for "
            "{{contact.company}}?</p>"
            "<p>{{persona.signature}}</p>"
        ),
    },
    {
        "key": "Acme_email_4",
        "channel": c.CHANNEL_EMAIL,
        "name": "06 · Breakup",
        "subject": "Closing the loop",
        "body": (
            "<p>Hi {{contact.first_name}},</p>"
            "<p>I'll stop here so I'm not crowding your inbox. If the hiring push turns into a "
            "production push, {{client.name}} is a quick call away.</p>"
            "<p>{{persona.signature}}</p>"
        ),
    },
]

# 4 email + 2 SMS = exactly the 6-touch / 2-SMS cadence cap.
SEQUENCE_GRAPH: dict = {
    "entry_step_keys": ["step_email_1"],
    "steps": [
        {
            "key": "step_email_1",
            "type": "send_email",
            "name": "01 · Signal opener (email)",
            "template_key": "Acme_email_1",
            "fallback": "send_sms",
            "fallback_template_key": "Acme_email_1_sms",
            "next": "step_wait_1",
        },
        {"key": "step_wait_1", "type": "delay", "name": "Wait 3 days", "amount": 3, "unit": "days", "next": "step_email_2"},
        {
            "key": "step_email_2",
            "type": "send_email",
            "name": "02 · Proof point (email)",
            "template_key": "Acme_email_2",
            "fallback": "skip",
            "next": "step_cond_sms",
        },
        {
            "key": "step_cond_sms",
            "type": "condition",
            "name": "SMS available?",
            "predicate": {"type": "channel_available", "channel": "sms"},
            "on_true": "step_sms_1",
            "on_false": "step_email_3",
        },
        {
            "key": "step_sms_1",
            "type": "send_sms",
            "name": "03 · SMS nudge",
            "template_key": "Acme_sms_1",
            "fallback": "send_email",
            "fallback_template_key": "Acme_sms_1_email",
            "next": "step_wait_2",
        },
        {"key": "step_wait_2", "type": "delay", "name": "Wait 3 days", "amount": 3, "unit": "days", "next": "step_email_3"},
        {
            "key": "step_email_3",
            "type": "send_email",
            "name": "04 · Different angle (email)",
            "template_key": "Acme_email_3",
            "fallback": "skip",
            "next": "step_cond_reply",
        },
        {
            "key": "step_cond_reply",
            "type": "condition",
            "name": "Already replied?",
            "predicate": {"type": "replied"},
            "on_true": "step_exit_replied",
            "on_false": "step_wait_3",
        },
        {"key": "step_wait_3", "type": "delay", "name": "Wait 3 days", "amount": 3, "unit": "days", "next": "step_sms_2"},
        {
            "key": "step_sms_2",
            "type": "send_sms",
            "name": "05 · Soft close (SMS)",
            "template_key": "Acme_sms_2",
            "fallback": "send_email",
            "fallback_template_key": "Acme_sms_2_email",
            "next": "step_wait_4",
        },
        {"key": "step_wait_4", "type": "delay", "name": "Wait 3 days", "amount": 3, "unit": "days", "next": "step_email_4"},
        {
            "key": "step_email_4",
            "type": "send_email",
            "name": "06 · Breakup (email)",
            "template_key": "Acme_email_4",
            "fallback": "skip",
            "next": "step_exit_done",
        },
        {"key": "step_exit_replied", "type": "exit", "name": "Exit - replied", "reason": c.EXIT_REPLIED},
        {"key": "step_exit_done", "type": "exit", "name": "Exit - cadence complete", "reason": c.EXIT_COMPLETED},
    ],
}

DEMO_CONTACTS = [
    {
        "email": "wei.lim@example-hardware.sg",
        "phone_e164": "+6591230001",
        "first_name": "Wei",
        "last_name": "Lim",
        "company": "Orbit Hardware",
        "job_title": "Head of Engineering",
        "custom_fields": {"role_title": "Mechanical Design Engineer"},
    },
    {
        "email": "priya.nair@example-robotics.sg",
        "phone_e164": "+6591230002",
        "first_name": "Priya",
        "last_name": "Nair",
        "company": "Sentinel Robotics",
        "job_title": "VP Product",
        "custom_fields": {"role_title": "Hardware Engineer"},
    },
    {
        "email": "daniel.tan@example-instruments.sg",
        "phone_e164": "",
        "first_name": "Daniel",
        "last_name": "Tan",
        "company": "Meridian Instruments",
        "job_title": "Founder",
        "custom_fields": {"role_title": "Product Design Engineer"},
    },
    {
        "email": "aisha.rahman@example-medtech.sg",
        "phone_e164": "+6591230004",
        "first_name": "Aisha",
        "last_name": "Rahman",
        "company": "Aster Medtech",
        "job_title": "Director of R&D",
        "custom_fields": {"role_title": "Mechanical Engineer"},
    },
]


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------
def seed_all(db: Session, settings: Settings | None = None) -> dict[str, str]:
    settings = settings or get_settings()
    ensure_bootstrap_admin(db, settings)

    client = db.scalar(select(Client).where(Client.slug == Acme_SLUG))
    if client is None:
        client = Client(
            name="Acme innovation",
            slug=Acme_SLUG,
            default_timezone="Asia/Singapore",
        )
        db.add(client)
        db.flush()

    persona = db.scalar(select(SenderPersona).where(SenderPersona.client_id == client.id))
    if persona is None:
        persona = SenderPersona(
            client_id=client.id,
            display_name="Ivan · Acme",
            from_name="Ivan Tan",
            from_email="ivan@acme.example",
            reply_to="ivan@acme.example",
            signature_html=(
                "Ivan Tan · Acme innovation<br>"
                "Design · Prototyping · Manufacturing<br>"
                "Acme.example.sg"
            ),
            calendar_link="https://cal.example.com/Acme/30min",
            is_default=True,
        )
        db.add(persona)
        db.flush()

    _seed_inboxes(db, client)
    _seed_phone_numbers(db, client)
    _seed_templates(db, client)
    sequence = _seed_sequence(db, client)
    _seed_contacts(db, client)

    if sequence.current_version_id is None:
        raise RuntimeError("Seeded sequence has no published version")

    db.flush()
    return {
        "client_id": client.id,
        "sequence_id": sequence.id,
        "sequence_version_id": sequence.current_version_id,
        "persona_id": persona.id,
    }


def _seed_inboxes(db: Session, client: Client) -> None:
    existing = {row.email for row in db.scalars(select(SenderInbox)).all()}
    warmup_start = utcnow() - timedelta(days=45)
    for index in range(1, 5):
        email = f"ivan{index}@Acme-outbound.sg"
        if email in existing:
            continue
        db.add(
            SenderInbox(
                client_id=client.id,
                email=email,
                display_name=f"Acme outbound {index}",
                smtp_host="smtp.Acme-outbound.sg",
                smtp_port=587,
                smtp_username=email,
                smtp_password_ref=f"Acme_SMTP_PASSWORD_{index}",
                imap_host="imap.Acme-outbound.sg",
                imap_port=993,
                use_tls=True,
                daily_cap=25,
                status=c.INBOX_ACTIVE,
                warmup_started_at=warmup_start,
            )
        )
    db.flush()


def _seed_phone_numbers(db: Session, client: Client) -> None:
    existing = {row.e164 for row in db.scalars(select(PhoneNumber)).all()}
    for number in ("+6580001000", "+6580001001"):
        if number in existing:
            continue
        db.add(
            PhoneNumber(
                client_id=client.id,
                e164=number,
                provider="twilio",
                daily_cap=200,
                status=c.INBOX_ACTIVE,
            )
        )
    db.flush()


def _seed_templates(db: Session, client: Client) -> None:
    existing = {row.key for row in db.scalars(select(Template)).all()}
    for spec in TEMPLATES:
        if spec["key"] in existing:
            continue
        db.add(
            Template(
                client_id=client.id,
                key=spec["key"],
                channel=spec["channel"],
                name=spec["name"],
                subject=spec["subject"],
                body=spec["body"],
            )
        )
    db.flush()


def _seed_sequence(db: Session, client: Client) -> Sequence:
    sequence = db.scalar(
        select(Sequence).where(Sequence.client_id == client.id, Sequence.name == SEQUENCE_NAME)
    )
    if sequence is None:
        sequence = Sequence(
            client_id=client.id,
            name=SEQUENCE_NAME,
            description=(
                "Signal-led cross-channel nurture: email for substance, SMS for urgency, "
                "6 touches over 14 days."
            ),
            status=c.SEQUENCE_ACTIVE,
            reaction_policy=c.REACTION_POLICY_CANCEL_AND_REBRANCH,
            allow_cadence_override=False,
            cadence_preset="short_direct_14d",
        )
        db.add(sequence)
        db.flush()

    if sequence.current_version_id is None:
        version = SequenceVersion(
            sequence_id=sequence.id,
            version=1,
            graph=SEQUENCE_GRAPH,
            notes="Seeded from the spec (§11). 4 email + 2 SMS over 14 days.",
            published_at=utcnow(),
        )
        db.add(version)
        db.flush()
        sequence.current_version_id = version.id
        db.flush()
    return sequence


def _seed_contacts(db: Session, client: Client) -> None:
    existing = {row.email for row in db.scalars(select(Contact)).all()}
    for spec in DEMO_CONTACTS:
        if spec["email"] in existing:
            continue
        db.add(
            Contact(
                client_id=client.id,
                email=spec["email"],
                phone_e164=spec["phone_e164"] or None,
                first_name=spec["first_name"],
                last_name=spec["last_name"],
                company=spec["company"],
                job_title=spec["job_title"],
                timezone="Asia/Singapore",
                source="manual",
                tags=["demo"],
                custom_fields=spec["custom_fields"],
            )
        )
    db.flush()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    if settings.auto_create_tables:
        create_all(settings)
    with session_scope() as db:
        ids = seed_all(db, settings)
    logger.info("Seeded: %s", ids)


if __name__ == "__main__":  # pragma: no cover
    main()
