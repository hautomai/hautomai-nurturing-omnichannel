"""Pre-aggregated counters.

The dashboard must never scan raw events (spec 22.3), so every engine action
bumps a ``sequence_stats`` row keyed by (version, step, channel). Upserts are
done with a read-then-write inside the existing transaction rather than
``ON CONFLICT`` so the same code path works on Postgres and SQLite.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SequenceStat

BUMPABLE = (
    "sent",
    "delivered",
    "bounced",
    "failed",
    "skipped",
    "substituted",
    "replied",
    "positive_replies",
    "meetings",
)


def bump(
    db: Session,
    *,
    client_id: str,
    sequence_id: str,
    sequence_version_id: str,
    step_key: str,
    channel: str,
    field_name: str,
    amount: int = 1,
) -> None:
    """Increment one counter, creating the row on first use."""
    if field_name not in BUMPABLE:
        raise ValueError(f"Cannot bump unknown stat field '{field_name}'")

    stat = db.scalar(
        select(SequenceStat).where(
            SequenceStat.sequence_version_id == sequence_version_id,
            SequenceStat.step_key == step_key,
            SequenceStat.channel == channel,
        )
    )
    if stat is None:
        stat = SequenceStat(
            client_id=client_id,
            sequence_id=sequence_id,
            sequence_version_id=sequence_version_id,
            step_key=step_key,
            channel=channel,
        )
        db.add(stat)
    # Column defaults only apply at INSERT time, so a brand new row reads None here.
    current = getattr(stat, field_name) or 0
    setattr(stat, field_name, current + amount)
    db.flush()


def bump_for_enrollment(
    db: Session,
    enrollment,
    *,
    step_key: str,
    channel: str,
    field_name: str,
    amount: int = 1,
) -> None:
    bump(
        db,
        client_id=enrollment.client_id,
        sequence_id=enrollment.sequence_id,
        sequence_version_id=enrollment.sequence_version_id,
        step_key=step_key,
        channel=channel,
        field_name=field_name,
        amount=amount,
    )


def bump_blocked(db: Session, enrollment, *, step_key: str, channel: str = "") -> None:
    bump_for_enrollment(
        db, enrollment, step_key=step_key, channel=channel, field_name="skipped"
    )


def reply_rates(sent: int, replied: int) -> float:
    if sent <= 0:
        return 0.0
    return round(replied / sent, 4)
