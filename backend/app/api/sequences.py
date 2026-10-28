"""Sequences: authoring, validation, publishing and simulation.

Versioning model (D21):

* A **draft** is a ``SequenceVersion`` row with ``published_at IS NULL``. Drafts
  are mutable; editing overwrites the draft in place.
* **Publishing** stamps the draft, points the sequence at it, and migrates every
  in-flight contact onto the new graph.
* **Rollback** repoints the sequence at an older published version and runs the
  same migration.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import constants as c
from app.api.deps import get_channels, get_crm, get_or_404, resolve_client
from app.config import Settings, get_settings
from app.db import get_db
from app.engine import runner
from app.engine.graph import blocking_issues, parse_graph, validate_graph
from app.engine.simulation import simulate
from app.models import Contact, Sequence, SequenceVersion, Template
from app.schemas import (
    GraphUpdate,
    PublishResult,
    SequenceCreate,
    SequenceDetail,
    SequenceOut,
    SequenceUpdate,
    SequenceVersionOut,
    SimulatedStep,
    SimulationRequest,
    SimulationResult,
    ValidationIssue,
    ValidateResult,
)
from app.security import get_current_user

router = APIRouter(tags=["sequences"], dependencies=[Depends(get_current_user)])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _template_map(db: Session, client_id: str) -> dict[str, dict[str, str]]:
    rows = db.scalars(select(Template).where(Template.client_id == client_id)).all()
    return {
        row.key: {"channel": row.channel, "subject": row.subject, "body": row.body}
        for row in rows
    }


def _draft_of(db: Session, sequence_id: str) -> SequenceVersion | None:
    return db.scalar(
        select(SequenceVersion)
        .where(SequenceVersion.sequence_id == sequence_id, SequenceVersion.published_at.is_(None))
        .order_by(SequenceVersion.version.desc())
        .limit(1)
    )


def _next_version_number(db: Session, sequence_id: str) -> int:
    highest = db.scalar(
        select(func.max(SequenceVersion.version)).where(SequenceVersion.sequence_id == sequence_id)
    )
    return int(highest or 0) + 1


def _graph_for_validation(db: Session, sequence: Sequence) -> dict:
    draft = _draft_of(db, sequence.id)
    if draft is not None:
        return draft.graph
    current = db.get(SequenceVersion, sequence.current_version_id or "")
    return current.graph if current else {}


def _issues_to_schema(issues) -> list[ValidationIssue]:
    return [ValidationIssue(**issue.to_dict()) for issue in issues]


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
@router.get("/clients/{client_id}/sequences", response_model=list[SequenceOut])
def list_sequences(client_id: str, db: Session = Depends(get_db)) -> list[Sequence]:
    client = resolve_client(db, client_id)
    return list(
        db.scalars(
            select(Sequence)
            .where(Sequence.client_id == client.id)
            .order_by(Sequence.updated_at.desc())
        ).all()
    )


@router.post("/clients/{client_id}/sequences", response_model=SequenceDetail, status_code=201)
def create_sequence(
    client_id: str, payload: SequenceCreate, db: Session = Depends(get_db)
) -> SequenceDetail:
    client = resolve_client(db, client_id)
    sequence = Sequence(
        client_id=client.id,
        name=payload.name,
        description=payload.description,
        status=c.SEQUENCE_DRAFT,
        reaction_policy=payload.reaction_policy,
        allow_cadence_override=payload.allow_cadence_override,
        cadence_preset=payload.cadence_preset,
    )
    db.add(sequence)
    db.flush()

    graph = payload.graph or _starter_graph()
    version = SequenceVersion(
        sequence_id=sequence.id,
        version=1,
        graph=graph,
        notes="Initial draft",
    )
    db.add(version)
    db.commit()
    db.refresh(sequence)
    return _detail(db, sequence)


@router.get("/sequences/{sequence_id}", response_model=SequenceDetail)
def get_sequence(sequence_id: str, db: Session = Depends(get_db)) -> SequenceDetail:
    sequence = get_or_404(db, Sequence, sequence_id, "Sequence")
    return _detail(db, sequence)


@router.patch("/sequences/{sequence_id}", response_model=SequenceOut)
def update_sequence(
    sequence_id: str, payload: SequenceUpdate, db: Session = Depends(get_db)
) -> Sequence:
    sequence = get_or_404(db, Sequence, sequence_id, "Sequence")
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(sequence, key, value)
    db.commit()
    db.refresh(sequence)
    return sequence


@router.get("/sequences/{sequence_id}/versions", response_model=list[SequenceVersionOut])
def list_versions(sequence_id: str, db: Session = Depends(get_db)) -> list[SequenceVersion]:
    get_or_404(db, Sequence, sequence_id, "Sequence")
    return list(
        db.scalars(
            select(SequenceVersion)
            .where(SequenceVersion.sequence_id == sequence_id)
            .order_by(SequenceVersion.version.desc())
        ).all()
    )


# ---------------------------------------------------------------------------
# Drafting
# ---------------------------------------------------------------------------
@router.put("/sequences/{sequence_id}/draft", response_model=SequenceVersionOut)
def save_draft(
    sequence_id: str, payload: GraphUpdate, db: Session = Depends(get_db)
) -> SequenceVersion:
    sequence = get_or_404(db, Sequence, sequence_id, "Sequence")
    draft = _draft_of(db, sequence.id)
    if draft is None:
        draft = SequenceVersion(
            sequence_id=sequence.id,
            version=_next_version_number(db, sequence.id),
            graph=payload.graph,
            notes=payload.notes,
        )
        db.add(draft)
    else:
        draft.graph = payload.graph
        draft.notes = payload.notes
        # JSON columns are mutable-in-place in some drivers; flag explicitly.
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(draft, "graph")
    db.commit()
    db.refresh(draft)
    return draft


@router.post("/sequences/{sequence_id}/validate", response_model=ValidateResult)
def validate(
    sequence_id: str,
    payload: GraphUpdate | None = None,
    db: Session = Depends(get_db),
) -> ValidateResult:
    sequence = get_or_404(db, Sequence, sequence_id, "Sequence")
    graph = payload.graph if payload is not None else _graph_for_validation(db, sequence)
    issues = validate_graph(
        graph,
        templates=_template_map(db, sequence.client_id),
        allow_cadence_override=sequence.allow_cadence_override,
    )
    return ValidateResult(ok=not blocking_issues(issues), issues=_issues_to_schema(issues))


@router.post("/sequences/{sequence_id}/publish", response_model=PublishResult)
def publish(
    sequence_id: str,
    payload: GraphUpdate | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PublishResult:
    sequence = get_or_404(db, Sequence, sequence_id, "Sequence")
    if payload is not None and payload.graph:
        save_draft(sequence_id, payload, db)

    draft = _draft_of(db, sequence.id)
    if draft is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="There is no draft to publish. Save a draft first.",
        )

    issues = validate_graph(
        draft.graph,
        templates=_template_map(db, sequence.client_id),
        allow_cadence_override=sequence.allow_cadence_override,
    )
    if blocking_issues(issues):
        return PublishResult(ok=False, issues=_issues_to_schema(issues))

    draft.published_at = datetime.now(timezone.utc)
    sequence.current_version_id = draft.id
    if sequence.status == c.SEQUENCE_DRAFT:
        sequence.status = c.SEQUENCE_ACTIVE
    db.flush()

    migrated, orphaned = runner.migrate_enrollments_to_version(
        db,
        sequence=sequence,
        new_version=draft,
        channels=get_channels(),
        settings=settings,
    )
    db.commit()
    db.refresh(draft)
    return PublishResult(
        ok=True,
        issues=_issues_to_schema(issues),
        version=SequenceVersionOut.model_validate(draft),
        migrated_enrollments=migrated,
        orphaned_enrollments=orphaned,
    )


@router.post("/sequences/{sequence_id}/rollback/{target_version}", response_model=PublishResult)
def rollback(
    sequence_id: str,
    target_version: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PublishResult:
    sequence = get_or_404(db, Sequence, sequence_id, "Sequence")
    target = db.scalar(
        select(SequenceVersion).where(
            SequenceVersion.sequence_id == sequence.id,
            SequenceVersion.version == target_version,
            SequenceVersion.published_at.is_not(None),
        )
    )
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Published version {target_version} not found",
        )

    previous = sequence.current_version_id
    sequence.current_version_id = target.id
    db.flush()
    migrated, orphaned = runner.migrate_enrollments_to_version(
        db,
        sequence=sequence,
        new_version=target,
        channels=get_channels(),
        settings=settings,
    )
    db.commit()
    return PublishResult(
        ok=True,
        version=SequenceVersionOut.model_validate(target),
        migrated_enrollments=migrated,
        orphaned_enrollments=orphaned,
    )


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------
@router.post("/sequences/{sequence_id}/simulate", response_model=SimulationResult)
def simulate_sequence(
    sequence_id: str,
    payload: SimulationRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SimulationResult:
    sequence = get_or_404(db, Sequence, sequence_id, "Sequence")
    contact = get_or_404(db, Contact, payload.contact_id, "Contact")
    if contact.client_id != sequence.client_id:
        raise HTTPException(status_code=400, detail="Contact belongs to a different client")

    outcome = simulate(
        db,
        sequence=sequence,
        contact=contact,
        settings=settings,
        hypotheticals=payload.hypotheticals,
        max_steps=payload.max_steps,
    )
    return SimulationResult(
        timeline=[
            SimulatedStep(
                step_key=item.step_key,
                step_type=item.step_type,
                channel=item.channel,
                run_at=item.run_at,
                detail=item.detail,
                rendered_subject=item.rendered_subject,
                rendered_body=item.rendered_body,
                outcome=item.outcome,
            )
            for item in outcome.timeline
        ],
        exited=outcome.exited,
        exit_reason=outcome.exit_reason,
        warnings=outcome.warnings,
    )


# ---------------------------------------------------------------------------
# Detail assembly
# ---------------------------------------------------------------------------
def _detail(db: Session, sequence: Sequence) -> SequenceDetail:
    versions = list(
        db.scalars(
            select(SequenceVersion)
            .where(SequenceVersion.sequence_id == sequence.id)
            .order_by(SequenceVersion.version.desc())
        ).all()
    )
    current = db.get(SequenceVersion, sequence.current_version_id or "")
    draft = next((row for row in versions if row.published_at is None), None)
    return SequenceDetail(
        sequence=SequenceOut.model_validate(sequence),
        current_version=SequenceVersionOut.model_validate(current) if current else None,
        draft_version=SequenceVersionOut.model_validate(draft) if draft else None,
        versions=[SequenceVersionOut.model_validate(row) for row in versions],
    )


def _starter_graph() -> dict:
    """A minimal valid starting point for a brand new sequence."""
    return {
        "entry_step_keys": ["step_email_1"],
        "steps": [
            {
                "key": "step_email_1",
                "type": "send_email",
                "name": "01 - Opener",
                "template_key": "",
                "fallback": "skip",
                "next": "step_wait_1",
            },
            {"key": "step_wait_1", "type": "delay", "name": "Wait 3 days", "amount": 3, "unit": "days", "next": "step_exit_done"},
            {"key": "step_exit_done", "type": "exit", "name": "Exit", "reason": c.EXIT_COMPLETED},
        ],
    }
