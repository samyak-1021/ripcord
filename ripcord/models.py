"""SQLAlchemy ORM models: feature flags, targeting rules, and the audit log."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import DateTime

from ripcord.db import Base


class Flag(Base):
    """A feature flag: the unit teams toggle, target, and roll out."""

    __tablename__ = "flags"
    __table_args__ = (
        # Rollout is a percentage — keep it sane at the database level too.
        CheckConstraint(
            "rollout_percentage >= 0 AND rollout_percentage <= 100",
            name="ck_flags_rollout_percentage",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stable programmatic identifier used by SDKs, e.g. "new-checkout".
    key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(String(1024), default=None)
    # Master switch. When False the flag is off for everyone (the kill switch).
    enabled: Mapped[bool] = mapped_column(default=False)
    # Percentage of otherwise-unmatched users who receive the flag (0..100).
    rollout_percentage: Mapped[int] = mapped_column(default=0)
    # Optimistic-locking counter, bumped on every update (used in Phase 2).
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    rules: Mapped[list["TargetingRule"]] = relationship(
        back_populates="flag",
        cascade="all, delete-orphan",
        order_by="TargetingRule.priority",
    )


class TargetingRule(Base):
    """An attribute-based rule attached to a flag (e.g. country in [IN, US])."""

    __tablename__ = "targeting_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    flag_id: Mapped[int] = mapped_column(
        ForeignKey("flags.id", ondelete="CASCADE"), index=True
    )
    # The user attribute this rule inspects, e.g. "country" or "plan".
    attribute: Mapped[str] = mapped_column(String(128))
    # Comparison operator, e.g. "in", "eq", "neq" (evaluated in Phase 3).
    operator: Mapped[str] = mapped_column(String(32))
    # Values to compare against, stored as JSONB (e.g. ["IN", "US"]).
    values: Mapped[list[str]] = mapped_column(JSONB)
    # Lower priority numbers are evaluated first.
    priority: Mapped[int] = mapped_column(default=0)

    flag: Mapped["Flag"] = relationship(back_populates="rules")


class AuditLog(Base):
    """Append-only history of every change made to a flag."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    flag_key: Mapped[str] = mapped_column(String(128), index=True)
    # What happened: "created", "updated", "deleted", ...
    action: Mapped[str] = mapped_column(String(32))
    # Who did it (an API-key label in a later phase; "system" for now).
    actor: Mapped[str] = mapped_column(String(128), default="system")
    # Optional structured context, e.g. a before/after snapshot.
    details: Mapped[dict | None] = mapped_column(JSONB, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
