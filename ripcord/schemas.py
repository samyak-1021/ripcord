"""Pydantic request/response schemas for the flag management API."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Operator(StrEnum):
    """Supported targeting-rule comparison operators (evaluated in Phase 3)."""

    in_ = "in"
    not_in = "not_in"
    eq = "eq"
    neq = "neq"


class TargetingRuleIn(BaseModel):
    """A targeting rule as supplied by the client."""

    attribute: str = Field(min_length=1, max_length=128)
    operator: Operator
    values: list[str] = Field(min_length=1)
    priority: int = 0


class TargetingRuleOut(BaseModel):
    """A targeting rule as returned to the client."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    attribute: str
    operator: Operator
    values: list[str]
    priority: int


class FlagCreate(BaseModel):
    """Payload to create a new flag."""

    # A URL/SDK-friendly key: lowercase, starts alphanumeric, then [a-z0-9._-].
    key: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    name: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=1024)
    enabled: bool = False
    rollout_percentage: int = Field(default=0, ge=0, le=100)
    rules: list[TargetingRuleIn] = Field(default_factory=list)


class FlagUpdate(BaseModel):
    """Partial update payload. `version` is required for optimistic locking."""

    name: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=1024)
    enabled: bool | None = None
    rollout_percentage: int | None = Field(default=None, ge=0, le=100)
    rules: list[TargetingRuleIn] | None = None
    version: int = Field(ge=1, description="Expected current version of the flag")


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
    rules: list[TargetingRuleOut] = Field(default_factory=list)


class EvaluateRequest(BaseModel):
    """Request to evaluate one flag for a given user + attribute context."""

    flag_key: str
    user_id: str
    context: dict[str, str] = Field(default_factory=dict)


class EvaluateResponse(BaseModel):
    """The evaluation outcome for a flag/user pair, with the deciding reason."""

    flag_key: str
    user_id: str
    enabled: bool
    reason: str


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
