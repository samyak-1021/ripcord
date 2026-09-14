"""HTTP endpoint for evaluating a flag against a user context."""

from fastapi import APIRouter

from ripcord import services
from ripcord.deps import RedisDep, SdkDep, SessionDep
from ripcord.metrics import flag_evaluations_total
from ripcord.schemas import EvaluateRequest, EvaluateResponse

router = APIRouter(tags=["evaluation"])


@router.post("/evaluate", response_model=EvaluateResponse)
async def evaluate_flag(
    payload: EvaluateRequest,
    principal: SdkDep,
    session: SessionDep,
    redis_client: RedisDep,
) -> EvaluateResponse:
    """Evaluate one flag for a user. Unknown flags resolve to 'off' (fail-safe).

    Served from the Redis ruleset cache — a single HGET — with the database
    touched only to repopulate a cold cache. Apps should still prefer the SDK,
    whose local path needs no network hop at all.
    """
    result = await services.evaluate_flag_cached(
        session, redis_client, payload.flag_key, payload.user_id, payload.context
    )
    flag_evaluations_total.labels(result=result.reason).inc()
    return EvaluateResponse(
        flag_key=payload.flag_key,
        user_id=payload.user_id,
        enabled=result.enabled,
        reason=result.reason,
        variant=result.variant,
        value=result.value,
    )
