# Cross-Channel Nurture Sequences — Product & Technical Spec

**Short name:** `cross-channel-nurture`
**Status:** Draft for review (no code written yet)
**Author:** Buffy (interview-driven)
**Date:** 2026-09-13

---

## 1. One-line summary

An internal Hautomai tool that runs **omnichannel nurture sequences** — coordinating email, SMS, and internal Slack alerts — where the **next touchpoint is chosen by how the prospect reacted to the previous one**, replacing the single-channel, linear Gmail loop currently living in `nurture-n8n.json`.

## 2. Why this exists

### 2.1 What we have today (`nurture-n8n.json`)

The existing n8n workflow is a working proof of concept, and it is deliberately narrow:

| Step | Node | Behaviour |
| --- | --- | --- |
| 1 | Fetch Contacts from HighLevel | Pulls all contacts from GoHighLevel via OAuth |
| 2 | Validate Deal Fetch Success | Guards for a missing/empty `id`, else routes to error logging |
| 3 | Log Errors in Google Sheets | Appends `error_id` / `error` to the "error log sheet" |
| 4 | Filter Contacts with No Response | JS: keeps contacts whose `dateUpdated` is **≥ 24h old** |
| 5 | Get Most Recent Contact | JS: sorts by `dateUpdated`, keeps **only one contact** |
| 6 | Send Follow-Up Email to Contact | Gmail node, hard-coded subject, "reply **yes**" call to action |
| 7 | Wait for 24 Hours | Static 24h wait (hard-coded `amount: 42` units) |
| 8 | Retrieve Email Thread for Response | Gmail thread fetch by `threadId` |
| 9 | Check If Contact Responded with 'Yes' | String `contains "yes"` on `messages[1].snippet` |
| 10 | Notify Sales Team in Slack | Two dead-end Slack notifications (responded / no response) |

### 2.2 The gaps this app fixes

1. **Single channel.** Email only. No SMS, no cross-channel substitution, no team alerting that closes the loop.
2. **One contact per run.** Step 5 collapses the whole batch to a single most-recent contact — no real fan-out.
3. **Fixed, non-branching path.** One delay, one follow-up, then stop. No multi-touch cadence.
4. **Naive reply detection.** `contains "yes"` on a snippet, with no sentiment, no auto-reply handling, no thread matching, and no handling of "no", objections, or questions.
5. **No durable state.** Contact position in the sequence is inferred from `dateUpdated` in the CRM rather than owned by the automation.
6. **No engagement model.** Sequencing cannot depend on whether someone replied, went silent, or booked.
7. **Dead ends.** Both branches only notify Slack; nothing is created or updated back in the CRM.
8. **No visibility.** Errors go to a Google Sheet; there is no per-contact timeline to debug why something did or didn't send.

### 2.3 The bet

The next touchpoint should be **earned, not scheduled**. Email carries the substance, SMS carries urgency and confirmation, Slack carries the human handoff — and the engine decides between them per contact based on what actually happened.

## 3. Users

- **Primary user:** the Hautomai team (this is an internal tool, not a product we sell).
- **Operators** build and tune sequences in a visual canvas.
- **Sales reps** watch the Slack channel, receive reaction alerts, and take over live conversations.
- **Client-facing outcomes** are still reported manually/verbally for now; there is no client login.
- **Client entity exists in the data model** (seeded with `Acme innovation`) but there is **no client-switching UI in v1**. Multiple clients are phase 2.

## 4. Decisions log (from the interview)

Every decision below was explicitly chosen; rationale is included where it matters.

| # | Area | Decision |
| --- | --- | --- |
| D1 | Audience | Internal tool for Hautomai only. Not multi-tenant SaaS. |
| D2 | Multi-client | Only `Acme` in v1, but a `Client`/`Brand` entity owns domains, inboxes, SMS numbers, personas, and templates so more clients can be added without a rewrite. |
| D3 | Authoring | Visual drag-and-drop canvas is the primary authoring surface. |
| D4 | Stack | Python (FastAPI) backend + React (Vite) frontend + Postgres + Redis-backed worker. See §5. |
| D5 | Contacts source of truth | Own Postgres; pluggable **CRM adapters**, GoHighLevel implemented first. |
| D6 | Branch signals (intended) | Email open/no-open, link clicks, replies + sentiment, messaging read/reply, meeting booked. |
| D7 | Branch signals (actual v1) | See D15 — open/click tracking is off, so v1 branches on **reply / sentiment / silence / meeting booked**. |
| D8 | Reaction policy | **Cancel pending steps and re-branch immediately.** Latest reaction wins. |
| D9 | Channels in v1 | 1) Cold email over rotated SMTP inboxes, 2) SMS via Twilio, 3) Internal Slack alerts/actions. |
| D10 | Out of scope channels | No WhatsApp, no LinkedIn automation, no Gmail-thread/cold-hybrid split. |
| D11 | Exit rules | Prospect replies (any reply), meeting booked / deal created, unsubscribe / opt-out / hard bounce. |
| D12 | Compliance | Deferred — business contacts only for v1. Global suppression list still implemented (it falls out of the opt-out exit rule). |
| D13 | Copy generation | **Templates with merge fields only. No LLM copy.** Seed templates authored as part of this spec (§11). |
| D14 | Missing channel | **Auto-substitute** with the other channel (no phone → send the email variant; no email → send SMS). |
| D15 | Engagement tracking | **No pixels, no link rewriting.** Opens/clicks are unobservable in v1; open/click branch nodes are dropped from v1 and deferred to phase 2. |
| D16 | Detection plumbing | Provider webhooks (Twilio, Slack, GHL) + IMAP/Gmail polling for email replies. (Instantly webhooks become moot now that sending is our own SMTP — keep as an optional future adapter.) |
| D17 | Delay semantics | **Elapsed duration from the previous touch.** "Wait 3 days" = 72h after the prior step fired. |
| D18 | Delivery failures | Retry transient errors with backoff, classify hard vs soft bounces, auto-pause an inbox past a bounce threshold, alert the team. |
| D19 | Inbound SMS reply | Same as email — **stop all automation** for that contact and alert the team. |
| D20 | Contact collisions | **One active sequence per contact per channel.** Two sequences may cover different channels, never the same one. |
| D21 | Sequence versioning | **In-flight contacts migrate to the newly published version.** Pending steps are re-evaluated against the new graph. |
| D22 | Live-edit safety | Mitigated by the per-sequence **dry-run/simulation mode** (MVP item) and an immutable version history so a bad publish can be rolled back. |
| D23 | Sender identity | **One fixed sender persona per client** (name, signature, reply-to). All inboxes in a pool present the same identity. |
| D24 | Meeting signal | **GoHighLevel appointment / opportunity stage change** is the success signal. |
| D25 | Meeting detection | Webhook from GHL; external automations (including the existing n8n flow) may also POST to our REST API. |
| D26 | Seed cadence | Short, direct, one idea per touch, **5–6 touches over ~14 days**. `Acme` preset defined in §11. |
| D27 | Lead intake | CSV upload, GoHighLevel adapter, REST API/webhook, and manual add — all four. |
| D28 | Analytics | Step-level funnel, channel comparison, failure/skip reporting, reply & meeting attribution per version, per-inbox and per-domain health. |
| D29 | MVP scope | Email over rotated SMTP, SMS via Twilio, reply detection + thread matching, Slack alerts, contact timeline/event log UI, dry-run mode, the canvas + engine (both phase 1, per D30), **and the performance dashboard home (§22)**. |
| D30 | Phasing | Engine **and** canvas are both phase 1. Canvas is not deferred. |
| D31 | Deployment | Local-first in Docker Compose; production target deferred. |
| D32 | Scale target | Sized for `Acme` volumes (~1,000 contacts/month, ~500 emails/week, ~4 inboxes). Don't over-engineer; revisit later. |
| D33 | Design language | The app follows the visual system in `Acme-proposal-signed-2026-09-12.html` exactly — dark stage, Syne + IBM Plex Mono, lime `#C9FF4A` accent, 28px panels, 68px rhythm. See §21. |
| D34 | Dashboard | The app home is a **campaign-performance dashboard** (per-step funnel, channel comparison, reply and meeting rates per sequence version), not an ops queue or a sending-health page. See §22. |
| D35 | Theme scope | **One dark theme across the entire app** — canvas, timeline, tables, forms, modals. No light mode in v1, and no light "inbox preview" surface either; email/SMS previews render inside a dark device frame. |
| D36 | Layout fidelity | Borrow the proposal's **visual language**, not its fixed 1920×1080 stage. The dashboard uses a responsive app shell with the deck's tokens, so it works on a laptop. |

## 5. Stack

Chosen for the branching graph, background timers, and a reactive canvas.

| Layer | Choice | Why |
| --- | --- | --- |
| API + engine | **Python 3.12 + FastAPI** | Best fit for scheduling, email/IMAP handling, and the eventual AI/analysis steps. |
| Background jobs | **Celery (or RQ) with Redis** + DB-backed timer rows | Durable per-contact timers survive restarts; Redis broker, Postgres is the source of truth for due work. |
| Database | **PostgreSQL** | Relational event log, versioned sequences, analytics views. |
| Frontend | **React + Vite + React Flow** | React Flow is the obvious choice for a node/edge sequence canvas. |
| Styling | Tailwind CSS wired to shared CSS variables | Speed of iteration for an internal tool; tokens shared with the proposal deck. |
| Design language | Design tokens taken from `Acme-proposal-signed-2026-09-12.html` (see §21) | The tool should look like Hautomai's client-facing work: dark stage, Syne + IBM Plex Mono, lime accent. |
| Charts | Recharts, styled with the same tokens | Funnel bars, channel comparisons and health gauges without a heavyweight chart dependency. |
| Email send | `smtplib` / `aiosmtplib` over rotated inboxes | Per D9/D15 — we own sending and do our own rotation and throttling. |
| Email read | IMAP (`imap-tools`) with an optional Gmail API adapter | Reply detection and thread matching. |
| SMS | **Twilio** | Send + delivery status webhooks; Singapore-capable numbers. |
| Team alerts | **Slack Web API** + interactive actions | Reaction alerts, human handoff, approve/pause actions. |
| CRM | **GoHighLevel adapter** behind a `CRMAdapter` interface | First implementation; HubSpot/Pipedrive stubbed. |
| Local runtime | **Docker Compose**: api, worker, beat/scheduler, postgres, redis, web | Single `docker compose up` dev loop. |

## 6. Architecture

```
┌──────────────────────────┐        ┌───────────────────────────────┐
│  React + React Flow UI   │◄──────►│  FastAPI  (REST + auth)       │
│  canvas, contacts,       │        │                               │
│  timeline, analytics     │        └──────────────┬────────────────┘
└──────────────────────────┘                       │
                                     ┌─────────────▼─────────────┐
                                     │  Postgres                 │
                                     │  contacts, sequences,     │
                                     │  enrollments, events,     │
                                     │  messages, timers         │
                                     └─────────────▲─────────────┘
                                                   │
                    ┌──────────────────────────────┴──────────────────────────┐
                    │  Nurture Engine Worker  (Celery beat + task workers)     │
                    │  • claims due timers      • runs branch rules            │
                    │  • renders templates      • writes messages + events     │
                    │  • per-inbox rate limiting / rotation                    │
                    └───────┬──────────────┬───────────────┬──────────────────┘
                            │              │               │
                    ┌───────▼─────┐ ┌──────▼──────┐ ┌──────▼───────┐
                    │ SMTP send   │ │ Twilio SMS  │ │ Slack API    │
                    │ (inbox pool)│ │             │ │ alerts       │
                    └─────────────┘ └─────────────┘ └──────────────┘
                            ▲              ▲               ▲
                            │              │               │
                    ┌───────┴──────────────┴───────────────┴──────────────────┐
                    │  Ingest: webhooks (Twilio/Slack/GHL) + IMAP poller       │
                    │  → normalize → dedupe → append event → wake engine       │
                    └──────────────────────────────────────────────────────────┘
```

Key property: **every decision is data**. The worker does not hold sequence state in memory; `enrollments.current_step_id` + `timers` rows + the immutable sequence version fully determine the next action, so a restart or a redeploy is safe.

## 7. Data model (v1)

Field lists are indicative, not final DDL.

**`clients`** — `id`, `name`, `slug`, `created_at`. Seeded with `Acme innovation`.

**`sender_personas`** — `id`, `client_id`, `display_name`, `from_name`, `from_email`, `reply_to`, `signature_html`, `calendar_link`, `is_default`.

**`sender_inboxes`** — `id`, `client_id`, `email`, `smtp_host/port/user`, `imap_host/port`, `secret_ref`, `daily_cap` (default 25), `sends_today`, `status` (`warming|active|paused`), `bounce_rate`, `last_bounce_at`, `warmup_started_at`.
`UNIQUE(client_id, email)`.

**`phone_numbers`** — `id`, `client_id`, `e164`, `provider` (`twilio`), `daily_cap`, `sends_today`, `status`.

**`contacts`** — `id`, `client_id`, `email`, `phone_e164`, `first_name`, `last_name`, `company`, `job_title`, `timezone`, `source` (`csv|ghl|api|manual`), `ghl_contact_id`, `created_at`, `updated_at`, plus arbitrary `custom_fields JSONB`.
`UNIQUE(client_id, lower(email))` and `UNIQUE(client_id, phone_e164)` where not null.

**`suppression`** — `id`, `client_id`, `contact_id`, `channel` (`email|sms|all`), `reason` (`unsubscribe|hard_bounce|opt_out|manual|complaint`), `created_at`. Checked before **every** send.

**`sequences`** — `id`, `client_id`, `name`, `description`, `status` (`draft|active|paused|archived`), `current_version_id`, `reaction_policy` (default `cancel_and_rebranch`), `cadence_preset`, `created_by`, timestamps.

**`sequence_versions`** — `id`, `sequence_id`, `version` (int, monotonic), `graph JSONB`, `created_at`, `published_at`, `notes`. **Immutable once published.**

**`enrollments`** — `id`, `contact_id`, `sequence_id`, `sequence_version_id`, `status` (`active|waiting|paused|completed|exited`), `current_step_id`, `next_run_at`, `exit_reason`, `entered_at`, `last_event_at`.
`UNIQUE (contact_id, sequence_id, channel_lane)` enforced for active rows — see §8.5.

**`timers`** — `id`, `enrollment_id`, `step_id`, `run_at`, `status` (`scheduled|claimed|done|cancelled|failed`), `claimed_by`, `claimed_at`, `attempts`, `last_error`.
Index on `(status, run_at)` — this is the engine's work queue.

**`messages`** — outbound log: `id`, `enrollment_id`, `step_id`, `contact_id`, `channel`, `direction` (`outbound`), `sender_inbox_id`/`phone_number_id`, `subject`, `body`, `provider_message_id`, `thread_key`, `status` (`queued|sent|delivered|bounced|failed|skipped`), `skip_reason`, `bounce_type` (`hard|soft|none`), `sent_at`.

**`inbound_messages`** — `id`, `contact_id`, `channel`, `from_address`, `subject`, `body`, `thread_key`, `in_reply_to`, `sentiment` (`positive|negative|neutral|auto_reply`), `is_auto_reply`, `received_at`, `raw JSONB`.

**`events`** — the append-only spine: `id`, `contact_id`, `enrollment_id`, `type`, `channel`, `occurred_at`, `payload JSONB`, `dedupe_key UNIQUE`, `created_at`. Every send, reaction, branch decision, skip, substitution, and exit is one row. This is what the contact timeline renders.

**`branch_decisions`** — `id`, `event_id`, `enrollment_id`, `step_id`, `rule_matched`, `outcome`, `created_at`. Makes "why did this contact go left?" answerable without log spelunking.

**`sequence_stats`** — rollup table or materialized view per `(sequence_version_id, step_id, channel)`: `sent`, `delivered`, `bounced`, `replied`, `positive_replies`, `meetings`, `skipped`, `substituted`.

## 8. Sequence model & engine semantics

### 8.1 Graph node types (v1)

| Node | Fields | Notes |
| --- | --- | --- |
| `enroll` / trigger | entry conditions | Sequences can be triggered manually, by CSV import, by API, or by a GHL tag/opportunity change. |
| `send_email` | template ref, subject, persona override, fallback (`send_sms\|skip\|halt`) | Renders templates, picks an inbox from the pool, honors caps. |
| `send_sms` | template ref, fallback (`send_email\|skip\|halt`) | ≤ 1 segment preferred; link-aware. |
| `delay` | `{amount, unit}` | **Elapsed** time from previous touch (D17). |
| `condition` | predicate | v1 predicates: `replied`, `reply_sentiment`, `no_reply_for(days)`, `meeting_booked`, `channel_available(email\|sms)`, `has_tag`, `custom_field_equals`. |
| `notify_slack` | channel, template, actions | Can carry interactive buttons (pause, mark-not-fit, hand off). |
| `human_task` | assignee, instructions, timeout | Freezes the contact as `paused` until a rep completes or the timeout fires. |
| `exit` | reason | Terminal. |

Explicitly **deferred**: `tracked_open`, `tracked_click`, `send_whatsapp`, `linkedin_task`, `webhook_http` (custom code) — candidates for phase 2.

### 8.2 Reaction policy

Default and only v1 policy: **cancel-and-re-branch** (D8).

When a reaction event arrives for an active enrollment:

1. Dedupe on `events.dedupe_key`.
2. Append the event.
3. Evaluate the current step's outgoing condition edges **against the new event, immediately**.
4. Cancel every `timers` row for that enrollment with `status='scheduled'`.
5. If a rule matched → set `current_step_id` to the target, schedule its next timer from `now`.
6. If nothing matched and the event is non-routing (e.g. a Slack delivery receipt) → reschedule the pending timer unchanged.
7. If the event is an exit condition → exit the enrollment, set `exit_reason`, cancel timers, alert if configured.

Because steps are cancelled, a sequence graph must be authored so that **every step has a defined behaviour for "reaction arrives while I am the current step"** — this is enforced at publish time (see §8.7).

### 8.3 Reaction → routing table (v1)

| Event | Detection | Default routing |
| --- | --- | --- |
| `email.replied` (positive sentiment) | IMAP poll, LLM-free heuristic classifier + keyword rules | Cancel pending → `notify_slack` high priority → GHL: create task/opportunity → `exit(replied_positive)` |
| `email.replied` (negative sentiment) | same | Cancel pending → `notify_slack` → `suppression.add(contact, reason='objection')` → `exit(replied_negative)` |
| `email.replied` (neutral / question) | same | Cancel pending → `notify_slack` with the body → `paused(human_task)` |
| `email.auto_reply` | `Auto-Submitted: auto-replied`, `X-Autoreply`, subject patterns | **Does not stop the sequence.** If a return date is parseable, delay the next touch until after it; otherwise continue the cadence. |
| `sms.replied` | Twilio inbound webhook | Same as an email reply: cancel pending, alert the team, `exit(replied)` |
| `sms.undelivered` / `sms.failed` | Twilio status callback | Auto-substitute per D14 if an email step can cover it; log a `skip` if not; count against number health |
| `meeting.booked` | GHL appointment/opportunity webhook | Cancel pending → `notify_slack` → `suppression.add(contact,'all')` → `exit(meeting_booked)` |
| `email.bounced(hard)` | provider/DSN classification on send or inbound DSN | Suppress email → `exit(bounced_hard)` → inbox bounce counter++ |
| `unsubscribe` | opt-out keyword / link (manual in v1) | Global (client-scoped) suppression → `exit(unsubscribed)` |
| `slack.action` | Slack interactive payload | Rep-driven: `pause`, `mark_not_fit`, `hand_off`, `send_next_step` |

**Sentiment classification without an LLM:** keyword/rule engine — affirmative set (`yes`, `sure`, `send`, `interested`, `book`, `call me`), negative set (`no thanks`, `not interested`, `remove`, `stop`, `unsubscribe`), question detection (`?`, interrogatives), and auto-reply header detection. Regexes live in config so they can be tuned without a deploy. The old n8n `contains "yes"` check is the degenerate case of this and is replaced.

### 8.4 Exit and stop rules

Enforced in priority order:

1. `unsubscribe` / `opt_out` / `hard_bounce` / `complaint` → immediate, cross-channel for that client.
2. `meeting.booked` / GHL opportunity created → success exit.
3. Any inbound reply (email or SMS) → automation stops; `paused` or `exited` depending on sentiment.
4. `human_task` timeout → configurable (`exit` or `continue`).
5. Sequence graph reaches an `exit` node → `completed`.

**Recommended additions (see §16):** a max-touch cap and a "no reply after final step → long-term nurture" timeout were considered during the interview but not selected. They are cheap to add and protect deliverability, so they should be confirmed before build.

### 8.5 Contact collisions

- Constraint: **one `active` enrollment per `(contact_id, channel_lane)`** (D20), where `channel_lane` is `email` or `sms`. Concurrent new enrollment is rejected, logged as a `collision` event, and surfaced in the UI.
- Contacts may therefore run one email sequence and one SMS sequence at once. When a sequence uses both channels it owns both lanes.
- No priority preemption in v1.

### 8.6 Versioning and in-flight contacts

- Publishing writes a new immutable `sequence_versions` row and points `sequences.current_version_id` at it.
- On publish, a migration job runs for every active enrollment: `sequence_version_id` is updated and `pending` steps are re-evaluated against the new graph. If the current `step_id` no longer exists in the new version, the enrollment re-anchors to the new version's nearest equivalent step (matched by a `step_key` stable across versions) or pauses with `exit_reason='orphaned_by_version'` and a Slack alert.
- Rollback = point `current_version_id` back and re-run the same migration job.
- **`step_key` is therefore mandatory** for every node: a stable string identifier so nodes survive editing.

### 8.7 Publish-time validation (blocking errors)

The canvas cannot publish a version that:

- has a node unreachable from an entry point, or an entry point with no path to `exit`;
- has a `send_*` step with no defined behaviour for an in-flight reaction;
- has a template with an unresolved merge field;
- has a cycle without an explicit max-iteration guard;
- references an archived template, a paused inbox pool with no active inbox, or a persona from another client;
- exceeds configured cadence caps (e.g. > 2 SMS to the same contact, > 6 total touches) without an explicit override.

### 8.8 Timing, rotation and throttling

- `delay` = elapsed duration from the previous touch firing (D17). No timezone snapping, no business-day logic, **no quiet hours** in v1 (compliance deferred — flagged in §16 because SMS especially wants it).
- Inbox rotation: pick the eligible inbox (status `active`, `sends_today < daily_cap`) with the lowest `sends_today`, tie-broken round-robin. Default cap **25/inbox/day** (matches the Acme proposal: 4 inboxes → ~100/day).
- Twilio number rotation uses the same pattern.
- Global per-contact rate limit of one outbound message per channel per **4 hours** (hard-coded in v1, tunable in settings).
- Worker claims due timers with `SELECT … FOR UPDATE SKIP LOCKED` over `(status='scheduled', run_at <= now())`, bounded batch size.

### 8.9 Failure handling (D18)

- **Transient** (SMTP 4xx, network, provider 429) → retry with exponential backoff (1m, 5m, 15m), max 3 attempts, timers stay `scheduled` with `attempts` incremented.
- **Hard bounce** (SMTP 5xx permanent / DSN `5.x.x`) → contact suppressed on email; inbox `bounce_rate` updated.
- **Soft bounce** → retry, then treat as skipped.
- **Inbox health:** if an inbox exceeds 3% hard-bounce rate over a rolling 100 sends, or 3 consecutive failures, set `status='paused'`, remove it from rotation, and post a Slack alert. If the pool drops to zero active inboxes, **halt all email sends for that client** and alert loudly.
- Every failure writes an `events` row and appears in failure/skip reporting (D28).

**Recommended addition:** an explicit account/client-level kill switch was not selected in the interview. Given "auto-pause the pool at zero active inboxes" already exists, a manual equivalent is a small addition and should be confirmed.

### 8.10 Dry-run / simulation

MVP item (D29). Given a contact (or a synthetic contact) and a sequence version, the simulator:

- walks the graph, resolving conditions against the contact's real event history where possible and against user-supplied hypothetical reactions otherwise;
- prints the projected timeline (touch N, channel, send time, which inbox, rendered body, branch taken at each condition);
- writes **nothing** except a `dry_run` audit row; sends nothing.

The same simulator doubles as the preview used to validate templates before publish.

## 9. Channels

### 9.1 Email (outbound, own SMTP pool)

- Multiple warmed inboxes per client, per-inbox daily caps, lowest-volume rotation.
- Send over SMTP with the client's persona as `From` and a fixed `Reply-To`.
- Reply detection over IMAP: poll each inbox on a short interval (default 2 min), match inbound to a contact by `From` address and thread by `Message-ID` / `In-Reply-To` / `References`.
- The generated `Message-ID` and a per-enrollment `thread_key` are stored on the outbound `messages` row so follow-ups stay in-thread and replies map back reliably.
- **No open pixel, no link rewriting** (D15). Consequence, stated plainly: `email.opened` and `email.clicked` events **do not exist** in v1, so "opened but didn't reply" is indistinguishable from silence. Branch nodes for those signals are removed from v1.

### 9.2 SMS (Twilio)

- One or more Twilio numbers per client, each with a daily cap and rotation.
- Templates rendered and truncated to a safe length; a warning (not a hard failure) if over 2 segments.
- Status callbacks update `messages.status` and raise `sms.delivered` / `sms.undelivered` / `sms.failed`.
- Inbound SMS webhook creates `inbound_messages`, and per D19 **stops all automation for the contact** and alerts the team.
- Missing phone number at an SMS step → **auto-substitute** with the email fallback variant (D14). Missing email at an email step → substitute SMS.

### 9.3 Slack (internal)

- Channels per client; message templates for: positive reply, neutral/question reply, negative reply, meeting booked, bounce/failure, inbox pool degraded, human task assigned.
- Interactive actions: `Pause sequence`, `Mark not a fit`, `Hand off to rep`, `Send next step now`.
- Slack replies from a rep are recorded as `slack.action` events (an input signal for `human_task` steps), not as prospect reactions.

## 10. CRM integration (GoHighLevel first)

`CRMAdapter` interface: `upsert_contact`, `find_contact`, `add_tag`, `remove_tag`, `create_note`, `create_task`, `create_opportunity`, `update_opportunity_stage`, `list_contacts`, `verify_webhook`.

- v1 ships **GoHighLevel** only. HubSpot and Pipedrive are interface stubs that raise `NotImplementedError`.
- Inbound GHL webhooks: contact tag change, appointment booked, opportunity stage change → normalized events.
- Outbound on key events: positive reply → note + task assigned to the owner; meeting booked → opportunity created/updated; negative/opt-out → tag update.
- Every API call is idempotent (external id stored) and failures queue for retry rather than blocking the engine.

## 11. Seed templates (authored here, no LLM at runtime)

Per D13 the copy is **static, authored now**, with merge fields. Per D26: short, direct, one idea per touch, 5–6 touches over ~14 days, `Acme` preset. Persona is fixed (D23): e.g. `{{persona.first_name}}` / `{{persona.signature}}` with `{{persona.calendar_link}}`.

**Merge fields available:** `{{contact.first_name}}`, `{{contact.last_name}}`, `{{contact.company}}`, `{{contact.job_title}}`, `{{persona.first_name}}`, `{{persona.signature}}`, `{{persona.calendar_link}}`, `{{client.name}}`, `{{signal.role_title}}`, `{{signal.source}}`, `{{enum.hours_since_last_update}}`.

### Sequence preset `Acme — Hiring-Signal Nurture`

| # | Day | Channel | Intent |
| --- | --- | --- | --- |
| 1 | 0 | Email | Signal-led opener: you're hiring hardware/mechanical engineering → you're likely moving toward production. |
| 2 | 3 | Email | Follow-up: one proof point (design → prototype → manufacturing under one roof). |
| 3 | 5 | SMS | Short nudge: "left you a note about {{signal.role_title}}" + soft question. |
| 4 | 8 | Email | Different angle: cost/time of splitting design and manufacturing across vendors. |
| 5 | 11 | SMS | Soft close: "should I close your file, or is Q4 better?" |
| 6 | 14 | Email | Breakup email: leave a door open, no hard CTA. |

**Email 1 — subject:** `Re: your {{signal.role_title}} opening at {{contact.company}}`
> Hi {{contact.first_name}},
>
> Saw {{contact.company}} is hiring a {{signal.role_title}} — that usually means a product is moving from concept toward production.
>
> That's the stage where {{client.name}} helps: design, prototyping and manufacturing under one roof, so nothing gets lost between vendors.
>
> Worth a short call, or should I send over the closest case study first?
>
> {{persona.signature}}

**Email 2 — subject:** `Following up on {{contact.company}}`
> Hi {{contact.first_name}},
>
> Quick follow-up — we recently took a hardware product from CAD to working prototypes to a small production run, which is usually where the handoffs between design and manufacturing houses hurt most.
>
> Happy to walk you through how that ran.
>
> {{persona.signature}}

**SMS 1:**
> Hi {{contact.first_name}}, {{persona.first_name}} from {{client.name}} — left you a note about {{contact.company}}'s {{signal.role_title}} opening. Worth 10 minutes?

**Email 3 — subject:** `The hidden cost of splitting design and manufacturing`
> Hi {{contact.first_name}},
>
> One thing we see a lot: teams split design from prototyping and manufacturing to save time, then pay for it in rework cycles and misaligned tolerances.
>
> Keeping all three with one team is usually cheaper by the time the first prototype run is done.
>
> {{persona.signature}}

**SMS 2:**
> Last one from me, {{contact.first_name}} — should I close your file, or is a later quarter better?

**Email 4 — subject:** `Closing the loop`
> Hi {{contact.first_name}},
>
> I'll stop here so I'm not crowding your inbox. If the hiring push turns into a production push, {{client.name}} is a quick call away.
>
> {{persona.signature}}

**Branch wiring for this preset:** after Email 1, `condition(no_reply_for(3d))` → Email 2; a reply at any point routes per §8.3. After Email 3, `condition(no_reply_for(3d))` → SMS 2. SMS steps declare `fallback: send_email` using an email variant of the same copy. Sequence exits on reply, meeting, or opt-out.

## 12. Lead intake

All four paths are in v1 (D27):

1. **CSV upload** — column mapping UI, per-row validation, dedupe against `(client_id, email)`, dry-run preview before commit, and a downloadable rejected-rows report. Matches the existing Apify → Anymailfinder → MillionVerifier output.
2. **GoHighLevel adapter** — pull contacts by tag/list; store `ghl_contact_id`; map GHL custom fields to `custom_fields`.
3. **REST API + webhook** — `POST /api/contacts`, `POST /api/enrollments`, documented with an OpenAPI page so Instantly, n8n, or a form can drive it.
4. **Manual add** — small form for a few contacts and testing.

## 13. Analytics & observability (all selected in D28)

The rollups described here are what the dashboard in §22 renders.

- **Step-level funnel** per sequence version: sent / delivered / bounced / skipped / substituted / replied / positive / meetings, with drop-off between steps.
- **Channel comparison:** email vs SMS reply and meeting rates for comparable steps.
- **Per-inbox and per-domain health:** daily volume vs cap, bounce rate, consecutive failures, status (`warming|active|paused`), last send.
- **Reply & meeting attribution per sequence version** — the reason immutable versions exist.
- **Failure & skip reporting:** grouped by reason (`no_phone`, `suppressed`, `cap_reached`, `inbox_paused`, `bounce`, `collision`, `orphaned_by_version`).
- **Per-contact timeline UI (MVP):** chronological `events` + `messages` + `branch_decisions`, rendering *why* each decision was made, with links to the raw payloads.
- Ops: structured JSON logs, a `/healthz` endpoint, a worker heartbeat, and a "stuck enrollments" check (`active` with `next_run_at` in the past by > 10 min).

## 14. Interfaces / API sketch

```
POST   /api/clients/{id}/contacts/import        CSV upload + mapping
POST   /api/contacts                            create
POST   /api/enrollments                         enroll a contact
POST   /api/sequences                           create
GET    /api/sequences/{id}                      graph + version
PUT    /api/sequences/{id}/draft                save draft graph
POST   /api/sequences/{id}/publish              new immutable version + migrate in-flight
POST   /api/sequences/{id}/rollback/{version}   repoint + migrate
POST   /api/sequences/{id}/simulate             dry-run for a contact
GET    /api/contacts/{id}/timeline              events + messages + decisions
GET    /api/analytics/sequences/{id}            funnel + channel breakdown
GET    /api/analytics/inboxes                   inbox health
POST   /api/webhooks/twilio                     SMS status + inbound
POST   /api/webhooks/slack                      interactive actions + events
POST   /api/webhooks/ghl                        appointments, opportunities, tags
GET    /api/inboxes/{id}/credentials            (auth-gated; secrets redacted)
```

## 15. Non-goals for v1

- Multi-tenant SaaS, client logins, billing.
- WhatsApp, LinkedIn, voice, push.
- A light mode or theme switcher (D35) — the app is dark-only in v1.
- A literal fixed-size 1920×1080 slide stage in the UI (D36); the dashboard is responsive.
- Custom report builder, scheduled PDF/email reports, and any client-facing dashboard view.
- AI/LLM-generated copy (templates only), and no LLM in the runtime path.
- Open/click tracking of any kind (no pixels, no link rewriting).
- Formal compliance tooling: TCPA/PDPA/GDPR consent capture, quiet hours, timezone-aware send windows, automatic STOP/UNSUBSCRIBE keyword handling.
- A/B testing and multi-variant steps.
- Timezone snapping and business-calendar awareness.
- Horizontal scaling beyond the Acme volume target.

## 16. Open questions / recommendations to confirm

1. **Max touches and terminal timeout.** Not selected during the interview, but a hard cap (`≤ 6 touches`, `≤ 2 SMS` per enrollment) and a "no reply after the final step → long-term nurture" exit protect deliverability. Recommend enabling both by default in the preset.
2. **Quiet hours and timezone.** Deferred with compliance, but SMS to Singapore business contacts at, say, 3am is a real reputational risk. Recommend a minimal guard: no SMS before 09:00 or after 18:00 in the contact's timezone, weekend SMS blocked.
3. **Manual kill switch.** Auto-pause on a degraded pool exists; confirm whether a one-click "halt all sends for this client" button is wanted in v1.
4. **Reply sentiment without an LLM.** Confirm a rule/keyword classifier is acceptable in v1, or whether this is the first place an LLM call should be allowed (it is read-only, so it doesn't affect deliverability).
5. **Suppression scope.** Opt-outs are client-scoped. Confirm that a contact who opts out for `Acme` should still be contactable for a different client — ethically and legally the answer is often "no".
6. **Tracking reversal.** If open/click engagement is later wanted (phase 2), it needs a tracking subdomain plus DNS, which is a deliverability decision, not just a code change. Confirm the door stays closed for now.
7. **Reporting to the client.** No client portal in v1 — confirm that a scheduled exported report (email/PDF) is sufficient output for `Acme`.

## 17. Suggested milestones

1. **Foundation** — repo scaffold (FastAPI + Vite + Docker Compose), Postgres schema/migrations, auth (single internal login), client + persona + inbox CRUD.
2. **Engine core** — sequence version persistence, timers, worker, cadence/caps, reaction policy, exit rules, event log, simulation mode; verified with a fake channel adapter and tests (no real sends).
3. **Email channel** — SMTP sending, inbox rotation, IMAP reply poller, thread matching, bounce classification, retries, inbox health.
4. **SMS + Slack** — Twilio send/status/inbound, substitution fallback, Slack alerts and interactive actions.
5. **Canvas + templates** — React Flow builder, node/edge editing with `step_key`s, publish validation, template library seeded from §11, merge-field preview.
6. **Dashboard + intake + analytics** — the performance dashboard (§22), CSV mapping flow, GHL adapter, REST/webhook endpoints, funnel and inbox-health panels, contact timeline UI.
7. **Hardening** — dry-run against real sequences, deliverability checks on a small live cohort, runbook for paused inboxes and stalled enrollments.

## 18. Acceptance criteria for v1

- A sequence authored in the canvas can be published, and an imported CSV cohort enrolls automatically.
- A prospect who never replies receives the 6-touch email/SMS cadence on schedule (elapsed delays), each send from a rotated inbox with caps respected, with a full timeline visible per contact.
- A prospect who replies by email **cancels all pending steps within one poll interval** (~2 min), posts a Slack alert containing the reply body, and is exited/paused per sentiment.
- A prospect who replies by SMS likewise stops all automation.
- A contact with no phone number has their SMS step **auto-substituted** with the email fallback, and the substitution appears in skip/failure reporting.
- A hard bounce suppresses the contact on email and exits the enrollment; three hard bounces in an inbox pause that inbox and alert Slack; zero active inboxes halts that client's email sends.
- A GHL appointment webhook exits the enrollment as `meeting_booked` and creates/updates the opportunity.
- Editing and republishing a version migrates in-flight contacts, and a rollback restores the prior routing for a spot-checked contact.
- Simulation mode shows the projected timeline for a contact without writing messages or sending anything.
- Analytics report per-step funnel, channel comparison, and per-inbox health for the seeded `Acme` sequence.
- The dashboard (§22) opens on campaign performance, renders the seeded `Acme` sequence without an empty state, and every panel drill-through lands on the filtered contacts or contact timeline.

## 19. Environment & configuration

```
# App
APP_ENV=development
DATABASE_URL=postgresql://...
REDIS_URL=redis://...
SECRET_KEY=...

# Email (per-inbox credentials stored encrypted in DB, referenced by secret_ref)
IMAP_POLL_INTERVAL_SECONDS=120
DEFAULT_INBOX_DAILY_CAP=25
MIN_HOURS_BETWEEN_CONTACT_TOUCHES=4

# Twilio
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_WEBHOOK_SIGNING_SECRET=...

# Slack
SLACK_BOT_TOKEN=...
SLACK_SIGNING_SECRET=...
SLACK_ALERT_CHANNEL=...

# GoHighLevel
GHL_CLIENT_ID=...
GHL_CLIENT_SECRET=...
GHL_LOCATION_ID=...
GHL_WEBHOOK_SECRET=...
```

## 20. Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Owning SMTP deliverability | Emails land in spam, tanking the whole channel | Low per-inbox caps (25/day), rotation, warm-up status tracking, auto-pause on bounce spikes, no aggressive tracking (already avoids pixels) |
| IMAP polling latency | Reply stops the sequence up to ~2 min late | Short poll interval, and per-inbox connections are cheap; consider IMAP IDLE later |
| Cancel-and-re-branch complexity | Race conditions between an in-flight send and a new reaction | Claimed timers are transactional; a step already `sent` is never unsent, only future steps cancel; every decision is written to `branch_decisions` |
| Migration-on-publish (D21) | A bad publish re-routes live contacts | Publish validation (§8.7), immutable versions, one-click rollback, simulation before publish |
| Keyword sentiment classifier | False positive stops a sequence | Log the matched rule on every decision; unknown/ambiguous replies route to a human rather than auto-exiting |
| No quiet hours / compliance | Reputational and legal exposure on SMS | Flagged as open question #2 — recommended minimum guard before launch |
| One active sequence per channel (D20) | A wanted follow-up sequence is silently rejected | Collision events surface in the UI rather than failing quietly |

---

## 21. Design system

The app's visual language is lifted from `Acme-proposal-signed-2026-09-12.html` (D33). That file is the **source of truth**: when the deck's styling changes, the token file changes with it. One theme, dark, for the entire app (D35).

### 21.1 Rule: borrow the tokens, not the deck's CSS

The proposal is a fixed `1920×1080` stage — every element is `position:absolute`, the document is `overflow:hidden`, and a JS scaler transforms the stage to fit the viewport. **Do not import that stylesheet into the app.** Instead:

1. Extract the custom properties and the type/scale values into one shared `packages/ui/tokens.css`.
2. Re-implement the deck's components as React components that read those tokens.
3. Leave the deck's print and stage-scaling CSS (`--stage-bg`, `--slide-bg`, `@media print`, `#stage` transforms) behind — they are presentation concerns, not app concerns.

This keeps the deck and the app visually identical without inheriting a rigid 16:9 layout (D36).

### 21.2 Color tokens (verbatim from the deck)

```css
:root {
  --bg: #0A0A0A;        /* app background */
  --panel: #111111;     /* panel fill, used at rgba(17,17,17,.88) */
  --panel-2: #171717;   /* raised / hovered surface */
  --ink: #F5F3EE;       /* primary text */
  --muted: #9A9A94;     /* secondary text */
  --line: rgba(245,243,238,.14);  /* every 1px border */
  --accent: #C9FF4A;    /* lime: primary action, active, success */
  --accent-2: #8FE35D;  /* secondary lime, gradients */
  --warm: #F3C969;      /* warning, SMS channel, skips */
  --danger: #FF7D6E;    /* failures, hard bounces, negative replies */
  --shadow: 0 30px 80px rgba(0,0,0,.38);
}
```

Supporting greys used inline throughout the deck, promoted to tokens so panels stay consistent: `#77776F` / `#6E6E67` (meta, kickers), `#8A8A82` / `#92928A` (body secondary), `#A4A49D` / `#B0B0A8` (body), `#B9B9B1` (tags), `#7E7E76` / `#7F7F77` (card body), `#9C9C94` (lead paragraphs).

**Semantic mapping for the app:** accent = active/healthy/primary CTA; warm = warming, skips, substitutions, neutral replies; danger = paused, bounced, failed, negative reply; `#8A8A82` = draft/disabled.

### 21.3 Typography

Loaded exactly as the deck does, via Google Fonts with `preconnect`:

- **Display:** `Syne` 500–800, letter-spacing `-0.04em` to `-0.06em`, line-height `0.95`.
- **Body / UI / data:** `IBM Plex Mono` 400–600.

| Role | Deck reference | App value |
| --- | --- | --- |
| Page title | `.h1` 92–104px | Syne 800, 40px, `-0.05em` |
| Section title | `.h2` 62–72px | Syne 700, 26px, `-0.04em` |
| Panel / card title | `.node h3`, `.day-card h3` 26–36px | Syne 700, 18–20px, `-0.03em` |
| Lead paragraph | `.body` 23px | IBM Plex Mono 400, 15px, line-height 1.5, `--muted` |
| Body / form label | `.small` 17px | IBM Plex Mono 400, 14px, line-height 1.5 |
| Data / figures | `.num`, `.metric b` | IBM Plex Mono 500, `font-variant-numeric: tabular-nums` |
| Eyebrow | `.eyebrow` 17px, `.16em` | IBM Plex Mono 500, 12px, uppercase, `0.14em`, `--accent` |
| Kicker / column head | `.kicker` 15px, `.12em` | IBM Plex Mono 500, 12px, uppercase, `0.12em`, `#77776F` |

### 21.4 Components to reimplement

Every one of these exists in the deck and maps onto a real app surface.

| Deck class | App component |
| --- | --- |
| `.panel` — `rgba(17,17,17,.88)` fill, `1px var(--line)` border, `28px` radius, `var(--shadow)` | `<Panel>`: the base surface for every dashboard and timeline block. Dense panels step down to 18px radius; keep 28px for hero/service panels. |
| `.pill` + `.dot` — lime-tinted capsule, glowing 8px dot | `<StatusPill>` / `<LiveDot>`: sequence status, inbox health, data freshness. |
| `.metric` — 10px/13px padding, 9px radius, 1px line border | `<MetricChip>`: the KPI strip. |
| `.close-step` — numbered row, `1fr` grid, bottom `--line` separator, lime `.snum` | `<NumberedStep>`: onboarding checklist and empty-state instructions. |
| `.price-line` + lime `.bullet` | `<BulletList>`: template merge-field lists, publish-validation errors, panel detail rows. |
| `.node` / `.flow-arrow` — bordered card, `#696962` tag, grey arrow | `<FlowNodePreview>` / `<FlowArrow>`: funnel step labels and canvas previews. |
| `.cta` — lime fill, `#0B0B0B` text, Syne 700, 11px radius | `<Button variant="primary">`. |
| `.sign-btn` — `rgba(201,255,74,.06)` fill, `rgba(201,255,74,.28)` border, lime uppercase mono 600/12px, hover inverts to lime fill + dark text | `<Button variant="ghost">`: the default secondary action. |
| `.sign-btn.alt` — `--line` border, `#8A8A82` text | `<Button variant="neutral">`. |
| `.sign-field` — `rgba(255,255,255,.04)` fill, `--line` border, 9px radius, mono 13px, focus border lime | `<Input>` / `<Select>` / `<Textarea>`: **all** form fields in the app. |
| `.sign-panel` + `.sign-canvas` + `.sign-stamp` | `<SignaturePanel>`: reused for client sign-off on a sequence version, including the signed-state ✓ badge and timestamp stamp. |
| `.nav` — pill container, circular buttons, `backdrop-filter: blur(12px)` | Canvas zoom/step controls and any floating toolbar. |
| `.chrome` + `.logo` (`Hautom` + lime italic `ai`) + `.page` | App header: logo lockup left, breadcrumb/page context right. |
| `.footer` — left/right caption pair, 14px, `#6E6E67` | Panel footers and the status bar (e.g. left: "Sending infrastructure", right: "4 inboxes · 100/day"). |
| `.rule` — 1px `--line` | `<Divider>`. |

**Buttons in one sentence:** primary is a lime fill with dark text; secondary is a lime-tinted ghost that inverts to lime on hover; neutral is a plain bordered ghost; destructive uses `--danger` on a transparent fill. Nothing else.

### 21.5 Motion

The deck's entrance animation is the app's motion vocabulary — nothing bespoke.

- `rise`: `opacity 0→1`, `translateY(24px)→0`, `0.8s cubic-bezier(.2,.7,.2,1)`.
- Staggered `d1…d6` delays at **0.08s** increments.
- Use on panel mount, list row stagger, and modal entry. **Never** on data refresh (a re-rendered metric must not re-animate); use a subtle count-up instead.
- `prefers-reduced-motion: reduce` collapses durations to `0.01ms` and transitions to `0.2s`, exactly as the deck does.
- The deck's `scan` sweep (210px lime gradient, 6s infinite) is reserved for a single live/loading indicator, disabled under reduced motion.

### 21.6 Textures (used sparingly)

- **Grid overlay:** `linear-gradient(rgba(255,255,255,.025) 1px, transparent 1px)` + the 90° twin at `90px` spacing, with the deck's bottom fade mask `linear-gradient(to bottom, rgba(0,0,0,.85), transparent 90%)`. Dashboard background only.
- **Ambient orbs:** lime `radial-gradient(circle at 85% 10%, rgba(201,255,74,.07), transparent 30%)` and warm `radial-gradient(circle at 10% 95%, rgba(243,201,105,.04), transparent 28%)`. Fixed page background, behind all panels.
- **Glow:** lime dots and active indicators carry `box-shadow: 0 0 14px–18px rgba(201,255,74,.35–.65)`. Never glow text.

### 21.7 Tailwind wiring

- `theme.extend.colors` maps every semantic name to `var(--…)` so no hex is ever typed in a component.
- `fontFamily: { display: ['Syne'], mono: ['IBM Plex Mono'] }`.
- `borderRadius` scale: `panel: 28px`, `card: 18px`, `input: 9px`, `button: 11px`, `pill: 999px`.
- `boxShadow.panel: 0 30px 80px rgba(0,0,0,.38)`.
- One `Panel` primitive with `variant="panel" | "raised"` (`--panel` / `--panel-2`) so surfaces never drift.

### 21.8 Accessibility notes

- `--accent` on `--bg` is very high contrast; `--muted` on `--bg` is roughly 7:1 (fine for body). `#6E6E67` on `--bg` is around 4.3:1 — restrict it to ≥12px meta text, never to primary content.
- Never encode status by color alone: every pill and status carries a label, and success uses the `✓` badge pattern from the deck's signed state.
- Focus rings: use the `.sign-field:focus` behaviour — a 1px lime border — plus a visible ring on keyboard focus for buttons and canvas nodes.

---

## 22. Dashboard (app home)

Yes — a dashboard is added, and it is the **app home**, scoped by D34 to **campaign performance**: it answers *"how are my sequences performing right now, and where should I tune the cadence?"*

Design follows §21 throughout: dark, panels, metric chips, lime accent, Syne headlines, IBM Plex Mono data. Responsive app shell, not a slide stage (D36).

### 22.1 Shell

- **Left rail** (collapsible, 64px collapsed / 240px expanded): `Hautom`+lime `ai` lockup, then Sequences · Contacts · Inboxes · Templates · Analytics · Settings. Active item: `--panel-2` background with a 2px lime left border.
- **Top bar:** page title in Syne, the client switcher pinned to `Acme innovation` (visible but disabled — single client in v1, D2), date-range selector (`7d · 30d · 90d · custom`), a **LiveDot** reusing the deck's glowing pill dot, and a global "Pause all sends" ghost button (pending confirmation — open question #3).
- **Body:** 12-column fluid grid, 24px gutters, ambient orbs plus the grid overlay from §21.6 behind it.
- Panels use the deck's `.panel` fill, border, radius, and shadow.

### 22.2 Panels

Ordered performance-first; ops information is present but demoted to a feed rather than the headline.

**Row 1 — KPI strip** (`<MetricChip>`s, sourced from `sequence_stats`)
Active enrollments · Sent today / daily cap · Reply rate (period) · Positive replies · Meetings booked · Live sequence versions.
Each chip shows a kicker label, an ink figure, and a delta versus the previous equal period in accent (up) or warm (down).

**Row 2 — Sequence funnel** (2/3 width)
- Header: sequence and **version** selector (published versions only), plus a channel legend — Email in ink/lime, SMS in `--warm`.
- Body: one bar per step in order; bar length ∝ `sent`; inline figures for sent / delivered / replied / positive; drop-off between consecutive steps rendered as the deck's `.flow-arrow` with a percentage lost.
- Below the bars: a compact per-step table (step #, channel, name, sent, reply rate, meetings).
- **Drill-through:** clicking a step filters the contacts list to contacts currently at or past that step; the filter token rides in the URL so the view is shareable.

**Row 2 — Channel comparison** (1/3 width)
Email versus SMS side by side, in the deck's two-column `.price-panel` treatment: sends, delivered, replies, reply rate, positive rate, meetings — with `.price-line` bullets for the qualitative notes (e.g. "SMS drives replies, email drives meetings").

**Row 3 — Sending health** (1/2 width)
Table grouped by sending domain: inbox address, status pill (active = accent, warming = warm, paused = danger), sends today vs cap as a thin lime progress bar on a `--line` track, bounce rate, warm-up day count, last send. A warm/danger banner appears when the pool has zero active inboxes or any inbox is auto-paused per §8.9.

**Row 3 — Failure & skip reporting** (1/2 width)
Grouped horizontal bars by reason — `no_phone`, `suppressed`, `cap_reached`, `inbox_paused`, `bounce`, `collision`, `orphaned_by_version`, `substituted`. Danger for hard failures, warm for skips and substitutions, accent for substitutions that still landed.

**Row 4 — Needs attention** (full width)
The one ops element, as a feed rather than the lead: newest replies with sentiment chips (positive = accent, question = warm, negative = danger), human tasks due, and stalled enrollments (`active` with `next_run_at` in the past by more than 10 minutes). Every row links to the contact timeline.

**Row 5 — Version compare** (full width, collapsed by default)
Reply and meeting attribution for the two most recent versions of the selected sequence. This is the payoff of immutable versions (§8.6) and the reason to keep the panel even though A/B testing is out of scope.

### 22.3 Behaviour

- **Data source:** the pre-aggregated `sequence_stats` table and materialized views from §7 — the dashboard must never scan raw `events`. The Needs-attention feed is the single exception: an indexed query with `LIMIT 50`.
- **Refresh:** every 60s plus a manual refresh; LiveDot glows accent when data is under 90s old, warm when stale, danger on fetch error.
- **State in the URL:** `?sequence=…&version=…&range=30d` — every panel and drill-through derives from it.
- **Performance target:** fully populated in under 1.5s on the seeded dataset, with no N+1 queries.
- **Empty state:** with no sequences, show the deck's numbered `.close-step` checklist — `01 Import contacts` · `02 Connect a sending inbox` · `03 Publish a sequence` — with lime step numbers, matching the proposal's next-steps slide.
- **Loading state:** per-panel skeletons that use the `rise` animation, respecting reduced motion.
- **First-run state:** `Acme — Hiring-Signal Nurture` (§11) and the 4-inbox pool are seeded so the dashboard has real shape immediately.
- **Responsive:** below ~1200px the funnel moves into its own row, channel comparison and health panels stack, and the funnel switches to a vertical list.
- **Dark only:** no light flash before hydration (D35); theme is a CSS variable block applied at the root.

### 22.4 Not in the v1 dashboard

- A custom widget/report builder, scheduled PDF or emailed reports, and any client-facing read-only view (open question #7).
- Multi-sequence side-by-side campaign comparison (the funnel is one sequence version at a time).
- Real-time per-event streaming — 60s rollups are sufficient at this volume (D32).

### 22.5 Dashboard acceptance criteria

- Opens on campaign performance with the seeded `Acme` sequence, every panel populated from rollups, no empty state.
- The funnel's sent/replied/positive figures reconcile with the contact timeline for a spot-checked contact.
- Clicking a funnel step and an inbox row each drill through to a correctly filtered list, with the filter preserved in the URL.
- A paused inbox (§8.9) surfaces the health banner, and a zero-active-inbox pool halts and shows it loudly.
- The dashboard renders dark from first paint, with no light flash, and remains legible at 1280×720.
