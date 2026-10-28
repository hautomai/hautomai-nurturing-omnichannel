"""Dashboard aggregations.

Reads ``sequence_stats`` rollups for anything chart-like, and only touches raw
tables with indexed + bounded queries for the freshness-sensitive panels (the
needs-attention feed). This is the contract from spec 22.3: the dashboard never
scans ``events``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import constants as c
from app.engine.graph import parse_graph
from app.models import (
    Enrollment,
    Event,
    HumanTask,
    InboundMessage,
    Message,
    SenderInbox,
    Sequence,
    SequenceStat,
    SequenceVersion,
    Timer,
    utcnow,
)

STALLED_AFTER = timedelta(minutes=10)


def _window(days: int, now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or utcnow()
    return now - timedelta(days=days), now


# ---------------------------------------------------------------------------
# Overview strip
# ---------------------------------------------------------------------------
def overview(db: Session, *, client_id: str, days: int = 30, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    start, end = _window(days, now)
    previous_start = start - timedelta(days=days)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    active_enrollments = int(
        db.scalar(
            select(func.count(Enrollment.id)).where(
                Enrollment.client_id == client_id, Enrollment.status == c.ENROLLMENT_ACTIVE
            )
        )
        or 0
    )

    sent_today = int(
        db.scalar(
            select(func.count(Message.id)).where(
                Message.client_id == client_id,
                Message.sent_at.is_not(None),
                Message.sent_at >= today_start,
                Message.status.in_(
                    [c.MESSAGE_SENT, c.MESSAGE_DELIVERED, c.MESSAGE_SUBSTITUTED]
                ),
            )
        )
        or 0
    )

    daily_capacity = int(
        db.scalar(
            select(func.coalesce(func.sum(SenderInbox.daily_cap), 0)).where(
                SenderInbox.client_id == client_id, SenderInbox.status == c.INBOX_ACTIVE
            )
        )
        or 0
    )

    def _replies(begin: datetime, finish: datetime) -> tuple[int, int]:
        rows = db.execute(
            select(InboundMessage.sentiment, func.count(InboundMessage.id))
            .where(
                InboundMessage.client_id == client_id,
                InboundMessage.is_auto_reply.is_(False),
                InboundMessage.received_at >= begin,
                InboundMessage.received_at < finish,
            )
            .group_by(InboundMessage.sentiment)
        ).all()
        total = sum(count for _, count in rows)
        positive = sum(count for sentiment, count in rows if sentiment == c.SENTIMENT_POSITIVE)
        return total, positive

    replies, positive = _replies(start, end)
    prev_replies, _ = _replies(previous_start, start)

    def _sent(begin: datetime, finish: datetime) -> int:
        return int(
            db.scalar(
                select(func.count(Message.id)).where(
                    Message.client_id == client_id,
                    Message.sent_at.is_not(None),
                    Message.sent_at >= begin,
                    Message.sent_at < finish,
                    Message.status.in_(
                        [c.MESSAGE_SENT, c.MESSAGE_DELIVERED, c.MESSAGE_SUBSTITUTED]
                    ),
                )
            )
            or 0
        )

    period_sent = _sent(start, end)
    prev_sent = _sent(previous_start, start)

    def _meetings(begin: datetime, finish: datetime) -> int:
        return int(
            db.scalar(
                select(func.count(Event.id)).where(
                    Event.client_id == client_id,
                    Event.type == c.EVENT_MEETING_BOOKED,
                    Event.occurred_at >= begin,
                    Event.occurred_at < finish,
                )
            )
            or 0
        )

    meetings = _meetings(start, end)
    prev_meetings = _meetings(previous_start, start)

    live_versions = int(
        db.scalar(
            select(func.count(Sequence.id)).where(
                Sequence.client_id == client_id, Sequence.status == c.SEQUENCE_ACTIVE
            )
        )
        or 0
    )

    def delta(current: float, previous: float) -> float:
        if previous <= 0:
            return 0.0
        return round((current - previous) / previous, 4)

    reply_rate = round(replies / period_sent, 4) if period_sent else 0.0
    prev_reply_rate = round(prev_replies / prev_sent, 4) if prev_sent else 0.0

    return {
        "active_enrollments": active_enrollments,
        "sent_today": sent_today,
        "daily_capacity": daily_capacity,
        "reply_rate": reply_rate,
        "replies": replies,
        "positive_replies": positive,
        "meetings": meetings,
        "live_versions": live_versions,
        "deltas": {
            "sent": delta(period_sent, prev_sent),
            "reply_rate": delta(reply_rate, prev_reply_rate),
            "meetings": delta(meetings, prev_meetings),
        },
    }


# ---------------------------------------------------------------------------
# Sequence funnel + channel comparison
# ---------------------------------------------------------------------------
def sequence_analytics(
    db: Session, *, sequence: Sequence, version_id: str | None = None
) -> dict[str, Any]:
    version = (
        db.get(SequenceVersion, version_id)
        if version_id
        else db.get(SequenceVersion, sequence.current_version_id or "")
    )
    if version is None:
        return {
            "sequence_id": sequence.id,
            "sequence_name": sequence.name,
            "version_id": "",
            "version": 0,
            "funnel": [],
            "channels": [],
            "totals": {},
        }

    stats_rows = db.scalars(
        select(SequenceStat).where(SequenceStat.sequence_version_id == version.id)
    ).all()
    by_step: dict[str, SequenceStat] = {}
    for row in stats_rows:
        existing = by_step.get(row.step_key)
        if existing is None:
            by_step[row.step_key] = row
        else:
            # Same step key can appear once per channel.
            for field in (
                "sent",
                "delivered",
                "bounced",
                "failed",
                "skipped",
                "substituted",
                "replied",
                "positive_replies",
                "meetings",
            ):
                setattr(existing, field, getattr(existing, field) + getattr(row, field))

    graph = parse_graph(version.graph)
    send_keys = graph.send_step_keys()
    funnel: list[dict[str, Any]] = []
    previous_sent: int | None = None

    for index, key in enumerate(send_keys, start=1):
        stat = by_step.get(key)
        sent = stat.sent + stat.substituted if stat else 0
        replied = stat.replied if stat else 0
        entry: dict[str, Any] = {
            "step_key": key,
            "name": graph.name_of(key),
            "channel": graph.channel_of(key),
            "index": index,
            "sent": sent,
            "delivered": stat.delivered if stat else 0,
            "bounced": stat.bounced if stat else 0,
            "failed": stat.failed if stat else 0,
            "skipped": stat.skipped if stat else 0,
            "substituted": stat.substituted if stat else 0,
            "replied": replied,
            "positive_replies": stat.positive_replies if stat else 0,
            "meetings": stat.meetings if stat else 0,
            "reply_rate": round(replied / sent, 4) if sent else 0.0,
            "drop_off": (
                round(max(0.0, (previous_sent - sent) / previous_sent), 4)
                if previous_sent
                else None
            ),
        }
        if sent or stat:
            previous_sent = sent
        funnel.append(entry)

    channels: list[dict[str, Any]] = []
    for channel in c.LANES:
        rows = [row for row in stats_rows if row.channel == channel]
        sent = sum(row.sent + row.substituted for row in rows)
        delivered = sum(row.delivered for row in rows)
        replies = sum(row.replied for row in rows)
        positive = sum(row.positive_replies for row in rows)
        meetings = sum(row.meetings for row in rows)
        channels.append(
            {
                "channel": channel,
                "sent": sent,
                "delivered": delivered,
                "replies": replies,
                "reply_rate": round(replies / sent, 4) if sent else 0.0,
                "positive_rate": round(positive / replies, 4) if replies else 0.0,
                "meetings": meetings,
            }
        )

    totals = {
        "sent": sum(item["sent"] for item in funnel),
        "delivered": sum(item["delivered"] for item in funnel),
        "bounced": sum(item["bounced"] for item in funnel),
        "skipped": sum(item["skipped"] for item in funnel),
        "substituted": sum(item["substituted"] for item in funnel),
        "replied": sum(item["replied"] for item in funnel),
        "positive_replies": sum(item["positive_replies"] for item in funnel),
        "meetings": sum(item["meetings"] for item in funnel),
    }
    totals["reply_rate"] = (
        round(totals["replied"] / totals["sent"], 4) if totals["sent"] else 0.0
    )

    return {
        "sequence_id": sequence.id,
        "sequence_name": sequence.name,
        "version_id": version.id,
        "version": version.version,
        "funnel": funnel,
        "channels": channels,
        "totals": totals,
    }


def version_comparison(db: Session, *, sequence: Sequence, limit: int = 2) -> list[dict[str, Any]]:
    versions = db.scalars(
        select(SequenceVersion)
        .where(SequenceVersion.sequence_id == sequence.id)
        .order_by(SequenceVersion.version.desc())
        .limit(limit)
    ).all()

    rows: list[dict[str, Any]] = []
    for version in versions:
        stats_rows = db.scalars(
            select(SequenceStat).where(SequenceStat.sequence_version_id == version.id)
        ).all()
        sent = sum(row.sent + row.substituted for row in stats_rows)
        replies = sum(row.replied for row in stats_rows)
        rows.append(
            {
                "version": version.version,
                "version_id": version.id,
                "sent": sent,
                "replies": replies,
                "reply_rate": round(replies / sent, 4) if sent else 0.0,
                "meetings": sum(row.meetings for row in stats_rows),
                "positive_replies": sum(row.positive_replies for row in stats_rows),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Inbox health, failures, needs-attention
# ---------------------------------------------------------------------------
def inbox_health(db: Session, *, client_id: str, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or utcnow()
    inboxes = db.scalars(
        select(SenderInbox)
        .where(SenderInbox.client_id == client_id)
        .order_by(SenderInbox.email)
    ).all()

    rows: list[dict[str, Any]] = []
    for inbox in inboxes:
        warmup_days = None
        if inbox.warmup_started_at is not None:
            started = inbox.warmup_started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            warmup_days = (now - started).days
        rows.append(
            {
                "id": inbox.id,
                "email": inbox.email,
                "display_name": inbox.display_name,
                "status": inbox.status,
                "sends_today": inbox.sends_today,
                "daily_cap": inbox.daily_cap,
                "bounce_rate": inbox.bounce_rate,
                "warmup_days": warmup_days,
                "suspended": inbox.status == c.INBOX_PAUSED,
                "paused_reason": inbox.paused_reason,
            }
        )
    return rows


def failure_report(db: Session, *, client_id: str, days: int = 30) -> list[dict[str, Any]]:
    start, _ = _window(days)
    rows: dict[str, dict[str, Any]] = {}

    skipped = db.execute(
        select(Message.skip_reason, Message.channel, func.count(Message.id))
        .where(
            Message.client_id == client_id,
            Message.status == c.MESSAGE_SKIPPED,
            Message.created_at >= start,
            Message.skip_reason.is_not(None),
        )
        .group_by(Message.skip_reason, Message.channel)
    ).all()
    for reason, channel, count in skipped:
        rows[str(reason)] = {"reason": str(reason), "count": count, "channel": channel or ""}

    bounced = db.execute(
        select(Message.channel, func.count(Message.id))
        .where(
            Message.client_id == client_id,
            Message.status == c.MESSAGE_BOUNCED,
            Message.created_at >= start,
        )
        .group_by(Message.channel)
    ).all()
    for channel, count in bounced:
        entry = rows.setdefault("bounce", {"reason": "bounce", "count": 0, "channel": channel or ""})
        entry["count"] += count

    substituted = db.execute(
        select(Message.channel, func.count(Message.id))
        .where(
            Message.client_id == client_id,
            Message.is_substitution.is_(True),
            Message.created_at >= start,
        )
        .group_by(Message.channel)
    ).all()
    for channel, count in substituted:
        entry = rows.setdefault(
            "substituted", {"reason": "substituted", "count": 0, "channel": channel or ""}
        )
        entry["count"] += count

    failed = db.execute(
        select(Message.channel, func.count(Message.id))
        .where(
            Message.client_id == client_id,
            Message.status == c.MESSAGE_FAILED,
            Message.created_at >= start,
        )
        .group_by(Message.channel)
    ).all()
    for channel, count in failed:
        entry = rows.setdefault("failed", {"reason": "failed", "count": 0, "channel": channel or ""})
        entry["count"] += count

    collisions = int(
        db.scalar(
            select(func.count(Event.id)).where(
                Event.client_id == client_id,
                Event.type == c.EVENT_SEQUENCE_COLLISION,
                Event.occurred_at >= start,
            )
        )
        or 0
    )
    if collisions:
        rows["collision"] = {"reason": "collision", "count": collisions, "channel": ""}

    orphaned = int(
        db.scalar(
            select(func.count(Enrollment.id)).where(
                Enrollment.client_id == client_id,
                Enrollment.pause_reason == c.EXIT_ORPHANED_BY_VERSION,
            )
        )
        or 0
    )
    if orphaned:
        rows["orphaned_by_version"] = {
            "reason": "orphaned_by_version",
            "count": orphaned,
            "channel": "",
        }

    return sorted(rows.values(), key=lambda row: row["count"], reverse=True)


def needs_attention(
    db: Session, *, client_id: str, limit: int = 25, now: datetime | None = None
) -> list[dict[str, Any]]:
    now = now or utcnow()
    rows: list[dict[str, Any]] = []

    recent = db.scalars(
        select(InboundMessage)
        .where(
            InboundMessage.client_id == client_id,
            InboundMessage.is_auto_reply.is_(False),
            InboundMessage.received_at >= now - timedelta(days=3),
        )
        .order_by(InboundMessage.received_at.desc())
        .limit(limit)
    ).all()
    contacts = _contact_labels(db, [row.contact_id for row in recent if row.contact_id])
    for row in recent:
        severity = {
            c.SENTIMENT_POSITIVE: "info",
            c.SENTIMENT_NEUTRAL: "warn",
            c.SENTIMENT_NEGATIVE: "danger",
        }.get(row.sentiment, "info")
        rows.append(
            {
                "kind": "reply",
                "at": row.received_at,
                "contact_id": row.contact_id or "",
                "contact_label": contacts.get(row.contact_id or "", row.from_address),
                "summary": (row.body or row.subject or "")[:180],
                "sentiment": row.sentiment,
                "severity": severity,
            }
        )

    tasks = db.scalars(
        select(HumanTask)
        .where(HumanTask.client_id == client_id, HumanTask.status == c.TASK_OPEN)
        .order_by(HumanTask.created_at.desc())
        .limit(limit)
    ).all()
    task_contacts = _contact_labels(db, [task.contact_id for task in tasks])
    for task in tasks:
        rows.append(
            {
                "kind": "task",
                "at": task.created_at,
                "contact_id": task.contact_id,
                "contact_label": task_contacts.get(task.contact_id, ""),
                "summary": task.title,
                "sentiment": "",
                "severity": "warn",
            }
        )

    stalled_cutoff = now - STALLED_AFTER
    stalled = db.scalars(
        select(Enrollment)
        .where(
            Enrollment.client_id == client_id,
            Enrollment.status == c.ENROLLMENT_ACTIVE,
            Enrollment.next_run_at.is_not(None),
            Enrollment.next_run_at < stalled_cutoff,
            # Only flag enrollments whose timer is not simply claimed and in flight.
            ~Enrollment.id.in_(
                select(Timer.enrollment_id).where(
                    Timer.status.in_([c.TIMER_SCHEDULED, c.TIMER_CLAIMED])
                )
            ),
        )
        .order_by(Enrollment.next_run_at.asc())
        .limit(limit)
    ).all()
    stalled_contacts = _contact_labels(db, [row.contact_id for row in stalled])
    for enrollment in stalled:
        rows.append(
            {
                "kind": "stalled",
                "at": enrollment.next_run_at or enrollment.updated_at,
                "contact_id": enrollment.contact_id,
                "contact_label": stalled_contacts.get(enrollment.contact_id, ""),
                "summary": f"sequence stuck at {enrollment.current_step_key or '?'} — no timer claimed",
                "sentiment": "",
                "severity": "danger",
            }
        )

    rows.sort(key=lambda row: row["at"], reverse=True)
    return rows[:limit]


def _contact_labels(db: Session, contact_ids: list[str]) -> dict[str, str]:
    from app.models import Contact  # local import keeps the module's imports flat

    ids = [cid for cid in contact_ids if cid]
    if not ids:
        return {}
    from sqlalchemy import select as _select

    contacts = db.scalars(_select(Contact).where(Contact.id.in_(ids))).all()
    labels: dict[str, str] = {}
    for contact in contacts:
        name = f"{contact.first_name} {contact.last_name}".strip()
        label = name or contact.email or contact.phone_e164 or contact.id
        if contact.company:
            label = f"{label} · {contact.company}"
        labels[contact.id] = label
    return labels
