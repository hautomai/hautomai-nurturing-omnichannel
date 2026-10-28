"""The engine runner.

Responsibilities, in the order a contact experiences them:

1. ``enroll_contact``   - claim the lanes, schedule the entry step.
2. ``execute_timer``    - run the step a due timer points at.
3. ``ingest_event``     - record an inbound fact and let it re-route the contact.

Invariants worth stating explicitly:

* An enrollment has **at most one pending timer**. Reactions cancel the pending
  row and re-schedule, which is what makes cancel-and-re-branch (D8) safe.
* A step that is already ``sent`` is never un-sent; only *future* steps cancel.
* Every decision - send, skip, substitution, branch, exit - writes an ``Event``
  and a ``BranchDecision`` where relevant, so the timeline can always explain
  itself without reading logs.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import constants as c
from app.channels.base import InboundEmail
from app.channels.registry import Channels
from app.config import Settings
from app.crm.base import CRMAdapter, CrmContact
from app.engine import scheduler, sentiment as sentiment_mod, stats
from app.engine.conditions import ConditionContext, evaluate
from app.engine.graph import (
    NODE_CONDITION,
    NODE_DELAY,
    NODE_EXIT,
    NODE_HUMAN_TASK,
    NODE_NOTIFY_SLACK,
    NODE_SEND_EMAIL,
    NODE_SEND_SMS,
    Graph,
    parse_graph,
)
from app.engine.routing import (
    ACTION_DELAY,
    ACTION_EXIT,
    ACTION_EXIT_SUPPRESS,
    ACTION_GOTO,
    ACTION_IGNORE,
    ACTION_PAUSE,
    ACTION_SUBSTITUTE,
    plan_for_event,
)
from app.engine.templates import build_context, render
from app.models import (
    BranchDecision,
    Client,
    Contact,
    Enrollment,
    Event,
    HumanTask,
    InboundMessage,
    Message,
    Sequence,
    SequenceVersion,
    SenderPersona,
    Suppression,
    Template,
    Timer,
    utcnow,
)

logger = logging.getLogger(__name__)

MAX_STEP_ITERATIONS = 50
RETRY_BACKOFF_SECONDS = (60, 300, 900)
EMAIL_RE = re.compile(r"<([^>]+)>")


class RetryStep(Exception):
    """Raised when a step should be attempted again later."""

    def __init__(self, message: str, delay_seconds: int = 60) -> None:
        super().__init__(message)
        self.delay_seconds = delay_seconds


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
@dataclass
class Pushback:
    """A send time the engine refused to use yet."""

    run_at: datetime
    reason: str


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def lanes_for_graph(graph: Graph) -> set[str]:
    lanes = {graph.channel_of(key) for key in graph.send_step_keys()}
    return {lane for lane in lanes if lane in c.LANES}


def parse_lanes(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {part for part in raw.split(",") if part}


def graph_of(db: Session, enrollment: Enrollment) -> Graph:
    version = db.get(SequenceVersion, enrollment.sequence_version_id)
    return parse_graph(version.graph if version else {})


def client_of(db: Session, client_id: str) -> Client | None:
    return db.get(Client, client_id)


def default_persona(db: Session, client_id: str) -> SenderPersona | None:
    return db.scalar(
        select(SenderPersona)
        .where(SenderPersona.client_id == client_id)
        .order_by(SenderPersona.is_default.desc())
        .limit(1)
    )


def email_of(address: str) -> str:
    """Pull the address out of ``"Name <a@b.com>"``."""
    if not address:
        return ""
    match = EMAIL_RE.search(address)
    if match:
        return match.group(1).strip().lower()
    return address.strip().lower()


# ---------------------------------------------------------------------------
# Events and decisions
# ---------------------------------------------------------------------------
def record_event(
    db: Session,
    *,
    client_id: str,
    contact_id: str | None,
    event_type: str,
    channel: str = "",
    payload: dict[str, Any] | None = None,
    dedupe_key: str = "",
    enrollment_id: str | None = None,
    occurred_at: datetime | None = None,
) -> Event | None:
    """Append an event, honouring the dedupe key."""
    if dedupe_key:
        existing = db.scalar(select(Event).where(Event.dedupe_key == dedupe_key))
        if existing is not None:
            return None
    event = Event(
        client_id=client_id,
        contact_id=contact_id,
        enrollment_id=enrollment_id,
        type=event_type,
        channel=channel,
        payload=payload or {},
        dedupe_key=dedupe_key,
        occurred_at=_utc(occurred_at) or utcnow(),
    )
    db.add(event)
    db.flush()
    return event


def record_decision(
    db: Session,
    *,
    enrollment: Enrollment,
    step_key: str,
    rule: str,
    outcome: str,
    detail: dict[str, Any] | None = None,
    event_id: str | None = None,
) -> BranchDecision:
    decision = BranchDecision(
        enrollment_id=enrollment.id,
        event_id=event_id,
        step_key=step_key,
        rule=rule,
        outcome=outcome,
        detail=detail or {},
    )
    db.add(decision)
    db.flush()
    return decision


# ---------------------------------------------------------------------------
# Suppression
# ---------------------------------------------------------------------------
def is_suppressed(db: Session, *, client_id: str, contact_id: str, channel: str) -> bool:
    rows = db.scalars(
        select(Suppression).where(
            Suppression.client_id == client_id, Suppression.contact_id == contact_id
        )
    ).all()
    for row in rows:
        if row.channel in ("all", channel):
            return True
    return False


def add_suppression(
    db: Session,
    *,
    client_id: str,
    contact_id: str,
    channel: str,
    reason: str,
    note: str = "",
) -> Suppression:
    existing = db.scalar(
        select(Suppression).where(
            Suppression.client_id == client_id,
            Suppression.contact_id == contact_id,
            Suppression.channel == channel,
        )
    )
    if existing:
        return existing
    row = Suppression(
        client_id=client_id, contact_id=contact_id, channel=channel, reason=reason, note=note
    )
    db.add(row)
    db.flush()
    return row


# ---------------------------------------------------------------------------
# Collisions (D20)
# ---------------------------------------------------------------------------
def find_collision(
    db: Session, *, contact_id: str, lanes: set[str], exclude_enrollment_id: str | None = None
) -> Enrollment | None:
    open_states = (c.ENROLLMENT_ACTIVE, c.ENROLLMENT_WAITING, c.ENROLLMENT_PAUSED)
    rows = db.scalars(
        select(Enrollment).where(
            Enrollment.contact_id == contact_id, Enrollment.status.in_(open_states)
        )
    ).all()
    for row in rows:
        if exclude_enrollment_id and row.id == exclude_enrollment_id:
            continue
        if parse_lanes(row.channel_lane) & lanes:
            return row
    return None


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
def _notify(
    db: Session,
    channels: Channels,
    settings: Settings,
    *,
    kind: str,
    text: str,
    client_id: str,
    contact_id: str | None = None,
    enrollment_id: str | None = None,
    blocks: list[dict[str, Any]] | None = None,
) -> bool:
    """Post to Slack and always leave a trace, even when Slack is not wired."""
    result = channels.slack.notify(
        channel=settings.slack_alert_channel, text=text, blocks=blocks
    )
    record_event(
        db,
        client_id=client_id,
        contact_id=contact_id,
        enrollment_id=enrollment_id,
        event_type=f"notify.{kind}",
        channel=c.CHANNEL_SLACK,
        payload={"ok": result.ok, "error": result.error, "text": text},
        dedupe_key=f"notify:{kind}:{enrollment_id or contact_id}:{utcnow().timestamp()}",
    )
    return result.ok


def exit_enrollment(
    db: Session,
    enrollment: Enrollment,
    *,
    reason: str,
    client_id: str,
    contact_id: str,
    channels: Channels,
    settings: Settings,
    status: str | None = None,
) -> None:
    scheduler.cancel_pending_timers(db, enrollment.id)
    enrollment.status = status or c.ENROLLMENT_EXITED
    enrollment.exit_reason = reason
    enrollment.pause_reason = None
    enrollment.next_run_at = None
    enrollment.updated_at = utcnow()
    db.flush()
    record_event(
        db,
        client_id=client_id,
        contact_id=contact_id,
        enrollment_id=enrollment.id,
        event_type=(
            c.EVENT_SEQUENCE_COMPLETED
            if enrollment.status == c.ENROLLMENT_COMPLETED
            else c.EVENT_SEQUENCE_EXITED
        ),
        payload={"reason": reason},
        dedupe_key=f"exit:{enrollment.id}:{reason}",
    )


def pause_enrollment(db: Session, enrollment: Enrollment, *, reason: str) -> None:
    scheduler.cancel_pending_timers(db, enrollment.id)
    enrollment.status = c.ENROLLMENT_PAUSED
    enrollment.pause_reason = reason
    enrollment.next_run_at = None
    enrollment.updated_at = utcnow()
    db.flush()


def resume_enrollment(
    db: Session,
    enrollment: Enrollment,
    *,
    reason: str = "resumed",
    now: datetime | None = None,
) -> None:
    """Re-open a paused enrollment at the step it stopped on."""
    now = now or utcnow()
    enrollment.status = c.ENROLLMENT_ACTIVE
    enrollment.pause_reason = None
    db.flush()
    record_event(
        db,
        client_id=enrollment.client_id,
        contact_id=enrollment.contact_id,
        enrollment_id=enrollment.id,
        event_type=c.EVENT_SEQUENCE_ENROLLED,
        payload={"reason": reason},
        dedupe_key=f"resume:{enrollment.id}:{reason}:{now.timestamp()}",
    )


def enroll_contact(
    db: Session,
    *,
    contact: Contact,
    sequence: Sequence,
    channels: Channels,
    settings: Settings,
    start_at: datetime | None = None,
    crm: CRMAdapter | None = None,
) -> tuple[Enrollment | None, str]:
    """Create an enrollment unless a lane collision or suppression blocks it."""
    if not sequence.current_version_id:
        return None, "sequence_has_no_published_version"
    version = db.get(SequenceVersion, sequence.current_version_id)
    if version is None:
        return None, "sequence_version_missing"

    graph = parse_graph(version.graph)
    lanes = lanes_for_graph(graph)
    if not lanes:
        return None, "sequence_has_no_send_steps"

    if not graph.entry_step_keys:
        return None, "sequence_has_no_entry_point"

    collision = find_collision(db, contact_id=contact.id, lanes=lanes)
    if collision is not None:
        record_event(
            db,
            client_id=contact.client_id,
            contact_id=contact.id,
            event_type=c.EVENT_SEQUENCE_COLLISION,
            payload={
                "requested_sequence_id": sequence.id,
                "blocking_enrollment_id": collision.id,
                "blocking_sequence_id": collision.sequence_id,
                "lanes": sorted(lanes),
            },
            dedupe_key=f"collision:{contact.id}:{sequence.id}",
        )
        return None, "lane_collision"

    # A suppression is a stop signal. It only blocks enrollment when *every*
    # lane the sequence uses is suppressed: a contact who bounced off email may
    # still be reachable on SMS, and the send path already skips dead lanes.
    suppressed_lanes = {
        lane
        for lane in lanes
        if is_suppressed(db, client_id=contact.client_id, contact_id=contact.id, channel=lane)
    }
    if suppressed_lanes and suppressed_lanes >= lanes:
        record_event(
            db,
            client_id=contact.client_id,
            contact_id=contact.id,
            event_type=c.EVENT_SEQUENCE_SUPPRESSED,
            payload={
                "requested_sequence_id": sequence.id,
                "channels": sorted(suppressed_lanes),
                "reason": _suppression_reason(
                    db, contact.client_id, contact.id, sorted(suppressed_lanes)[0]
                ),
            },
            dedupe_key=f"suppressed:{contact.id}:{sequence.id}",
        )
        return None, "suppressed"

    if not contact.email and not contact.phone_e164:
        return None, "contact_has_no_channel"

    enrollment = Enrollment(
        contact_id=contact.id,
        client_id=contact.client_id,
        sequence_id=sequence.id,
        sequence_version_id=version.id,
        status=c.ENROLLMENT_ACTIVE,
        channel_lane=",".join(sorted(lanes)),
        current_step_key=graph.entry_step_keys[0],
        entered_at=utcnow(),
    )
    db.add(enrollment)
    db.flush()

    record_event(
        db,
        client_id=contact.client_id,
        contact_id=contact.id,
        enrollment_id=enrollment.id,
        event_type=c.EVENT_SEQUENCE_ENROLLED,
        payload={"sequence_id": sequence.id, "version": version.version},
        dedupe_key=f"enrolled:{enrollment.id}",
    )

    started_at = start_at or utcnow()
    try:
        _advance(
            db,
            enrollment,
            graph.entry_step_keys[0],
            channels=channels,
            crm=crm,
            settings=settings,
            now=started_at,
        )
    except RetryStep as retry:
        # A transient failure on the very first touch must not fail the enrollment
        # call; hand it to the timer queue with the attempt already counted.
        timer = scheduler.schedule_timer(
            db,
            enrollment_id=enrollment.id,
            step_key=graph.entry_step_keys[0],
            run_at=started_at + timedelta(seconds=retry.delay_seconds),
        )
        timer.attempts = 1
        timer.last_error = str(retry)
        enrollment.next_run_at = timer.run_at
        db.flush()
    return enrollment, "enrolled"


# ---------------------------------------------------------------------------
# Timer execution
# ---------------------------------------------------------------------------
def execute_timer(
    db: Session,
    timer: Timer,
    *,
    channels: Channels,
    settings: Settings,
    crm: CRMAdapter | None = None,
    now: datetime | None = None,
) -> str:
    """Run the step a claimed timer points at, with retry semantics (D18)."""
    now = now or utcnow()
    enrollment = db.get(Enrollment, timer.enrollment_id)
    if enrollment is None:
        timer.status = c.TIMER_DONE
        db.flush()
        return "orphaned"

    if enrollment.status not in c.ENROLLMENT_OPEN_STATES:
        timer.status = c.TIMER_DONE
        db.flush()
        return f"skipped_enrollment_{enrollment.status}"

    # A delay counts from when it became due, not from whenever the worker
    # happened to poll - otherwise every slow tick silently lengthens the
    # cadence (D17: elapsed duration from the previous touch).
    try:
        outcome = _advance(
            db,
            enrollment,
            timer.step_key,
            channels=channels,
            crm=crm,
            settings=settings,
            now=now,
            anchor=_utc(timer.run_at) or now,
        )
    except RetryStep as retry:
        timer.attempts += 1
        timer.last_error = str(retry)
        if timer.attempts >= settings.timer_max_attempts:
            timer.status = c.TIMER_FAILED
            record_event(
                db,
                client_id=enrollment.client_id,
                contact_id=enrollment.contact_id,
                enrollment_id=enrollment.id,
                event_type=c.EVENT_SEQUENCE_SKIPPED,
                payload={"reason": "retries_exhausted", "error": str(retry)},
                dedupe_key=f"retry_failed:{timer.id}",
            )
            _notify(
                db,
                channels,
                settings,
                kind=c.NOTIFY_FAILURE,
                text=(
                    f"⚠️ Step `{timer.step_key}` failed {timer.attempts}× and was abandoned: "
                    f"{retry}"
                ),
                client_id=enrollment.client_id,
                contact_id=enrollment.contact_id,
                enrollment_id=enrollment.id,
            )
        else:
            timer.status = c.TIMER_SCHEDULED
            timer.run_at = now + timedelta(seconds=retry.delay_seconds)
            enrollment.next_run_at = timer.run_at
        db.flush()
        return f"retry:{timer.attempts}"

    timer.status = c.TIMER_DONE
    db.flush()
    return outcome


def _advance(
    db: Session,
    enrollment: Enrollment,
    step_key: str,
    *,
    channels: Channels,
    crm: CRMAdapter | None,
    settings: Settings,
    now: datetime,
    anchor: datetime | None = None,
) -> str:
    """Walk from ``step_key`` until something needs to wait.

    ``now`` is the real clock used for sending windows. ``anchor`` is the point
    delays are measured from (the timer's due time), so a late worker tick does
    not stretch the cadence.
    """
    anchor = anchor or now
    graph = graph_of(db, enrollment)
    iterations: dict[str, int] = {}
    current = step_key
    outcome = "noop"

    for _ in range(MAX_STEP_ITERATIONS):
        step = graph.get(current)
        if step is None:
            exit_enrollment(
                db,
                enrollment,
                reason=c.EXIT_ORPHANED_BY_VERSION,
                client_id=enrollment.client_id,
                contact_id=enrollment.contact_id,
                channels=channels,
                settings=settings,
            )
            return "exited:orphaned"

        step_type = str(step.get("type"))
        iterations[current] = iterations.get(current, 0) + 1
        guard = step.get("max_iterations")
        if isinstance(guard, int) and iterations[current] > guard:
            record_decision(
                db,
                enrollment=enrollment,
                step_key=current,
                rule=f"max_iterations={guard}",
                outcome="loop_guard",
            )
            exit_enrollment(
                db,
                enrollment,
                reason=c.EXIT_CADENCE_CAP,
                client_id=enrollment.client_id,
                contact_id=enrollment.contact_id,
                channels=channels,
                settings=settings,
            )
            return "exited:loop_guard"

        enrollment.current_step_key = current

        if step_type in (NODE_SEND_EMAIL, NODE_SEND_SMS):
            return _handle_send(
                db, enrollment, graph, step, channels=channels, crm=crm, settings=settings, now=now
            )

        if step_type == NODE_DELAY:
            delay = timedelta(seconds=graph.delay_seconds(current))
            run_at = anchor + delay
            _schedule(db, enrollment, str(step.get("next")), run_at, settings=settings)
            record_event(
                db,
                client_id=enrollment.client_id,
                contact_id=enrollment.contact_id,
                enrollment_id=enrollment.id,
                event_type=c.EVENT_SEQUENCE_STEP_FIRED,
                payload={"step": current, "type": NODE_DELAY, "delay_seconds": delay.total_seconds()},
                dedupe_key=f"delay:{enrollment.id}:{current}:{now.timestamp()}",
            )
            return f"waiting:{delay.total_seconds()}s"

        if step_type == NODE_CONDITION:
            next_key, description = _evaluate_condition(
                db, enrollment, graph, step, now=now
            )
            if next_key is None:
                exit_enrollment(
                    db,
                    enrollment,
                    reason=c.EXIT_ORPHANED_BY_VERSION,
                    client_id=enrollment.client_id,
                    contact_id=enrollment.contact_id,
                    channels=channels,
                    settings=settings,
                )
                return "exited:orphaned_condition"
            current = next_key
            outcome = f"branch:{description}"
            continue

        if step_type == NODE_NOTIFY_SLACK:
            _notify(
                db,
                channels,
                settings,
                kind=c.NOTIFY_REACTION,
                text=str(step.get("text") or f"Sequence step reached: {graph.name_of(current)}"),
                client_id=enrollment.client_id,
                contact_id=enrollment.contact_id,
                enrollment_id=enrollment.id,
            )
            current = str(step.get("next"))
            continue

        if step_type == NODE_HUMAN_TASK:
            _create_human_task(
                db, enrollment, step, channels=channels, settings=settings, now=now
            )
            return "paused:human_task"

        if step_type == NODE_EXIT:
            exit_enrollment(
                db,
                enrollment,
                reason=str(step.get("reason") or c.EXIT_COMPLETED),
                client_id=enrollment.client_id,
                contact_id=enrollment.contact_id,
                channels=channels,
                settings=settings,
                status=c.ENROLLMENT_COMPLETED,
            )
            return "completed"

        logger.warning("Unknown step type %r at %s", step_type, current)
        current = str(step.get("next") or "")

    return "exited:iteration_cap"


def _schedule(
    db: Session,
    enrollment: Enrollment,
    step_key: str,
    run_at: datetime,
    *,
    settings: Settings,
) -> Timer:
    """Schedule the next step, honouring the channel's sending window.

    The window is applied here as well as at send time so that
    ``enrollment.next_run_at`` (and therefore the dashboard) shows when the touch
    will really go out, not a time the engine will refuse to use.
    """
    graph = graph_of(db, enrollment)
    channel = graph.channel_of(step_key)
    if channel:
        contact = db.get(Contact, enrollment.contact_id)
        client = db.get(Client, enrollment.client_id)
        timezone_name = (
            (contact.timezone if contact else "")
            or (client.default_timezone if client else settings.default_contact_timezone)
        )
        run_at = scheduler.apply_send_window(
            run_at, channel=channel, timezone_name=timezone_name, settings=settings
        )

    timer = scheduler.schedule_timer(
        db, enrollment_id=enrollment.id, step_key=step_key, run_at=run_at
    )
    # ``current_step_key`` always points at the step we are *waiting on*, never at
    # the one we just executed. Resume and version migration both re-anchor from
    # it, so pointing at a completed send would re-send it.
    enrollment.current_step_key = step_key
    enrollment.next_run_at = run_at
    db.flush()
    return timer


def _evaluate_condition(
    db: Session, enrollment: Enrollment, graph: Graph, step: dict[str, Any], *, now: datetime
) -> tuple[str | None, str]:
    context = _condition_context(db, enrollment, now=now)
    result, description = evaluate(step.get("predicate"), context, now=now)
    branch = "on_true" if result else "on_false"
    target = step.get(branch)
    record_decision(
        db,
        enrollment=enrollment,
        step_key=str(step.get("key")),
        rule=str((step.get("predicate") or {}).get("type")),
        outcome=f"{description} -> {branch}",
        detail={"result": result, "target": target},
    )
    return (str(target) if target else None), f"{branch}({description})"


def _condition_context(db: Session, enrollment: Enrollment, *, now: datetime) -> ConditionContext:
    contact = db.get(Contact, enrollment.contact_id)
    inbound = db.scalars(
        select(InboundMessage)
        .where(InboundMessage.contact_id == enrollment.contact_id)
        .order_by(InboundMessage.received_at.desc())
    ).all()
    real_replies = [row for row in inbound if not row.is_auto_reply]
    meeting = db.scalar(
        select(Event).where(
            Event.contact_id == enrollment.contact_id, Event.type == c.EVENT_MEETING_BOOKED
        )
    )
    previous = db.scalar(
        select(Message)
        .where(Message.contact_id == enrollment.contact_id, Message.sent_at.is_not(None))
        .order_by(Message.sent_at.desc())
    )

    available: set[str] = set()
    if contact is not None:
        if contact.email and not is_suppressed(
            db, client_id=enrollment.client_id, contact_id=contact.id, channel=c.CHANNEL_EMAIL
        ):
            available.add(c.CHANNEL_EMAIL)
        if contact.phone_e164 and not is_suppressed(
            db, client_id=enrollment.client_id, contact_id=contact.id, channel=c.CHANNEL_SMS
        ):
            available.add(c.CHANNEL_SMS)

    return ConditionContext(
        has_reply=bool(real_replies),
        last_reply_sentiment=real_replies[0].sentiment if real_replies else None,
        last_reply_at=real_replies[0].received_at if real_replies else None,
        meeting_booked=meeting is not None,
        available_channels=available,
        tags=set(contact.tags or []) if contact else set(),
        custom_fields=dict(contact.custom_fields or {}) if contact else {},
        reference_at=_utc(previous.sent_at) if previous else None,
    )


def _create_human_task(
    db: Session,
    enrollment: Enrollment,
    step: dict[str, Any],
    *,
    channels: Channels,
    settings: Settings,
    now: datetime,
) -> HumanTask:
    timeout_seconds = step.get("timeout_seconds")
    task = HumanTask(
        client_id=enrollment.client_id,
        contact_id=enrollment.contact_id,
        enrollment_id=enrollment.id,
        step_key=str(step.get("key") or ""),
        title=str(step.get("title") or "Human follow-up needed"),
        instructions=str(step.get("instructions") or ""),
        assignee=str(step.get("assignee") or ""),
        timeout_at=(
            now + timedelta(seconds=int(timeout_seconds)) if timeout_seconds else None
        ),
        on_timeout=str(step.get("on_timeout") or c.TASK_TIMEOUT_EXIT),
    )
    db.add(task)
    db.flush()
    # A manual resume should continue the cadence, not re-create the task, so the
    # waiting pointer moves to the task's successor.
    successor = str(step.get("next") or step.get("key") or "")
    if successor:
        enrollment.current_step_key = successor
    pause_enrollment(db, enrollment, reason="human_task")
    record_event(
        db,
        client_id=enrollment.client_id,
        contact_id=enrollment.contact_id,
        enrollment_id=enrollment.id,
        event_type=c.EVENT_SEQUENCE_PAUSED,
        payload={"reason": "human_task", "task_id": task.id, "title": task.title},
        dedupe_key=f"task:{task.id}",
    )
    _notify(
        db,
        channels,
        settings,
        kind=c.NOTIFY_HUMAN_TASK,
        text=(
            f"🙋 *Human task* — {task.title}\n"
            f"<{settings.slack_alert_channel}|sequence paused for a person to act>\n"
            f"_{task.instructions}_"
        ),
        client_id=enrollment.client_id,
        contact_id=enrollment.contact_id,
        enrollment_id=enrollment.id,
    )
    return task


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------
def _handle_send(
    db: Session,
    enrollment: Enrollment,
    graph: Graph,
    step: dict[str, Any],
    *,
    channels: Channels,
    crm: CRMAdapter | None,
    settings: Settings,
    now: datetime,
) -> str:
    step_key = str(step.get("key"))
    channel = graph.channel_of(step_key)
    contact = db.get(Contact, enrollment.contact_id)
    if contact is None:
        return "skipped:no_contact"
    client = db.get(Client, enrollment.client_id)

    # --- cadence caps (runtime backstop over publish-time validation) ------
    if enrollment.touches >= settings.max_touches_per_enrollment:
        exit_enrollment(
            db,
            enrollment,
            reason=c.EXIT_MAX_TOUCHES,
            client_id=enrollment.client_id,
            contact_id=contact.id,
            channels=channels,
            settings=settings,
        )
        return "exited:max_touches"

    # --- client-level halt: the kill switch stops every channel ------------
    if client is not None and client.sending_halted:
        _mark_skipped(db, enrollment, step_key, channel, "halted", settings=settings)
        pause_enrollment(db, enrollment, reason=c.EXIT_CLIENT_HALTED)
        return "paused:client_halted"

    # --- suppression -------------------------------------------------------
    if is_suppressed(
        db, client_id=enrollment.client_id, contact_id=contact.id, channel=channel
    ):
        _mark_skipped(db, enrollment, step_key, channel, "suppressed", settings=settings)
        reason = _suppression_reason(db, enrollment.client_id, contact.id, channel)
        if reason in (c.SUPPRESS_UNSUBSCRIBED, c.SUPPRESS_OPT_OUT, c.SUPPRESS_HARD_BOUNCE):
            exit_enrollment(
                db,
                enrollment,
                reason=(
                    c.EXIT_BOUNCED_HARD
                    if reason == c.SUPPRESS_HARD_BOUNCE
                    else c.EXIT_UNSUBSCRIBED
                ),
                client_id=enrollment.client_id,
                contact_id=contact.id,
                channels=channels,
                settings=settings,
            )
            return "exited:suppressed"
        _schedule(db, enrollment, str(step.get("next")), now, settings=settings)
        return "skipped:suppressed"

    # --- SMS cadence sub-cap ----------------------------------------------
    if channel == c.CHANNEL_SMS and enrollment.sms_touches >= settings.max_sms_per_enrollment:
        fallback = graph.fallback_of(step_key)
        if fallback == NODE_SEND_EMAIL:
            return _substitute(
                db,
                enrollment,
                graph,
                step,
                channels=channels,
                crm=crm,
                settings=settings,
                now=now,
                reason="sms_cap",
            )
        _mark_skipped(db, enrollment, step_key, channel, "cap_reached", settings=settings)
        _schedule(db, enrollment, str(step.get("next")), now, settings=settings)
        return "skipped:sms_cap"

    # --- window + frequency cap -------------------------------------------
    address = contact.email if channel == c.CHANNEL_EMAIL else contact.phone_e164
    if not address:
        return _handle_missing_address(
            db, enrollment, graph, step, channels=channels, crm=crm, settings=settings, now=now
        )

    timezone_name = contact.timezone or (
        client.default_timezone if client else settings.default_contact_timezone
    )
    windowed = scheduler.apply_send_window(
        now, channel=channel, timezone_name=timezone_name, settings=settings
    )
    send_at = scheduler.apply_frequency_cap(
        db,
        windowed,
        contact_id=contact.id,
        channel=channel,
        timezone_name=timezone_name,
        settings=settings,
    )
    if send_at > now + timedelta(seconds=1):
        _schedule(db, enrollment, step_key, send_at, settings=settings)
        # Whichever guard moved the time is the one worth naming: if the
        # frequency cap pulled it later than the window alone, it is the cap.
        rule = "frequency_cap" if send_at != windowed else "send_window"
        record_decision(
            db,
            enrollment=enrollment,
            step_key=step_key,
            rule=rule,
            outcome=f"deferred to {send_at.isoformat()}",
            detail={"channel": channel, "run_at": send_at.isoformat(), "reason": rule},
        )
        return f"waiting:{rule}"

    if channel == c.CHANNEL_EMAIL:
        return _deliver_email(
            db, enrollment, graph, step, contact, client, channels=channels, crm=crm, settings=settings, now=now
        )
    return _deliver_sms(
        db, enrollment, graph, step, contact, client, channels=channels, crm=crm, settings=settings, now=now
    )


def _suppression_reason(db: Session, client_id: str, contact_id: str, channel: str) -> str:
    rows = db.scalars(
        select(Suppression).where(
            Suppression.client_id == client_id, Suppression.contact_id == contact_id
        )
    ).all()
    for row in rows:
        if row.channel in ("all", channel):
            return row.reason
    return c.SUPPRESS_MANUAL


def _mark_skipped(
    db: Session,
    enrollment: Enrollment,
    step_key: str,
    channel: str,
    reason: str,
    *,
    settings: Settings,
) -> Message:
    message = Message(
        client_id=enrollment.client_id,
        contact_id=enrollment.contact_id,
        enrollment_id=enrollment.id,
        step_key=step_key,
        channel=channel or c.CHANNEL_EMAIL,
        status=c.MESSAGE_SKIPPED,
        skip_reason=reason,
    )
    db.add(message)
    stats.bump_for_enrollment(
        db, enrollment, step_key=step_key, channel=channel, field_name="skipped"
    )
    record_event(
        db,
        client_id=enrollment.client_id,
        contact_id=enrollment.contact_id,
        enrollment_id=enrollment.id,
        event_type=c.EVENT_SEQUENCE_SKIPPED,
        channel=channel,
        payload={"step": step_key, "reason": reason},
        dedupe_key=f"skip:{enrollment.id}:{step_key}:{utcnow().timestamp()}",
    )
    db.flush()
    return message


def _handle_missing_address(
    db: Session,
    enrollment: Enrollment,
    graph: Graph,
    step: dict[str, Any],
    *,
    channels: Channels,
    crm: CRMAdapter | None,
    settings: Settings,
    now: datetime,
) -> str:
    """D14: substitute the other channel, or skip."""
    step_key = str(step.get("key"))
    channel = graph.channel_of(step_key)
    fallback = graph.fallback_of(step_key)
    fallback_channel = (
        c.CHANNEL_EMAIL if fallback == NODE_SEND_EMAIL else c.CHANNEL_SMS if fallback == NODE_SEND_SMS else ""
    )

    if fallback_channel and fallback_channel != channel:
        return _substitute(
            db,
            enrollment,
            graph,
            step,
            channels=channels,
            crm=crm,
            settings=settings,
            now=now,
            reason=f"no_{channel}_address",
        )

    if fallback == "halt":
        _mark_skipped(db, enrollment, step_key, channel, f"no_{channel}", settings=settings)
        pause_enrollment(db, enrollment, reason=f"no_{channel}")
        return f"paused:no_{channel}"

    _mark_skipped(db, enrollment, step_key, channel, f"no_{channel}", settings=settings)
    _schedule(db, enrollment, str(step.get("next")), now, settings=settings)
    return f"skipped:no_{channel}"


def _substitute(
    db: Session,
    enrollment: Enrollment,
    graph: Graph,
    step: dict[str, Any],
    *,
    channels: Channels,
    crm: CRMAdapter | None,
    settings: Settings,
    now: datetime,
    reason: str,
) -> str:
    """Send the fallback channel instead of the blocked one (D14)."""
    step_key = str(step.get("key"))
    original_channel = graph.channel_of(step_key)
    fallback = graph.fallback_of(step_key)
    fallback_channel = c.CHANNEL_EMAIL if fallback == NODE_SEND_EMAIL else c.CHANNEL_SMS
    contact = db.get(Contact, enrollment.contact_id)
    client = db.get(Client, enrollment.client_id)
    template_key = graph.fallback_template_key_of(step_key)

    if contact is None or client is None:
        return "skipped:no_contact"

    address = contact.email if fallback_channel == c.CHANNEL_EMAIL else contact.phone_e164
    if not address or is_suppressed(
        db, client_id=enrollment.client_id, contact_id=contact.id, channel=fallback_channel
    ):
        _mark_skipped(db, enrollment, step_key, original_channel, "no_fallback_address", settings=settings)
        _schedule(db, enrollment, str(step.get("next")), now, settings=settings)
        return "skipped:no_fallback"

    template = db.scalar(
        select(Template).where(
            Template.key == template_key, Template.client_id == client.id
        )
    )
    body = template.body if template else ""
    subject = template.subject if template else ""
    persona = default_persona(db, client.id)
    context = build_context(contact=contact, persona=persona, client=client)
    try:
        body = render(body, context)
        subject = render(subject, context)
    except Exception as exc:  # noqa: BLE001 - render failure must not kill the run
        logger.warning("Substitution render failed: %s", exc)

    message = Message(
        client_id=enrollment.client_id,
        contact_id=contact.id,
        enrollment_id=enrollment.id,
        step_key=step_key,
        channel=fallback_channel,
        status=c.MESSAGE_SUBSTITUTED,
        from_address=(persona.from_email if persona else client.slug),
        to_address=address,
        subject=subject,
        body=body,
        is_substitution=True,
        sent_at=now,
    )
    db.add(message)
    db.flush()

    if fallback_channel == c.CHANNEL_EMAIL:
        inbox = scheduler.pick_sender_inbox(db, enrollment.client_id)
        if inbox is None:
            message.status = c.MESSAGE_SKIPPED
            message.skip_reason = "inbox_paused"
            _mark_skipped(db, enrollment, step_key, original_channel, "inbox_paused", settings=settings)
            _schedule(db, enrollment, str(step.get("next")), now, settings=settings)
            return "skipped:no_inbox"
        scheduler.reset_daily_counter(inbox)
        inbox.sends_today += 1
        inbox.total_sends += 1
        inbox.last_send_at = now
        message.sender_inbox_id = inbox.id
        result = channels.email.send(
            inbox=inbox,
            to_address=address,
            from_name=persona.from_name if persona else client.name,
            from_email=persona.from_email if persona else inbox.email,
            reply_to=persona.reply_to if persona else "",
            subject=subject,
            body=body,
            headers={"X-Nurture-Substitution": reason},
        )
        if not result.ok:
            message.status = c.MESSAGE_FAILED
            message.error = result.error
            stats.bump_for_enrollment(
                db, enrollment, step_key=step_key, channel=original_channel, field_name="failed"
            )
            db.flush()
            raise RetryStep(result.error or "substituted send failed", 60)
        message.provider_message_id = result.provider_message_id
        message.thread_key = f"sub:{enrollment.id}"
    else:
        number = scheduler.pick_phone_number(db, enrollment.client_id)
        if number is None:
            _mark_skipped(db, enrollment, step_key, original_channel, "no_phone_number", settings=settings)
            _schedule(db, enrollment, str(step.get("next")), now, settings=settings)
            return "skipped:no_number"
        scheduler.reset_daily_counter(number)
        number.sends_today += 1
        number.total_sends += 1
        number.last_send_at = now
        message.phone_number_id = number.id
        result = channels.sms.send(from_number=number.e164, to_address=address, body=body)
        if not result.ok:
            message.status = c.MESSAGE_FAILED
            message.error = result.error
            stats.bump_for_enrollment(
                db, enrollment, step_key=step_key, channel=original_channel, field_name="failed"
            )
            db.flush()
            raise RetryStep(result.error or "substituted SMS failed", 60)
        message.provider_message_id = result.provider_message_id

    # The original step is recorded as substituted, and the contact gets one
    # touch, so cadence counters stay honest.
    enrollment.touches += 1
    stats.bump_for_enrollment(
        db, enrollment, step_key=step_key, channel=original_channel, field_name="substituted"
    )
    record_event(
        db,
        client_id=enrollment.client_id,
        contact_id=contact.id,
        enrollment_id=enrollment.id,
        event_type=c.EVENT_SEQUENCE_SUBSTITUTED,
        channel=fallback_channel,
        payload={"step": step_key, "reason": reason, "to_channel": fallback_channel},
        dedupe_key=f"sub:{enrollment.id}:{step_key}:{now.timestamp()}",
    )
    record_decision(
        db,
        enrollment=enrollment,
        step_key=step_key,
        rule="channel_substitution",
        outcome=f"{original_channel} -> {fallback_channel} ({reason})",
    )
    _schedule(db, enrollment, str(step.get("next")), now, settings=settings)
    return f"substituted:{fallback_channel}"


def _deliver_email(
    db: Session,
    enrollment: Enrollment,
    graph: Graph,
    step: dict[str, Any],
    contact: Contact,
    client: Client,
    *,
    channels: Channels,
    crm: CRMAdapter | None,
    settings: Settings,
    now: datetime,
) -> str:
    from app.models import Template

    step_key = str(step.get("key"))
    template = db.scalar(
        select(Template).where(
            Template.client_id == client.id, Template.key == graph.template_key_of(step_key)
        )
    )
    if template is None:
        _mark_skipped(db, enrollment, step_key, c.CHANNEL_EMAIL, "missing_template", settings=settings)
        _schedule(db, enrollment, str(step.get("next")), now, settings=settings)
        return "skipped:missing_template"

    persona = default_persona(db, client.id)
    context = build_context(
        contact=contact,
        persona=persona,
        client=client,
        extra={"hours_since_last_update": _hours_since_update(contact, now)},
    )
    try:
        subject = render(template.subject, context)
        body = render(template.body, context)
    except Exception as exc:  # noqa: BLE001
        record_decision(
            db,
            enrollment=enrollment,
            step_key=step_key,
            rule="template_render",
            outcome=f"failed: {exc}",
        )
        _mark_skipped(db, enrollment, step_key, c.CHANNEL_EMAIL, "missing_template", settings=settings)
        _schedule(db, enrollment, str(step.get("next")), now, settings=settings)
        return "skipped:render_failed"

    inbox = scheduler.pick_sender_inbox(db, client.id)
    if inbox is None:
        _mark_skipped(db, enrollment, step_key, c.CHANNEL_EMAIL, "inbox_paused", settings=settings)
        _halt_client_if_pool_empty(db, client, channels, settings)
        pause_enrollment(db, enrollment, reason="no_active_inbox")
        return "paused:no_inbox"

    scheduler.reset_daily_counter(inbox)
    previous = db.scalar(
        select(Message)
        .where(
            Message.enrollment_id == enrollment.id,
            Message.channel == c.CHANNEL_EMAIL,
            Message.provider_message_id != "",
        )
        .order_by(Message.created_at.desc())
    )
    thread_key = enrollment.id
    headers: dict[str, str] = {}
    if previous is not None:
        headers["In-Reply-To"] = previous.provider_message_id
        headers["References"] = previous.provider_message_id

    message = Message(
        client_id=client.id,
        contact_id=contact.id,
        enrollment_id=enrollment.id,
        step_key=step_key,
        channel=c.CHANNEL_EMAIL,
        status=c.MESSAGE_QUEUED,
        sender_inbox_id=inbox.id,
        from_address=persona.from_email if persona else inbox.email,
        to_address=contact.email or "",
        subject=subject,
        body=body,
        thread_key=thread_key,
    )
    db.add(message)
    db.flush()

    result = channels.email.send(
        inbox=inbox,
        to_address=contact.email or "",
        from_name=persona.from_name if persona else client.name,
        from_email=(persona.from_email if persona else inbox.email) or inbox.email,
        reply_to=persona.reply_to if persona else "",
        subject=subject,
        body=body,
        headers=headers,
    )

    inbox.total_sends += 1
    inbox.last_send_at = now
    if result.ok:
        scheduler.reset_daily_counter(inbox)
        inbox.sends_today += 1
        inbox.consecutive_failures = 0
        message.status = c.MESSAGE_SENT
        message.provider_message_id = result.provider_message_id
        message.sent_at = now
        enrollment.touches += 1
        stats.bump_for_enrollment(
            db, enrollment, step_key=step_key, channel=c.CHANNEL_EMAIL, field_name="sent"
        )
        record_event(
            db,
            client_id=client.id,
            contact_id=contact.id,
            enrollment_id=enrollment.id,
            event_type=c.EVENT_EMAIL_SENT,
            channel=c.CHANNEL_EMAIL,
            payload={
                "step": step_key,
                "inbox": inbox.email,
                "subject": subject,
                "message_id": result.provider_message_id,
            },
            dedupe_key=f"email_sent:{message.id}",
            occurred_at=now,
        )
        db.flush()
        _schedule(db, enrollment, str(step.get("next")), now, settings=settings)
        return "sent:email"

    return _handle_send_failure(
        db,
        enrollment,
        message,
        inbox,
        result,
        graph=graph,
        step=step,
        channels=channels,
        settings=settings,
        now=now,
    )


def _deliver_sms(
    db: Session,
    enrollment: Enrollment,
    graph: Graph,
    step: dict[str, Any],
    contact: Contact,
    client: Client,
    *,
    channels: Channels,
    crm: CRMAdapter | None,
    settings: Settings,
    now: datetime,
) -> str:
    from app.models import Template

    step_key = str(step.get("key"))
    template = db.scalar(
        select(Template).where(
            Template.client_id == client.id, Template.key == graph.template_key_of(step_key)
        )
    )
    if template is None:
        _mark_skipped(db, enrollment, step_key, c.CHANNEL_SMS, "missing_template", settings=settings)
        _schedule(db, enrollment, str(step.get("next")), now, settings=settings)
        return "skipped:missing_template"

    persona = default_persona(db, client.id)
    context = build_context(contact=contact, persona=persona, client=client)
    try:
        body = render(template.body, context)
    except Exception:  # noqa: BLE001
        _mark_skipped(db, enrollment, step_key, c.CHANNEL_SMS, "missing_template", settings=settings)
        _schedule(db, enrollment, str(step.get("next")), now, settings=settings)
        return "skipped:render_failed"

    number = scheduler.pick_phone_number(db, client.id)
    if number is None:
        _mark_skipped(db, enrollment, step_key, c.CHANNEL_SMS, "no_phone_number", settings=settings)
        _schedule(db, enrollment, str(step.get("next")), now, settings=settings)
        return "skipped:no_number"

    message = Message(
        client_id=client.id,
        contact_id=contact.id,
        enrollment_id=enrollment.id,
        step_key=step_key,
        channel=c.CHANNEL_SMS,
        status=c.MESSAGE_QUEUED,
        phone_number_id=number.id,
        from_address=number.e164,
        to_address=contact.phone_e164 or "",
        body=body,
        thread_key=enrollment.id,
    )
    db.add(message)
    db.flush()

    result = channels.sms.send(
        from_number=number.e164, to_address=contact.phone_e164 or "", body=body
    )
    if result.ok:
        scheduler.reset_daily_counter(number)
        number.sends_today += 1
        number.total_sends += 1
        number.last_send_at = now
        message.status = c.MESSAGE_SENT
        message.provider_message_id = result.provider_message_id
        message.sent_at = now
        enrollment.touches += 1
        enrollment.sms_touches += 1
        stats.bump_for_enrollment(
            db, enrollment, step_key=step_key, channel=c.CHANNEL_SMS, field_name="sent"
        )
        record_event(
            db,
            client_id=client.id,
            contact_id=contact.id,
            enrollment_id=enrollment.id,
            event_type=c.EVENT_SMS_SENT,
            channel=c.CHANNEL_SMS,
            payload={"step": step_key, "from": number.e164, "sid": result.provider_message_id},
            dedupe_key=f"sms_sent:{message.id}",
            occurred_at=now,
        )
        db.flush()
        _schedule(db, enrollment, str(step.get("next")), now, settings=settings)
        return "sent:sms"

    number.failures += 1
    message.status = c.MESSAGE_FAILED
    message.error = result.error
    stats.bump_for_enrollment(
        db, enrollment, step_key=step_key, channel=c.CHANNEL_SMS, field_name="failed"
    )
    db.flush()
    if result.retryable:
        raise RetryStep(result.error or "SMS send failed", 60)
    _notify(
        db,
        channels,
        settings,
        kind=c.NOTIFY_FAILURE,
        text=f"⚠️ SMS to {contact.phone_e164} failed permanently: {result.error}",
        client_id=client.id,
        contact_id=contact.id,
        enrollment_id=enrollment.id,
    )
    _schedule(db, enrollment, str(step.get("next")), now, settings=settings)
    return "failed:sms"


def _handle_send_failure(
    db: Session,
    enrollment: Enrollment,
    message: Message,
    inbox: Any,
    result: Any,
    *,
    graph: Graph,
    step: dict[str, Any],
    channels: Channels,
    settings: Settings,
    now: datetime,
) -> str:
    contact_id = enrollment.contact_id
    if result.retryable:
        inbox.consecutive_failures += 1
        inbox.last_error = result.error
        message.status = c.MESSAGE_FAILED
        message.error = result.error
        db.flush()
        should_pause, reason = scheduler.evaluate_inbox_health(db, inbox, settings)
        if should_pause:
            scheduler.pause_inbox(inbox, reason or "unhealthy")
            _notify(
                db,
                channels,
                settings,
                kind=c.NOTIFY_INBOX_DEGRADED,
                text=f"🛑 Inbox {inbox.email} paused: {reason}",
                client_id=enrollment.client_id,
                contact_id=contact_id,
                enrollment_id=enrollment.id,
            )
            _halt_client_if_pool_empty(db, db.get(Client, enrollment.client_id), channels, settings)
        raise RetryStep(result.error or "send failed", 60)

    if result.bounce_type == c.BOUNCE_HARD:
        inbox.hard_bounces += 1
        message.status = c.MESSAGE_BOUNCED
        message.bounce_type = c.BOUNCE_HARD
        message.error = result.error
        stats.bump_for_enrollment(
            db, enrollment, step_key=message.step_key, channel=c.CHANNEL_EMAIL, field_name="bounced"
        )
        add_suppression(
            db,
            client_id=enrollment.client_id,
            contact_id=contact_id,
            channel=c.CHANNEL_EMAIL,
            reason=c.SUPPRESS_HARD_BOUNCE,
            note=result.error,
        )
        record_event(
            db,
            client_id=enrollment.client_id,
            contact_id=contact_id,
            enrollment_id=enrollment.id,
            event_type=c.EVENT_EMAIL_BOUNCED,
            channel=c.CHANNEL_EMAIL,
            payload={"step": message.step_key, "inbox": inbox.email, "error": result.error},
            dedupe_key=f"bounce:{message.id}",
        )
        db.flush()
        should_pause, reason = scheduler.evaluate_inbox_health(db, inbox, settings)
        if should_pause:
            scheduler.pause_inbox(inbox, reason or "bounce rate")
            _notify(
                db,
                channels,
                settings,
                kind=c.NOTIFY_INBOX_DEGRADED,
                text=f"🛑 Inbox {inbox.email} paused: {reason}",
                client_id=enrollment.client_id,
                contact_id=contact_id,
                enrollment_id=enrollment.id,
            )
            _halt_client_if_pool_empty(db, db.get(Client, enrollment.client_id), channels, settings)
        exit_enrollment(
            db,
            enrollment,
            reason=c.EXIT_BOUNCED_HARD,
            client_id=enrollment.client_id,
            contact_id=contact_id,
            channels=channels,
            settings=settings,
        )
        return "exited:bounced_hard"

    if result.bounce_type == c.BOUNCE_SOFT:
        inbox.soft_bounces += 1
        message.status = c.MESSAGE_FAILED
        message.bounce_type = c.BOUNCE_SOFT
        message.error = result.error
        stats.bump_for_enrollment(
            db, enrollment, step_key=message.step_key, channel=c.CHANNEL_EMAIL, field_name="failed"
        )
        db.flush()
        raise RetryStep(result.error or "soft bounce", 300)

    message.status = c.MESSAGE_FAILED
    message.error = result.error
    stats.bump_for_enrollment(
        db, enrollment, step_key=message.step_key, channel=c.CHANNEL_EMAIL, field_name="failed"
    )
    db.flush()
    _notify(
        db,
        channels,
        settings,
        kind=c.NOTIFY_FAILURE,
        text=f"⚠️ Send from {inbox.email} failed: {result.error}",
        client_id=enrollment.client_id,
        contact_id=contact_id,
        enrollment_id=enrollment.id,
    )
    next_key = str(step.get("next") or "")
    if next_key:
        _schedule(db, enrollment, next_key, now, settings=settings)
    else:
        exit_enrollment(
            db,
            enrollment,
            reason=c.EXIT_COMPLETED,
            client_id=enrollment.client_id,
            contact_id=contact_id,
            channels=channels,
            settings=settings,
            status=c.ENROLLMENT_COMPLETED,
        )
    return "failed:email"


def _halt_client_if_pool_empty(
    db: Session, client: Client | None, channels: Channels, settings: Settings
) -> None:
    if client is None:
        return
    if scheduler.active_inbox_count(db, client.id) == 0 and not client.sending_halted:
        client.sending_halted = True
        client.halt_reason = "no active inboxes in the sending pool"
        db.flush()
        _notify(
            db,
            channels,
            settings,
            kind=c.NOTIFY_POOL_HALTED,
            text=(
                f"🚨 *All sending halted for {client.name}* — the inbox pool has no active "
                f"mailboxes. Fix the pool before email can resume."
            ),
            client_id=client.id,
        )


def _hours_since_update(contact: Contact, now: datetime) -> int:
    updated = _utc(contact.updated_at)
    if updated is None:
        return 0
    return max(0, int((now - updated).total_seconds() // 3600))


# ---------------------------------------------------------------------------
# Event ingestion and reactions
# ---------------------------------------------------------------------------
def ingest_event(
    db: Session,
    *,
    client_id: str,
    event_type: str,
    channel: str = "",
    contact: Contact | None = None,
    payload: dict[str, Any] | None = None,
    dedupe_key: str = "",
    channels: Channels,
    crm: CRMAdapter | None = None,
    settings: Settings,
    occurred_at: datetime | None = None,
) -> Event | None:
    """Record an inbound fact and let it re-route any open enrollment."""
    payload = payload or {}
    event = record_event(
        db,
        client_id=client_id,
        contact_id=contact.id if contact else None,
        event_type=event_type,
        channel=channel,
        payload=payload,
        dedupe_key=dedupe_key,
        occurred_at=occurred_at,
    )
    if event is None:
        return None

    if contact is not None:
        contact.updated_at = utcnow()

    enrollments = (
        db.scalars(
            select(Enrollment).where(
                Enrollment.contact_id == contact.id,
                Enrollment.status.in_(c.ENROLLMENT_OPEN_STATES),
            )
        ).all()
        if contact is not None
        else []
    )

    for enrollment in enrollments:
        enrollment.last_event_at = event.occurred_at
        graph = graph_of(db, enrollment)
        plan = plan_for_event(
            event_type,
            sentiment=str(payload.get("sentiment") or "") or None,
            overrides=graph.reaction_overrides(),
        )
        _apply_reaction(
            db,
            enrollment,
            graph,
            event,
            plan,
            channels=channels,
            crm=crm,
            settings=settings,
        )

    _attribute_stats(db, event, contact, channels=channels, settings=settings)
    db.flush()
    return event


def _apply_reaction(
    db: Session,
    enrollment: Enrollment,
    graph: Graph,
    event: Event,
    plan: Any,
    *,
    channels: Channels,
    crm: CRMAdapter | None,
    settings: Settings,
) -> None:
    now = utcnow()
    policy = db.get(Sequence, enrollment.sequence_id)
    reaction_policy = policy.reaction_policy if policy else c.REACTION_POLICY_CANCEL_AND_REBRANCH

    if plan.action == ACTION_IGNORE:
        return

    record_decision(
        db,
        enrollment=enrollment,
        step_key=enrollment.current_step_key or "",
        rule=f"reaction:{plan.meta.get('reaction_key', event.type)}",
        outcome=plan.action,
        detail={"description": plan.description, "event_id": event.id},
        event_id=event.id,
    )

    if plan.action in (ACTION_EXIT, ACTION_EXIT_SUPPRESS):
        if plan.suppression:
            channel, reason = plan.suppression
            add_suppression(
                db,
                client_id=enrollment.client_id,
                contact_id=enrollment.contact_id,
                channel=channel,
                reason=reason,
                note=f"from {event.type}",
            )
        if plan.notify:
            _notify(
                db,
                channels,
                settings,
                kind=plan.notify,
                text=_notification_text(plan, event),
                client_id=enrollment.client_id,
                contact_id=enrollment.contact_id,
                enrollment_id=enrollment.id,
            )
        if plan.create_task and crm is not None:
            _crm_side_effects(db, enrollment, plan, event, crm=crm)
        exit_enrollment(
            db,
            enrollment,
            reason=plan.exit_reason or c.EXIT_REPLIED,
            client_id=enrollment.client_id,
            contact_id=enrollment.contact_id,
            channels=channels,
            settings=settings,
        )
        return

    if plan.action == ACTION_PAUSE:
        if plan.create_task and crm is not None:
            _crm_side_effects(db, enrollment, plan, event, crm=crm)
        if plan.notify:
            _notify(
                db,
                channels,
                settings,
                kind=plan.notify,
                text=_notification_text(plan, event),
                client_id=enrollment.client_id,
                contact_id=enrollment.contact_id,
                enrollment_id=enrollment.id,
            )
        _create_reaction_task(db, enrollment, event, plan)
        pause_enrollment(db, enrollment, reason="awaiting_human_reply")
        record_event(
            db,
            client_id=enrollment.client_id,
            contact_id=enrollment.contact_id,
            enrollment_id=enrollment.id,
            event_type=c.EVENT_SEQUENCE_PAUSED,
            payload={"reason": "awaiting_human_reply", "event_id": event.id},
            dedupe_key=f"paused:{enrollment.id}:{event.id}",
        )
        return

    if plan.action == ACTION_GOTO:
        if not plan.target_step or not graph.get(plan.target_step):
            return
        if reaction_policy != c.REACTION_POLICY_CANCEL_AND_REBRANCH:
            # keep_pending: the branch queues behind whatever is already scheduled.
            _schedule(db, enrollment, plan.target_step, now + timedelta(seconds=1), settings=settings)
            return
        scheduler.cancel_pending_timers(db, enrollment.id)
        enrollment.status = c.ENROLLMENT_ACTIVE
        db.flush()
        _advance(
            db,
            enrollment,
            plan.target_step or "",
            channels=channels,
            crm=crm,
            settings=settings,
            now=now,
        )
        return

    if plan.action == ACTION_DELAY:
        _apply_auto_reply_delay(db, enrollment, event, plan, now=now)
        return

    if plan.action == ACTION_SUBSTITUTE:
        _substitute_failed_message(
            db, enrollment, graph, channels=channels, crm=crm, settings=settings, now=now
        )
        return


def _notification_text(plan: Any, event: Event) -> str:
    payload = event.payload or {}
    contact_label = payload.get("contact_label") or payload.get("from_address") or "prospect"
    body = str(payload.get("body") or payload.get("snippet") or "").strip()
    excerpt = f"\n> {body[:400]}" if body else ""
    icons = {
        c.NOTIFY_POSITIVE_REPLY: "🎉 *Positive reply*",
        c.NOTIFY_NEGATIVE_REPLY: "🚫 *Negative reply*",
        c.NOTIFY_NEUTRAL_REPLY: "❓ *Question / neutral reply*",
        c.NOTIFY_MEETING_BOOKED: "📅 *Meeting booked*",
    }
    header = icons.get(plan.notify or "", "📨 *Sequence reaction*")
    return f"{header} — {contact_label}{excerpt}\nstep: `{event.payload.get('step', '')}`"


def _crm_side_effects(
    db: Session, enrollment: Enrollment, plan: Any, event: Event, *, crm: CRMAdapter
) -> None:
    contact = db.get(Contact, enrollment.contact_id)
    if contact is None:
        return
    external_id = contact.ghl_contact_id or ""
    if not external_id:
        result = crm.upsert_contact(
            CrmContact(
                external_id="",
                email=contact.email or "",
                phone=contact.phone_e164 or "",
                first_name=contact.first_name,
                last_name=contact.last_name,
                company=contact.company,
                tags=list(contact.tags or []),
            )
        )
        if result.ok and result.external_id:
            contact.ghl_contact_id = result.external_id
            external_id = result.external_id
            db.flush()
    if not external_id:
        return
    if plan.create_task:
        crm.create_task(
            external_id,
            plan.task_title or "Follow up",
            body=f"Triggered by {event.type}: {event.payload.get('body', '')[:400]}",
        )
    crm.create_note(external_id, f"Nurture: {event.type} at {event.occurred_at.isoformat()}")


def _create_reaction_task(
    db: Session, enrollment: Enrollment, event: Event, plan: Any
) -> HumanTask:
    payload = event.payload or {}
    task = HumanTask(
        client_id=enrollment.client_id,
        contact_id=enrollment.contact_id,
        enrollment_id=enrollment.id,
        step_key=enrollment.current_step_key or "",
        title=plan.task_title or "Reply to the prospect",
        instructions=str(payload.get("body") or "")[:2000],
        assignee="",
    )
    db.add(task)
    db.flush()
    record_event(
        db,
        client_id=enrollment.client_id,
        contact_id=enrollment.contact_id,
        enrollment_id=enrollment.id,
        event_type=c.EVENT_SEQUENCE_PAUSED,
        payload={"reason": "human_task", "task_id": task.id},
        dedupe_key=f"task:{task.id}",
    )
    return task


def _apply_auto_reply_delay(
    db: Session, enrollment: Enrollment, event: Event, plan: Any, *, now: datetime
) -> None:
    """An out-of-office reply must not stop the sequence (spec 8.3)."""
    payload = event.payload or {}
    until_raw = payload.get("auto_reply_until")
    target = now + timedelta(seconds=plan.delay_seconds)
    if isinstance(until_raw, str) and until_raw:
        try:
            parsed = datetime.fromisoformat(until_raw)
            target = max(target, _utc(parsed) or target)
        except ValueError:
            pass

    graph = graph_of(db, enrollment)
    pending = db.scalars(
        select(Timer).where(
            Timer.enrollment_id == enrollment.id, Timer.status == c.TIMER_SCHEDULED
        )
    ).all()
    if not pending:
        record_decision(
            db,
            enrollment=enrollment,
            step_key=enrollment.current_step_key or "",
            rule="auto_reply",
            outcome="no pending timer to shift",
        )
        return
    for timer in pending:
        current = _utc(timer.run_at) or now
        # Pushing a *delay* to the return date would add the delay on top of it,
        # so the delay's own duration is subtracted: the next touch lands at
        # (or after) the return date either way.
        if graph.step_type(timer.step_key) == NODE_DELAY:
            target_for_timer = target - timedelta(seconds=graph.delay_seconds(timer.step_key))
        else:
            target_for_timer = target
        if current < target_for_timer:
            timer.run_at = target_for_timer
            enrollment.next_run_at = target_for_timer
    db.flush()
    record_decision(
        db,
        enrollment=enrollment,
        step_key=enrollment.current_step_key or "",
        rule="auto_reply",
        outcome=f"next touch pushed to {target.isoformat()}",
    )


def _substitute_failed_message(
    db: Session,
    enrollment: Enrollment,
    graph: Graph,
    *,
    channels: Channels,
    crm: CRMAdapter | None,
    settings: Settings,
    now: datetime,
) -> None:
    """An undelivered SMS substitutes the email fallback (D14)."""
    last = db.scalar(
        select(Message)
        .where(Message.enrollment_id == enrollment.id, Message.channel == c.CHANNEL_SMS)
        .order_by(Message.created_at.desc())
    )
    if last is None:
        return
    step = graph.get(last.step_key)
    if not step or graph.fallback_of(last.step_key) != NODE_SEND_EMAIL:
        return
    _substitute(
        db,
        enrollment,
        graph,
        step,
        channels=channels,
        crm=crm,
        settings=settings,
        now=now,
        reason="sms_undelivered",
    )


def _attribute_stats(
    db: Session,
    event: Event,
    contact: Contact | None,
    *,
    channels: Channels,
    settings: Settings,
) -> None:
    """Credit replies and meetings to the last touch that earned them."""
    if contact is None:
        return
    if event.type not in c.REPLY_EVENTS + (c.EVENT_MEETING_BOOKED,):
        return

    last = db.scalar(
        select(Message)
        .where(Message.contact_id == contact.id, Message.sent_at.is_not(None))
        .order_by(Message.sent_at.desc())
    )
    if last is None or not last.enrollment_id:
        return
    enrollment = db.get(Enrollment, last.enrollment_id)
    if enrollment is None:
        return

    if event.type in c.REPLY_EVENTS:
        stats.bump_for_enrollment(
            db, enrollment, step_key=last.step_key, channel=last.channel, field_name="replied"
        )
        if (event.payload or {}).get("sentiment") == c.SENTIMENT_POSITIVE:
            stats.bump_for_enrollment(
                db,
                enrollment,
                step_key=last.step_key,
                channel=last.channel,
                field_name="positive_replies",
            )
    if event.type == c.EVENT_MEETING_BOOKED:
        stats.bump_for_enrollment(
            db, enrollment, step_key=last.step_key, channel=last.channel, field_name="meetings"
        )


def record_delivery(
    db: Session,
    *,
    provider_message_id: str,
    status: str,
    bounce_type: str | None = None,
) -> Message | None:
    """Apply a provider delivery callback to the matching outbound message."""
    message = db.scalar(
        select(Message).where(Message.provider_message_id == provider_message_id)
    )
    if message is None:
        return None

    if status in ("delivered", "sent"):
        message.status = c.MESSAGE_DELIVERED
        if message.enrollment_id:
            enrollment = db.get(Enrollment, message.enrollment_id)
            if enrollment:
                stats.bump_for_enrollment(
                    db,
                    enrollment,
                    step_key=message.step_key,
                    channel=message.channel,
                    field_name="delivered",
                )
        record_event(
            db,
            client_id=message.client_id,
            contact_id=message.contact_id,
            enrollment_id=message.enrollment_id,
            event_type=(
                c.EVENT_EMAIL_DELIVERED
                if message.channel == c.CHANNEL_EMAIL
                else c.EVENT_SMS_DELIVERED
            ),
            channel=message.channel,
            payload={"message_id": provider_message_id},
            dedupe_key=f"delivered:{provider_message_id}",
        )
        db.flush()
        return message

    if status in ("undelivered", "failed"):
        message.status = c.MESSAGE_FAILED
        message.error = status
        record_event(
            db,
            client_id=message.client_id,
            contact_id=message.contact_id,
            enrollment_id=message.enrollment_id,
            event_type=(
                c.EVENT_SMS_UNDELIVERED if message.channel == c.CHANNEL_SMS else c.EVENT_EMAIL_BOUNCED
            ),
            channel=message.channel,
            payload={"message_id": provider_message_id, "status": status},
            dedupe_key=f"undelivered:{provider_message_id}",
        )
        db.flush()
        return message

    return message


# ---------------------------------------------------------------------------
# Inbound processing
# ---------------------------------------------------------------------------
def handle_inbound_email(
    db: Session,
    inbound: InboundEmail,
    *,
    client_id: str,
    channels: Channels,
    crm: CRMAdapter | None,
    settings: Settings,
) -> InboundMessage | None:
    """Match, classify and ingest one inbound email."""
    from_address = email_of(inbound.from_address)
    if not from_address:
        return None

    if inbound.message_id:
        existing = db.scalar(
            select(InboundMessage).where(InboundMessage.in_reply_to == inbound.message_id)
        )
        if existing is not None:
            return existing

    contact = db.scalar(
        select(Contact).where(Contact.client_id == client_id, Contact.email == from_address)
    )

    thread_key = ""
    if contact is not None:
        hints = [inbound.in_reply_to, *inbound.references]
        for hint in hints:
            if not hint:
                continue
            match = db.scalar(
                select(Message).where(Message.provider_message_id == hint, Message.channel == c.CHANNEL_EMAIL)
            )
            if match is not None:
                thread_key = match.thread_key
                break

    classification = sentiment_mod.classify(
        inbound.subject, inbound.body, inbound.headers
    )
    auto_reply_until = None
    if classification.is_auto_reply:
        raw_date = sentiment_mod.extract_return_date(inbound.body)
        auto_reply_until = _parse_return_date(raw_date)

    record = InboundMessage(
        client_id=client_id,
        contact_id=contact.id if contact else None,
        channel=c.CHANNEL_EMAIL,
        from_address=from_address,
        to_address=email_of(inbound.to_address),
        subject=inbound.subject,
        body=inbound.body,
        thread_key=thread_key,
        in_reply_to=inbound.message_id,
        sentiment=classification.sentiment,
        matched_rule=classification.matched_rule,
        is_auto_reply=classification.is_auto_reply,
        auto_reply_until=auto_reply_until,
        raw=inbound.raw,
        received_at=inbound.received_at or utcnow(),
    )
    db.add(record)
    db.flush()

    if contact is None:
        # An unknown sender is still recorded; it just cannot route anywhere.
        return record

    event_type = (
        c.EVENT_EMAIL_AUTO_REPLY if classification.is_auto_reply else c.EVENT_EMAIL_REPLIED
    )
    ingest_event(
        db,
        client_id=client_id,
        event_type=event_type,
        channel=c.CHANNEL_EMAIL,
        contact=contact,
        payload={
            "sentiment": classification.sentiment,
            "matched_rule": classification.matched_rule,
            "subject": inbound.subject,
            "body": inbound.body,
            "from_address": from_address,
            "contact_label": _contact_label(contact),
            "auto_reply_until": auto_reply_until.isoformat() if auto_reply_until else None,
        },
        dedupe_key=f"inbound:{inbound.message_id or record.id}",
        channels=channels,
        crm=crm,
        settings=settings,
        occurred_at=record.received_at,
    )
    return record


def handle_inbound_sms(
    db: Session,
    *,
    client_id: str,
    from_address: str,
    body: str,
    provider_message_id: str = "",
    contact: Contact | None = None,
    channels: Channels,
    crm: CRMAdapter | None,
    settings: Settings,
) -> InboundMessage | None:
    """D19: an inbound SMS stops automation exactly like an email reply."""
    contact = contact or db.scalar(
        select(Contact).where(Contact.client_id == client_id, Contact.phone_e164 == from_address)
    )
    classification = sentiment_mod.classify("", body)
    record = InboundMessage(
        client_id=client_id,
        contact_id=contact.id if contact else None,
        channel=c.CHANNEL_SMS,
        from_address=from_address,
        body=body,
        sentiment=classification.sentiment,
        matched_rule=classification.matched_rule,
        raw={"sid": provider_message_id},
    )
    db.add(record)
    db.flush()

    if contact is None:
        return record

    ingest_event(
        db,
        client_id=client_id,
        event_type=c.EVENT_SMS_REPLIED,
        channel=c.CHANNEL_SMS,
        contact=contact,
        payload={
            "sentiment": classification.sentiment,
            "matched_rule": classification.matched_rule,
            "body": body,
            "from_address": from_address,
            "contact_label": _contact_label(contact),
        },
        dedupe_key=f"inbound_sms:{provider_message_id or record.id}",
        channels=channels,
        crm=crm,
        settings=settings,
    )
    return record


def _contact_label(contact: Contact) -> str:
    name = f"{contact.first_name} {contact.last_name}".strip()
    return f"{name} ({contact.company})" if contact.company else (name or contact.email or "")


def _parse_return_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    from email.utils import parsedate_to_datetime

    for parser in (
        lambda value: datetime.fromisoformat(value),
        lambda value: parsedate_to_datetime(value),
        lambda value: datetime.strptime(value, "%d/%m/%Y"),
    ):
        try:
            parsed = parser(raw)
            if parsed is not None:
                return _utc(parsed)
        except (ValueError, TypeError):
            continue
    return None


# ---------------------------------------------------------------------------
# Version migration (D21)
# ---------------------------------------------------------------------------
def migrate_enrollments_to_version(
    db: Session,
    *,
    sequence: Sequence,
    new_version: SequenceVersion,
    channels: Channels,
    settings: Settings,
) -> tuple[int, int]:
    """Move in-flight contacts onto a newly published version."""
    new_graph = parse_graph(new_version.graph)
    open_states = (c.ENROLLMENT_ACTIVE, c.ENROLLMENT_WAITING, c.ENROLLMENT_PAUSED)
    enrollments = db.scalars(
        select(Enrollment).where(
            Enrollment.sequence_id == sequence.id, Enrollment.status.in_(open_states)
        )
    ).all()

    migrated = 0
    orphaned = 0
    now = utcnow()

    for enrollment in enrollments:
        pending_run_at = enrollment.next_run_at
        scheduler.cancel_pending_timers(db, enrollment.id)

        # Capture the outgoing graph *before* repointing the enrollment.
        previous_version = db.get(SequenceVersion, enrollment.sequence_version_id)
        old_graph = parse_graph(previous_version.graph if previous_version else {})
        enrollment.sequence_version_id = new_version.id

        current = enrollment.current_step_key
        target = current if current and new_graph.get(current) else None
        if target is None:
            # Re-anchor by display name, which survives step renaming.
            old_name = old_graph.name_of(current) if current else ""
            for key in new_graph.steps:
                if old_name and new_graph.name_of(key) == old_name:
                    target = key
                    break

        if target is None:
            orphaned += 1
            record_event(
                db,
                client_id=enrollment.client_id,
                contact_id=enrollment.contact_id,
                enrollment_id=enrollment.id,
                event_type=c.EVENT_SEQUENCE_EXITED,
                payload={"reason": c.EXIT_ORPHANED_BY_VERSION, "from_step": current},
                dedupe_key=f"orphaned:{enrollment.id}:{new_version.id}",
            )
            pause_enrollment(db, enrollment, reason=c.EXIT_ORPHANED_BY_VERSION)
            _notify(
                db,
                channels,
                settings,
                kind=c.NOTIFY_FAILURE,
                text=(
                    f"⚠️ Contact in sequence *{sequence.name}* was orphaned by version "
                    f"{new_version.version} and is paused at `{current}`. Re-anchor it manually."
                ),
                client_id=enrollment.client_id,
                contact_id=enrollment.contact_id,
                enrollment_id=enrollment.id,
            )
            continue

        enrollment.current_step_key = target
        # In-flight contacts keep their schedule unless it is now in the past.
        run_at = _utc(pending_run_at) or now
        run_at = max(run_at, now)
        _schedule(db, enrollment, target, run_at, settings=settings)
        if enrollment.status == c.ENROLLMENT_PAUSED and enrollment.pause_reason == c.EXIT_CLIENT_HALTED:
            # Leave halted contacts paused; they resume when the halt is lifted.
            scheduler.cancel_pending_timers(db, enrollment.id)
            enrollment.next_run_at = None
        migrated += 1
        record_decision(
            db,
            enrollment=enrollment,
            step_key=target,
            rule=f"migrated_to_version:{new_version.version}",
            outcome=f"pending step re-anchored to {target}",
        )

    db.flush()
    return migrated, orphaned
