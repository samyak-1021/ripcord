"""Integration tests for the ORM models (run against a real Postgres)."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ripcord.models import AuditLog, Flag, TargetingRule


async def test_create_flag_uses_safe_defaults(session):
    """A new flag should default to off, 0% rollout, version 1."""
    flag = Flag(key="new-checkout", name="New Checkout")
    session.add(flag)
    await session.commit()
    await session.refresh(flag)

    assert flag.id is not None
    assert flag.enabled is False
    assert flag.rollout_percentage == 0
    assert flag.version == 1
    assert flag.created_at is not None


async def test_flag_key_is_unique(session):
    """Two flags cannot share the same key."""
    session.add(Flag(key="dup", name="First"))
    await session.commit()

    session.add(Flag(key="dup", name="Second"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_rules_cascade_from_flag(session):
    """Rules added via the relationship persist and read back in priority order."""
    flag = Flag(key="beta", name="Beta")
    flag.rules.append(
        TargetingRule(attribute="country", operator="in", values=["IN", "US"])
    )
    session.add(flag)
    await session.commit()

    result = await session.execute(
        select(TargetingRule).where(TargetingRule.flag_id == flag.id)
    )
    rules = result.scalars().all()
    assert len(rules) == 1
    assert rules[0].values == ["IN", "US"]  # JSONB round-trips as a list


async def test_rollout_percentage_check_constraint(session):
    """The DB rejects an out-of-range rollout percentage."""
    session.add(Flag(key="bad", name="Bad", rollout_percentage=150))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_audit_log_records_details(session):
    """Audit rows store a structured JSONB details payload."""
    session.add(
        AuditLog(
            flag_key="new-checkout",
            action="created",
            actor="samyak",
            details={"enabled": False},
        )
    )
    await session.commit()

    result = await session.execute(select(AuditLog))
    logs = result.scalars().all()
    assert len(logs) == 1
    assert logs[0].action == "created"
    assert logs[0].details == {"enabled": False}
