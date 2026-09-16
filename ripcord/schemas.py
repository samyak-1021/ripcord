"""Pydantic request/response schemas for the flag management API."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Operator(StrEnum):
    """Supported targeting-rule comparison operators."""

    in_ = "in"
    not_in = "not_in"
    eq = "eq"
    neq = "neq"


VariantValue = dict | list | str | int | float | bool | None


class VariantIn(BaseModel):
    """One arm of a multivariate flag, as supplied by the client."""

    key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    value: VariantValue = None
    weight: int = Field(default=0, ge=0, le=100)


class VariantOut(BaseModel):
    """A variant as returned to the client."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    key: str
    value: VariantValue = None
    weight: int


class TargetingRuleIn(BaseModel):
    """A targeting rule as supplied by the client."""

    attribute: str = Field(min_length=1, max_length=128)
    operator: Operator
    values: list[str] = Field(min_length=1)
    priority: int = 0
    # On a multivariate flag, pin a specific variant for matching users.
    variant: str | None = Field(default=None, max_length=64)


class TargetingRuleOut(BaseModel):
    """A targeting rule as returned to the client."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    attribute: str
    operator: Operator
    values: list[str]
    priority: int
    variant: str | None = None



def _check_rule_variants(
    rules: list[TargetingRuleIn], variants: list[VariantIn]
) -> None:
    """A rule may only pin a variant the flag actually defines."""
    keys = {v.key for v in variants}
    for rule in rules:
        if rule.variant is not None and rule.variant not in keys:
            raise ValueError(
                f"rule pins unknown variant '{rule.variant}'"
                + (f"; defined: {', '.join(sorted(keys))}" if keys else "")
            )


def _validate_variants(
    variants: list[VariantIn] | None, off_variant: str | None
) -> None:
    """Reject variant sets that the engine could not serve coherently.

    Validating here rather than in the engine keeps the engine pure and total:
    it never raises, so the SDK can evaluate a stale-but-parsable ruleset
    without a try/except on the hot path.
    """
    if not variants:
        # A boolean flag may not name an off variant - there are none to name.
        if off_variant is not None:
            raise ValueError("off_variant requires at least one variant")
        return

    keys = [v.key for v in variants]
    duplicates = sorted({k for k in keys if keys.count(k) > 1})
    if duplicates:
        raise ValueError(f"duplicate variant key(s): {', '.join(duplicates)}")
    if len(variants) < 2:
        raise ValueError("a multivariate flag needs at least 2 variants")

    total = sum(v.weight for v in variants)
    if total != 100:
        raise ValueError(f"variant weights must sum to 100 (got {total})")

    if off_variant is not None and off_variant not in keys:
        raise ValueError(f"off_variant '{off_variant}' is not one of: {', '.join(keys)}")


class FlagCreate(BaseModel):
    """Payload to create a new flag."""

    # A URL/SDK-friendly key: lowercase, starts alphanumeric, then [a-z0-9._-].
    key: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    name: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=1024)
    enabled: bool = False
    rollout_percentage: int = Field(default=0, ge=0, le=100)
    rules: list[TargetingRuleIn] = Field(default_factory=list)
    variants: list[VariantIn] = Field(default_factory=list)
    off_variant: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _check_variants(self) -> "FlagCreate":
        _validate_variants(self.variants, self.off_variant)
        _check_rule_variants(self.rules, self.variants)
        return self


class FlagUpdate(BaseModel):
    """Partial update payload. `version` is required for optimistic locking."""

    name: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=1024)
    enabled: bool | None = None
    rollout_percentage: int | None = Field(default=None, ge=0, le=100)
    rules: list[TargetingRuleIn] | None = None
    variants: list[VariantIn] | None = None
    off_variant: str | None = Field(default=None, max_length=64)
    version: int = Field(ge=1, description="Expected current version of the flag")

    @model_validator(mode="after")
    def _check_variants(self) -> "FlagUpdate":
        # Only validate the variant set when this request actually replaces it;
        # a PATCH that only flips `enabled` must not have to resend variants.
        if self.variants is not None:
            _validate_variants(self.variants, self.off_variant)
            _check_rule_variants(self.rules or [], self.variants)
        return self


class FlagOut(BaseModel):
    """A flag as returned to the client."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    key: str
    name: str
    description: str | None
    enabled: bool
    rollout_percentage: int
    version: int
    created_at: datetime
    updated_at: datetime
    off_variant: str | None = None
    rules: list[TargetingRuleOut] = Field(default_factory=list)
    variants: list[VariantOut] = Field(default_factory=list)

    @field_validator("variants")
    @classmethod
    def _stable_variant_order(cls, value: list[VariantOut]) -> list[VariantOut]:
        """Always serialise variants in key order.

        The ORM relationship declares ``order_by``, but that only applies to
        rows loaded by a query — a flag still in the session's identity map
        keeps its insertion order. Sorting here makes the response identical
        either way, which matters because this JSON is what gets cached in
        Redis and diffed by clients. (The engine sorts independently, so
        evaluation was never affected.)
        """
        return sorted(value, key=lambda v: v.key)


class EvaluateRequest(BaseModel):
    """Request to evaluate one flag for a given user + attribute context.

    Every field is bounded. This is the one unauthenticated-by-volume endpoint —
    an SDK calls it on every evaluation — and without limits a single request
    could carry a megabyte of `user_id` to be MD5'd and thousands of context
    entries to be held in memory and walked once per rule. The caps are far
    above any legitimate use and far below anything that hurts.
    """

    flag_key: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=256)
    context: dict[str, str] = Field(default_factory=dict, max_length=64)

    @field_validator("context")
    @classmethod
    def _bound_context_entries(cls, value: dict[str, str]) -> dict[str, str]:
        for key, entry in value.items():
            if len(key) > 128 or len(entry) > 512:
                raise ValueError(
                    f"context entry '{key[:32]}' exceeds the size limit "
                    "(128-char keys, 512-char values)"
                )
        return value


class EvaluateResponse(BaseModel):
    """The evaluation outcome for a flag/user pair, with the deciding reason."""

    flag_key: str
    user_id: str
    enabled: bool
    reason: str
    # None on boolean flags; the served variant key/value on multivariate ones.
    variant: str | None = None
    value: VariantValue = None


class AuditEntry(BaseModel):
    """One row of the append-only change history."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    flag_key: str
    action: str
    actor: str
    details: dict | None
    created_at: datetime


class Stats(BaseModel):
    """Aggregate numbers for the metrics page."""

    flags_total: int
    flags_enabled: int
    flags_disabled: int
    evaluations_total: int
    evaluations_by_result: dict[str, int]


class ApiKeyCreate(BaseModel):
    """Request body for minting a new API key."""

    name: str = Field(
        ..., min_length=1, max_length=128, description="Human label, e.g. 'ci-deploy'"
    )
    scopes: list[str] = Field(
        ..., min_length=1, description="Granted scopes, e.g. ['flags:read', 'sdk']"
    )

    @field_validator("scopes")
    @classmethod
    def _known_scopes(cls, value: list[str]) -> list[str]:
        """Reject unknown scopes loudly rather than silently granting nothing."""
        from ripcord.auth import ALL_SCOPES

        unknown = sorted(set(value) - ALL_SCOPES)
        if unknown:
            raise ValueError(
                f"unknown scope(s): {', '.join(unknown)}. "
                f"Valid scopes: {', '.join(sorted(ALL_SCOPES))}"
            )
        return value


class ApiKeyOut(BaseModel):
    """An API key as returned by list/revoke — never includes the secret."""

    model_config = ConfigDict(from_attributes=True)

    key_id: str
    name: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class ApiKeyCreated(ApiKeyOut):
    """The one and only response that carries the plaintext key."""

    key: str = Field(
        ..., description="The full key. Shown once — it cannot be recovered later."
    )
