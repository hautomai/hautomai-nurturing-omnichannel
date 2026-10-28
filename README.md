# Cross-Channel Nurture Sequences

Omnichannel nurture for Hautomai: coordinated email, SMS and internal Slack,
where **the next touchpoint is chosen by how the prospect reacted to the last
one**.

An "interaction" here is a reply, a silence, or a booked meeting — not an open or
a pixel. Sending runs over your own warmed SMTP inboxes, so there is no open/click
tracking to branch on and none is faked (see *Deliberate limits*).

The full agreed design lives in [`cross-channel-nurture-spec.md`](cross-channel-nurture-spec.md).
This file is how to run it.

---

## Contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Quick start (no Docker)](#quick-start-no-docker)
- [Quick start (Docker)](#quick-start-docker)
- [Configuration](#configuration)
- [Authoring a sequence](#authoring-a-sequence)
- [How the engine works](#how-the-engine-works)
- [Testing and verification](#testing-and-verification)
- [Going live with real channels](#going-live-with-real-channels)
- [Deliberate limits](#deliberate-limits)
- [Repository layout](#repository-layout)

---

## What it does

- **Visual sequence builder.** A drag-and-drop canvas of steps and branches
  (`@xyflow/react`): send email, send SMS, wait, condition, Slack alert, human
  task, exit. Steps carry a template key plus a per-step `fallback` channel.
- **Reaction-driven branching.** A reply, a meeting booked in the CRM, an opt-out
  or a hard bounce cancels the steps already scheduled and re-routes the contact
  immediately (policy `cancel_and_rebranch`).
- **Inbox rotation.** Email goes out over a pool of warmed mailboxes, rotating to
  the least-used inbox, honouring per-inbox daily caps, pausing inboxes that
  bounce too much or fail repeatedly.
- **SMS with guardrails.** SMS is only sent inside a per-contact local-time
  window (09:00–18:00, weekdays by default) with a separate per-enrollment SMS
  cap. Outside the window the touch is deferred, not dropped.
- **Versioned and safe to edit.** The published graph is immutable. Saving a
  draft creates a new version; publishing migrates in-flight contacts to it and
  reports any contact whose current step no longer exists.
- **Dry-run simulation.** Project a contact's whole path through a graph with
  hypothetical reactions, before publishing, with zero side effects.
- **Dashboard.** Campaign performance first (KPIs, step-level funnel, email-vs-SMS
  comparison, sending health, failure and skip reporting) plus a replies-and-tasks
  feed.
- **Compliance rails.** Per-contact, per-channel suppression is a stop signal:
  opting out blocks new enrollment and exits open ones. Unsubscribe text and
  List-Unsubscribe headers are handled by the templates and the routing table.

## Architecture

```
frontend/                 Vite + React + TypeScript + Tailwind + @xyflow/react
  src/theme/tokens.css     design tokens lifted from the Acme proposal deck
  src/features/dashboard/  KPI strip, funnel, channel comparison, sending health
  src/features/canvas/     builder canvas, step inspector, simulation dialog
  src/pages/               one page per route
  src/lib/api.ts           typed API client (single origin, bearer token)

backend/                  FastAPI + SQLAlchemy + Celery
  app/api/                routers: auth, clients, contacts, sequences,
                          enrollments, analytics, webhooks
  app/engine/             graph validation, conditions, sentiment, templates,
                          routing, scheduler, runner, stats, simulation
  app/channels/           base + fake (default), smtp, imap, twilio, slack
  app/crm/                base + fake, ghl (GoHighLevel)
  app/workers/            celery app, beat schedule, tasks
  app/analytics.py        every dashboard read model
  migrations/             alembic
  scripts/smoke_e2e.py    end-to-end verification against a running API
  tests/                  149 tests, fake channels, no network
```

Two periodic Celery jobs carry the engine:

| Job | Interval | Purpose |
| --- | --- | --- |
| `process_due_timers` | 20s | claims and executes due timers (idempotent) |
| `poll_inboxes` | `IMAP_POLL_INTERVAL_SECONDS` | reads replies over IMAP, refreshes inbox health |
| `evaluate_human_task_timeouts` | 5m | nags or completes stalled human tasks |

The API never sends anything inline: it writes a `Timer` row and lets the worker
pick it up, so a slow SMTP handshake cannot block a request.

## Quick start (no Docker)

Needs Python 3.11+ and Node 20+. No Postgres, no Redis — the defaults use a
single sqlite file, so `CHANNEL_MODE=fake` plus a single process is enough to
click through the whole app.

```bash
# 1. Backend
cd backend
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env                 # sqlite + fake channels + seed on startup

# Optional but recommended: start Redis so the worker and beat actually run.
# Without it the API and UI still work; only background ticks are missing.
.venv/bin/uvicorn app.main:app --reload           # http://localhost:8000

# 2. Frontend (second terminal)
cd frontend
npm install
npm run dev                                       # http://localhost:5173
```

Sign in with the bootstrap admin from `.env`
(`ops@hautomai.com` / `change-me`). On first boot the app creates the tables,
the admin user, and a seeded `Acme innovation` workspace: a publishing persona,
four outbound inboxes, phone numbers, nine templates and a published
six-touch **Hiring-Signal Nurture** sequence.

To run the engine against that seed, in further terminals:

```bash
cd backend
.venv/bin/celery -A app.workers.celery_app:celery_app worker -l info
.venv/bin/celery -A app.workers.celery_app:celery_app beat -l info
# or the Makefile shortcuts: make worker / make beat
```

`make help` lists every target.

## Quick start (Docker)

```bash
cp .env.example .env      # compose reads this one; it sets the postgres hostnames
docker compose up -d --build
docker compose --profile mail up -d     # optional: mailpit catcher on :8025
```

`api` (:8000), `web` (:5173), `worker`, `beat`, `postgres` and `redis` come up
together, with the API waiting on healthy Postgres and Redis.

## Configuration

Everything is env-driven; see the commented [`.env.example`](.env.example) (Docker)
and [`backend/.env.example`](backend/.env.example) (local). The values that change
behaviour most:

| Variable | Default | Effect |
| --- | --- | --- |
| `CHANNEL_MODE` | `fake` | `fake` records sends in-process and sends nothing. `real` uses SMTP/Twilio/Slack. |
| `CRM_MODE` | `fake` | `none` / `fake` / `ghl`. |
| `MIN_HOURS_BETWEEN_CONTACT_TOUCHES` | `4` | Per-contact, per-channel cooldown. |
| `MAX_TOUCHES_PER_ENROLLMENT` | `6` | Hard stop on total touches. |
| `MAX_SMS_PER_ENROLLMENT` | `2` | Separate SMS cap. |
| `SMS_QUIET_HOURS_ENABLED` / `SMS_WINDOW_*` / `SMS_ALLOW_WEEKENDS` | on, 9–18, off | SMS send window in the contact's timezone. |
| `DEFAULT_CONTACT_TIMEZONE` | `Asia/Singapore` | Used when a contact has no timezone of their own. |
| `DEFAULT_INBOX_DAILY_CAP` | `25` | Per-mailbox daily send cap. |
| `INBOX_BOUNCE_RATE_LIMIT` | `0.03` | Auto-pause a mailbox above this bounce rate. |
| `WEBHOOK_SECRET` / `GHL_WEBHOOK_SECRET` | empty | Verified when set. **Set them before exposing the webhooks.** |

`GET /api/meta` reports the live channel/CRM modes and the effective policy, which
is what the UI uses to show whether it is pointed at fake or real channels.

## Authoring a sequence

1. **Sequences → New** (or open the seeded one).
2. Drag steps onto the canvas. Each send step picks a template and an optional
   `fallback` channel, so a dead lane substitutes instead of stalling.
3. **Validate.** Publishing is blocked on real errors: no entry point, a missing
   template, an unreachable step, a delay shorter than the cadence floor, a
   sequence with no send step.
4. **Simulate.** Pick a contact, set hypothetical reactions, and read the
   projected timeline. Nothing is written.
5. **Publish.** A new immutable version is created and contacts in flight are
   migrated; the response reports `migrated_enrollments` and
   `orphaned_enrollments`.

Contacts arrive by CSV import, manual entry, the REST API, or the GoHighLevel
adapter. `/api/clients/{id}/contacts/import` maps columns and reports rejects per
row rather than failing the batch.

## How the engine works

The reaction table is the heart of it ([`app/engine/routing.py`](backend/app/engine/routing.py)):

| Reaction | Action |
| --- | --- |
| Positive reply | exit with `replied_positive`, notify Slack |
| Negative reply / objection | exit, suppress across channels, notify Slack |
| Any other reply | exit `replied`, notify Slack |
| Auto-reply / out-of-office | **not** a reaction — the sequence waits until the stated return date |
| Meeting booked (CRM) | exit `meeting_booked`, suppress, notify Slack |
| Unsubscribe / opt-out | exit, suppress, notify Slack |
| Hard bounce | suppress that channel, exit, pause the inbox if it keeps happening |
| No reply before timeout | configurable exit |

On a reaction, `cancel_and_rebranch` drops every pending timer for that
enrollment and routes from the reaction if the graph defines one, otherwise
exits. A reply therefore stops automation immediately rather than letting an
already-scheduled SMS fire the next morning.

Timing semantics worth knowing:

- **Delays are elapsed durations** measured from when the previous step was
  *due*, not from when a worker happened to poll. A 3-day wait is 3 days.
- **Send-time guards** are applied twice: when the next step is scheduled (so the
  dashboard shows a truthful `next_run_at`) and again at send time (so a config
  change takes effect). Whichever guard deferred a touch is named in the decision
  log — `send_window` vs `frequency_cap`.
- **Every decision is recorded.** `branch_decisions` plus the append-only `events`
  table is what the contact timeline renders.

## Testing and verification

```bash
cd backend && .venv/bin/python -m pytest        # 149 tests, no network needed
cd frontend && npm run typecheck && npm run build
```

The suite drives real code paths with fake channels: graph validation, sentiment
and auto-reply parsing, template rendering and substitution, timer claiming,
cadence and caps, inbox rotation and bounce pausing, version migration, the four
webhooks, the analytics read models and the JSON API.

`scripts/smoke_e2e.py` is the wider check: it talks to a **running** API over
HTTP *and* runs the engine in a second process, so it verifies the deployment
topology rather than an in-process shortcut. It logs in, validates and publishes
a version, enrolls two contacts, forces real timers through the engine, injects a
reply and a booked meeting, then asserts the funnel, the inbox health and the
suppression block.

```bash
# terminal 1
cd backend
export DATABASE_URL="sqlite+pysqlite:///$(pwd)/.smoke.db"
DATABASE_URL="$DATABASE_URL" AUTO_CREATE_TABLES=true SEED_ON_STARTUP=true \
  CHANNEL_MODE=fake SECRET_KEY=smoke-test-secret \
  WEBHOOK_SECRET=smoke-webhook GHL_WEBHOOK_SECRET=smoke-ghl \
  .venv/bin/uvicorn app.main:app --port 8013

# terminal 2
cd backend
.venv/bin/python scripts/smoke_e2e.py --base http://localhost:8013 \
  --webhook-secret smoke-webhook --ghl-secret smoke-ghl \
  --database-url "$DATABASE_URL"
```

It refuses to run unless the API reports `CHANNEL_MODE=fake`, so it can never
send a real message by accident. `make smoke` runs it against `localhost:8000`.

Migrations:

```bash
cd backend
alembic upgrade head                      # or: make migrate m="add something"
```

`AUTO_CREATE_TABLES=true` is a development convenience; production should run
`alembic upgrade head` instead and leave the flag off.

## Going live with real channels

1. Warm the sending inboxes and register them under **Inboxes** with their daily
   caps. The engine rotates across all `active` inboxes.
2. Fill in `SMTP_TEST_INBOX` and switch `CHANNEL_MODE=real`. Verify with the
   mailpit profile (`docker compose --profile mail up -d`, UI on
   `http://localhost:8025`) before pointing at a live domain.
3. Set `WEBHOOK_SECRET`, `GHL_WEBHOOK_SECRET` and `TWILIO_WEBHOOK_SIGNING_SECRET`,
   and point Twilio's status/inbound callbacks at `/api/webhooks/twilio/*`.
4. Fill in the Slack bot token so reactions reach the sales channel.
5. Point `CRM_MODE=ghl` at a location and confirm `/api/webhooks/ghl` receives
   appointments.
6. Send one contact through a single-step sequence and confirm the send appears in
   the mailbox, the reply shows up in the contact timeline, and the enrollment
   exits with `replied`.

## Deliberate limits

- **No open or click tracking, in v1.** Sending runs through your own SMTP
  inboxes, so neither can be observed. Branching uses three signals only:
  `reply`, `silence`, `meeting`. `GET /api/meta` reports `open_pixel: false` and
  `link_rewriting: false` so the UI never implies otherwise.
- **No AI copywriting.** Templates use `{{contact.*}}`, `{{client.*}}` and
  `{{sender.*}}` merge fields, authored by hand.
- **One active sequence per contact per channel lane.** Enrolling a busy contact
  returns `409` with `lane_collision` rather than silently stacking sequences.
- **Single internal login.** One Hautomai workspace; no self-serve signup.
  Multi-client is designed into the schema (`client_id` on every row) but the UI
  has a single workspace switcher.
- **Slack is one-way** — internal team alerts in, no inbound Slack replies as a
  branching signal.
- **Business contacts only.** No consumer consent flow, no WhatsApp.

## Repository layout

```
.
├── docker-compose.yml      api · worker · beat · web · postgres · redis (+mailpit)
├── Makefile                install test smoke migrate seed api worker beat
├── .env.example            Docker configuration
├── cross-channel-nurture-spec.md   the agreed design and every decision
├── backend/
│   ├── .env.example        local, no-Docker configuration
│   ├── alembic.ini
│   ├── migrations/
│   ├── scripts/smoke_e2e.py
│   ├── tests/
│   └── app/
└── frontend/
    ├── src/theme/tokens.css    design tokens from the Acme proposal deck
    ├── src/lib/                typed API client, hooks, formatting
    ├── src/components/         UI primitives and the app shell
    ├── src/features/           dashboard panels, sequence canvas
    └── src/pages/              one page per route
```
