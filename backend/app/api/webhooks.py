"""Inbound webhooks.

All four are unauthenticated in the JWT sense but verify a shared secret when one
is configured (``verify_shared_secret``), and every one of them is idempotent via
the engine's dedupe keys.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import constants as c
from app.api.deps import get_channels, get_crm, resolve_client
from app.channels.base import InboundEmail
from app.config import Settings, get_settings
from app.db import get_db
from app.engine import runner
from app.models import Contact, Enrollment, PhoneNumber
from app.security import verify_shared_secret

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# ---------------------------------------------------------------------------
# Twilio
# ---------------------------------------------------------------------------
@router.post("/twilio/status")
async def twilio_status(
    request: Request,
    MessageSid: str = Form(default=""),
    MessageStatus: str = Form(default=""),
    To: str = Form(default=""),
    From: str = Form(default=""),
    ErrorCode: str = Form(default=""),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Delivery receipts. Undelivered SMS triggers the fallback substitution."""
    channel_status = (MessageStatus or "").lower()
    message = runner.record_delivery(
        db, provider_message_id=MessageSid, status=channel_status
    )
    if message is None:
        db.rollback()
        return {"ok": True, "ignored": "unknown message"}

    if channel_status in ("undelivered", "failed") and message.contact_id:
        contact = db.get(Contact, message.contact_id)
        if contact is not None:
            runner.ingest_event(
                db,
                client_id=message.client_id,
                event_type=(
                    c.EVENT_SMS_UNDELIVERED
                    if message.channel == c.CHANNEL_SMS
                    else c.EVENT_EMAIL_BOUNCED
                ),
                channel=message.channel,
                contact=contact,
                payload={
                    "status": channel_status,
                    "error_code": ErrorCode,
                    "to": To or From,
                    "contact_label": runner._contact_label(contact),  # noqa: SLF001
                },
                dedupe_key=f"reaction:undelivered:{MessageSid}",
                channels=get_channels(),
                crm=get_crm(),
                settings=settings,
            )
    db.commit()
    return {"ok": True, "status": channel_status}


@router.post("/twilio/inbound")
async def twilio_inbound(
    From: str = Form(...),
    Body: str = Form(default=""),
    MessageSid: str = Form(default=""),
    To: str = Form(default=""),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """An inbound SMS stops automation exactly like an email reply (D19)."""
    number = db.scalar(select(PhoneNumber).where(PhoneNumber.e164 == To))
    client = resolve_client(db, number.client_id if number else None)

    record = runner.handle_inbound_sms(
        db,
        client_id=client.id,
        from_address=From,
        body=Body,
        provider_message_id=MessageSid,
        channels=get_channels(),
        crm=get_crm(),
        settings=settings,
    )
    db.commit()
    return {
        "ok": True,
        "matched": bool(record and record.contact_id),
        "sentiment": record.sentiment if record else None,
    }


# ---------------------------------------------------------------------------
# Slack interactions
# ---------------------------------------------------------------------------
class SlackActionPayload(BaseModel):
    enrollment_id: str = ""
    action: str = ""
    user: str = ""
    text: str = ""


@router.post("/slack/actions")
async def slack_actions(
    payload: SlackActionPayload,
    x_slack_signature: str | None = Header(default=None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Interactive buttons: pause, resume, exit, send-next, mark-not-fit."""
    verify_shared_secret(x_slack_signature, settings.slack_signing_secret)

    enrollment = db.get(Enrollment, payload.enrollment_id)
    if enrollment is None:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    action = payload.action
    if action == "pause":
        runner.pause_enrollment(db, enrollment, reason="slack_action")
    elif action == "resume":
        runner.resume_enrollment(db, enrollment, reason="slack_action")
        now = datetime.now(timezone.utc)
        if enrollment.current_step_key:
            from app.engine import scheduler

            scheduler.schedule_timer(
                db,
                enrollment_id=enrollment.id,
                step_key=enrollment.current_step_key,
                run_at=now,
            )
            enrollment.next_run_at = now
    elif action == "exit":
        runner.exit_enrollment(
            db,
            enrollment,
            reason="slack_action",
            client_id=enrollment.client_id,
            contact_id=enrollment.contact_id,
            channels=get_channels(),
            settings=settings,
        )
    elif action == "send_next":
        now = datetime.now(timezone.utc)
        if enrollment.current_step_key:
            from app.engine import scheduler

            enrollment.status = c.ENROLLMENT_ACTIVE
            enrollment.pause_reason = None
            scheduler.schedule_timer(
                db,
                enrollment_id=enrollment.id,
                step_key=enrollment.current_step_key,
                run_at=now,
            )
            enrollment.next_run_at = now
    elif action == "mark_not_fit":
        runner.add_suppression(
            db,
            client_id=enrollment.client_id,
            contact_id=enrollment.contact_id,
            channel="all",
            reason=c.SUPPRESS_MANUAL,
            note=f"marked not a fit by {payload.user or 'slack'}",
        )
        runner.exit_enrollment(
            db,
            enrollment,
            reason="not_a_fit",
            client_id=enrollment.client_id,
            contact_id=enrollment.contact_id,
            channels=get_channels(),
            settings=settings,
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action '{action}'")

    runner.record_event(
        db,
        client_id=enrollment.client_id,
        contact_id=enrollment.contact_id,
        enrollment_id=enrollment.id,
        event_type=c.EVENT_SLACK_ACTION,
        channel=c.CHANNEL_SLACK,
        payload={"action": action, "user": payload.user, "text": payload.text},
        dedupe_key=f"slack:{enrollment.id}:{action}:{datetime.now(timezone.utc).timestamp()}",
    )
    db.commit()
    return {"ok": True, "action": action}


# ---------------------------------------------------------------------------
# GoHighLevel
# ---------------------------------------------------------------------------
class GhlWebhook(BaseModel):
    type: str = ""
    client_id: str | None = None
    email: str | None = None
    phone: str | None = None
    stage: str = ""
    appointment_id: str = ""
    contact_label: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


@router.post("/ghl")
async def ghl_webhook(
    payload: GhlWebhook,
    x_ghl_signature: str | None = Header(default=None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Appointments and opportunity stage changes are the meeting signal (D24)."""
    verify_shared_secret(x_ghl_signature, settings.ghl_webhook_secret)
    client = resolve_client(db, payload.client_id)

    event_type = payload.type or "appointment.created"
    if event_type not in ("appointment.created", "appointment.booked", "opportunity.stage_changed"):
        return {"ok": True, "ignored": event_type}

    contact = None
    if payload.email:
        contact = db.scalar(
            select(Contact).where(
                Contact.client_id == client.id, Contact.email == payload.email.lower()
            )
        )
    if contact is None and payload.phone:
        contact = db.scalar(
            select(Contact).where(Contact.client_id == client.id, Contact.phone_e164 == payload.phone)
        )
    if contact is None:
        return {"ok": True, "matched": False}

    runner.ingest_event(
        db,
        client_id=client.id,
        event_type=c.EVENT_MEETING_BOOKED,
        channel=c.CHANNEL_EMAIL,
        contact=contact,
        payload={
            "source": "ghl",
            "event": event_type,
            "stage": payload.stage,
            "appointment_id": payload.appointment_id,
            "contact_label": payload.contact_label or runner._contact_label(contact),  # noqa: SLF001
        },
        dedupe_key=f"ghl:{event_type}:{payload.appointment_id or contact.id}",
        channels=get_channels(),
        crm=get_crm(),
        settings=settings,
    )
    db.commit()
    return {"ok": True, "matched": True}


# ---------------------------------------------------------------------------
# Pushed email replies (for external automations, e.g. the existing n8n flow)
# ---------------------------------------------------------------------------
class InboundEmailPayload(BaseModel):
    client_id: str | None = None
    from_address: str
    to_address: str = ""
    subject: str = ""
    body: str = ""
    message_id: str = ""
    in_reply_to: str = ""
    references: list[str] = Field(default_factory=list)
    headers: dict[str, str] = Field(default_factory=dict)


@router.post("/email/inbound")
async def inbound_email(
    payload: InboundEmailPayload,
    x_webhook_secret: str | None = Header(default=None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Let an external tool push a reply in when IMAP polling is not wanted."""
    verify_shared_secret(x_webhook_secret, settings.webhook_secret)
    client = resolve_client(db, payload.client_id)

    record = runner.handle_inbound_email(
        db,
        InboundEmail(
            from_address=payload.from_address,
            to_address=payload.to_address,
            subject=payload.subject,
            body=payload.body,
            message_id=payload.message_id,
            in_reply_to=payload.in_reply_to,
            references=payload.references,
            headers=payload.headers,
        ),
        client_id=client.id,
        channels=get_channels(),
        crm=get_crm(),
        settings=settings,
    )
    db.commit()
    return {
        "ok": True,
        "matched": bool(record and record.contact_id),
        "sentiment": record.sentiment if record else None,
        "auto_reply": bool(record and record.is_auto_reply),
    }
