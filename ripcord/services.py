"""Business logic for managing flags: CRUD, audit logging, optimistic locking.

Kept separate from the HTTP layer so the same operations can be reused (e.g. by
the real-time layer) and unit-tested without going through FastAPI.
"""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from ripcord import cache
from ripcord.engine import Evaluation, FlagSpec, RuleSpec, VariantSpec, evaluate
from ripcord.logging_config import log
from ripcord.models import AuditLog, Flag, TargetingRule, Variant
from ripcord.schemas import FlagCreate, FlagUpdate, TargetingRuleIn, VariantIn


class DuplicateFlagError(Exception):
    """Raised when creating a flag whose key already exists."""


class FlagNotFoundError(Exception):
    """Raised when a flag key does not exist."""


class InvalidVariantError(Exception):
    """Raised when a rule pins a variant the flag does not define."""


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
            variant=rule.variant,
        )
        for rule in rules
    ]


def _variants_from_input(variants: Iterable[VariantIn]) -> list[Variant]:
    """Convert inbound schema variants into ORM Variant rows."""
    return [
        Variant(key=v.key, value=v.value, weight=v.weight) for v in variants
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
        off_variant=data.off_variant,
        rules=_rules_from_input(data.rules),
        variants=_variants_from_input(data.variants),
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
                "variants": [v.key for v in data.variants] or None,
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
        # The schema can only cross-check rules against variants when the same
        # request replaces both. A PATCH that edits rules alone (the common
        # case from the dashboard) has to be checked against the variants
        # already stored, which only the service layer can see.
        if data.variants is None:
            known = {v.key for v in flag.variants}
            unknown = sorted(
                {r.variant for r in data.rules if r.variant and r.variant not in known}
            )
            if unknown:
                raise InvalidVariantError(
                    f"rule pins unknown variant(s): {', '.join(unknown)}"
                    + (f"; defined: {', '.join(sorted(known))}" if known else "")
                )
        flag.rules = _rules_from_input(data.rules)
        changes["rules"] = len(data.rules)
    if data.variants is not None:
        flag.variants = _variants_from_input(data.variants)
        flag.off_variant = data.off_variant
        changes["variants"] = [v.key for v in data.variants]
        changes["off_variant"] = data.off_variant

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
    """Project an ORM Flag (with its rules and variants) into an engine FlagSpec."""
    return FlagSpec(
        key=flag.key,
        enabled=flag.enabled,
        rollout_percentage=flag.rollout_percentage,
        off_variant=flag.off_variant,
        rules=[
            RuleSpec(
                attribute=r.attribute,
                operator=r.operator,
                values=list(r.values),
                variant=r.variant,
            )
            for r in flag.rules
        ],
        variants=[
            VariantSpec(key=v.key, value=v.value, weight=v.weight)
            for v in flag.variants
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


def flag_payload(flag: Flag) -> str:
    """Serialise one flag to the JSON shape both the cache and /ruleset use."""
    from ripcord.schemas import FlagOut

    return FlagOut.model_validate(flag).model_dump_json()


async def build_ruleset_mapping(session: AsyncSession) -> dict[str, str]:
    """Every flag as ``{key: json}`` — the exact shape of the Redis hash."""
    flags = await list_flags(session)
    return {flag.key: flag_payload(flag) for flag in flags}


def spec_from_payload(payload: str) -> FlagSpec:
    """Rebuild an engine FlagSpec from a cached flag's JSON.

    Deliberately tolerant: missing optional keys fall back to defaults so an
    entry cached by an older build is still evaluable rather than a 500.
    """
    import json

    data = json.loads(payload)
    return FlagSpec(
        key=data["key"],
        enabled=data["enabled"],
        rollout_percentage=data["rollout_percentage"],
        off_variant=data.get("off_variant"),
        rules=[
            RuleSpec(
                attribute=r["attribute"],
                operator=r["operator"],
                values=list(r["values"]),
                variant=r.get("variant"),
            )
            for r in data.get("rules", [])
        ],
        variants=[
            VariantSpec(key=v["key"], value=v.get("value"), weight=v["weight"])
            for v in data.get("variants", [])
        ],
    )


async def evaluate_flag_cached(
    session: AsyncSession,
    redis_client,
    key: str,
    user_id: str,
    context: dict[str, str] | None = None,
) -> Evaluation:
    """Evaluate a flag, serving the ruleset from Redis instead of Postgres.

    This is the hot path. Previously every ``/evaluate`` call did a database
    lookup; now it is a single ``HGET``, with the database touched only to
    repopulate a cold cache. If Redis itself is unreachable we fall back to the
    database rather than failing the request — a degraded cache must not become
    an outage.
    """
    try:
        cached = await cache.read_flag(redis_client, key)
    except Exception:
        log.warning("cache.read_failed", flag_key=key, fallback="database")
        cached = None

    if isinstance(cached, str):
        return evaluate(spec_from_payload(cached), user_id, context)
    if cached is False:
        # Cache is authoritative and has no such flag.
        return Evaluation(enabled=False, reason="flag_not_found")

    # Cold cache: rebuild it from the database, then answer from what we built.
    mapping = await build_ruleset_mapping(session)
    try:
        await cache.write_ruleset(redis_client, mapping)
    except Exception:
        log.warning("cache.write_failed", flag_key=key)
    payload = mapping.get(key)
    if payload is None:
        return Evaluation(enabled=False, reason="flag_not_found")
    return evaluate(spec_from_payload(payload), user_id, context)
