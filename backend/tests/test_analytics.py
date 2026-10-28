"""Dashboard aggregations read from rollups, not from raw events."""

from __future__ import annotations

from datetime import timedelta

from app import analytics, constants as c
from tests import factories
from tests.test_engine import T0, enroll, reply, run_until_idle


def test_overview_reflects_enrollments_and_sends(workspace):
    enroll(workspace)
    data = analytics.overview(workspace["db"], client_id=workspace["client"].id)
    assert data["active_enrollments"] == 1
    assert data["sent_today"] in (0, 1)  # depends on the wall clock, both are valid
    assert data["daily_capacity"] == workspace["inbox"].daily_cap
    assert data["live_versions"] == 1
    assert set(data["deltas"]) == {"sent", "reply_rate", "meetings"}


def test_funnel_reports_every_send_step_in_order(workspace):
    enroll(workspace)
    run_until_idle(workspace, T0 + timedelta(days=1, seconds=5))
    run_until_idle(workspace, T0 + timedelta(days=2, seconds=10))

    data = analytics.sequence_analytics(workspace["db"], sequence=workspace["sequence"])
    keys = [step["step_key"] for step in data["funnel"]]
    assert keys == ["step_email_1", "step_sms_1", "step_email_2"]
    assert all(step["sent"] == 1 for step in data["funnel"])
    assert data["totals"]["sent"] == 3

    channels = {row["channel"]: row for row in data["channels"]}
    assert channels[c.CHANNEL_EMAIL]["sent"] == 2
    assert channels[c.CHANNEL_SMS]["sent"] == 1


def test_funnel_attributes_a_reply_to_the_last_touch(workspace):
    enroll(workspace)
    reply(workspace, "Yes, let's talk.")

    data = analytics.sequence_analytics(workspace["db"], sequence=workspace["sequence"])
    first = next(step for step in data["funnel"] if step["step_key"] == "step_email_1")
    assert first["replied"] == 1
    assert first["positive_replies"] == 1
    assert first["reply_rate"] == 1.0
    assert data["totals"]["replied"] == 1


def test_failure_report_groups_skips_and_substitutions(workspace):
    contact = factories.make_contact(
        workspace["db"], workspace["client"], email="nophone@test.example", phone=None
    )
    workspace["db"].commit()
    enroll(workspace, contact=contact)
    run_until_idle(workspace, T0 + timedelta(days=1, seconds=5))

    rows = analytics.failure_report(workspace["db"], client_id=workspace["client"].id)
    reasons = {row["reason"] for row in rows}
    assert "substituted" in reasons


def test_inbox_health_rows_expose_caps_and_status(workspace):
    rows = analytics.inbox_health(workspace["db"], client_id=workspace["client"].id)
    assert len(rows) == 1
    row = rows[0]
    assert row["email"] == workspace["inbox"].email
    assert row["status"] == c.INBOX_ACTIVE
    assert row["daily_cap"] == workspace["inbox"].daily_cap
    assert row["warmup_days"] is not None and row["warmup_days"] >= 29
    assert row["suspended"] is False


def test_needs_attention_surfaces_replies_and_open_tasks(workspace):
    from app.channels.base import InboundEmail
    from app.engine import runner
    from app.models import HumanTask

    enrollment = enroll(workspace)
    # Go through the inbound path so a real InboundMessage row exists, which is
    # what the feed reads.
    runner.handle_inbound_email(
        workspace["db"],
        InboundEmail(
            from_address=workspace["contact"].email,
            to_address="dana@test-client.example",
            subject="Re: hi",
            body="How much does this cost?",
            message_id="<attention-1@mail.example>",
        ),
        client_id=workspace["client"].id,
        channels=workspace["channels"],
        crm=workspace["crm"],
        settings=workspace["settings"],
    )

    workspace["db"].add(
        HumanTask(
            client_id=workspace["client"].id,
            contact_id=workspace["contact"].id,
            enrollment_id=enrollment.id,
            title="Answer the pricing question",
        )
    )
    workspace["db"].flush()

    rows = analytics.needs_attention(workspace["db"], client_id=workspace["client"].id)
    kinds = {row["kind"] for row in rows}
    assert "reply" in kinds
    assert "task" in kinds
    reply_row = next(row for row in rows if row["kind"] == "reply")
    assert reply_row["sentiment"] == c.SENTIMENT_NEUTRAL
    assert reply_row["severity"] == "warn"
    assert reply_row["contact_label"].startswith("Alex Ng")


def test_needs_attention_flags_a_stalled_enrollment(workspace):
    from app.models import Enrollment

    enrollment = enroll(workspace)
    run_until_idle(workspace, T0 + timedelta(days=1, seconds=5))

    # Simulate a lost timer: the enrollment is active but nothing is pending.
    workspace["db"].query(type(enrollment)).filter(
        type(enrollment).id == enrollment.id
    ).update({"status": c.ENROLLMENT_ACTIVE})
    from app.models import Timer

    workspace["db"].query(Timer).update({"status": c.TIMER_CANCELLED})
    workspace["db"].flush()

    rows = analytics.needs_attention(workspace["db"], client_id=workspace["client"].id)
    assert any(row["kind"] == "stalled" for row in rows)


def test_version_comparison_lists_recent_versions(workspace):
    enroll(workspace)
    factories.add_version(
        workspace["db"],
        workspace["sequence"],
        graph=factories.three_touch_graph(),
        version=2,
        publish=True,
    )
    workspace["db"].flush()

    rows = analytics.version_comparison(workspace["db"], sequence=workspace["sequence"])
    assert [row["version"] for row in rows] == [2, 1]
