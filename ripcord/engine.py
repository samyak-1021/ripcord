"""The flag-evaluation engine — pure, deterministic, and dependency-free.

Given a flag's configuration and a user context, decide whether the flag is on
for that user. This module has no I/O and no ORM types on purpose: it is trivial
to unit-test, and can be reused verbatim by the client SDK (which evaluates
flags locally instead of calling the server on every check).
"""

import hashlib
from dataclasses import dataclass

# Number of buckets a user can fall into. 10,000 gives rollout precision of
# 0.01%, which is plenty and keeps the percentage arithmetic exact.
_BUCKET_COUNT = 10_000


@dataclass(frozen=True)
class RuleSpec:
    """A single targeting rule, decoupled from the ORM model."""

    attribute: str
    operator: str
    values: list[str]


@dataclass(frozen=True)
class FlagSpec:
    """Everything the engine needs to evaluate one flag."""

    key: str
    enabled: bool
    rollout_percentage: int
    rules: list[RuleSpec]


@dataclass(frozen=True)
class Evaluation:
    """The outcome of evaluating a flag for a user, plus why."""

    enabled: bool
    reason: str


def bucket_for(flag_key: str, user_id: str) -> int:
    """Map a (flag, user) pair to a stable bucket in ``[0, _BUCKET_COUNT)``.

    We hash ``flag_key:user_id`` with MD5 (fast, and only used for bucketing —
    not security) so the result is identical across processes and machines. That
    determinism is what makes a percentage rollout *sticky*: the same user always
    lands in the same bucket, so they never flip-flop between on and off.
    """
    digest = hashlib.md5(
        f"{flag_key}:{user_id}".encode(), usedforsecurity=False
    ).hexdigest()
    return int(digest[:8], 16) % _BUCKET_COUNT


def _rule_matches(rule: RuleSpec, context: dict[str, str]) -> bool:
    """Return True if the user context satisfies the rule.

    A missing attribute never matches — targeting is opt-in — and both an
    unknown operator and a rule with no values fail closed rather than raising.
    """
    actual = context.get(rule.attribute)
    if actual is None:
        return False
    if rule.operator == "in":
        return actual in rule.values
    if rule.operator == "not_in":
        return actual not in rule.values
    if rule.operator == "eq":
        return bool(rule.values) and actual == rule.values[0]
    if rule.operator == "neq":
        return bool(rule.values) and actual != rule.values[0]
    return False


def _in_rollout(flag_key: str, user_id: str, rollout_percentage: int) -> bool:
    """Decide the percentage rollout for a user, stably and monotonically.

    The user's bucket is fixed, and the threshold only grows with the
    percentage — so raising the rollout can only *add* users, never drop one
    who was already in (the "monotonic rollout" property).
    """
    if rollout_percentage <= 0:
        return False
    if rollout_percentage >= 100:
        return True
    threshold = rollout_percentage * (_BUCKET_COUNT // 100)
    return bucket_for(flag_key, user_id) < threshold


def evaluate(
    flag: FlagSpec, user_id: str, context: dict[str, str] | None = None
) -> Evaluation:
    """Evaluate a flag for a user.

    Precedence:
      1. Master switch off        -> off for everyone (the kill switch).
      2. A matching targeting rule -> on (rules are allow-list overrides).
      3. Percentage rollout by sticky bucket -> on / off.
    """
    context = context or {}

    if not flag.enabled:
        return Evaluation(enabled=False, reason="flag_disabled")

    for rule in flag.rules:
        if _rule_matches(rule, context):
            return Evaluation(enabled=True, reason="targeting_match")

    if _in_rollout(flag.key, user_id, flag.rollout_percentage):
        return Evaluation(enabled=True, reason="rollout_included")

    return Evaluation(enabled=False, reason="rollout_excluded")
