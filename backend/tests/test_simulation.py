"""Dry-run simulation writes nothing and sends nothing (spec 8.10)."""

from __future__ import annotations

from datetime import datetime, timezone

from app.engine.simulation import simulate
from app.models import Enrollment, Event, Message
from tests import factories

START = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


def test_simulation_projects_the_whole_cadence(workspace):
    outcome = simulate(
        workspace["db"],
        sequence=workspace["sequence"],
        contact=workspace["contact"],
        settings=workspace["settings"],
        start_at=START,
    )

    sends = [step for step in outcome.timeline if step.outcome == "send"]
    assert [step.channel for step in sends] == ["email", "sms", "email"]
    assert outcome.exited is True
    assert outcome.exit_reason is not None

    # Bodies are rendered against the real contact.
    assert "Alex" in sends[0].rendered_body
    assert "Orbit Hardware" in sends[0].rendered_subject


def test_simulation_writes_nothing(workspace):
    before = (
        workspace["db"].query(Message).count(),
        workspace["db"].query(Enrollment).count(),
        workspace["db"].query(Event).count(),
    )
    simulate(
        workspace["db"],
        sequence=workspace["sequence"],
        contact=workspace["contact"],
        settings=workspace["settings"],
        start_at=START,
    )
    after = (
        workspace["db"].query(Message).count(),
        workspace["db"].query(Enrollment).count(),
        workspace["db"].query(Event).count(),
    )
    assert before == after
    assert workspace["channels"].email.sent == []
    assert workspace["channels"].sms.sent == []


def test_simulation_stops_at_a_hypothetical_reply(workspace):
    outcome = simulate(
        workspace["db"],
        sequence=workspace["sequence"],
        contact=workspace["contact"],
        settings=workspace["settings"],
        hypotheticals={"step_email_1": True},
        start_at=START,
    )
    outcomes = [step.outcome for step in outcome.timeline]
    assert "reply" in outcomes
    assert outcome.exited is True
    assert outcome.exit_reason == "replied"
    assert len([step for step in outcome.timeline if step.outcome == "send"]) == 1


def test_simulation_reports_a_missing_template(workspace):
    workspace["db"].query(factories.Template).filter(
        factories.Template.key == "email_2"
    ).delete()
    workspace["db"].flush()

    outcome = simulate(
        workspace["db"],
        sequence=workspace["sequence"],
        contact=workspace["contact"],
        settings=workspace["settings"],
        start_at=START,
    )
    assert any("missing" in warning.lower() for warning in outcome.warnings)


def test_simulation_handles_a_sequence_without_a_published_version(workspace):
    draft_only = factories.make_sequence(
        workspace["db"],
        workspace["client"],
        name="Draft only",
        graph=factories.three_touch_graph(),
        publish=False,
    )
    workspace["db"].flush()
    outcome = simulate(
        workspace["db"],
        sequence=draft_only,
        contact=workspace["contact"],
        settings=workspace["settings"],
    )
    assert outcome.timeline == []
    assert any("no published version" in warning for warning in outcome.warnings)


def test_simulation_defers_sms_into_the_window_when_enabled(workspace):
    workspace["settings"].sms_quiet_hours_enabled = True
    workspace["settings"].sms_allow_weekends = True
    outcome = simulate(
        workspace["db"],
        sequence=workspace["sequence"],
        contact=workspace["contact"],
        settings=workspace["settings"],
        start_at=datetime(2026, 9, 1, 23, 0, tzinfo=timezone.utc),
    )
    sms_steps = [step for step in outcome.timeline if step.channel == "sms"]
    assert sms_steps
    assert sms_steps[0].run_at.hour == 9
    assert "window" in sms_steps[0].detail
