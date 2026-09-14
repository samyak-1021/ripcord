"""SQLAlchemy ORM models: feature flags, targeting rules, and the audit log."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint, func
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
    # On a multivariate flag, which variant to serve when the flag is off or
    # the user falls outside the rollout. None = serve nothing (boolean flag).
    off_variant: Mapped[str | None] = mapped_column(String(64), default=None)
    # Optimistic-locking counter. We bump it ourselves on every update, and
    # `version_id_col` below makes SQLAlchemy add a `WHERE version = :old`
    # guard to each UPDATE/DELETE — so a concurrent writer's stale change is
    # rejected instead of silently lost.
    version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}

    # `selectin` eager-loads rules in a follow-up query, avoiding lazy-load
    # I/O inside async handlers (which SQLAlchemy forbids).
    rules: Mapped[list["TargetingRule"]] = relationship(
        back_populates="flag",
        cascade="all, delete-orphan",
        order_by="TargetingRule.priority",
        lazy="selectin",
    )

    variants: Mapped[list["Variant"]] = relationship(
        back_populates="flag",
        cascade="all, delete-orphan",
        order_by="Variant.key",
        lazy="selectin",
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
    # Comparison operator, e.g. "in", "eq", "neq" (see the evaluation engine).
    operator: Mapped[str] = mapped_column(String(32))
    # Values to compare against, stored as JSONB (e.g. ["IN", "US"]).
    values: Mapped[list[str]] = mapped_column(JSONB)
    # Lower priority numbers are evaluated first.
    priority: Mapped[int] = mapped_column(default=0)
    # On a multivariate flag, a matching rule may pin a specific variant
    # ("users in IN get blue"). None means "just turn it on".
    variant: Mapped[str | None] = mapped_column(String(64), default=None)

    flag: Mapped["Flag"] = relationship(back_populates="rules")


class Variant(Base):
    """One arm of a multivariate flag.

    A flag with no variants is a plain boolean flag — the two shapes share the
    same table and the same evaluation engine, so there is no second code path
    to keep in sync.
    """

    __tablename__ = "variants"
    __table_args__ = (
        # A variant key is only meaningful within its flag.
        UniqueConstraint("flag_id", "key", name="uq_variants_flag_key"),
        CheckConstraint(
            "weight >= 0 AND weight <= 100", name="ck_variants_weight"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    flag_id: Mapped[int] = mapped_column(
        ForeignKey("flags.id", ondelete="CASCADE"), index=True
    )
    # Identifier used by rules and returned in evaluations, e.g. "control".
    key: Mapped[str] = mapped_column(String(64))
    # Arbitrary JSON payload served to the user. The engine never inspects it.
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        JSONB, default=None
    )
    # Share of rollout-included traffic, 0..100. Weights across a flag sum to 100.
    weight: Mapped[int] = mapped_column(default=0)

    flag: Mapped["Flag"] = relationship(back_populates="variants")


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


class ApiKey(Base):
    """A credential for the management API, stored as a digest of its secret.

    Only the ``key_id`` half of a key is stored in plaintext (so lookup is an
    indexed hit); the secret half exists in plaintext exactly once, at creation
    time, and is never recoverable afterwards. See ``ripcord.auth`` for the
    format and the reasoning behind the hash choice.
    """

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Public half of the key — safe to log, index, and show in a UI.
    key_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    # SHA-256 digest of the secret half. Never the secret itself.
    secret_hash: Mapped[str] = mapped_column(String(64))
    # Human label, e.g. "ci-deploy" or "checkout-service". Becomes the audit actor.
    name: Mapped[str] = mapped_column(String(128))
    # Granted scopes, e.g. ["flags:read", "sdk"].
    scopes: Mapped[list[str]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Refreshed lazily (see auth._LAST_USED_REFRESH) so reads stay reads.
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    # Revocation is a tombstone, not a delete: the audit log references the
    # key's name, and "when was this key turned off" is worth keeping.
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
