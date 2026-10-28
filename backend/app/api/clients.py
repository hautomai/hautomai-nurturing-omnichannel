"""Clients and their sending assets."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import constants as c
from app.api.deps import get_or_404, list_clients, resolve_client
from app.config import Settings, get_settings
from app.db import get_db
from app.engine.runner import resume_enrollment
from app.engine.scheduler import reset_daily_counter
from app.models import Client, Enrollment, PhoneNumber, SenderInbox, SenderPersona, Template
from app.schemas import (
    ClientOut,
    ClientUpdate,
    InboxCreate,
    InboxOut,
    InboxUpdate,
    PersonaCreate,
    PersonaOut,
    PhoneNumberCreate,
    PhoneNumberOut,
    TemplateCreate,
    TemplateOut,
    TemplateUpdate,
)
from app.security import get_current_user

router = APIRouter(tags=["clients"], dependencies=[Depends(get_current_user)])


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------
@router.get("/clients", response_model=list[ClientOut])
def get_clients(db: Session = Depends(get_db)) -> list[Client]:
    return list_clients(db)


@router.get("/clients/{client_id}", response_model=ClientOut)
def get_client(client_id: str, db: Session = Depends(get_db)) -> Client:
    return resolve_client(db, client_id)


@router.patch("/clients/{client_id}", response_model=ClientOut)
def update_client(
    client_id: str,
    payload: ClientUpdate,
    db: Session = Depends(get_db),
) -> Client:
    """Includes the one-click kill switch (halt all sends for this client)."""
    client = resolve_client(db, client_id)
    was_halted = client.sending_halted

    if payload.sending_halted is not None:
        client.sending_halted = payload.sending_halted
        if payload.sending_halted:
            client.halt_reason = payload.halt_reason or "manually halted"
        else:
            client.halt_reason = None
    if payload.halt_reason is not None:
        client.halt_reason = payload.halt_reason
    if payload.default_timezone is not None:
        client.default_timezone = payload.default_timezone

    # Lifting the halt resumes anything that was paused because of it.
    if was_halted and not client.sending_halted:
        paused = db.scalars(
            select(Enrollment).where(
                Enrollment.client_id == client.id,
                Enrollment.status == c.ENROLLMENT_PAUSED,
                Enrollment.pause_reason == c.EXIT_CLIENT_HALTED,
            )
        ).all()
        for enrollment in paused:
            resume_enrollment(db, enrollment, reason="halt_lifted")

    db.commit()
    db.refresh(client)
    return client


# ---------------------------------------------------------------------------
# Personas
# ---------------------------------------------------------------------------
@router.get("/clients/{client_id}/personas", response_model=list[PersonaOut])
def get_personas(client_id: str, db: Session = Depends(get_db)) -> list[SenderPersona]:
    client = resolve_client(db, client_id)
    return list(
        db.scalars(
            select(SenderPersona)
            .where(SenderPersona.client_id == client.id)
            .order_by(SenderPersona.is_default.desc())
        ).all()
    )


@router.post("/clients/{client_id}/personas", response_model=PersonaOut, status_code=201)
def create_persona(
    client_id: str, payload: PersonaCreate, db: Session = Depends(get_db)
) -> SenderPersona:
    client = resolve_client(db, client_id)
    if payload.is_default:
        for persona in db.scalars(
            select(SenderPersona).where(SenderPersona.client_id == client.id)
        ).all():
            persona.is_default = False
    persona = SenderPersona(
        client_id=client.id,
        display_name=payload.display_name,
        from_name=payload.from_name,
        from_email=payload.from_email,
        reply_to=payload.reply_to or payload.from_email,
        signature_html=payload.signature_html,
        calendar_link=payload.calendar_link,
        is_default=payload.is_default,
    )
    db.add(persona)
    db.commit()
    db.refresh(persona)
    return persona


# ---------------------------------------------------------------------------
# Inboxes
# ---------------------------------------------------------------------------
@router.get("/clients/{client_id}/inboxes", response_model=list[InboxOut])
def get_inboxes(client_id: str, db: Session = Depends(get_db)) -> list[SenderInbox]:
    client = resolve_client(db, client_id)
    return list(
        db.scalars(
            select(SenderInbox)
            .where(SenderInbox.client_id == client.id)
            .order_by(SenderInbox.email)
        ).all()
    )


@router.post("/clients/{client_id}/inboxes", response_model=InboxOut, status_code=201)
def create_inbox(
    client_id: str,
    payload: InboxCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SenderInbox:
    client = resolve_client(db, client_id)
    inbox = SenderInbox(
        client_id=client.id,
        email=payload.email.lower(),
        display_name=payload.display_name or payload.email,
        smtp_host=payload.smtp_host,
        smtp_port=payload.smtp_port,
        smtp_username=payload.smtp_username,
        smtp_password_ref=payload.smtp_password_ref,
        imap_host=payload.imap_host,
        imap_port=payload.imap_port,
        use_tls=payload.use_tls,
        daily_cap=payload.daily_cap or settings.default_inbox_daily_cap,
        status=c.INBOX_WARMING,
    )
    db.add(inbox)
    db.commit()
    db.refresh(inbox)
    return inbox


@router.patch("/inboxes/{inbox_id}", response_model=InboxOut)
def update_inbox(
    inbox_id: str, payload: InboxUpdate, db: Session = Depends(get_db)
) -> SenderInbox:
    inbox = get_or_404(db, SenderInbox, inbox_id, "Inbox")
    if payload.status is not None:
        inbox.status = payload.status
        if payload.status == c.INBOX_ACTIVE:
            inbox.paused_reason = None
            inbox.consecutive_failures = 0
        elif payload.status == c.INBOX_PAUSED:
            inbox.paused_reason = payload.paused_reason or "paused manually"
    if payload.daily_cap is not None:
        inbox.daily_cap = payload.daily_cap
    db.commit()
    db.refresh(inbox)
    return inbox


@router.post("/inboxes/{inbox_id}/reset-counter", response_model=InboxOut)
def reset_inbox_counter(inbox_id: str, db: Session = Depends(get_db)) -> SenderInbox:
    inbox = get_or_404(db, SenderInbox, inbox_id, "Inbox")
    inbox.sends_today = 0
    db.commit()
    db.refresh(inbox)
    return inbox


# ---------------------------------------------------------------------------
# Phone numbers
# ---------------------------------------------------------------------------
@router.get("/clients/{client_id}/phone-numbers", response_model=list[PhoneNumberOut])
def get_phone_numbers(client_id: str, db: Session = Depends(get_db)) -> list[PhoneNumber]:
    client = resolve_client(db, client_id)
    return list(
        db.scalars(
            select(PhoneNumber).where(PhoneNumber.client_id == client.id).order_by(PhoneNumber.e164)
        ).all()
    )


@router.post("/clients/{client_id}/phone-numbers", response_model=PhoneNumberOut, status_code=201)
def create_phone_number(
    client_id: str, payload: PhoneNumberCreate, db: Session = Depends(get_db)
) -> PhoneNumber:
    client = resolve_client(db, client_id)
    number = PhoneNumber(
        client_id=client.id,
        e164=payload.e164,
        provider=payload.provider,
        daily_cap=payload.daily_cap,
        status=c.INBOX_ACTIVE,
    )
    db.add(number)
    db.commit()
    db.refresh(number)
    return number


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
@router.get("/clients/{client_id}/templates", response_model=list[TemplateOut])
def get_templates(client_id: str, db: Session = Depends(get_db)) -> list[Template]:
    client = resolve_client(db, client_id)
    return list(
        db.scalars(
            select(Template)
            .where(Template.client_id == client.id)
            .order_by(Template.channel, Template.key)
        ).all()
    )


@router.post("/clients/{client_id}/templates", response_model=TemplateOut, status_code=201)
def create_template(
    client_id: str, payload: TemplateCreate, db: Session = Depends(get_db)
) -> Template:
    client = resolve_client(db, client_id)
    existing = db.scalar(
        select(Template).where(Template.client_id == client.id, Template.key == payload.key)
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"Template '{payload.key}' already exists"
        )
    template = Template(
        client_id=client.id,
        key=payload.key,
        channel=payload.channel,
        name=payload.name,
        subject=payload.subject,
        body=payload.body,
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


@router.patch("/templates/{template_id}", response_model=TemplateOut)
def update_template(
    template_id: str, payload: TemplateUpdate, db: Session = Depends(get_db)
) -> Template:
    template = get_or_404(db, Template, template_id, "Template")
    if payload.name is not None:
        template.name = payload.name
    if payload.subject is not None:
        template.subject = payload.subject
    if payload.body is not None:
        template.body = payload.body
    db.commit()
    db.refresh(template)
    return template


@router.delete("/templates/{template_id}", status_code=204)
def delete_template(template_id: str, db: Session = Depends(get_db)) -> None:
    template = get_or_404(db, Template, template_id, "Template")
    db.delete(template)
    db.commit()
