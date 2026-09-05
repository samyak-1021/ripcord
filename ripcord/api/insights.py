"""Read-only endpoints powering the dashboard's audit log and metrics pages."""

from fastapi import APIRouter

from ripcord import services
from ripcord.deps import SessionDep
from ripcord.metrics import evaluation_counts
from ripcord.schemas import AuditEntry, Stats

router = APIRouter(tags=["insights"])


@router.get("/audit", response_model=list[AuditEntry])
async def get_audit(
    session: SessionDep, flag_key: str | None = None, limit: int = 100
) -> list[AuditEntry]:
    """Return recent change history, newest first (optionally for one flag)."""
    return await services.list_audit(session, flag_key=flag_key, limit=limit)


@router.get("/stats", response_model=Stats)
async def get_stats(session: SessionDep) -> Stats:
    """Aggregate flag counts + evaluation totals for the metrics page."""
    base = await services.compute_stats(session)
    counts = evaluation_counts()
    return Stats(
        flags_total=base["flags_total"],
        flags_enabled=base["flags_enabled"],
        flags_disabled=base["flags_disabled"],
        evaluations_total=sum(counts.values()),
        evaluations_by_result=counts,
    )
