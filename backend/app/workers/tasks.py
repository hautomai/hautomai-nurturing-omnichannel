"""Celery tasks.

Each task is a thin wrapper: the real work happens in functions that accept a
session, so the API, the tests and the scheduler all exercise the same code.
"""

from __future__ import annotations

import logging
import socket
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import constants as c
from app.channels.registry import Channels, build_channels
from app.config import Settings, get_settings
from app.crm.base import CRMAdapter
from app.crm.registry import build_crm
from app.db import session_scope
from app.engine import runner, scheduler
from app.engine.graph import parse_graph
from app.models import Enrollment, HumanTask, SenderInbox, SequenceVersion, utcnow
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

WORKER_ID = f"{socket.gethostname()}"


def process_due_timers(
    db: Session,
    *,
    channels: Channels,
    settings: Settings,
    crm: CRMAdapter | None = None,
    limit: int = 50,
    now: datetime | None = None,
) -> dict[str, int]:
    """Claim due timers and execute them. Safe to run concurrently."""
    now = now or utcnow()
    timers = scheduler.claim_due_timers(
        db, limit=limit, worker_id=WORKER_ID, now=now
    )
    outcomes: dict[str, int] = {}
    for timer in timers:
        try:
            outcome = runner.execute_timer(
                db, timer, channels=channels, settings=settings, crm=crm, now=now
            )
        except Exception as exc:  # noqa: BLE001 - one bad timer must not stop the batch
            logger.exception("Timer %s failed unexpectedly", timer.id)
            timer.attempts += 1
            timer.last_error = f"{type(exc).__name__}: {exc}"
            timer.status = (
                c.TIMER_FAILED
                if timer.attempts >= settings.timer_max_attempts
                else c.TIMER_SCHEDULED
            )
            if timer.status == c.TIMER_SCHEDULED:
                timer.run_at = now + timedelta(minutes=5)
            outcome = "error"
        db.flush()
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    return outcomes


def poll_inboxes(
    db: Session,
    *,
    channels: Channels,
    settings: Settings,
    crm: CRMAdapter | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """Read every mailbox in the pool and ingest replies."""
    inboxes = db.scalars(
        select(SenderInbox).where(SenderInbox.status.in_([c.INBOX_ACTIVE, c.INBOX_WARMING]))
    ).all()

    ingested = 0
    matched = 0
    for inbox in inboxes:
        try:
            messages = channels.inbound.poll(inbox)
        except Exception:  # noqa: BLE001 - a flaky mailbox must not stop the sweep
            logger.exception("Polling %s failed", inbox.email)
            continue
        for message in messages:
            record = runner.handle_inbound_email(
                db,
                message,
                client_id=inbox.client_id,
                channels=channels,
                crm=crm,
                settings=settings,
            )
            if record is None:
                continue
            ingested += 1
            if record.contact_id:
                matched += 1
    return {"polled": len(inboxes), "ingested": ingested, "matched": matched}


def evaluate_human_task_timeouts(
    db: Session,
    *,
    channels: Channels,
    settings: Settings,
    crm: CRMAdapter | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """Apply the configured timeout policy to overdue human tasks."""
    now = now or utcnow()
    tasks = db.scalars(
        select(HumanTask).where(
            HumanTask.status == c.TASK_OPEN,
            HumanTask.timeout_at.is_not(None),
            HumanTask.timeout_at <= now,
        )
    ).all()

    exited = 0
    resumed = 0
    for task in tasks:
        enrollment = db.get(Enrollment, task.enrollment_id) if task.enrollment_id else None
        if enrollment is None or enrollment.status != c.ENROLLMENT_PAUSED:
            task.status = c.TASK_CANCELLED
            continue

        version = db.get(SequenceVersion, enrollment.sequence_version_id)
        graph = parse_graph(version.graph if version else {})
        step = graph.get(task.step_key) or {}
        next_key = str(step.get("next") or "")

        if task.on_timeout == c.TASK_TIMEOUT_CONTINUE and next_key:
            runner.resume_enrollment(db, enrollment, reason="task_timeout_continue", now=now)
            scheduler.schedule_timer(
                db,
                enrollment_id=enrollment.id,
                step_key=next_key,
                run_at=now,
            )
            enrollment.next_run_at = now
            resumed += 1
        else:
            # The sequence never reached its own exit node, so this is an exit
            # (abandoned), not a completion.
            runner.exit_enrollment(
                db,
                enrollment,
                reason=c.EXIT_TASK_TIMEOUT,
                client_id=enrollment.client_id,
                contact_id=enrollment.contact_id,
                channels=channels,
                settings=settings,
            )
            exited += 1
        task.status = c.TASK_COMPLETED
        task.completed_at = now
        task.outcome = "timeout"
    db.flush()
    return {"timed_out": len(tasks), "exited": exited, "resumed": resumed}


def refresh_inbox_health(
    db: Session, *, channels: Channels, settings: Settings, now: datetime | None = None
) -> dict[str, int]:
    """Recompute bounce rates and pause inboxes that crossed the threshold."""
    paused = 0
    for inbox in db.scalars(
        select(SenderInbox).where(SenderInbox.status == c.INBOX_ACTIVE)
    ).all():
        should_pause, reason = scheduler.evaluate_inbox_health(db, inbox, settings)
        if should_pause:
            scheduler.pause_inbox(inbox, reason or "unhealthy")
            paused += 1
            runner._notify(  # noqa: SLF001 - deliberate reuse of the alert helper
                db,
                channels,
                settings,
                kind=c.NOTIFY_INBOX_DEGRADED,
                text=f"🛑 Inbox {inbox.email} paused: {reason}",
                client_id=inbox.client_id,
            )
    db.flush()
    return {"paused": paused}


# ---------------------------------------------------------------------------
# Celery entry points
# ---------------------------------------------------------------------------
@celery_app.task(name="app.workers.tasks.process_due_timers")
def process_due_timers_task() -> dict[str, int]:
    settings = get_settings()
    channels = build_channels(settings)
    crm = build_crm(settings)
    with session_scope() as db:
        return process_due_timers(db, channels=channels, settings=settings, crm=crm)


@celery_app.task(name="app.workers.tasks.poll_inboxes")
def poll_inboxes_task() -> dict[str, int]:
    settings = get_settings()
    channels = build_channels(settings)
    crm = build_crm(settings)
    with session_scope() as db:
        polled = poll_inboxes(db, channels=channels, settings=settings, crm=crm)
        health = refresh_inbox_health(db, channels=channels, settings=settings)
    return {**polled, **health}


@celery_app.task(name="app.workers.tasks.evaluate_human_task_timeouts")
def evaluate_human_task_timeouts_task() -> dict[str, int]:
    settings = get_settings()
    channels = build_channels(settings)
    crm = build_crm(settings)
    with session_scope() as db:
        return evaluate_human_task_timeouts(
            db, channels=channels, settings=settings, crm=crm
        )
