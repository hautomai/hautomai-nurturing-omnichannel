#!/usr/bin/env python3
"""End-to-end smoke test for the cross-channel nurture app.

Boots against a *running* API (default http://localhost:8000) and drives the whole
system the way the UI does: log in, validate a graph, draft and publish a version,
enroll contacts, force the engine forward through real timers, inject reactions
(reply and meeting), and assert the resulting analytics.

It deliberately talks to the API over HTTP and runs the engine in a *separate
process* (``process_due_timers_task``), so it exercises the deployment topology
rather than a single in-process shortcut.

Usage::

    # terminal 1
    cd backend && DATABASE_URL=sqlite+pysqlite:///$(pwd)/.smoke.db \\
        AUTO_CREATE_TABLES=true SEED_ON_STARTUP=true CHANNEL_MODE=fake \\
        SECRET_KEY=... BOOTSTRAP_ADMIN_EMAIL=ops@hautomai.com \\
        BOOTSTRAP_ADMIN_PASSWORD=change-me uvicorn app.main:app --port 8000

    # terminal 2 (engine ticks run in their own process, so it needs the same DB)
    cd backend && export DATABASE_URL=sqlite+pysqlite:///$(pwd)/.smoke.db
    .venv/bin/python scripts/smoke_e2e.py --base http://localhost:8000

Exits non-zero on the first hard failure. Nothing is sent anywhere: it expects
``CHANNEL_MODE=fake`` and says so loudly if the API disagrees.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]

BASE = "http://localhost:8000"
TOKEN: str | None = None
FAILURES: list[tuple[str, str, int, Any]] = []


# ---------------------------------------------------------------------------
# Tiny HTTP helper (stdlib only, so the script needs no extra dependencies)
# ---------------------------------------------------------------------------
def call(
    method: str,
    path: str,
    body: Any = None,
    expect: tuple[int, ...] = (200, 201),
    headers: dict[str, str] | None = None,
    quiet: bool = False,
) -> tuple[Any, int]:
    payload = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(BASE + path, data=payload, method=method)
    request.add_header("Content-Type", "application/json")
    if TOKEN:
        request.add_header("Authorization", f"Bearer {TOKEN}")
    for key, value in (headers or {}).items():
        request.add_header(key, value)

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw, status = response.read().decode(), response.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read().decode(), exc.code

    try:
        data = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        data = raw

    if status not in expect:
        FAILURES.append((method, path, status, data))
        if not quiet:
            print(f"  [FAIL] {method} {path} -> {status}: {json.dumps(data)[:300]}")
    elif not quiet:
        print(f"  [ ok ] {method} {path} -> {status}")
    return data, status


def section(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


# ---------------------------------------------------------------------------
# Reads that go through the API only, so the script works against any dialect.
# ---------------------------------------------------------------------------
def outbound_messages(contact_id: str) -> list[dict[str, Any]]:
    """Messages the engine actually sent to a contact, newest first."""
    entries, _ = call("GET", f"/api/contacts/{contact_id}/timeline", quiet=True)
    return [
        entry
        for entry in entries
        if entry["kind"] == "message"
        and entry["status"] in ("sent", "delivered", "substituted")
    ]


def engine_tick(database_url: str | None) -> dict[str, int]:
    """Run one beat of the real scheduler in a separate process."""
    code = (
        "import json;"
        "from app.workers.tasks import process_due_timers_task as t;"
        "print(json.dumps(t()))"
    )
    env = dict(os.environ)
    if database_url:
        env["DATABASE_URL"] = database_url
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    if proc.returncode != 0:
        FAILURES.append(("engine", "process_due_timers_task", proc.returncode, proc.stderr[-800:]))
        print(f"  [FAIL] engine tick: {proc.stderr.strip()[-500:]}")
        return {}
    line = [row for row in proc.stdout.splitlines() if row.startswith("{")]
    return json.loads(line[-1]) if line else {}


def drive(enrollment_id: str, ticks: int, label: str, database_url: str | None) -> list[dict[str, Any]]:
    """Force the pending step due, then execute it. Returns the step history."""
    history: list[dict[str, Any]] = []
    for _ in range(ticks):
        enrollment, _ = call("GET", f"/api/enrollments/{enrollment_id}", quiet=True)
        if enrollment["status"] not in ("active", "paused"):
            break
        if not enrollment.get("current_step_key"):
            break
        call("POST", f"/api/enrollments/{enrollment_id}/send-next", quiet=True)
        outcomes = engine_tick(database_url)
        after, _ = call("GET", f"/api/enrollments/{enrollment_id}", quiet=True)
        history.append(
            {
                "step": enrollment["current_step_key"],
                "status": after["status"],
                "touches": after.get("touches"),
                "outcomes": outcomes,
            }
        )
        print(
            f"  tick {len(history):>2}: {enrollment['current_step_key']:<22} "
            f"-> status={after['status']:<8} touches={after.get('touches')} {outcomes or ''}"
        )
    print(f"  ({label}: {len(history)} step(s) executed)")
    return history


def main() -> int:
    global BASE, TOKEN

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument("--email", default=os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "ops@hautomai.com"))
    parser.add_argument("--password", default=os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "change-me"))
    parser.add_argument("--webhook-secret", default=os.environ.get("WEBHOOK_SECRET", ""))
    parser.add_argument("--ghl-secret", default=os.environ.get("GHL_WEBHOOK_SECRET", ""))
    parser.add_argument("--ticks", type=int, default=2, help="steps to force before reacting")
    parser.add_argument(
        "--timezone",
        default="Etc/UTC",
        help="contact timezone; pick one where 'now' is inside the SMS window to exercise SMS",
    )
    parser.add_argument(
        "--run-tag",
        default="",
        help="suffix that keeps runs' contacts unique; defaults to the current time",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", ""),
        help="must match the API's DATABASE_URL so engine ticks hit the same database",
    )
    args = parser.parse_args()
    BASE = args.base.rstrip("/")

    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url

    section("1. auth and runtime meta")
    token, status = call("POST", "/api/auth/login", {"email": args.email, "password": args.password})
    if status != 200:
        print("\nCannot log in. Is the API running with a known bootstrap admin?")
        return 1
    TOKEN = token["access_token"]
    me, _ = call("GET", "/api/auth/me", quiet=True)
    print(f"  user: {me['email']} ({me['role']})")
    meta, _ = call("GET", "/api/meta", quiet=True)
    if meta["channel_mode"] != "fake":
        print(f"\n  !! CHANNEL_MODE={meta['channel_mode']}: this smoke test will SEND REAL MESSAGES. Stopping.")
        return 2
    print(f"  env={meta['app_env']} channels={meta['channel_mode']} crm={meta['crm_mode']}")
    print(f"  policy: max_touches={meta['policy']['max_touches']} "
          f"min_hours={meta['policy']['min_hours_between_touches']} "
          f"sms_window={meta['policy']['sms_window']}")
    print(f"  tracking: {meta['tracking']['signals']} (open pixel={meta['tracking']['open_pixel']})")

    section("2. workspace and sequence")
    clients, _ = call("GET", "/api/clients", quiet=True)
    client = clients[0]
    print(f"  client: {client['name']} [{client['slug']}] tz={client['default_timezone']}")
    sequences, _ = call("GET", f"/api/clients/{client['id']}/sequences", quiet=True)
    sequence = sequences[0]
    print(f"  sequence: {sequence['name']} status={sequence['status']} policy={sequence['reaction_policy']}")
    detail, _ = call("GET", f"/api/sequences/{sequence['id']}", quiet=True)
    graph = detail["current_version"]["graph"]
    steps = graph.get("steps", [])
    kinds: dict[str, int] = {}
    for step in steps:
        kinds[step["type"]] = kinds.get(step["type"], 0) + 1
    print(f"  v{detail['current_version']['version']}: {len(steps)} steps {kinds}, "
          f"entry={graph.get('entry_step_keys')}")

    for label, path in (
        ("inboxes", f"/api/clients/{client['id']}/inboxes"),
        ("phone numbers", f"/api/clients/{client['id']}/phone-numbers"),
        ("templates", f"/api/clients/{client['id']}/templates"),
        ("personas", f"/api/clients/{client['id']}/personas"),
    ):
        rows, _ = call("GET", path, quiet=True)
        print(f"  {label}: {len(rows)}")

    section("3. validation, draft and publish")
    result, _ = call("POST", f"/api/sequences/{sequence['id']}/validate", quiet=True)
    print(f"  published graph valid={result['ok']} issues={len(result['issues'])}")
    for issue in result["issues"][:6]:
        print(f"    {issue['level']}: {issue['code']} {issue['message'][:80]}")

    draft_graph = json.loads(json.dumps(graph))
    draft_graph.setdefault("meta", {})["smoke"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    draft, _ = call("PUT", f"/api/sequences/{sequence['id']}/draft", {"graph": draft_graph, "notes": "smoke"}, quiet=True)
    print(f"  draft saved as version {draft['version']}")

    section("4. contacts and enrollment")
    # Contacts are unique per (client, email), so every run gets its own pair.
    # That keeps the script re-runnable and lets repeated runs build up real
    # dashboard history instead of colliding on the first insert.
    run_tag = args.run_tag or time.strftime("%m%d-%H%M%S")
    contact_a, status = call(
        "POST",
        f"/api/clients/{client['id']}/contacts",
        {
            "email": f"ada.smoke+{run_tag}@example-ae.com",
            "phone_e164": "+6591234567",
            "first_name": "Ada",
            "last_name": "Smoke",
            "company": "Analytical Engines",
            "job_title": "Head of Operations",
            "timezone": args.timezone,
            "tags": ["smoke"],
            "source": "smoke",
        },
        quiet=True,
    )
    contact_b, _ = call(
        "POST",
        f"/api/clients/{client['id']}/contacts",
        {
            "email": f"grace.smoke+{run_tag}@example-ae.com",
            "phone_e164": "+6591234568",
            "first_name": "Grace",
            "last_name": "Smoke",
            "company": "Compiler Co",
            "job_title": "VP Engineering",
            "timezone": args.timezone,
            "tags": ["smoke"],
            "source": "smoke",
        },
        quiet=True,
    )
    if status not in (200, 201) or "email" not in contact_a or "email" not in contact_b:
        print("\nCould not create the smoke contacts. Response:")
        print(f"  contact A: {json.dumps(contact_a)[:400]}")
        print(f"  contact B: {json.dumps(contact_b)[:400]}")
        return 1
    print(f"  run tag: {run_tag}")
    print(f"  contact A: {contact_a['email']} (id {contact_a['id'][:8]}...)")
    print(f"  contact B: {contact_b['email']} (id {contact_b['id'][:8]}...)")

    enrollment_a, status = call(
        "POST", "/api/enrollments", {"contact_id": contact_a["id"], "sequence_id": sequence["id"]}, quiet=True
    )
    print(f"  enrolled A: status={enrollment_a['status']} step={enrollment_a['current_step_key']}")
    _, status = call(
        "POST", "/api/enrollments", {"contact_id": contact_a["id"], "sequence_id": sequence["id"]}, expect=(409,), quiet=True
    )
    print(f"  duplicate enrollment rejected: {status == 409}")

    section("5. simulation (dry run, no side effects)")
    simulation, _ = call(
        "POST",
        f"/api/sequences/{sequence['id']}/simulate",
        {"contact_id": contact_a["id"], "hypotheticals": {}, "max_steps": 40},
        quiet=True,
    )
    print(f"  {len(simulation['timeline'])} projected steps, exited={simulation['exited']} ({simulation['exit_reason']})")
    for step in simulation["timeline"][:16]:
        channel = f":{step['channel']}" if step["channel"] else ""
        print(f"    {step['step_key']:<22} {step['step_type'] + channel:<18} {step['outcome']:<10} {step['detail'][:46]}")

    section("6. publishing a new version migrates in-flight enrollments")
    publish, _ = call(
        "POST", f"/api/sequences/{sequence['id']}/publish", {"graph": draft_graph, "notes": "smoke publish"}, quiet=True
    )
    print(f"  published v{publish['version']['version']} migrated={publish['migrated_enrollments']} "
          f"orphaned={publish['orphaned_enrollments']}")
    if publish["migrated_enrollments"] < 1:
        FAILURES.append(("publish", "migrated_enrollments", 0, publish))

    enrollment_b, _ = call(
        "POST", "/api/enrollments", {"contact_id": contact_b["id"], "sequence_id": sequence["id"]}, quiet=True
    )
    print(f"  enrolled B after publish: step={enrollment_b['current_step_key']}")

    section("7. driving the engine through real timers")
    if not args.database_url:
        print("  !! DATABASE_URL is not set, so engine ticks will use the worker's own")
        print("     default database. Pass --database-url to point them at the API's.")
    else:
        print(f"  engine database: {args.database_url}")
    print("  contact A:")
    drive(enrollment_a["id"], args.ticks, "A", args.database_url)
    before_a = len(outbound_messages(contact_a["id"]))
    print(f"  A outbound messages so far: {before_a}")
    print("  contact B:")
    drive(enrollment_b["id"], args.ticks, "B", args.database_url)
    before_b = len(outbound_messages(contact_b["id"]))
    print(f"  B outbound messages so far: {before_b}")
    if before_a < 1 or before_b < 1:
        FAILURES.append(
            ("engine", "expected an outbound message per contact", 0, {"a": before_a, "b": before_b})
        )

    section("8. reaction: prospect replies (email)")
    # The reaction has to interrupt a live enrollment, not one that already ran out.
    live, _ = call("GET", f"/api/enrollments/{enrollment_a['id']}", quiet=True)
    print(f"  enrollment A is live before the reply: {live['status']} ({live['current_step_key']})")
    if live["status"] not in ("active", "waiting", "paused"):
        FAILURES.append(
            ("reaction", "enrollment finished before the reply could interrupt it", live["status"], live)
        )

    sent = outbound_messages(contact_a["id"])
    last_detail = (sent[0]["detail"] or {}) if sent else {}
    message_id = last_detail.get("message_id", "")
    inbox_address = last_detail.get("inbox", "")
    subject = last_detail.get("subject", "")
    print(f"  replying to {message_id or '(no message id recorded)'}")
    reply, _ = call(
        "POST",
        "/api/webhooks/email/inbound",
        {
            "client_id": client["id"],
            "from_address": contact_a["email"],
            "to_address": inbox_address,
            "subject": f"Re: {subject}",
            "body": "Thanks for reaching out - this looks great. Can we book a call next week?",
            "message_id": f"<smoke-reply-{int(time.time())}@example-ae.com>",
            "in_reply_to": message_id,
            "references": [message_id] if message_id else [],
        },
        headers={"X-Webhook-Secret": args.webhook_secret} if args.webhook_secret else None,
        quiet=True,
    )
    print(f"  matched={reply.get('matched')} sentiment={reply.get('sentiment')} auto_reply={reply.get('auto_reply')}")

    after_reply, _ = call("GET", f"/api/enrollments/{enrollment_a['id']}", quiet=True)
    print(f"  enrollment A after reply: status={after_reply['status']} reason={after_reply['exit_reason']}")
    if after_reply["status"] != "exited":
        FAILURES.append(("reaction", "reply should stop the enrollment", after_reply["status"], after_reply))

    # Automation must stay stopped: a forced advance must not send anything.
    call("POST", f"/api/enrollments/{enrollment_a['id']}/send-next", expect=(409,), quiet=True)
    engine_tick(args.database_url)
    after_count = len(outbound_messages(contact_a["id"]))
    print(f"  A outbound after reply: {after_count} (was {before_a})")
    if after_count != before_a:
        FAILURES.append(("reaction", "message sent after a reply", after_count, before_a))

    section("9. reaction: meeting booked (CRM webhook)")
    meeting, _ = call(
        "POST",
        "/api/webhooks/ghl",
        {
            "client_id": client["id"],
            "type": "appointment.created",
            "email": contact_b["email"],
            "appointment_id": f"smoke-appt-{int(time.time())}",
            "stage": "booked",
        },
        headers={"X-Ghl-Signature": args.ghl_secret} if args.ghl_secret else None,
        quiet=True,
    )
    print(f"  webhook: matched={meeting.get('matched')}")
    after_meeting, _ = call("GET", f"/api/enrollments/{enrollment_b['id']}", quiet=True)
    print(f"  enrollment B after meeting: status={after_meeting['status']} reason={after_meeting['exit_reason']}")
    if after_meeting["status"] != "exited":
        FAILURES.append(("reaction", "meeting should stop the enrollment", after_meeting["status"], after_meeting))

    section("10. contact timeline")
    timeline, _ = call("GET", f"/api/contacts/{contact_a['id']}/timeline", quiet=True)
    for entry in timeline[:18]:
        print(f"  {entry['at'][:19]} {entry['kind']:<9} {entry['title'][:64]}")
    print(f"  ({len(timeline)} entries)")

    section("11. dashboard analytics")
    overview, _ = call("GET", f"/api/clients/{client['id']}/analytics/overview", quiet=True)
    print("  KPI strip:")
    print(f"    active_enrollments={overview['active_enrollments']} sent_today={overview['sent_today']}/"
          f"{overview['daily_capacity']} replies={overview['replies']} "
          f"positive={overview['positive_replies']} meetings={overview['meetings']} "
          f"reply_rate={overview['reply_rate']} live_versions={overview['live_versions']}")
    print(f"    deltas vs previous window: {json.dumps(overview.get('deltas') or {})}")
    if overview.get("sent_today", 0) < 1:
        FAILURES.append(("analytics", "overview.sent_today", overview.get("sent_today"), overview))

    analytics, _ = call("GET", f"/api/sequences/{sequence['id']}/analytics", quiet=True)
    print(f"  sequence funnel - v{analytics['version']} ({analytics['sequence_name']}), "
          f"totals={json.dumps(analytics.get('totals') or {})}")
    for step in analytics.get("funnel") or []:
        print(f"    {step['index']:>2} {step['step_key']:<22} {step['channel']:<6} "
              f"sent={step['sent']:<3} delivered={step['delivered']:<3} bounced={step['bounced']:<2} "
              f"skipped={step['skipped']:<2} subbed={step['substituted']:<2} "
              f"replied={step['replied']:<2} meetings={step['meetings']:<2} "
              f"reply_rate={step['reply_rate']} drop_off={step['drop_off']}")
    print("  channel comparison:")
    for row in analytics.get("channels") or []:
        print(f"    {row['channel']:<6} sent={row['sent']:<3} delivered={row['delivered']:<3} "
              f"replies={row['replies']:<2} reply_rate={row['reply_rate']} "
              f"positive_rate={row['positive_rate']} meetings={row['meetings']}")

    health, _ = call("GET", f"/api/clients/{client['id']}/analytics/inboxes", quiet=True)
    print(f"  sending health ({len(health)} inbox(es)):")
    for row in health[:6]:
        print(f"    {row['email']:<28} {row['status']:<12} sends={row['sends_today']}/{row['daily_cap']} "
              f"bounce_rate={row['bounce_rate']} warmup={row['warmup_days']} paused={row['paused_reason']}")
    failures, _ = call("GET", f"/api/clients/{client['id']}/analytics/failures", quiet=True)
    print(f"  failure report: {len(failures)} row(s)")
    for row in failures[:6]:
        print(f"    {row['channel'] or '-'} {row['reason']}: {row['count']}")
    attention, _ = call("GET", f"/api/clients/{client['id']}/analytics/needs-attention", quiet=True)
    print(f"  needs attention: {len(attention)} row(s)")
    for row in attention[:6]:
        print(f"    [{row['severity']:<6}] {row['kind']:<7} {row['contact_label']:<28} {row['summary'][:52]}")
    versions, _ = call(
        "GET", f"/api/sequences/{sequence['id']}/analytics/version-compare?limit=2", quiet=True
    )
    print(f"  version compare: {len(versions)} row(s)")
    for row in versions:
        print(f"    v{row['version']:<3} sent={row['sent']:<3} replies={row['replies']:<2} "
              f"reply_rate={row['reply_rate']} meetings={row['meetings']}")

    section("12. suppression")
    suppression, _ = call(
        "POST", f"/api/contacts/{contact_a['id']}/suppressions", {"channel": "all", "reason": "smoke", "note": "opt out"}, quiet=True
    )
    print(f"  suppression: {suppression['channel']}/{suppression['reason']}")
    listed, _ = call("GET", f"/api/contacts/{contact_a['id']}/suppressions", quiet=True)
    print(f"  suppressions on contact: {[row['channel'] for row in listed]}")
    if not any(row["id"] == suppression["id"] for row in listed):
        FAILURES.append(("suppression", "suppression not listed on contact", None, listed))
    # A suppressed contact must not be enrollable again.
    _, blocked = call(
        "POST",
        "/api/enrollments",
        {"contact_id": contact_a["id"], "sequence_id": sequence["id"]},
        expect=(409,),
        quiet=True,
    )
    print(f"  re-enrolling a suppressed contact rejected: {blocked == 409}")
    call("DELETE", f"/api/contacts/{contact_a['id']}/suppressions/{suppression['id']}", expect=(204,), quiet=True)

    section("result")
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s)")
        for method, path, status, payload in FAILURES:
            print(f"  {method} {path} -> {status}: {json.dumps(payload)[:300]}")
        return 1
    print("ALL END-TO-END CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
