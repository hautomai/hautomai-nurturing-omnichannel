"""Dry-run simulation.

Walks a sequence version for one contact and projects the timeline without
sending anything or writing any state (other than an optional audit event).

Hypothetical reactions are supplied by the caller, keyed by step:

    hypotheticals = {"step_email_1": True}   # pretend this step was replied to

Conditions still fall back to the contact's *real* history when no hypothesis is
given, so the projection reflects what would actually happen today.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import constants as c
from app.config import Settings
from app.engine.conditions import ConditionContext, evaluate
from app.engine.graph import NODE_CONDITION, NODE_DELAY, NODE_EXIT, NODE_HUMAN_TASK, parse_graph
from app.engine.scheduler import apply_send_window
from app.engine.templates import build_context, render
from app.models import Client, Contact, Sequence, SequenceVersion, SenderPersona, Template


@dataclass
class SimulatedStep:
    step_key: str
    step_type: str
    channel: str = ""
    run_at: datetime | None = None
    detail: str = ""
    rendered_subject: str = ""
    rendered_body: str = ""
    outcome: str = ""


@dataclass
class SimulationOutcome:
    timeline: list[SimulatedStep] = field(default_factory=list)
    exited: bool = False
    exit_reason: str | None = None
    warnings: list[str] = field(default_factory=list)


def simulate(
    db: Session,
    *,
    sequence: Sequence,
    contact: Contact,
    settings: Settings,
    hypotheticals: dict[str, bool] | None = None,
    start_at: datetime | None = None,
    max_steps: int = 40,
) -> SimulationOutcome:
    hypotheticals = hypotheticals or {}
    version = db.get(SequenceVersion, sequence.current_version_id or "")
    if version is None:
        return SimulationOutcome(warnings=["Sequence has no published version to simulate."])

    graph = parse_graph(version.graph)
    client = db.get(Client, sequence.client_id)
    persona = db.scalar(
        select(SenderPersona)
        .where(SenderPersona.client_id == sequence.client_id)
        .order_by(SenderPersona.is_default.desc())
        .limit(1)
    )
    context = build_context(contact=contact, persona=persona, client=client)

    outcome = SimulationOutcome()
    now = start_at or datetime.now(timezone.utc)
    step_key = graph.entry_step_keys[0] if graph.entry_step_keys else None
    iterations: dict[str, int] = {}

    for _ in range(max_steps):
        if not step_key:
            outcome.warnings.append("Ran into a step with no successor.")
            break
        step = graph.get(step_key)
        if step is None:
            outcome.warnings.append(f"Step '{step_key}' does not exist in this version.")
            break

        iterations[step_key] = iterations.get(step_key, 0) + 1
        step_type = str(step.get("type"))

        if step_type == NODE_EXIT:
            outcome.exited = True
            outcome.exit_reason = str(step.get("reason") or c.EXIT_COMPLETED)
            outcome.timeline.append(
                SimulatedStep(
                    step_key=step_key,
                    step_type=step_type,
                    run_at=now,
                    detail=f"exit: {outcome.exit_reason}",
                    outcome="exit",
                )
            )
            break

        if step_type == NODE_DELAY:
            delta = timedelta(seconds=graph.delay_seconds(step_key))
            now = now + delta
            outcome.timeline.append(
                SimulatedStep(
                    step_key=step_key,
                    step_type=step_type,
                    run_at=now,
                    detail=f"wait {graph.delay_seconds(step_key) // 86400 or '-'}d",
                    outcome="delay",
                )
            )
            step_key = str(step.get("next") or "")
            continue

        if step_type == NODE_CONDITION:
            ctx = _context(db, contact, sequence, now, hypotheticals)
            result, description = evaluate(step.get("predicate"), ctx, now=now)
            branch = "on_true" if result else "on_false"
            outcome.timeline.append(
                SimulatedStep(
                    step_key=step_key,
                    step_type=step_type,
                    run_at=now,
                    detail=f"{description} -> {branch}",
                    outcome=branch,
                )
            )
            step_key = str(step.get(branch) or "")
            continue

        if step_type == NODE_HUMAN_TASK:
            outcome.timeline.append(
                SimulatedStep(
                    step_key=step_key,
                    step_type=step_type,
                    run_at=now,
                    detail=str(step.get("title") or "human task"),
                    outcome="pause",
                )
            )
            outcome.warnings.append(
                "Simulation stops at a human task: the outcome depends on what the rep does."
            )
            break

        if step_type in ("send_email", "send_sms"):
            channel = graph.channel_of(step_key)
            template = db.scalar(
                select(Template).where(
                    Template.client_id == sequence.client_id,
                    Template.key == graph.template_key_of(step_key),
                )
            )
            subject = ""
            body = ""
            if template is not None:
                try:
                    subject = render(template.subject, context)
                    body = render(template.body, context)
                except Exception as exc:  # noqa: BLE001
                    outcome.warnings.append(
                        f"Template '{template.key}' did not render for this contact: {exc}"
                    )
            else:
                outcome.warnings.append(
                    f"Template '{graph.template_key_of(step_key)}' is missing."
                )

            send_at = apply_send_window(
                now,
                channel=channel,
                timezone_name=contact.timezone or (client.default_timezone if client else None),
                settings=settings,
            )
            detail = ""
            if send_at != now:
                detail = f"deferred into the {channel} sending window"
            outcome.timeline.append(
                SimulatedStep(
                    step_key=step_key,
                    step_type=step_type,
                    channel=channel,
                    run_at=send_at,
                    detail=detail,
                    rendered_subject=subject,
                    rendered_body=body,
                    outcome="send",
                )
            )
            now = send_at
            if hypotheticals.get(step_key):
                outcome.timeline.append(
                    SimulatedStep(
                        step_key=step_key,
                        step_type="reaction",
                        channel=channel,
                        run_at=now,
                        detail="hypothetical reply -> sequence stops",
                        outcome="reply",
                    )
                )
                outcome.exited = True
                outcome.exit_reason = "replied"
                break
            step_key = str(step.get("next") or "")
            continue

        # notify_slack and anything else: pass through.
        outcome.timeline.append(
            SimulatedStep(
                step_key=step_key,
                step_type=step_type,
                run_at=now,
                detail=str(step.get("text") or ""),
                outcome="pass",
            )
        )
        step_key = str(step.get("next") or "")

    return outcome


def _context(
    db: Session,
    contact: Contact,
    sequence: Sequence,
    now: datetime,
    hypotheticals: dict[str, bool],
) -> ConditionContext:
    """Real engagement state, with hypotheses layered on top."""
    from app.engine.runner import _condition_context
    from app.models import Enrollment

    enrollment = db.scalar(
        select(Enrollment)
        .where(Enrollment.contact_id == contact.id, Enrollment.sequence_id == sequence.id)
        .order_by(Enrollment.entered_at.desc())
        .limit(1)
    )
    if enrollment is None:
        return ConditionContext(reference_at=now)
    ctx = _condition_context(db, enrollment, now=now)
    if hypotheticals.get("__replied__"):
        ctx.has_reply = True
        ctx.last_reply_sentiment = c.SENTIMENT_POSITIVE
    return ctx
