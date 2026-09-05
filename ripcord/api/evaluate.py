"""HTTP endpoint for evaluating a flag against a user context."""

from fastapi import APIRouter

from ripcord import services
from ripcord.deps import SessionDep
from ripcord.metrics import flag_evaluations_total
from ripcord.schemas import EvaluateRequest, EvaluateResponse

router = APIRouter(tags=["evaluation"])


@router.post("/evaluate", response_model=EvaluateResponse)
async def evaluate_flag(payload: EvaluateRequest, session: SessionDep) -> EvaluateResponse:
    """Evaluate one flag for a user. Unknown flags resolve to 'off' (fail-safe)."""
    result = await services.evaluate_flag(
        session, payload.flag_key, payload.user_id, payload.context
    )
    flag_evaluations_total.labels(result=result.reason).inc()
    return EvaluateResponse(
        flag_key=payload.flag_key,
        user_id=payload.user_id,
        enabled=result.enabled,
        reason=result.reason,
    )
