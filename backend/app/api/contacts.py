"""Contacts: CRUD, CSV intake, timeline and suppression."""

from __future__ import annotations

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import constants as c
from app.api.deps import get_channels, get_crm, get_or_404, resolve_client
from app.config import Settings, get_settings
from app.db import get_db
from app.engine import runner
from app.models import BranchDecision, Contact, Enrollment, Event, HumanTask, InboundMessage, Message, Suppression
from app.schemas import (
    ContactCreate,
    ContactOut,
    ContactUpdate,
    EnrollmentOut,
    ImportResult,
    SuppressionCreate,
    SuppressionOut,
    TimelineEntry,
)
from app.security import get_current_user

router = APIRouter(tags=["contacts"], dependencies=[Depends(get_current_user)])

IMPORTABLE_FIELDS = (
    "email",
    "phone_e164",
    "first_name",
    "last_name",
    "company",
    "job_title",
    "timezone",
)


def _normalize_email(value: str | None) -> str | None:
    value = (value or "").strip().lower()
    return value or None


def _normalize_phone(value: str | None) -> str | None:
    value = (value or "").strip().replace(" ", "").replace("-", "")
    return value or None


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
@router.get("/clients/{client_id}/contacts", response_model=list[ContactOut])
def list_contacts(
    client_id: str,
    q: str | None = Query(default=None, description="Search name, email or company"),
    limit: int = Query(default=100, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list[Contact]:
    client = resolve_client(db, client_id)
    stmt = select(Contact).where(Contact.client_id == client.id)
    if q:
        needle = f"%{q.lower()}%"
        stmt = stmt.where(
            Contact.email.ilike(needle)
            | Contact.first_name.ilike(needle)
            | Contact.last_name.ilike(needle)
            | Contact.company.ilike(needle)
        )
    stmt = stmt.order_by(Contact.created_at.desc()).limit(limit).offset(offset)
    return list(db.scalars(stmt).all())


@router.post("/clients/{client_id}/contacts", response_model=ContactOut, status_code=201)
def create_contact(
    client_id: str,
    payload: ContactCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Contact:
    client = resolve_client(db, client_id)
    email = _normalize_email(payload.email)
    if email:
        existing = db.scalar(
            select(Contact).where(Contact.client_id == client.id, Contact.email == email)
        )
        if existing is not None:
            raise HTTPException(status_code=409, detail="A contact with that email already exists")

    contact = Contact(
        client_id=client.id,
        email=email,
        phone_e164=_normalize_phone(payload.phone_e164),
        first_name=payload.first_name,
        last_name=payload.last_name,
        company=payload.company,
        job_title=payload.job_title,
        timezone=payload.timezone,
        source=payload.source,
        tags=payload.tags,
        custom_fields=payload.custom_fields,
    )
    db.add(contact)
    db.flush()

    if payload.enroll_in_sequence_id:
        from app.models import Sequence

        sequence = get_or_404(db, Sequence, payload.enroll_in_sequence_id, "Sequence")
        enrollment, reason = runner.enroll_contact(
            db,
            contact=contact,
            sequence=sequence,
            channels=get_channels(),
            settings=settings,
            crm=get_crm(),
        )
        if enrollment is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Contact created but could not be enrolled: {reason}",
            )

    db.commit()
    db.refresh(contact)
    return contact


@router.get("/contacts/{contact_id}", response_model=ContactOut)
def get_contact(contact_id: str, db: Session = Depends(get_db)) -> Contact:
    return get_or_404(db, Contact, contact_id, "Contact")


@router.patch("/contacts/{contact_id}", response_model=ContactOut)
def update_contact(
    contact_id: str, payload: ContactUpdate, db: Session = Depends(get_db)
) -> Contact:
    contact = get_or_404(db, Contact, contact_id, "Contact")
    data = payload.model_dump(exclude_unset=True)
    if "email" in data:
        data["email"] = _normalize_email(data["email"])
    if "phone_e164" in data:
        data["phone_e164"] = _normalize_phone(data["phone_e164"])
    for key, value in data.items():
        setattr(contact, key, value)
    db.commit()
    db.refresh(contact)
    return contact


@router.delete("/contacts/{contact_id}", status_code=204)
def delete_contact(contact_id: str, db: Session = Depends(get_db)) -> None:
    contact = get_or_404(db, Contact, contact_id, "Contact")
    db.delete(contact)
    db.commit()


@router.get("/contacts/{contact_id}/enrollments", response_model=list[EnrollmentOut])
def contact_enrollments(contact_id: str, db: Session = Depends(get_db)) -> list[Enrollment]:
    get_or_404(db, Contact, contact_id, "Contact")
    return list(
        db.scalars(
            select(Enrollment)
            .where(Enrollment.contact_id == contact_id)
            .order_by(Enrollment.entered_at.desc())
        ).all()
    )


# ---------------------------------------------------------------------------
# CSV intake
# ---------------------------------------------------------------------------
@router.post("/clients/{client_id}/contacts/import", response_model=ImportResult)
async def import_contacts(
    client_id: str,
    file: UploadFile = File(...),
    email_column: str = Query(default="email"),
    phone_column: str = Query(default="phone"),
    first_name_column: str = Query(default="first_name"),
    last_name_column: str = Query(default="last_name"),
    company_column: str = Query(default="company"),
    job_title_column: str = Query(default="job_title"),
    timezone_column: str = Query(default="timezone"),
    enroll_sequence_id: str | None = Query(default=None),
    dry_run: bool = Query(default=False),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ImportResult:
    """CSV upload with explicit column mapping and per-row validation."""
    client = resolve_client(db, client_id)
    raw = (await file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))
    if reader.fieldnames is None:
        raise HTTPException(status_code=400, detail="CSV has no header row")

    mapping = {
        "email": email_column,
        "phone_e164": phone_column,
        "first_name": first_name_column,
        "last_name": last_name_column,
        "company": company_column,
        "job_title": job_title_column,
        "timezone": timezone_column,
    }
    # A contact needs at least one way to reach them. Optional columns (names,
    # company, timezone) may simply be absent from the file.
    identity_columns = [
        column for field, column in mapping.items() if field in ("email", "phone_e164") and column
    ]
    present_identity = [column for column in identity_columns if column in reader.fieldnames]
    if not present_identity:
        raise HTTPException(
            status_code=400,
            detail=(
                "CSV is missing mapped columns: expected '"
                f"{email_column}' or '{phone_column}' in {', '.join(reader.fieldnames)}"
            ),
        )
    mapping = {
        field: column for field, column in mapping.items() if column and column in reader.fieldnames
    }

    created = 0
    updated = 0
    enrolled = 0
    rejected: list[dict[str, str]] = []

    for line_number, row in enumerate(reader, start=2):
        values = {
            field: (row.get(column) or "").strip()
            for field, column in mapping.items()
            if column
        }
        email = _normalize_email(values.get("email"))
        phone = _normalize_phone(values.get("phone_e164"))
        label = email or phone or f"row {line_number}"

        if not email and not phone:
            rejected.append({"row": str(line_number), "reason": "neither email nor phone", "value": label})
            continue
        if email and "@" not in email:
            rejected.append({"row": str(line_number), "reason": "invalid email", "value": email})
            continue

        existing = None
        if email:
            existing = db.scalar(
                select(Contact).where(Contact.client_id == client.id, Contact.email == email)
            )
        if existing is None and phone:
            existing = db.scalar(
                select(Contact).where(Contact.client_id == client.id, Contact.phone_e164 == phone)
            )

        if existing is not None:
            for field in IMPORTABLE_FIELDS:
                if values.get(field):
                    setattr(existing, field, values[field])
            updated += 1
            contact = existing
        else:
            if dry_run:
                created += 1
                continue
            contact = Contact(
                client_id=client.id,
                email=email,
                phone_e164=phone,
                first_name=values.get("first_name", ""),
                last_name=values.get("last_name", ""),
                company=values.get("company", ""),
                job_title=values.get("job_title", ""),
                timezone=values.get("timezone", ""),
                source="csv",
            )
            db.add(contact)
            db.flush()
            created += 1

        if enroll_sequence_id and not dry_run:
            from app.models import Sequence

            sequence = get_or_404(db, Sequence, enroll_sequence_id, "Sequence")
            enrollment, _reason = runner.enroll_contact(
                db,
                contact=contact,
                sequence=sequence,
                channels=get_channels(),
                settings=settings,
                crm=get_crm(),
            )
            if enrollment is not None:
                enrolled += 1

    if dry_run:
        db.rollback()
    else:
        db.commit()

    return ImportResult(created=created, updated=updated, rejected=rejected, enrolled=enrolled)


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------
@router.get("/contacts/{contact_id}/timeline", response_model=list[TimelineEntry])
def timeline(
    contact_id: str, limit: int = Query(default=200, le=500), db: Session = Depends(get_db)
) -> list[TimelineEntry]:
    """Everything that happened to one contact, newest first."""
    get_or_404(db, Contact, contact_id, "Contact")
    entries: list[TimelineEntry] = []

    for message in db.scalars(
        select(Message).where(Message.contact_id == contact_id).order_by(Message.created_at.desc()).limit(limit)
    ).all():
        label = {
            c.MESSAGE_SENT: "sent",
            c.MESSAGE_DELIVERED: "delivered",
            c.MESSAGE_BOUNCED: "bounced",
            c.MESSAGE_FAILED: "failed",
            c.MESSAGE_SKIPPED: "skipped",
            c.MESSAGE_SUBSTITUTED: "substituted",
            c.MESSAGE_QUEUED: "queued",
        }.get(message.status, message.status)
        title = f"{message.channel} {label}"
        if message.skip_reason:
            title += f" ({message.skip_reason})"
        entries.append(
            TimelineEntry(
                kind="message",
                at=message.sent_at or message.created_at,
                title=title,
                channel=message.channel,
                status=message.status,
                detail={
                    "step_key": message.step_key,
                    "subject": message.subject,
                    "body": message.body[:600],
                    "subject_line": message.subject,
                    "skip_reason": message.skip_reason,
                    "bounce_type": message.bounce_type,
                    "error": message.error,
                    "inbox": message.from_address,
                    "is_substitution": message.is_substitution,
                    "message_id": message.provider_message_id,
                },
            )
        )

    for inbound in db.scalars(
        select(InboundMessage).where(InboundMessage.contact_id == contact_id).order_by(InboundMessage.received_at.desc()).limit(limit)
    ).all():
        entries.append(
            TimelineEntry(
                kind="inbound",
                at=inbound.received_at,
                title=(
                    f"inbound {inbound.channel} "
                    f"({'auto-reply' if inbound.is_auto_reply else inbound.sentiment})"
                ),
                channel=inbound.channel,
                status=inbound.sentiment,
                detail={
                    "subject": inbound.subject,
                    "body": inbound.body[:600],
                    "matched_rule": inbound.matched_rule,
                    "is_auto_reply": inbound.is_auto_reply,
                },
            )
        )

    for event in db.scalars(
        select(Event).where(Event.contact_id == contact_id).order_by(Event.occurred_at.desc()).limit(limit)
    ).all():
        entries.append(
            TimelineEntry(
                kind="event",
                at=event.occurred_at,
                title=event.type,
                channel=event.channel,
                status=event.type,
                detail=event.payload or {},
            )
        )

    enrollment_ids = [
        row.id
        for row in db.scalars(select(Enrollment).where(Enrollment.contact_id == contact_id)).all()
    ]
    if enrollment_ids:
        for decision in db.scalars(
            select(BranchDecision)
            .where(BranchDecision.enrollment_id.in_(enrollment_ids))
            .order_by(BranchDecision.created_at.desc())
            .limit(limit)
        ).all():
            entries.append(
                TimelineEntry(
                    kind="decision",
                    at=decision.created_at,
                    title=f"branch: {decision.rule}",
                    status=decision.outcome,
                    detail={
                        "step_key": decision.step_key,
                        "outcome": decision.outcome,
                        **(decision.detail or {}),
                    },
                )
            )

    for task in db.scalars(
        select(HumanTask).where(HumanTask.contact_id == contact_id).order_by(HumanTask.created_at.desc()).limit(limit)
    ).all():
        entries.append(
            TimelineEntry(
                kind="task",
                at=task.created_at,
                title=f"human task: {task.title}",
                status=task.status,
                detail={
                    "instructions": task.instructions,
                    "assignee": task.assignee,
                    "completed_at": task.completed_at.isoformat() if task.completed_at else None,
                    "outcome": task.outcome,
                },
            )
        )

    entries.sort(key=lambda entry: entry.at, reverse=True)
    return entries[:limit]


# ---------------------------------------------------------------------------
# Suppression
# ---------------------------------------------------------------------------
@router.get("/contacts/{contact_id}/suppressions", response_model=list[SuppressionOut])
def list_suppressions(contact_id: str, db: Session = Depends(get_db)) -> list[Suppression]:
    get_or_404(db, Contact, contact_id, "Contact")
    return list(
        db.scalars(
            select(Suppression).where(Suppression.contact_id == contact_id).order_by(Suppression.created_at.desc())
        ).all()
    )


@router.post("/contacts/{contact_id}/suppressions", response_model=SuppressionOut, status_code=201)
def create_suppression(
    contact_id: str, payload: SuppressionCreate, db: Session = Depends(get_db)
) -> Suppression:
    contact = get_or_404(db, Contact, contact_id, "Contact")
    row = runner.add_suppression(
        db,
        client_id=contact.client_id,
        contact_id=contact.id,
        channel=payload.channel,
        reason=payload.reason,
        note=payload.note,
    )
    # A suppression is a stop signal, not just a flag: exit open enrollments.
    open_enrollments = db.scalars(
        select(Enrollment).where(
            Enrollment.contact_id == contact.id,
            Enrollment.status.in_(c.ENROLLMENT_OPEN_STATES),
        )
    ).all()
    for enrollment in open_enrollments:
        enrollment.status = c.ENROLLMENT_EXITED
        enrollment.exit_reason = (
            c.EXIT_UNSUBSCRIBED if payload.reason != c.SUPPRESS_MANUAL else "suppressed"
        )
        enrollment.next_run_at = None
    db.commit()
    db.refresh(row)
    return row


@router.delete("/contacts/{contact_id}/suppressions/{suppression_id}", status_code=204)
def delete_suppression(
    contact_id: str, suppression_id: str, db: Session = Depends(get_db)
) -> None:
    row = get_or_404(db, Suppression, suppression_id, "Suppression")
    if row.contact_id != contact_id:
        raise HTTPException(status_code=404, detail="Suppression not found for this contact")
    db.delete(row)
    db.commit()
