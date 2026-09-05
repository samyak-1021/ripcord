"""Business logic for managing flags: CRUD, audit logging, optimistic locking.

Kept separate from the HTTP layer so the same operations can be reused (e.g. by
the real-time layer) and unit-tested without going through FastAPI.
"""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from ripcord.engine import Evaluation, FlagSpec, RuleSpec, evaluate
from ripcord.logging_config import log
from ripcord.models import AuditLog, Flag, TargetingRule
from ripcord.schemas import FlagCreate, FlagUpdate, TargetingRuleIn


class DuplicateFlagError(Exception):
    """Raised when creating a flag whose key already exists."""


class FlagNotFoundError(Exception):
    """Raised when a flag key does not exist."""


class VersionConflictError(Exception):
    """Raised when an update's expected version != the flag's current version."""

    def __init__(self, expected: int, current: int | None) -> None:
        self.expected = expected
        self.current = current
        super().__init__(f"version conflict: expected {expected}, current {current}")


def _rules_from_input(rules: Iterable[TargetingRuleIn]) -> list[TargetingRule]:
    """Convert inbound schema rules into ORM TargetingRule rows."""
    return [
        TargetingRule(
            attribute=rule.attribute,
            operator=rule.operator.value,
            values=rule.values,
            priority=rule.priority,
        )
        for rule in rules
    ]


async def get_flag(session: AsyncSession, key: str) -> Flag | None:
    """Fetch a single flag by key (rules eager-loaded), or None if absent."""
    result = await session.execute(select(Flag).where(Flag.key == key))
    return result.scalar_one_or_none()


async def list_flags(session: AsyncSession) -> list[Flag]:
    """Return all flags, ordered by key."""
    result = await session.execute(select(Flag).order_by(Flag.key))
    return list(result.scalars().all())


async def create_flag(
    session: AsyncSession, data: FlagCreate, actor: str = "system"
) -> Flag:
    """Create a flag and its rules, recording a 'created' audit entry."""
    flag = Flag(
        key=data.key,
        name=data.name,
        description=data.description,
        enabled=data.enabled,
        rollout_percentage=data.rollout_percentage,
        rules=_rules_from_input(data.rules),
    )
    session.add(flag)
    session.add(
        AuditLog(
            flag_key=data.key,
            action="created",
            actor=actor,
            details={
                "enabled": data.enabled,
                "rollout_percentage": data.rollout_percentage,
            },
        )
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise DuplicateFlagError(data.key) from exc
    log.info("flag.created", key=data.key, actor=actor)
    return await get_flag(session, data.key)


async def update_flag(
    session: AsyncSession, key: str, data: FlagUpdate, actor: str = "system"
) -> Flag:
    """Apply a partial, version-checked update and record an audit entry."""
    flag = await get_flag(session, key)
    if flag is None:
        raise FlagNotFoundError(key)
    # Fast path: reject a client acting on a stale copy, with a precise message.
    if flag.version != data.version:
        raise VersionConflictError(expected=data.version, current=flag.version)

    changes: dict[str, object] = {}
    if data.name is not None:
        flag.name = data.name
        changes["name"] = data.name
    if data.description is not None:
        flag.description = data.description
        changes["description"] = data.description
    if data.enabled is not None:
        flag.enabled = data.enabled
        changes["enabled"] = data.enabled
    if data.rollout_percentage is not None:
        flag.rollout_percentage = data.rollout_percentage
        changes["rollout_percentage"] = data.rollout_percentage
    if data.rules is not None:
        flag.rules = _rules_from_input(data.rules)
        changes["rules"] = len(data.rules)

    # Bump the version ourselves; SQLAlchemy adds the WHERE-version guard.
    flag.version += 1
    session.add(AuditLog(flag_key=key, action="updated", actor=actor, details=changes))
    try:
        await session.commit()
    except StaleDataError as exc:
        # A concurrent writer changed the row between our read and our commit.
        await session.rollback()
        raise VersionConflictError(expected=data.version, current=None) from exc
    log.info("flag.updated", key=key, version=flag.version, actor=actor)
    return await get_flag(session, key)


async def delete_flag(session: AsyncSession, key: str, actor: str = "system") -> bool:
    """Delete a flag, recording a 'deleted' audit entry. False if it was absent."""
    flag = await get_flag(session, key)
    if flag is None:
        return False
    # Audit keys off the string, so history survives the row's deletion.
    session.add(AuditLog(flag_key=key, action="deleted", actor=actor))
    await session.delete(flag)
    try:
        await session.commit()
    except StaleDataError as exc:
        # A concurrent writer changed the row between our read and our delete.
        await session.rollback()
        raise VersionConflictError(expected=flag.version, current=None) from exc
    log.info("flag.deleted", key=key, actor=actor)
    return True


def _to_spec(flag: Flag) -> FlagSpec:
    """Project an ORM Flag (with its rules) into an engine FlagSpec."""
    return FlagSpec(
        key=flag.key,
        enabled=flag.enabled,
        rollout_percentage=flag.rollout_percentage,
        rules=[
            RuleSpec(attribute=r.attribute, operator=r.operator, values=list(r.values))
            for r in flag.rules
        ],
    )


async def evaluate_flag(
    session: AsyncSession,
    key: str,
    user_id: str,
    context: dict[str, str] | None = None,
) -> Evaluation:
    """Evaluate a flag for a user. An unknown flag resolves to 'off' (fail-safe)."""
    flag = await get_flag(session, key)
    if flag is None:
        return Evaluation(enabled=False, reason="flag_not_found")
    return evaluate(_to_spec(flag), user_id, context)


async def list_audit(
    session: AsyncSession, flag_key: str | None = None, limit: int = 100
) -> list[AuditLog]:
    """Return recent audit entries, newest first, optionally filtered by flag."""
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if flag_key is not None:
        stmt = stmt.where(AuditLog.flag_key == flag_key)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def compute_stats(session: AsyncSession) -> dict[str, int]:
    """Aggregate flag counts (enabled vs disabled) for the metrics page."""
    flags = await list_flags(session)
    enabled = sum(1 for flag in flags if flag.enabled)
    return {
        "flags_total": len(flags),
        "flags_enabled": enabled,
        "flags_disabled": len(flags) - enabled,
    }
