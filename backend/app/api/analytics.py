"""Dashboard read models.

Every panel on the dashboard has exactly one endpoint here, so the UI never
composes its own cross-table queries.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import analytics as analytics_service
from app.api.deps import get_or_404, resolve_client
from app.db import get_db
from app.models import Sequence
from app.schemas import (
    ChannelComparisonRow,
    FailureRow,
    FunnelStep,
    InboxHealthRow,
    NeedsAttentionRow,
    OverviewMetrics,
    SequenceAnalytics,
    VersionComparisonRow,
)
from app.security import get_current_user

router = APIRouter(tags=["analytics"], dependencies=[Depends(get_current_user)])


@router.get("/clients/{client_id}/analytics/overview", response_model=OverviewMetrics)
def overview(
    client_id: str,
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> OverviewMetrics:
    client = resolve_client(db, client_id)
    return OverviewMetrics(**analytics_service.overview(db, client_id=client.id, days=days))


@router.get("/sequences/{sequence_id}/analytics", response_model=SequenceAnalytics)
def sequence_analytics(
    sequence_id: str,
    version_id: str | None = None,
    db: Session = Depends(get_db),
) -> SequenceAnalytics:
    sequence = get_or_404(db, Sequence, sequence_id, "Sequence")
    data = analytics_service.sequence_analytics(db, sequence=sequence, version_id=version_id)
    return SequenceAnalytics(
        sequence_id=data["sequence_id"],
        sequence_name=data["sequence_name"],
        version_id=data["version_id"],
        version=data["version"],
        funnel=[FunnelStep(**item) for item in data["funnel"]],
        channels=[ChannelComparisonRow(**item) for item in data["channels"]],
        totals=data["totals"],
    )


@router.get("/clients/{client_id}/analytics/inboxes", response_model=list[InboxHealthRow])
def inbox_health(client_id: str, db: Session = Depends(get_db)) -> list[InboxHealthRow]:
    client = resolve_client(db, client_id)
    return [
        InboxHealthRow(**row) for row in analytics_service.inbox_health(db, client_id=client.id)
    ]


@router.get("/clients/{client_id}/analytics/failures", response_model=list[FailureRow])
def failures(
    client_id: str,
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> list[FailureRow]:
    client = resolve_client(db, client_id)
    return [
        FailureRow(**row) for row in analytics_service.failure_report(db, client_id=client.id, days=days)
    ]


@router.get("/clients/{client_id}/analytics/needs-attention", response_model=list[NeedsAttentionRow])
def needs_attention(
    client_id: str,
    limit: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[NeedsAttentionRow]:
    client = resolve_client(db, client_id)
    return [
        NeedsAttentionRow(**row)
        for row in analytics_service.needs_attention(db, client_id=client.id, limit=limit)
    ]


@router.get(
    "/sequences/{sequence_id}/analytics/version-compare",
    response_model=list[VersionComparisonRow],
)
def version_compare(
    sequence_id: str,
    limit: int = Query(default=2, ge=2, le=10),
    db: Session = Depends(get_db),
) -> list[VersionComparisonRow]:
    sequence = get_or_404(db, Sequence, sequence_id, "Sequence")
    return [
        VersionComparisonRow(**row)
        for row in analytics_service.version_comparison(db, sequence=sequence, limit=limit)
    ]
