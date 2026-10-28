"""REST API coverage: auth, authoring, intake, lifecycle, analytics and webhooks."""

from __future__ import annotations

from app import constants as c
from app.api import deps
from app.models import Enrollment, Message, SequenceVersion
from tests import factories


# ---------------------------------------------------------------------------
# Auth and meta
# ---------------------------------------------------------------------------
def test_login_rejects_a_bad_password(api_client, settings):
    response = api_client.post(
        "/api/auth/login",
        json={"email": settings.bootstrap_admin_email, "password": "wrong"},
    )
    assert response.status_code == 401


def test_me_returns_the_operator(api_client, settings):
    response = api_client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["email"] == settings.bootstrap_admin_email


def test_protected_routes_require_a_token(api_client):
    api_client.headers.pop("Authorization", None)
    assert api_client.get("/api/clients").status_code == 401


def test_meta_describes_policy_and_tracking(api_client):
    body = api_client.get("/api/meta").json()
    assert body["channel_mode"] == "fake"
    assert body["crm_mode"] == "fake"
    assert body["policy"]["max_touches"] == 6
    assert body["policy"]["max_sms"] == 2
    assert body["tracking"]["open_pixel"] is False
    assert "reply" in body["tracking"]["signals"]


def test_healthz(api_client):
    assert api_client.get("/healthz").json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Clients, inboxes, templates
# ---------------------------------------------------------------------------
def test_clients_and_assets(api_client, workspace):
    client_id = workspace["client"].id
    assert api_client.get("/api/clients").json()[0]["id"] == client_id

    inboxes = api_client.get(f"/api/clients/{client_id}/inboxes").json()
    assert len(inboxes) == 1 and inboxes[0]["email"] == workspace["inbox"].email

    templates = api_client.get(f"/api/clients/{client_id}/templates").json()
    assert {row["key"] for row in templates} >= {"email_1", "sms_1"}

    numbers = api_client.get(f"/api/clients/{client_id}/phone-numbers").json()
    assert numbers[0]["e164"] == workspace["number"].e164

    personas = api_client.get(f"/api/clients/{client_id}/personas").json()
    assert personas[0]["from_name"] == "Dana Tan"


def test_pause_and_reactivate_an_inbox(api_client, workspace):
    inbox_id = workspace["inbox"].id
    paused = api_client.patch(f"/api/inboxes/{inbox_id}", json={"status": "paused"}).json()
    assert paused["status"] == c.INBOX_PAUSED

    resumed = api_client.patch(
        f"/api/inboxes/{inbox_id}", json={"status": "active", "daily_cap": 40}
    ).json()
    assert resumed["status"] == c.INBOX_ACTIVE
    assert resumed["daily_cap"] == 40

    reset = api_client.post(f"/api/inboxes/{inbox_id}/reset-counter").json()
    assert reset["sends_today"] == 0


def test_create_and_edit_a_template(api_client, workspace):
    client_id = workspace["client"].id
    created = api_client.post(
        f"/api/clients/{client_id}/templates",
        json={
            "key": "email_extra",
            "channel": "email",
            "name": "Extra",
            "subject": "Hi {{contact.first_name}}",
            "body": "Body",
        },
    )
    assert created.status_code == 201
    template_id = created.json()["id"]

    duplicate = api_client.post(
        f"/api/clients/{client_id}/templates",
        json={"key": "email_extra", "channel": "email"},
    )
    assert duplicate.status_code == 409

    patched = api_client.patch(
        f"/api/templates/{template_id}", json={"body": "New body"}
    ).json()
    assert patched["body"] == "New body"

    assert api_client.delete(f"/api/templates/{template_id}").status_code == 204


def test_kill_switch_halts_and_lifting_it_resumes(api_client, workspace):
    client_id = workspace["client"].id
    enrollment = api_client.post(
        "/api/enrollments",
        json={"contact_id": workspace["contact"].id, "sequence_id": workspace["sequence"].id},
    ).json()

    halted = api_client.patch(
        f"/api/clients/{client_id}", json={"sending_halted": True, "halt_reason": "trial pause"}
    ).json()
    assert halted["sending_halted"] is True
    assert halted["halt_reason"] == "trial pause"

    # Advance the pending step so the halt is actually exercised. The graph is
    # send -> delay -> send, so the first tick only clears the delay; the halt is
    # only reached once a *send* step is due.
    from datetime import timedelta

    from app.models import utcnow
    from app.workers.tasks import process_due_timers

    db = workspace["db"]
    # The halt was written by the API's session; drop this session's identity map
    # so the worker-side read sees it.
    db.expire_all()
    now = utcnow() + timedelta(days=1, seconds=30)
    for _ in range(5):
        process_due_timers(
            db,
            channels=deps.get_channels(),
            settings=workspace["settings"],
            crm=deps.get_crm(),
            now=now,
        )
        db.expire_all()
        if db.get(Enrollment, enrollment["id"]).status == c.ENROLLMENT_PAUSED:
            break
    paused = db.get(Enrollment, enrollment["id"])
    assert paused.status == c.ENROLLMENT_PAUSED
    assert paused.pause_reason == c.EXIT_CLIENT_HALTED

    # Release the read transaction the worker ticks opened, otherwise SQLite
    # refuses the API's write while this session still holds the lock.
    db.commit()
    api_client.patch(f"/api/clients/{client_id}", json={"sending_halted": False})
    db.expire_all()
    assert db.get(Enrollment, enrollment["id"]).status == c.ENROLLMENT_ACTIVE


# ---------------------------------------------------------------------------
# Sequences: draft, validate, publish, rollback
# ---------------------------------------------------------------------------
def test_sequence_detail_and_draft_roundtrip(api_client, workspace):
    sequence_id = workspace["sequence"].id
    detail = api_client.get(f"/api/sequences/{sequence_id}").json()
    assert detail["current_version"]["version"] == 1
    assert detail["draft_version"] is None

    graph = factories.three_touch_graph()
    graph["steps"][0]["template_key"] = "email_2"
    saved = api_client.put(
        f"/api/sequences/{sequence_id}/draft", json={"graph": graph, "notes": "tweak"}
    )
    assert saved.status_code == 200
    assert saved.json()["version"] == 2

    detail = api_client.get(f"/api/sequences/{sequence_id}").json()
    assert detail["draft_version"]["version"] == 2
    assert detail["current_version"]["version"] == 1


def test_validate_reports_blocking_issues(api_client, workspace):
    sequence_id = workspace["sequence"].id
    graph = factories.three_touch_graph()
    graph["steps"][0]["template_key"] = "missing_template"
    result = api_client.post(
        f"/api/sequences/{sequence_id}/validate", json={"graph": graph}
    ).json()
    assert result["ok"] is False
    assert any(issue["code"] == "template_unknown" for issue in result["issues"])


def test_publish_creates_a_version_and_migrates_in_flight(api_client, workspace):
    sequence_id = workspace["sequence"].id
    enrollment = api_client.post(
        "/api/enrollments",
        json={"contact_id": workspace["contact"].id, "sequence_id": sequence_id},
    ).json()

    graph = factories.three_touch_graph()
    result = api_client.post(
        f"/api/sequences/{sequence_id}/publish", json={"graph": graph, "notes": "v2"}
    ).json()
    assert result["ok"] is True
    assert result["version"]["version"] == 2
    assert result["migrated_enrollments"] == 1
    assert result["orphaned_enrollments"] == 0

    db = workspace["db"]
    db.expire_all()
    assert db.get(Enrollment, enrollment["id"]).sequence_version_id == result["version"]["id"]


def test_publish_records_a_rollback_path(api_client, workspace):
    sequence_id = workspace["sequence"].id
    graph = factories.three_touch_graph()
    published = api_client.post(
        f"/api/sequences/{sequence_id}/publish", json={"graph": graph}
    ).json()
    assert published["ok"] is True

    rolled = api_client.post(f"/api/sequences/{sequence_id}/rollback/1").json()
    assert rolled["ok"] is True
    assert rolled["version"]["version"] == 1

    versions = api_client.get(f"/api/sequences/{sequence_id}/versions").json()
    assert [row["version"] for row in versions] == [2, 1]


def test_publish_refuses_an_invalid_graph(api_client, workspace):
    sequence_id = workspace["sequence"].id
    graph = factories.three_touch_graph()
    del graph["steps"][0]["fallback"]
    result = api_client.post(
        f"/api/sequences/{sequence_id}/publish", json={"graph": graph}
    ).json()
    assert result["ok"] is False
    assert any(issue["code"] == "fallback_missing" for issue in result["issues"])


def test_new_sequence_starts_as_a_draft(api_client, workspace):
    response = api_client.post(
        f"/api/clients/{workspace['client'].id}/sequences",
        json={"name": "Brand new", "description": "test"},
    )
    assert response.status_code == 201
    detail = response.json()
    assert detail["sequence"]["status"] == c.SEQUENCE_DRAFT
    assert detail["draft_version"]["version"] == 1


def test_simulate_endpoint_returns_a_timeline(api_client, workspace):
    response = api_client.post(
        f"/api/sequences/{workspace['sequence'].id}/simulate",
        json={"contact_id": workspace["contact"].id, "hypotheticals": {"step_email_1": True}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["exited"] is True
    assert body["timeline"][0]["step_type"] == "send_email"
    # Nothing was actually sent.
    assert workspace["db"].query(Message).count() == 0


# ---------------------------------------------------------------------------
# Contacts and intake
# ---------------------------------------------------------------------------
def test_create_contact_and_reject_duplicates(api_client, workspace):
    client_id = workspace["client"].id
    created = api_client.post(
        f"/api/clients/{client_id}/contacts",
        json={"email": "new@test.example", "phone_e164": "+6590001234", "first_name": "New"},
    )
    assert created.status_code == 201

    duplicate = api_client.post(
        f"/api/clients/{client_id}/contacts", json={"email": "new@test.example"}
    )
    assert duplicate.status_code == 409


def test_create_contact_can_enroll_immediately(api_client, workspace):
    response = api_client.post(
        f"/api/clients/{workspace['client'].id}/contacts",
        json={
            "email": "enroll-me@test.example",
            "phone_e164": "+6590004321",
            "enroll_in_sequence_id": workspace["sequence"].id,
        },
    )
    assert response.status_code == 201
    assert workspace["db"].query(Enrollment).count() == 1


def test_csv_import_dry_run_then_real(api_client, workspace):
    client_id = workspace["client"].id
    csv_body = (
        "email,phone,first_name,company\n"
        "csv1@test.example,+6590000001,One,Alpha\n"
        "csv2@test.example,,Two,Beta\n"
        "not-an-email,,Bad,Gone\n"
    )
    files = {"file": ("contacts.csv", csv_body, "text/csv")}

    preview = api_client.post(
        f"/api/clients/{client_id}/contacts/import",
        params={"dry_run": True},
        files=files,
    ).json()
    assert preview["created"] == 2
    assert len(preview["rejected"]) == 1

    files = {"file": ("contacts.csv", csv_body, "text/csv")}
    committed = api_client.post(
        f"/api/clients/{client_id}/contacts/import",
        params={"enroll_sequence_id": workspace["sequence"].id},
        files=files,
    ).json()
    assert committed["created"] == 2
    assert committed["enrolled"] == 2


def test_csv_import_reports_missing_columns(api_client, workspace):
    files = {"file": ("contacts.csv", "name,company\nA,B\n", "text/csv")}
    response = api_client.post(
        f"/api/clients/{workspace['client'].id}/contacts/import", files=files
    )
    assert response.status_code == 400
    assert "missing mapped columns" in response.json()["detail"]


def test_timeline_explains_what_happened(api_client, workspace):
    contact_id = workspace["contact"].id
    api_client.post(
        "/api/enrollments",
        json={"contact_id": contact_id, "sequence_id": workspace["sequence"].id},
    )
    entries = api_client.get(f"/api/contacts/{contact_id}/timeline").json()
    kinds = {entry["kind"] for entry in entries}
    assert "message" in kinds
    assert "event" in kinds
    sent = next(entry for entry in entries if entry["kind"] == "message")
    assert sent["status"] == c.MESSAGE_SENT
    assert "Orbit Hardware" in sent["detail"]["subject"]


def test_suppression_endpoint_stops_open_enrollments(api_client, workspace):
    contact_id = workspace["contact"].id
    enrollment = api_client.post(
        "/api/enrollments",
        json={"contact_id": contact_id, "sequence_id": workspace["sequence"].id},
    ).json()

    created = api_client.post(
        f"/api/contacts/{contact_id}/suppressions",
        json={"channel": "all", "reason": "unsubscribe"},
    )
    assert created.status_code == 201

    db = workspace["db"]
    db.expire_all()
    assert db.get(Enrollment, enrollment["id"]).status == c.ENROLLMENT_EXITED

    suppression_id = created.json()["id"]
    assert (
        api_client.delete(f"/api/contacts/{contact_id}/suppressions/{suppression_id}").status_code
        == 204
    )


# ---------------------------------------------------------------------------
# Enrollment lifecycle
# ---------------------------------------------------------------------------
def test_enrollment_collision_returns_409(api_client, workspace):
    payload = {"contact_id": workspace["contact"].id, "sequence_id": workspace["sequence"].id}
    assert api_client.post("/api/enrollments", json=payload).status_code == 201
    second = api_client.post("/api/enrollments", json=payload)
    assert second.status_code == 409
    assert "lane_collision" in second.json()["detail"]


def test_pause_resume_and_exit(api_client, workspace):
    enrollment = api_client.post(
        "/api/enrollments",
        json={"contact_id": workspace["contact"].id, "sequence_id": workspace["sequence"].id},
    ).json()
    enrollment_id = enrollment["id"]

    paused = api_client.post(f"/api/enrollments/{enrollment_id}/pause", json={"reason": "manual"})
    assert paused.json()["status"] == c.ENROLLMENT_PAUSED

    resumed = api_client.post(f"/api/enrollments/{enrollment_id}/resume")
    assert resumed.json()["status"] == c.ENROLLMENT_ACTIVE

    exited = api_client.post(f"/api/enrollments/{enrollment_id}/exit", json={"reason": "not_a_fit"})
    assert exited.json()["status"] == c.ENROLLMENT_EXITED
    assert exited.json()["exit_reason"] == "not_a_fit"


def test_send_next_reschedules_the_pending_step(api_client, workspace):
    enrollment = api_client.post(
        "/api/enrollments",
        json={"contact_id": workspace["contact"].id, "sequence_id": workspace["sequence"].id},
    ).json()
    response = api_client.post(f"/api/enrollments/{enrollment['id']}/send-next")
    assert response.status_code == 200
    assert response.json()["next_run_at"] is not None


def test_skip_wait_only_applies_to_delays(api_client, workspace):
    enrollment = api_client.post(
        "/api/enrollments",
        json={"contact_id": workspace["contact"].id, "sequence_id": workspace["sequence"].id},
    ).json()

    from app.models import Timer

    timer = (
        workspace["db"].query(Timer)
        .filter(Timer.status == c.TIMER_SCHEDULED)
        .one()
    )
    assert timer.step_key == "step_wait_1"

    response = api_client.post(f"/api/enrollments/{enrollment['id']}/skip-wait")
    assert response.status_code == 200
    workspace["db"].expire_all()
    pending = workspace["db"].query(Timer).filter(Timer.status == c.TIMER_SCHEDULED).one()
    assert pending.step_key == "step_sms_1"


def test_bulk_enroll_by_tag(api_client, workspace):
    contact = workspace["contact"]
    contact.tags = ["apollo-import"]
    workspace["db"].commit()

    response = api_client.post(
        f"/api/sequences/{workspace['sequence'].id}/enroll", json={"tag": "apollo-import"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["enrolled"] == 1
    assert body["rejected"] == []


def test_enrollment_listing_filters_by_status(api_client, workspace):
    api_client.post(
        "/api/enrollments",
        json={"contact_id": workspace["contact"].id, "sequence_id": workspace["sequence"].id},
    )
    active = api_client.get(
        f"/api/clients/{workspace['client'].id}/enrollments", params={"status": "active"}
    ).json()
    assert len(active) == 1
    exited = api_client.get(
        f"/api/clients/{workspace['client'].id}/enrollments", params={"status": "exited"}
    ).json()
    assert exited == []


# ---------------------------------------------------------------------------
# Analytics endpoints
# ---------------------------------------------------------------------------
def test_analytics_endpoints(api_client, workspace):
    client_id = workspace["client"].id
    sequence_id = workspace["sequence"].id
    api_client.post(
        "/api/enrollments",
        json={"contact_id": workspace["contact"].id, "sequence_id": sequence_id},
    )

    overview = api_client.get(f"/api/clients/{client_id}/analytics/overview").json()
    assert overview["active_enrollments"] == 1

    funnel = api_client.get(f"/api/sequences/{sequence_id}/analytics").json()
    assert [step["step_key"] for step in funnel["funnel"]] == [
        "step_email_1",
        "step_sms_1",
        "step_email_2",
    ]
    assert {row["channel"] for row in funnel["channels"]} == {"email", "sms"}

    inboxes = api_client.get(f"/api/clients/{client_id}/analytics/inboxes").json()
    assert inboxes[0]["suspended"] is False

    assert isinstance(api_client.get(f"/api/clients/{client_id}/analytics/failures").json(), list)
    assert isinstance(
        api_client.get(f"/api/clients/{client_id}/analytics/needs-attention").json(), list
    )
    compare = api_client.get(f"/api/sequences/{sequence_id}/analytics/version-compare").json()
    assert compare[0]["version"] == 1


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------
def test_twilio_status_marks_the_message_delivered(api_client, workspace):
    api_client.post(
        "/api/enrollments",
        json={"contact_id": workspace["contact"].id, "sequence_id": workspace["sequence"].id},
    )
    message = (
        workspace["db"].query(Message)
        .filter(Message.channel == c.CHANNEL_EMAIL, Message.provider_message_id != "")
        .one()
    )
    response = api_client.post(
        "/api/webhooks/twilio/status",
        data={
            "MessageSid": message.provider_message_id,
            "MessageStatus": "delivered",
            "To": workspace["contact"].email,
        },
    )
    assert response.status_code == 200
    workspace["db"].expire_all()
    assert workspace["db"].get(Message, message.id).status == c.MESSAGE_DELIVERED


def test_twilio_inbound_stops_automation(api_client, workspace):
    enrollment = api_client.post(
        "/api/enrollments",
        json={"contact_id": workspace["contact"].id, "sequence_id": workspace["sequence"].id},
    ).json()

    response = api_client.post(
        "/api/webhooks/twilio/inbound",
        data={
            "From": workspace["contact"].phone_e164,
            "Body": "yes please",
            "MessageSid": "SM-inbound-1",
            "To": workspace["number"].e164,
        },
    )
    assert response.status_code == 200
    assert response.json()["matched"] is True

    workspace["db"].expire_all()
    assert workspace["db"].get(Enrollment, enrollment["id"]).status == c.ENROLLMENT_EXITED


def test_ghl_webhook_books_a_meeting(api_client, workspace):
    enrollment = api_client.post(
        "/api/enrollments",
        json={"contact_id": workspace["contact"].id, "sequence_id": workspace["sequence"].id},
    ).json()

    response = api_client.post(
        "/api/webhooks/ghl",
        json={
            "type": "appointment.created",
            "client_id": workspace["client"].id,
            "email": workspace["contact"].email,
            "appointment_id": "apt-42",
        },
    )
    assert response.status_code == 200
    assert response.json()["matched"] is True

    workspace["db"].expire_all()
    row = workspace["db"].get(Enrollment, enrollment["id"])
    assert row.exit_reason == c.EXIT_MEETING_BOOKED


def test_inbound_email_webhook_classifies_a_reply(api_client, workspace):
    enrollment = api_client.post(
        "/api/enrollments",
        json={"contact_id": workspace["contact"].id, "sequence_id": workspace["sequence"].id},
    ).json()

    response = api_client.post(
        "/api/webhooks/email/inbound",
        json={
            "client_id": workspace["client"].id,
            "from_address": workspace["contact"].email,
            "subject": "Re: hello",
            "body": "Not interested, please stop.",
            "message_id": "<wh-1@mail.example>",
        },
    )
    assert response.status_code == 200
    assert response.json()["sentiment"] == c.SENTIMENT_NEGATIVE

    workspace["db"].expire_all()
    assert workspace["db"].get(Enrollment, enrollment["id"]).status == c.ENROLLMENT_EXITED


def test_slack_action_pauses_an_enrollment(api_client, workspace):
    enrollment = api_client.post(
        "/api/enrollments",
        json={"contact_id": workspace["contact"].id, "sequence_id": workspace["sequence"].id},
    ).json()

    response = api_client.post(
        "/api/webhooks/slack/actions",
        json={"enrollment_id": enrollment["id"], "action": "pause", "user": "U123"},
    )
    assert response.status_code == 200
    workspace["db"].expire_all()
    assert workspace["db"].get(Enrollment, enrollment["id"]).status == c.ENROLLMENT_PAUSED


def test_slack_action_rejects_an_unknown_action(api_client, workspace):
    enrollment = api_client.post(
        "/api/enrollments",
        json={"contact_id": workspace["contact"].id, "sequence_id": workspace["sequence"].id},
    ).json()
    response = api_client.post(
        "/api/webhooks/slack/actions",
        json={"enrollment_id": enrollment["id"], "action": "explode"},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Seed integrity
# ---------------------------------------------------------------------------
def test_seed_creates_the_Acme_workspace(db, settings):
    from app.seed import seed_all

    ids = seed_all(db, settings)
    db.commit()

    assert ids["client_id"] and ids["sequence_id"] and ids["sequence_version_id"]
    version = db.get(SequenceVersion, ids["sequence_version_id"])
    assert version.published_at is not None
    assert len(version.graph["steps"]) >= 10

    # Idempotent: running it again changes nothing.
    again = seed_all(db, settings)
    assert again["sequence_version_id"] == ids["sequence_version_id"]
