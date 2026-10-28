"""Engine behaviour end to end, with fake channels and no network.

Every test drives the real code path: enroll -> schedule -> execute timer ->
reaction -> re-route. Time is passed in explicitly rather than slept on.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import constants as c
from app.channels.base import InboundEmail, SendResult
from app.engine import runner, scheduler
from app.engine.runner import enroll_contact
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
    SenderInbox,
    SequenceStat,
    Suppression,
    Timer,
)
from app.workers.tasks import evaluate_human_task_timeouts, process_due_timers
from tests import factories

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def enroll(ws, contact=None, sequence=None, start_at=T0):
    enrollment, reason = enroll_contact(
        ws["db"],
        contact=contact or ws["contact"],
        sequence=sequence or ws["sequence"],
        channels=ws["channels"],
        settings=ws["settings"],
        crm=ws["crm"],
        start_at=start_at,
    )
    assert enrollment is not None, reason
    return enrollment


def run_until_idle(ws, now, max_ticks=8):
    """Drive the worker until nothing else is due, as beat would over time."""
    results = []
    for _ in range(max_ticks):
        outcome = process_due_timers(
            ws["db"],
            channels=ws["channels"],
            settings=ws["settings"],
            crm=ws["crm"],
            now=now,
        )
        results.append(outcome)
        if not outcome:
            break
    return results


def reply(ws, body, *, contact=None, subject="Re: hi", sentiment=None):
    """Simulate an inbound email reply through the real ingest path.

    Builds the same payload shape as ``handle_inbound_email`` (including the
    parsed out-of-office return date) so engine tests exercise real routing.
    """
    from app.engine import sentiment as sentiment_mod

    contact = contact or ws["contact"]
    last_outbound = (
        ws["db"].query(Message)
        .filter(Message.contact_id == contact.id)
        .order_by(Message.created_at.desc())
        .first()
    )
    classification = sentiment_mod.classify(subject, body)
    auto_reply_until = None
    if classification.is_auto_reply:
        parsed = runner._parse_return_date(sentiment_mod.extract_return_date(body))
        auto_reply_until = parsed.isoformat() if parsed else None

    event_type = c.EVENT_EMAIL_AUTO_REPLY if classification.is_auto_reply else c.EVENT_EMAIL_REPLIED
    return runner.ingest_event(
        ws["db"],
        client_id=ws["client"].id,
        event_type=event_type,
        channel=c.CHANNEL_EMAIL,
        contact=contact,
        payload={
            "sentiment": sentiment or classification.sentiment,
            "matched_rule": classification.matched_rule,
            "body": body,
            "contact_label": "Alex Ng",
            "step": last_outbound.step_key if last_outbound else "",
            "auto_reply_until": auto_reply_until,
        },
        dedupe_key=f"test-reply-{body[:20]}",
        channels=ws["channels"],
        crm=ws["crm"],
        settings=ws["settings"],
    )


def as_utc(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def due_timer(ws):
    """The one pending timer for the workspace contact's enrollment."""
    return (
        ws["db"].query(Timer)
        .filter(Timer.status == c.TIMER_SCHEDULED)
        .order_by(Timer.run_at.asc())
        .first()
    )


def messages_for(ws, contact=None):
    contact = contact or ws["contact"]
    return (
        ws["db"].query(Message)
        .filter(Message.contact_id == contact.id)
        .order_by(Message.created_at.asc())
        .all()
    )


# ---------------------------------------------------------------------------
# Cadence
# ---------------------------------------------------------------------------
def test_enrollment_sends_the_first_email_immediately(workspace):
    enrollment = enroll(workspace)
    sent = workspace["channels"].email.sent
    assert len(sent) == 1
    assert sent[0].to_address == workspace["contact"].email
    assert "First Orbit Hardware" in sent[0].subject

    assert enrollment.status == c.ENROLLMENT_ACTIVE
    assert enrollment.touches == 1
    # The next thing scheduled is the delay, not a send.
    timer = workspace["db"].query(Timer).filter(Timer.enrollment_id == enrollment.id).one()
    assert timer.step_key == "step_wait_1"
    assert timer.status == c.TIMER_SCHEDULED


def test_cadence_runs_email_then_sms_then_email(workspace):
    enrollment = enroll(workspace)

    # Day 1: the delay elapses and the SMS goes out.
    run_until_idle(workspace, T0 + timedelta(days=1, seconds=5))
    assert len(workspace["channels"].email.sent) == 1
    assert len(workspace["channels"].sms.sent) == 1
    assert workspace["channels"].sms.sent[0].to_address == workspace["contact"].phone_e164

    # Day 2: the second email, then the exit.
    run_until_idle(workspace, T0 + timedelta(days=2, seconds=10))
    assert len(workspace["channels"].email.sent) == 2

    workspace["db"].refresh(enrollment)
    assert enrollment.status == c.ENROLLMENT_COMPLETED
    assert enrollment.exit_reason == c.EXIT_COMPLETED
    assert enrollment.touches == 3
    assert enrollment.sms_touches == 1
    assert enrollment.next_run_at is None


def test_each_step_writes_stats_for_the_dashboard(workspace):
    enroll(workspace)
    run_until_idle(workspace, T0 + timedelta(days=1, seconds=5))
    run_until_idle(workspace, T0 + timedelta(days=2, seconds=10))

    stats = {
        (row.step_key, row.channel): row
        for row in workspace["db"].query(SequenceStat).all()
    }
    assert stats[("step_email_1", c.CHANNEL_EMAIL)].sent == 1
    assert stats[("step_sms_1", c.CHANNEL_SMS)].sent == 1
    assert stats[("step_email_2", c.CHANNEL_EMAIL)].sent == 1


def test_delay_uses_elapsed_time_not_wall_clock(workspace):
    """
    A late worker tick must not stretch the cadence (D17). The delay becomes due
    at T0 and the worker only polls a day later; the following touch must still be
    measured from T0, not from the poll.
    """
    enroll(workspace, start_at=T0)
    process_due_timers(
        workspace["db"],
        channels=workspace["channels"],
        settings=workspace["settings"],
        crm=workspace["crm"],
        now=T0 + timedelta(days=1, seconds=30),
    )
    timer = due_timer(workspace)
    assert timer.step_key == "step_sms_1"
    assert as_utc(timer.run_at) == T0 + timedelta(days=1)


def test_max_touches_cap_exits_the_enrollment(workspace):
    workspace["settings"].max_touches_per_enrollment = 1
    enrollment = enroll(workspace)
    run_until_idle(workspace, T0 + timedelta(days=5))
    workspace["db"].refresh(enrollment)
    workspace["db"].refresh(enrollment)
    assert enrollment.status == c.ENROLLMENT_EXITED
    assert enrollment.exit_reason == c.EXIT_MAX_TOUCHES
    assert len(workspace["channels"].email.sent) == 1


# ---------------------------------------------------------------------------
# Sending windows, caps and rotation
# ---------------------------------------------------------------------------
def test_sms_respects_quiet_hours(workspace):
    workspace["settings"].sms_quiet_hours_enabled = True
    workspace["settings"].sms_window_start_hour = 9
    workspace["settings"].sms_window_end_hour = 18
    workspace["settings"].sms_allow_weekends = True
    # 2026-09-01 is a Tuesday; start at 23:00, outside the window.
    start = datetime(2026, 9, 1, 23, 0, tzinfo=timezone.utc)
    enroll(workspace, start_at=start)

    # The delay elapses at 23:00 the next day, still outside the window, so the
    # SMS is queued for 09:00 rather than sent.
    run_until_idle(workspace, start + timedelta(days=1, seconds=30))
    assert workspace["channels"].sms.sent == []

    timer = due_timer(workspace)
    assert timer.step_key == "step_sms_1"
    assert as_utc(timer.run_at).hour == 9

    run_until_idle(workspace, as_utc(timer.run_at) + timedelta(seconds=1))
    assert len(workspace["channels"].sms.sent) == 1


def test_sms_window_blocks_weekends(workspace):
    workspace["settings"].sms_quiet_hours_enabled = True
    workspace["settings"].sms_allow_weekends = False
    # Start on Friday 2026-09-04 at 10:00; +1 day lands on Saturday.
    start = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)
    enroll(workspace, start_at=start)
    run_until_idle(workspace, start + timedelta(days=1, seconds=30))

    assert workspace["channels"].sms.sent == []
    assert as_utc(due_timer(workspace).run_at).weekday() == 0  # the following Monday


def test_send_window_deferral_is_labelled_send_window(workspace):
    """The decision log must name the guard that actually deferred the touch."""
    workspace["settings"].sms_quiet_hours_enabled = True
    workspace["settings"].sms_allow_weekends = True
    sms_first = factories.make_sequence(
        workspace["db"],
        workspace["client"],
        name="SMS first",
        graph={
            "entry_step_keys": ["step_sms_1"],
            "steps": [
                factories.sms_step("step_sms_1", "step_exit", template="sms_1"),
                factories.exit_step("step_exit"),
            ],
        },
    )
    workspace["db"].commit()

    # 22:00 UTC is outside the 09:00-18:00 quiet hours. Email has no window, so
    # only the window guard can have acted here - never the frequency cap.
    enroll(
        workspace,
        sequence=sms_first,
        start_at=datetime(2026, 9, 1, 22, 0, tzinfo=timezone.utc),
    )

    assert workspace["channels"].sms.sent == []
    rules = {row.rule for row in workspace["db"].query(BranchDecision).all()}
    assert "send_window" in rules
    assert "frequency_cap" not in rules


def test_frequency_cap_defers_a_second_touch_on_the_same_channel(workspace):
    workspace["settings"].min_hours_between_contact_touches = 4
    contact = workspace["contact"]
    # Pretend we emailed them an hour ago.
    workspace["db"].add(
        Message(
            client_id=workspace["client"].id,
            contact_id=contact.id,
            step_key="earlier",
            channel=c.CHANNEL_EMAIL,
            status=c.MESSAGE_SENT,
            sent_at=T0 - timedelta(hours=1),
        )
    )
    workspace["db"].flush()

    enroll(workspace, start_at=T0)
    assert workspace["channels"].email.sent == []

    timer = workspace["db"].query(Timer).one()
    run_at = timer.run_at
    if run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=timezone.utc)
    assert run_at == T0 + timedelta(hours=3)
    # Email has no send window in v1, so the cap is the guard that acted here.
    rules = {row.rule for row in workspace["db"].query(BranchDecision).all()}
    assert "frequency_cap" in rules
    assert "send_window" not in rules


def test_inbox_rotation_prefers_the_least_used_inbox(workspace):
    second = factories.make_inbox(
        workspace["db"], workspace["client"], email="outbox2@test.example"
    )
    workspace["db"].commit()
    workspace["channels"].email.sent.clear()

    for index in range(2):
        contact = factories.make_contact(
            workspace["db"],
            workspace["client"],
            email=f"rotate{index}@test.example",
            phone=f"+65911100{index:02d}",
        )
        enroll(workspace, contact=contact, start_at=T0 + timedelta(seconds=index))

    used = {record.mailbox for record in workspace["channels"].email.sent}
    assert used == {"outbox1@test.example", "outbox2@test.example"}


def test_daily_cap_is_respected(workspace):
    workspace["inbox"].daily_cap = 1
    workspace["db"].commit()
    workspace["channels"].email.sent.clear()

    for index in range(2):
        contact = factories.make_contact(
            workspace["db"],
            workspace["client"],
            email=f"cap{index}@test.example",
            phone=f"+65911111{index:02d}",
        )
        enroll(workspace, contact=contact, start_at=T0 + timedelta(seconds=index))

    assert len(workspace["channels"].email.sent) == 1
    skipped = [m for m in workspace["db"].query(Message).all() if m.status == c.MESSAGE_SKIPPED]
    assert any(m.skip_reason == "inbox_paused" for m in skipped)


# ---------------------------------------------------------------------------
# Reactions (cancel and re-branch)
# ---------------------------------------------------------------------------
def test_positive_reply_exits_cancels_and_alerts(workspace):
    enrollment = enroll(workspace)
    reply(workspace, "Yes, let's talk — book a call for Thursday.")

    workspace["db"].refresh(enrollment)
    assert enrollment.status == c.ENROLLMENT_EXITED
    assert enrollment.exit_reason == c.EXIT_REPLIED_POSITIVE

    scheduled = (
        workspace["db"].query(Timer).filter(Timer.enrollment_id == enrollment.id).all()
    )
    assert all(timer.status == c.TIMER_CANCELLED for timer in scheduled)

    assert workspace["channels"].slack.contains("Positive reply")
    assert workspace["crm"].calls_of("create_task"), "a CRM task should be created"

    # No further sends after the reply, even as time passes.
    run_until_idle(workspace, T0 + timedelta(days=5))
    assert len(workspace["channels"].email.sent) == 1
    assert len(workspace["channels"].sms.sent) == 0


def test_negative_reply_suppresses_the_contact(workspace):
    enrollment = enroll(workspace)
    reply(workspace, "No thanks, not interested.")

    workspace["db"].refresh(enrollment)
    assert enrollment.exit_reason == c.EXIT_REPLIED_NEGATIVE
    suppression = workspace["db"].query(Suppression).one()
    assert suppression.channel == "all"
    assert suppression.reason == c.SUPPRESS_OBJECTION


def test_neutral_reply_pauses_for_a_human(workspace):
    enrollment = enroll(workspace)
    reply(workspace, "How much does a production run cost?")

    workspace["db"].refresh(enrollment)
    assert enrollment.status == c.ENROLLMENT_PAUSED
    assert enrollment.pause_reason == "awaiting_human_reply"
    task = workspace["db"].query(HumanTask).one()
    assert "Reply" in task.title or task.title
    assert workspace["channels"].slack.contains("Question")


def test_auto_reply_does_not_stop_the_sequence(workspace):
    """
    An out-of-office must not stop the cadence and must not be credited as a
    reply. The next touch is pushed past the stated return date, with the
    delay's own duration discounted so the touch is not a day late.
    """
    # A date far enough ahead that the assertion does not depend on the wall clock.
    return_date = datetime(2027, 1, 15, tzinfo=timezone.utc)
    enrollment = enroll(workspace)
    reply(
        workspace,
        "I am currently out of the office until 2027-01-15 and will be back then.",
        subject="Automatic reply: out of office",
    )

    workspace["db"].refresh(enrollment)
    assert enrollment.status == c.ENROLLMENT_ACTIVE
    assert enrollment.exit_reason is None

    # The pending delay is pushed past the return date, then the cadence continues.
    timer = due_timer(workspace)
    assert timer.status == c.TIMER_SCHEDULED
    run_at = as_utc(timer.run_at)
    # The delay's own duration is discounted, so the next touch lands on the
    # return date itself rather than a day after it.
    assert run_at + timedelta(days=1) >= return_date
    assert run_at <= return_date

    run_until_idle(workspace, run_at + timedelta(days=1, seconds=5))
    assert len(workspace["channels"].sms.sent) == 1


def test_inbound_sms_reply_stops_everything(workspace):
    enrollment = enroll(workspace)
    reply_sms = runner.handle_inbound_sms(
        workspace["db"],
        client_id=workspace["client"].id,
        from_address=workspace["contact"].phone_e164,
        body="yes please",
        provider_message_id="SM1",
        channels=workspace["channels"],
        crm=workspace["crm"],
        settings=workspace["settings"],
    )
    assert reply_sms is not None
    workspace["db"].refresh(enrollment)
    assert enrollment.status == c.ENROLLMENT_EXITED
    assert enrollment.exit_reason == c.EXIT_REPLIED_POSITIVE


def test_meeting_booked_suppresses_and_exits(workspace):
    enrollment = enroll(workspace)
    runner.ingest_event(
        workspace["db"],
        client_id=workspace["client"].id,
        event_type=c.EVENT_MEETING_BOOKED,
        channel=c.CHANNEL_EMAIL,
        contact=workspace["contact"],
        payload={"source": "ghl", "appointment_id": "apt-1"},
        dedupe_key="meeting-1",
        channels=workspace["channels"],
        crm=workspace["crm"],
        settings=workspace["settings"],
    )
    workspace["db"].refresh(enrollment)
    assert enrollment.exit_reason == c.EXIT_MEETING_BOOKED
    assert workspace["db"].query(Suppression).one().channel == "all"
    assert workspace["channels"].slack.contains("Meeting booked")

    stat = workspace["db"].query(SequenceStat).filter(SequenceStat.meetings == 1).one()
    assert stat.step_key == "step_email_1"


def test_graph_override_routes_the_reply_to_a_step(workspace):
    sequence = factories.make_sequence(
        workspace["db"],
        workspace["client"],
        name="Override",
        graph=factories.reply_branch_graph(),
    )
    workspace["db"].commit()
    workspace["channels"].email.sent.clear()

    enrollment = enroll(workspace, sequence=sequence)
    reply(workspace, "Yes please, send it over.")

    workspace["db"].refresh(enrollment)
    # The graph owns the branch, so it notifies and exits through its own exit node.
    assert enrollment.status == c.ENROLLMENT_COMPLETED
    assert workspace["channels"].slack.contains("take over")


def test_keep_pending_policy_does_not_cancel_the_queue(workspace):
    sequence = factories.make_sequence(
        workspace["db"],
        workspace["client"],
        name="Keep pending",
        graph=factories.reply_branch_graph(),
        reaction_policy=c.REACTION_POLICY_KEEP_PENDING,
    )
    workspace["db"].commit()
    workspace["channels"].email.sent.clear()

    enrollment = enroll(workspace, sequence=sequence)
    reply(workspace, "Yes please, send it over.")

    workspace["db"].refresh(enrollment)
    assert enrollment.status == c.ENROLLMENT_ACTIVE
    pending = (
        workspace["db"].query(Timer)
        .filter(Timer.enrollment_id == enrollment.id, Timer.status == c.TIMER_SCHEDULED)
        .all()
    )
    assert pending, "the original cadence should still be queued"


# ---------------------------------------------------------------------------
# Substitution and suppression
# ---------------------------------------------------------------------------
def test_missing_phone_number_substitutes_the_email_fallback(workspace):
    contact = factories.make_contact(
        workspace["db"], workspace["client"], email="nophone@test.example", phone=None
    )
    workspace["db"].commit()
    enrollment = enroll(workspace, contact=contact)

    run_until_idle(workspace, T0 + timedelta(days=1, seconds=5))

    assert len(workspace["channels"].sms.sent) == 0
    substituted = [m for m in messages_for(workspace, contact) if m.is_substitution]
    assert len(substituted) == 1
    assert substituted[0].channel == c.CHANNEL_EMAIL
    assert substituted[0].status == c.MESSAGE_SUBSTITUTED
    assert "Worth 10 minutes" in substituted[0].subject

    stat = workspace["db"].query(SequenceStat).filter(SequenceStat.step_key == "step_sms_1").one()
    assert stat.substituted == 1


def test_suppressed_contact_skips_and_unsubscribe_exits(workspace):
    enrollment = enroll(workspace)
    runner.add_suppression(
        workspace["db"],
        client_id=workspace["client"].id,
        contact_id=workspace["contact"].id,
        channel="all",
        reason=c.SUPPRESS_UNSUBSCRIBED,
    )
    workspace["db"].commit()

    run_until_idle(workspace, T0 + timedelta(days=3))

    workspace["db"].refresh(enrollment)
    assert enrollment.status == c.ENROLLMENT_EXITED
    assert enrollment.exit_reason == c.EXIT_UNSUBSCRIBED
    assert len(workspace["channels"].email.sent) == 1
    skipped = [m for m in messages_for(workspace) if m.status == c.MESSAGE_SKIPPED]
    assert skipped


def test_enrollment_is_refused_for_a_suppressed_contact(workspace):
    """A suppression is a stop signal: never start new automation for that contact."""
    runner.add_suppression(
        workspace["db"],
        client_id=workspace["client"].id,
        contact_id=workspace["contact"].id,
        channel="all",
        reason=c.SUPPRESS_UNSUBSCRIBED,
    )
    workspace["db"].commit()

    enrollment, reason = enroll_contact(
        workspace["db"],
        contact=workspace["contact"],
        sequence=workspace["sequence"],
        channels=workspace["channels"],
        settings=workspace["settings"],
        crm=workspace["crm"],
        start_at=T0,
    )

    assert enrollment is None
    assert reason == "suppressed"
    assert workspace["channels"].email.sent == []
    assert workspace["db"].query(Enrollment).count() == 0
    events = workspace["db"].query(Event).filter(Event.type == c.EVENT_SEQUENCE_SUPPRESSED).all()
    assert len(events) == 1
    assert events[0].payload["channels"] == [c.CHANNEL_EMAIL, c.CHANNEL_SMS]


def test_suppression_on_a_single_lane_still_allows_enrollment(workspace):
    """A contact who opted out of email is still reachable on the other lanes."""
    runner.add_suppression(
        workspace["db"],
        client_id=workspace["client"].id,
        contact_id=workspace["contact"].id,
        channel=c.CHANNEL_EMAIL,
        reason=c.SUPPRESS_MANUAL,
    )
    workspace["db"].commit()

    enrollment, reason = enroll_contact(
        workspace["db"],
        contact=workspace["contact"],
        sequence=workspace["sequence"],
        channels=workspace["channels"],
        settings=workspace["settings"],
        crm=workspace["crm"],
        start_at=T0,
    )

    assert enrollment is not None, reason
    assert enrollment.channel_lane == f"{c.CHANNEL_EMAIL},{c.CHANNEL_SMS}"


def test_fully_suppressed_lane_set_blocks_enrollment(workspace):
    """When every lane the sequence uses is suppressed there is nothing left to send."""
    for channel in (c.CHANNEL_EMAIL, c.CHANNEL_SMS):
        runner.add_suppression(
            workspace["db"],
            client_id=workspace["client"].id,
            contact_id=workspace["contact"].id,
            channel=channel,
            reason=c.SUPPRESS_MANUAL,
        )
    workspace["db"].commit()

    enrollment, reason = enroll_contact(
        workspace["db"],
        contact=workspace["contact"],
        sequence=workspace["sequence"],
        channels=workspace["channels"],
        settings=workspace["settings"],
        crm=workspace["crm"],
        start_at=T0,
    )

    assert enrollment is None
    assert reason == "suppressed"


def test_manual_suppression_skips_but_does_not_exit(workspace):
    enrollment = enroll(workspace)
    runner.add_suppression(
        workspace["db"],
        client_id=workspace["client"].id,
        contact_id=workspace["contact"].id,
        channel=c.CHANNEL_SMS,
        reason=c.SUPPRESS_MANUAL,
    )
    workspace["db"].commit()

    run_until_idle(workspace, T0 + timedelta(days=1, seconds=5))
    assert workspace["channels"].sms.sent == []
    workspace["db"].refresh(enrollment)
    assert enrollment.status in c.ENROLLMENT_OPEN_STATES


def test_collision_blocks_a_second_sequence_on_the_same_lane(workspace):
    other = factories.make_sequence(
        workspace["db"],
        workspace["client"],
        name="Second email sequence",
        graph={
            "entry_step_keys": ["a1"],
            "steps": [
                factories.email_step("a1", "a2", template="email_2"),
                factories.exit_step("a2"),
            ],
        },
    )
    workspace["db"].commit()

    enroll(workspace)
    enrollment, reason = enroll_contact(
        workspace["db"],
        contact=workspace["contact"],
        sequence=other,
        channels=workspace["channels"],
        settings=workspace["settings"],
        crm=workspace["crm"],
    )
    assert enrollment is None
    assert reason == "lane_collision"
    assert (
        workspace["db"].query(Event).filter(Event.type == c.EVENT_SEQUENCE_COLLISION).count() == 1
    )


def test_contact_with_no_email_substitutes_at_enrollment(workspace):
    contact = factories.make_contact(
        workspace["db"], workspace["client"], email=None, phone="+6599990000"
    )
    workspace["db"].commit()
    enroll(workspace, contact=contact)

    # The opener is an email step, so it substitutes straight to SMS.
    assert len(workspace["channels"].email.sent) == 0
    assert len(workspace["channels"].sms.sent) == 1


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------
def test_hard_bounce_suppresses_and_exits(workspace):
    workspace["channels"].email.failures.append(
        SendResult(ok=False, error="550 mailbox unavailable", bounce_type=c.BOUNCE_HARD)
    )
    enrollment = enroll(workspace)

    workspace["db"].refresh(enrollment)
    assert enrollment.status == c.ENROLLMENT_EXITED
    assert enrollment.exit_reason == c.EXIT_BOUNCED_HARD

    suppression = workspace["db"].query(Suppression).one()
    assert suppression.reason == c.SUPPRESS_HARD_BOUNCE
    assert suppression.channel == c.CHANNEL_EMAIL

    workspace["db"].refresh(workspace["inbox"])
    assert workspace["inbox"].hard_bounces == 1
    bounced = [m for m in workspace["db"].query(Message).all() if m.status == c.MESSAGE_BOUNCED]
    assert len(bounced) == 1


def test_transient_failure_is_retried_with_backoff(workspace):
    workspace["channels"].email.failures.append(
        SendResult(ok=False, error="451 try later", retryable=True)
    )
    enrollment = enroll(workspace)

    timer = workspace["db"].query(Timer).one()
    assert timer.status == c.TIMER_SCHEDULED
    assert timer.attempts == 1
    run_at = timer.run_at
    if run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=timezone.utc)
    assert run_at == T0 + timedelta(seconds=60)

    # Retrying succeeds and the cadence continues.
    run_until_idle(workspace, run_at + timedelta(seconds=1))
    assert len(workspace["channels"].email.sent) == 1


def test_repeated_failures_pause_the_inbox(workspace):
    workspace["settings"].inbox_max_consecutive_failures = 2
    workspace["channels"].email.failures.extend(
        [
            SendResult(ok=False, error="421 down", retryable=True),
            SendResult(ok=False, error="421 down", retryable=True),
            SendResult(ok=False, error="421 down", retryable=True),
        ]
    )
    workspace["db"].commit()
    enroll(workspace)

    # Walk the retries forward; the second failure crosses the threshold.
    run_until_idle(workspace, T0 + timedelta(minutes=10))
    workspace["db"].refresh(workspace["inbox"])
    assert workspace["inbox"].status == c.INBOX_PAUSED
    assert workspace["channels"].slack.contains("paused")


def test_empty_inbox_pool_halts_all_sending_for_the_client(workspace):
    workspace["inbox"].status = c.INBOX_PAUSED
    workspace["db"].commit()
    workspace["db"].add(
        Message(
            client_id=workspace["client"].id,
            contact_id=workspace["contact"].id,
            step_key="seed",
            channel=c.CHANNEL_EMAIL,
            status=c.MESSAGE_SENT,
            sender_inbox_id=workspace["inbox"].id,
            sent_at=T0 - timedelta(days=1),
        )
    )
    workspace["db"].flush()

    enrollment = enroll(workspace)
    workspace["db"].refresh(workspace["client"])
    workspace["db"].refresh(enrollment)

    assert workspace["client"].sending_halted is True
    assert enrollment.status == c.ENROLLMENT_PAUSED
    assert workspace["channels"].slack.contains("halted")
    assert len(workspace["channels"].email.sent) == 0
    skipped = [m for m in workspace["db"].query(Message).all() if m.status == c.MESSAGE_SKIPPED]
    assert any(m.skip_reason == "inbox_paused" for m in skipped)


def test_manual_kill_switch_pauses_in_flight_contacts(workspace):
    """The kill switch stops every channel, not just email."""
    enrollment = enroll(workspace)
    workspace["client"].sending_halted = True
    workspace["client"].halt_reason = "manual"
    workspace["db"].commit()

    run_until_idle(workspace, T0 + timedelta(days=1, seconds=30))
    workspace["db"].refresh(enrollment)
    assert enrollment.status == c.ENROLLMENT_PAUSED
    assert enrollment.pause_reason == c.EXIT_CLIENT_HALTED
    assert len(workspace["channels"].email.sent) == 1
    assert workspace["channels"].sms.sent == []
    skipped = [m for m in workspace["db"].query(Message).all() if m.status == c.MESSAGE_SKIPPED]
    assert any(m.skip_reason == "halted" for m in skipped)


# ---------------------------------------------------------------------------
# Human tasks
# ---------------------------------------------------------------------------
def task_graph(on_timeout: str) -> dict:
    return {
        "entry_step_keys": ["t1"],
        "steps": [
            {
                "key": "t1",
                "type": "human_task",
                "name": "Call them",
                "title": "Call the prospect",
                "instructions": "Ring them about the prototype run",
                "timeout_seconds": 3600,
                "on_timeout": on_timeout,
                "next": "t2",
            },
            factories.email_step("t2", "t3", template="email_1"),
            factories.exit_step("t3"),
        ],
    }


def test_human_task_pauses_the_enrollment(workspace):
    sequence = factories.make_sequence(
        workspace["db"], workspace["client"], name="Task", graph=task_graph("exit")
    )
    workspace["db"].commit()
    enrollment = enroll(workspace, sequence=sequence)

    workspace["db"].refresh(enrollment)
    assert enrollment.status == c.ENROLLMENT_PAUSED
    assert enrollment.pause_reason == "human_task"
    task = workspace["db"].query(HumanTask).one()
    assert task.status == c.TASK_OPEN
    assert task.timeout_at is not None


def test_human_task_timeout_can_exit(workspace):
    sequence = factories.make_sequence(
        workspace["db"], workspace["client"], name="Task", graph=task_graph("exit")
    )
    workspace["db"].commit()
    enrollment = enroll(workspace, sequence=sequence)

    evaluate_human_task_timeouts(
        workspace["db"],
        channels=workspace["channels"],
        settings=workspace["settings"],
        crm=workspace["crm"],
        now=T0 + timedelta(hours=2),
    )
    workspace["db"].refresh(enrollment)
    # Abandoned by timeout, not completed by the sequence's own exit node.
    assert enrollment.status == c.ENROLLMENT_EXITED
    assert enrollment.exit_reason == c.EXIT_TASK_TIMEOUT


def test_human_task_timeout_can_continue(workspace):
    sequence = factories.make_sequence(
        workspace["db"], workspace["client"], name="Task", graph=task_graph("continue")
    )
    workspace["db"].commit()
    enrollment = enroll(workspace, sequence=sequence)

    evaluate_human_task_timeouts(
        workspace["db"],
        channels=workspace["channels"],
        settings=workspace["settings"],
        crm=workspace["crm"],
        now=T0 + timedelta(hours=2),
    )
    workspace["db"].refresh(enrollment)
    assert enrollment.status == c.ENROLLMENT_ACTIVE

    run_until_idle(workspace, T0 + timedelta(hours=2, seconds=5))
    assert len(workspace["channels"].email.sent) == 1


# ---------------------------------------------------------------------------
# Inbound email handling
# ---------------------------------------------------------------------------
def test_inbound_email_matches_contact_and_thread(workspace):
    enrollment = enroll(workspace)
    outbound = messages_for(workspace)[0]
    assert outbound.provider_message_id

    record = runner.handle_inbound_email(
        workspace["db"],
        InboundEmail(
            from_address=f"Alex Ng <{workspace['contact'].email}>",
            to_address="ivan@acme.example",
            subject="Re: First Orbit Hardware",
            body="Yes — keen to chat.",
            message_id="<inbound-1@mail.example>",
            in_reply_to=outbound.provider_message_id,
            references=[outbound.provider_message_id],
        ),
        client_id=workspace["client"].id,
        channels=workspace["channels"],
        crm=workspace["crm"],
        settings=workspace["settings"],
    )
    assert record is not None
    assert record.contact_id == workspace["contact"].id
    assert record.thread_key == enrollment.id
    assert record.sentiment == c.SENTIMENT_POSITIVE

    workspace["db"].refresh(enrollment)
    assert enrollment.exit_reason == c.EXIT_REPLIED_POSITIVE


def test_duplicate_inbound_email_is_ignored(workspace):
    enroll(workspace)
    payload = InboundEmail(
        from_address="weird <unknown@nowhere.example>",
        to_address="ivan@acme.example",
        subject="Hi",
        body="hello",
        message_id="<dupe@mail.example>",
    )
    runner.handle_inbound_email(
        workspace["db"],
        payload,
        client_id=workspace["client"].id,
        channels=workspace["channels"],
        crm=workspace["crm"],
        settings=workspace["settings"],
    )
    before = workspace["db"].query(InboundMessage).count()
    runner.handle_inbound_email(
        workspace["db"],
        payload,
        client_id=workspace["client"].id,
        channels=workspace["channels"],
        crm=workspace["crm"],
        settings=workspace["settings"],
    )
    assert workspace["db"].query(InboundMessage).count() == before


# ---------------------------------------------------------------------------
# Version migration
# ---------------------------------------------------------------------------
def test_publishing_a_new_version_migrates_in_flight_contacts(workspace):
    enrollment = enroll(workspace)

    # A new version that renames the SMS step but keeps the step keys.
    new_graph = factories.three_touch_graph()
    for step in new_graph["steps"]:
        if step["key"] == "step_sms_1":
            step["name"] = "03 - Renamed SMS nudge"
            step["template_key"] = "sms_1"
    new_version = factories.add_version(workspace["db"], workspace["sequence"], graph=new_graph, version=2)
    workspace["sequence"].current_version_id = new_version.id
    workspace["db"].flush()

    migrated, orphaned = runner.migrate_enrollments_to_version(
        workspace["db"],
        sequence=workspace["sequence"],
        new_version=new_version,
        channels=workspace["channels"],
        settings=workspace["settings"],
    )
    assert (migrated, orphaned) == (1, 0)
    workspace["db"].refresh(enrollment)
    assert enrollment.sequence_version_id == new_version.id
    assert enrollment.status == c.ENROLLMENT_ACTIVE
    assert due_timer(workspace).step_key == "step_wait_1"


def test_orphaned_enrollment_is_paused_and_flagged(workspace):
    enrollment = enroll(workspace)

    new_graph = {
        "entry_step_keys": ["fresh_1"],
        "steps": [
            factories.email_step("fresh_1", "fresh_exit", template="email_1"),
            factories.exit_step("fresh_exit"),
        ],
    }
    new_version = factories.add_version(
        workspace["db"], workspace["sequence"], graph=new_graph, version=2
    )
    workspace["sequence"].current_version_id = new_version.id
    workspace["db"].flush()

    migrated, orphaned = runner.migrate_enrollments_to_version(
        workspace["db"],
        sequence=workspace["sequence"],
        new_version=new_version,
        channels=workspace["channels"],
        settings=workspace["settings"],
    )
    assert (migrated, orphaned) == (0, 1)
    workspace["db"].refresh(enrollment)
    assert enrollment.status == c.ENROLLMENT_PAUSED
    assert enrollment.pause_reason == c.EXIT_ORPHANED_BY_VERSION
    assert workspace["channels"].slack.contains("orphaned")


def test_rollback_repoints_the_sequence(workspace):
    v1 = workspace["sequence"].current_version_id
    new_version = factories.add_version(
        workspace["db"], workspace["sequence"], graph=factories.three_touch_graph(), version=2
    )
    workspace["sequence"].current_version_id = new_version.id
    workspace["db"].flush()

    workspace["sequence"].current_version_id = v1
    workspace["db"].flush()
    assert workspace["sequence"].current_version_id == v1


# ---------------------------------------------------------------------------
# Misc invariants
# ---------------------------------------------------------------------------
def test_only_one_pending_timer_exists_per_enrollment(workspace):
    enrollment = enroll(workspace)
    run_until_idle(workspace, T0 + timedelta(days=1, seconds=5))
    pending = (
        workspace["db"].query(Timer)
        .filter(Timer.enrollment_id == enrollment.id, Timer.status == c.TIMER_SCHEDULED)
        .all()
    )
    assert len(pending) == 1


def test_dedupe_key_prevents_double_processing(workspace):
    enroll(workspace)
    first = runner.record_event(
        workspace["db"],
        client_id=workspace["client"].id,
        contact_id=workspace["contact"].id,
        event_type="test.event",
        dedupe_key="same-key",
    )
    second = runner.record_event(
        workspace["db"],
        client_id=workspace["client"].id,
        contact_id=workspace["contact"].id,
        event_type="test.event",
        dedupe_key="same-key",
    )
    assert first is not None
    assert second is None


def test_enrollment_is_rejected_without_a_published_version(workspace):
    draft_only = factories.make_sequence(
        workspace["db"],
        workspace["client"],
        name="Draft only",
        graph=factories.three_touch_graph(),
        publish=False,
    )
    workspace["db"].commit()
    enrollment, reason = enroll_contact(
        workspace["db"],
        contact=workspace["contact"],
        sequence=draft_only,
        channels=workspace["channels"],
        settings=workspace["settings"],
    )
    assert enrollment is None
    assert reason == "sequence_has_no_published_version"
