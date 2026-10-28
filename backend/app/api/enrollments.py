"""Enrollment lifecycle: enroll, pause, resume, exit, force-advance."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import constants as c
from app.api.deps import get_channels, get_crm, get_or_404, resolve_client
from app.config import Settings, get_settings
from app.db import get_db
from app.engine import runner, scheduler
from app.engine.graph import parse_graph
from app.models import Contact, Enrollment, Sequence, SequenceVersion, Timer
from app.schemas import EnrollmentCreate, EnrollmentOut
from app.security import get_current_user

router = APIRouter(tags=["enrollments"], dependencies=[Depends(get_current_user)])


class ReasonPayload(BaseModel):
    reason: str = ""


class BulkEnrollPayload(BaseModel):
    contact_ids: list[str] = Field(default_factory=list)
    tag: str | None = None


class BulkEnrollResult(BaseModel):
    enrolled: int
    rejected: list[dict[str, str]] = Field(default_factory=list)


@router.get("/clients/{client_id}/enrollments", response_model=list[EnrollmentOut])
def list_enrollments(
    client_id: str,
    status_filter: str | None = Query(default=None, alias="status"),
    sequence_id: str | None = None,
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_db),
) -> list[Enrollment]:
    client = resolve_client(db, client_id)
    stmt = select(Enrollment).where(Enrollment.client_id == client.id)
    if status_filter:
        stmt = stmt.where(Enrollment.status == status_filter)
    if sequence_id:
        stmt = stmt.where(Enrollment.sequence_id == sequence_id)
    stmt = stmt.order_by(Enrollment.updated_at.desc()).limit(limit)
    return list(db.scalars(stmt).all())


@router.post("/enrollments", response_model=EnrollmentOut, status_code=201)
def create_enrollment(
    payload: EnrollmentCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Enrollment:
    contact = get_or_404(db, Contact, payload.contact_id, "Contact")
    sequence = get_or_404(db, Sequence, payload.sequence_id, "Sequence")
    if sequence.client_id != contact.client_id:
        raise HTTPException(status_code=400, detail="Contact and sequence belong to different clients")

    enrollment, reason = runner.enroll_contact(
        db,
        contact=contact,
        sequence=sequence,
        channels=get_channels(),
        settings=settings,
        start_at=payload.start_at,
        crm=get_crm(),
    )
    if enrollment is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Not enrolled: {reason}")
    db.commit()
    db.refresh(enrollment)
    return enrollment


@router.post(
    "/sequences/{sequence_id}/enroll", response_model=BulkEnrollResult, status_code=201
)
def bulk_enroll(
    sequence_id: str,
    payload: BulkEnrollPayload,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> BulkEnrollResult:
    """Enroll by explicit ids, or by tag across the whole client."""
    sequence = get_or_404(db, Sequence, sequence_id, "Sequence")

    if payload.tag:
        contacts = list(
            db.scalars(select(Contact).where(Contact.client_id == sequence.client_id)).all()
        )
        contacts = [row for row in contacts if payload.tag in (row.tags or [])]
        if payload.contact_ids:
            wanted = set(payload.contact_ids)
            contacts = [row for row in contacts if row.id in wanted]
    else:
        contacts = [
            get_or_404(db, Contact, contact_id, "Contact") for contact_id in payload.contact_ids
        ]

    enrolled = 0
    rejected: list[dict[str, str]] = []
    for contact in contacts:
        if contact.client_id != sequence.client_id:
            rejected.append({"contact_id": contact.id, "reason": "wrong_client"})
            continue
        enrollment, reason = runner.enroll_contact(
            db,
            contact=contact,
            sequence=sequence,
            channels=get_channels(),
            settings=settings,
            crm=get_crm(),
        )
        if enrollment is None:
            rejected.append({"contact_id": contact.id, "reason": reason})
        else:
            enrolled += 1

    db.commit()
    return BulkEnrollResult(enrolled=enrolled, rejected=rejected)


@router.get("/enrollments/{enrollment_id}", response_model=EnrollmentOut)
def get_enrollment(enrollment_id: str, db: Session = Depends(get_db)) -> Enrollment:
    return get_or_404(db, Enrollment, enrollment_id, "Enrollment")


@router.post("/enrollments/{enrollment_id}/pause", response_model=EnrollmentOut)
def pause(enrollment_id: str, payload: ReasonPayload | None = None, db: Session = Depends(get_db)) -> Enrollment:
    enrollment = get_or_404(db, Enrollment, enrollment_id, "Enrollment")
    if enrollment.status not in c.ENROLLMENT_OPEN_STATES:
        raise HTTPException(status_code=409, detail=f"Cannot pause an enrollment that is {enrollment.status}")
    runner.pause_enrollment(db, enrollment, reason=(payload.reason if payload else "manual"))
    db.commit()
    db.refresh(enrollment)
    return enrollment


@router.post("/enrollments/{enrollment_id}/resume", response_model=EnrollmentOut)
def resume(
    enrollment_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Enrollment:
    enrollment = get_or_404(db, Enrollment, enrollment_id, "Enrollment")
    if enrollment.status != c.ENROLLMENT_PAUSED:
        raise HTTPException(status_code=409, detail="Enrollment is not paused")

    runner.resume_enrollment(db, enrollment, reason="manual_resume")
    now = datetime.now(timezone.utc)
    if enrollment.current_step_key:
        scheduler.schedule_timer(
            db,
            enrollment_id=enrollment.id,
            step_key=enrollment.current_step_key,
            run_at=now,
        )
        enrollment.next_run_at = now
    db.commit()
    db.refresh(enrollment)
    return enrollment


@router.post("/enrollments/{enrollment_id}/exit", response_model=EnrollmentOut)
def exit_now(
    enrollment_id: str,
    payload: ReasonPayload | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Enrollment:
    enrollment = get_or_404(db, Enrollment, enrollment_id, "Enrollment")
    runner.exit_enrollment(
        db,
        enrollment,
        reason=(payload.reason or "manual_exit") if payload else "manual_exit",
        client_id=enrollment.client_id,
        contact_id=enrollment.contact_id,
        channels=get_channels(),
        settings=settings,
    )
    db.commit()
    db.refresh(enrollment)
    return enrollment


@router.post("/enrollments/{enrollment_id}/send-next", response_model=EnrollmentOut)
def send_next(enrollment_id: str, db: Session = Depends(get_db)) -> Enrollment:
    """Slack's 'Send next step now' action: run the current step immediately."""
    enrollment = get_or_404(db, Enrollment, enrollment_id, "Enrollment")
    if enrollment.status == c.ENROLLMENT_EXITED:
        raise HTTPException(status_code=409, detail="Enrollment has already exited")
    if not enrollment.current_step_key:
        raise HTTPException(status_code=409, detail="Enrollment has no current step")

    now = datetime.now(timezone.utc)
    enrollment.status = c.ENROLLMENT_ACTIVE
    enrollment.pause_reason = None
    scheduler.schedule_timer(
        db,
        enrollment_id=enrollment.id,
        step_key=enrollment.current_step_key,
        run_at=now,
    )
    enrollment.next_run_at = now
    db.commit()
    db.refresh(enrollment)
    return enrollment


@router.post("/enrollments/{enrollment_id}/skip-wait", response_model=EnrollmentOut)
def skip_wait(
    enrollment_id: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Enrollment:
    """Collapse a pending delay so the next touch fires now.

    Only shortens a *pending delay*: a send step that was deferred into a quiet
    hours window is left alone, because that window exists for a reason.
    """
    enrollment = get_or_404(db, Enrollment, enrollment_id, "Enrollment")
    timer = db.scalar(
        select(Timer).where(
            Timer.enrollment_id == enrollment.id, Timer.status == c.TIMER_SCHEDULED
        )
    )
    if timer is None:
        raise HTTPException(status_code=409, detail="No pending step to advance")

    version = db.get(SequenceVersion, enrollment.sequence_version_id)
    graph = parse_graph(version.graph if version else {})
    if graph.step_type(timer.step_key) != "delay":
        raise HTTPException(
            status_code=409, detail="The pending step is a send, not a delay; it cannot be skipped"
        )

    step = graph.get(timer.step_key) or {}
    next_key = str(step.get("next") or "")
    if not next_key:
        raise HTTPException(status_code=409, detail="Delay has no successor")

    now = datetime.now(timezone.utc)
    scheduler.schedule_timer(db, enrollment_id=enrollment.id, step_key=next_key, run_at=now)
    enrollment.next_run_at = now
    db.commit()
    db.refresh(enrollment)
    return enrollment
