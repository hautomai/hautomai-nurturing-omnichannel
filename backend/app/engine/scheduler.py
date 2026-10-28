"""Scheduling primitives: timers, send windows, rotation and health.

Everything here is deliberately database-backed. The worker holds no sequence
state in memory, so a restart mid-cadence is safe.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import constants as c
from app.config import Settings
from app.models import Message, PhoneNumber, SenderInbox, Timer, utcnow

WINDOW_SEARCH_DAYS = 21


# --------------------------------------------------------------------------
# Time helpers
# --------------------------------------------------------------------------
def _tz(name: str | None, fallback: str) -> ZoneInfo:
    for candidate in (name, fallback):
        if candidate:
            try:
                return ZoneInfo(candidate)
            except (ZoneInfoNotFoundError, ValueError, KeyError):
                continue
    return ZoneInfo("UTC")


def reset_daily_counter(row: SenderInbox | PhoneNumber, *, today: date | None = None) -> bool:
    """Reset a per-day send counter when the day has rolled over."""
    today = today or datetime.now(timezone.utc).date()
    if row.sends_today_date != today.isoformat():
        row.sends_today = 0
        row.sends_today_date = today.isoformat()
        return True
    return False


def apply_send_window(
    run_at: datetime,
    *,
    channel: str,
    timezone_name: str | None,
    settings: Settings,
) -> datetime:
    """Push a send time into the allowed window for its channel.

    Only SMS has a window in v1 (quiet hours 09:00-18:00 local, weekends off by
    default). Email is unrestricted, matching D17's elapsed-delay semantics.
    """
    if channel != c.CHANNEL_SMS or not settings.sms_quiet_hours_enabled:
        return run_at

    tz = _tz(timezone_name, settings.default_contact_timezone)
    local = run_at.astimezone(tz)
    start_hour = settings.sms_window_start_hour
    end_hour = settings.sms_window_end_hour

    def at_start(moment: datetime) -> datetime:
        return moment.replace(hour=start_hour, minute=0, second=0, microsecond=0)

    def weekend(moment: datetime) -> bool:
        return moment.weekday() >= 5

    candidate = local
    for _ in range(WINDOW_SEARCH_DAYS * 2):
        if not settings.sms_allow_weekends and weekend(candidate):
            candidate = at_start(candidate + timedelta(days=1))
            continue
        if candidate.hour < start_hour:
            candidate = at_start(candidate)
        elif candidate.hour >= end_hour:
            candidate = at_start(candidate + timedelta(days=1))
            continue
        break
    return candidate.astimezone(timezone.utc)


def last_outbound_at(db: Session, contact_id: str, channel: str) -> datetime | None:
    return db.scalar(
        select(func.max(Message.sent_at)).where(
            Message.contact_id == contact_id,
            Message.channel == channel,
            Message.sent_at.is_not(None),
            Message.status.in_([c.MESSAGE_SENT, c.MESSAGE_DELIVERED, c.MESSAGE_SUBSTITUTED]),
        )
    )


def apply_frequency_cap(
    db: Session,
    run_at: datetime,
    *,
    contact_id: str,
    channel: str,
    timezone_name: str | None,
    settings: Settings,
) -> datetime:
    """Never touch the same contact twice on a channel inside the cooldown."""
    cooldown = timedelta(hours=settings.min_hours_between_contact_touches)
    if cooldown <= timedelta(0):
        return run_at
    previous = last_outbound_at(db, contact_id, channel)
    if previous is None:
        return run_at
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=timezone.utc)
    earliest = previous + cooldown
    if earliest <= run_at:
        return run_at
    return apply_send_window(
        earliest, channel=channel, timezone_name=timezone_name, settings=settings
    )


# --------------------------------------------------------------------------
# Timer queue
# --------------------------------------------------------------------------
def schedule_timer(
    db: Session,
    *,
    enrollment_id: str,
    step_key: str,
    run_at: datetime,
) -> Timer:
    """Schedule the next step, cancelling any other pending timer for the run.

    One pending timer per enrollment is an invariant: a reaction cancels the
    pending row and schedules a new one (cancel-and-re-branch, D8).
    """
    cancel_pending_timers(db, enrollment_id)
    timer = Timer(enrollment_id=enrollment_id, step_key=step_key, run_at=run_at)
    db.add(timer)
    db.flush()
    return timer


def cancel_pending_timers(db: Session, enrollment_id: str) -> int:
    pending = db.scalars(
        select(Timer).where(
            Timer.enrollment_id == enrollment_id,
            Timer.status.in_([c.TIMER_SCHEDULED, c.TIMER_CLAIMED]),
        )
    ).all()
    for timer in pending:
        timer.status = c.TIMER_CANCELLED
    if pending:
        db.flush()
    return len(pending)


def claim_due_timers(
    db: Session,
    *,
    limit: int = 50,
    worker_id: str = "worker",
    now: datetime | None = None,
) -> list[Timer]:
    """Atomically claim due timers.

    ``FOR UPDATE SKIP LOCKED`` on Postgres; on SQLite (tests) the write lock plus
    the status transition is sufficient because there is a single connection.
    """
    now = now or utcnow()
    stmt = (
        select(Timer)
        .where(Timer.status == c.TIMER_SCHEDULED, Timer.run_at <= now)
        .order_by(Timer.run_at.asc())
        .limit(limit)
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)

    timers = list(db.scalars(stmt).all())
    for timer in timers:
        timer.status = c.TIMER_CLAIMED
        timer.claimed_by = worker_id
        timer.claimed_at = now
    if timers:
        db.flush()
    return timers


# --------------------------------------------------------------------------
# Rotation
# --------------------------------------------------------------------------
def pick_sender_inbox(db: Session, client_id: str) -> SenderInbox | None:
    """Least-used active inbox under its daily cap; ties break round-robin."""
    inboxes = list(
        db.scalars(
            select(SenderInbox).where(
                SenderInbox.client_id == client_id,
                SenderInbox.status == c.INBOX_ACTIVE,
            )
        ).all()
    )
    if not inboxes:
        return None
    eligible: list[SenderInbox] = []
    for inbox in inboxes:
        reset_daily_counter(inbox)
        if inbox.sends_today < inbox.daily_cap:
            eligible.append(inbox)
    if not eligible:
        return None
    eligible.sort(key=lambda row: (row.sends_today, row.last_send_at or datetime.min.replace(tzinfo=timezone.utc)))
    return eligible[0]


def pick_phone_number(db: Session, client_id: str) -> PhoneNumber | None:
    numbers = list(
        db.scalars(
            select(PhoneNumber).where(
                PhoneNumber.client_id == client_id,
                PhoneNumber.status == c.INBOX_ACTIVE,
            )
        ).all()
    )
    if not numbers:
        return None
    eligible = []
    for number in numbers:
        reset_daily_counter(number)
        if number.sends_today < number.daily_cap:
            eligible.append(number)
    if not eligible:
        return None
    eligible.sort(key=lambda row: (row.sends_today, row.last_send_at or datetime.min.replace(tzinfo=timezone.utc)))
    return eligible[0]


def active_inbox_count(db: Session, client_id: str) -> int:
    return int(
        db.scalar(
            select(func.count(SenderInbox.id)).where(
                SenderInbox.client_id == client_id,
                SenderInbox.status == c.INBOX_ACTIVE,
            )
        )
        or 0
    )


# --------------------------------------------------------------------------
# Inbox health
# --------------------------------------------------------------------------
def inbox_bounce_rate(
    db: Session, inbox_id: str, *, sample: int, settings: Settings | None = None
) -> tuple[float, int]:
    """Bounce rate over the most recent ``sample`` sends, with the sample size."""
    recent = list(
        db.scalars(
            select(Message)
            .where(
                Message.sender_inbox_id == inbox_id,
                Message.channel == c.CHANNEL_EMAIL,
                Message.status.in_(
                    [c.MESSAGE_SENT, c.MESSAGE_DELIVERED, c.MESSAGE_BOUNCED, c.MESSAGE_FAILED]
                ),
            )
            .order_by(Message.created_at.desc())
            .limit(sample)
        ).all()
    )
    if not recent:
        return 0.0, 0
    bounced = sum(1 for message in recent if message.status == c.MESSAGE_BOUNCED)
    return round(bounced / len(recent), 4), len(recent)


def evaluate_inbox_health(
    db: Session, inbox: SenderInbox, settings: Settings
) -> tuple[bool, str | None]:
    """Return ``(should_pause, reason)`` for an inbox after an outcome."""
    if inbox.consecutive_failures >= settings.inbox_max_consecutive_failures:
        return True, f"{inbox.consecutive_failures} consecutive send failures"

    rate, sample = inbox_bounce_rate(db, inbox.id, sample=settings.inbox_bounce_sample)
    inbox.bounce_rate = rate
    # Require a meaningful sample before pausing, otherwise one early bounce on a
    # brand new inbox would take the whole pool down.
    minimum_sample = min(20, settings.inbox_bounce_sample)
    if sample >= minimum_sample and rate > settings.inbox_bounce_rate_limit:
        return True, f"{rate:.1%} bounce rate over the last {sample} sends"
    return False, None


def pause_inbox(inbox: SenderInbox, reason: str) -> None:
    inbox.status = c.INBOX_PAUSED
    inbox.paused_reason = reason
