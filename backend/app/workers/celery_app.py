"""Celery application and beat schedule.

Two periodic jobs carry the whole engine:

* ``process-due-timers`` every 20s claims and executes due timers.
* ``poll-inboxes`` every ``IMAP_POLL_INTERVAL_SECONDS`` reads replies, because
  email replies have no webhook (D16).

Both are idempotent: timers are claimed with a status transition, and inbound
messages are deduped on their Message-ID.
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import timedelta

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "cross_channel_nurture",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_queue="nurture",
    broker_connection_retry_on_startup=True,
)

celery_app.conf.beat_schedule = {
    "process-due-timers": {
        "task": "app.workers.tasks.process_due_timers",
        "schedule": timedelta(seconds=20),
    },
    "poll-inboxes": {
        "task": "app.workers.tasks.poll_inboxes",
        "schedule": timedelta(seconds=settings.imap_poll_interval_seconds),
    },
    "evaluate-human-task-timeouts": {
        "task": "app.workers.tasks.evaluate_human_task_timeouts",
        "schedule": timedelta(minutes=5),
    },
}
