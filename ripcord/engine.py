"""The flag-evaluation engine — pure, deterministic, and dependency-free.

Given a flag's configuration and a user context, decide what that flag serves
for that user. This module has no I/O and no ORM types on purpose: it is trivial
to unit-test, and can be reused verbatim by the client SDK (which evaluates
flags locally instead of calling the server on every check).

Two flag shapes share one code path:

* **Boolean** (no variants) — the classic on/off flag.
* **Multivariate** — a set of weighted variants (``control`` / ``blue`` /
  ``green``), each carrying an arbitrary JSON value. The rollout percentage
  still decides *whether a user is in the experiment at all*; the weights then
  decide *which variant* they get.

Three properties are load-bearing, and each has a test named after it:

1. **Sticky** — a given (flag, user) always resolves the same way.
2. **Monotonic** — raising a boolean rollout only ever *adds* users.
3. **Decorrelated** — rollout inclusion and variant choice are hashed with
   different salts, so variant assignment isn't skewed by rollout position.
"""

import hashlib
from dataclasses import dataclass, field
from typing import Any

# Number of buckets a user can fall into. 10,000 gives rollout precision of
# 0.01%, which is plenty and keeps the percentage arithmetic exact.
_BUCKET_COUNT = 10_000

# Salt appended to the bucket key when choosing a variant. Without it, variant
# choice and rollout inclusion would share a hash: everyone admitted by a 10%
# rollout would sit in the lowest buckets and therefore all receive whichever
# variant happens to be first. See `test_variant_choice_is_decorrelated`.
_VARIANT_SALT = "variant"


@dataclass(frozen=True)
class RuleSpec:
    """A single targeting rule, decoupled from the ORM model.

    ``variant`` lets a rule serve a specific variant ("users in IN get blue").
    On a boolean flag, or when None, a matching rule simply means "on".
    """

    attribute: str
    operator: str
    values: list[str]
    variant: str | None = None


@dataclass(frozen=True)
class VariantSpec:
    """One arm of a multivariate flag.

    ``value`` is whatever the application needs — a string, a number, a config
    object. The engine never inspects it; it only decides which one is served.
    """

    key: str
    value: Any = None
    weight: int = 0


@dataclass(frozen=True)
class FlagSpec:
    """Everything the engine needs to evaluate one flag."""

    key: str
    enabled: bool
    rollout_percentage: int
    rules: list[RuleSpec] = field(default_factory=list)
    # Empty for a boolean flag. When present, weights must sum to 100.
    variants: list[VariantSpec] = field(default_factory=list)
    # Which variant to serve when the flag is off or the user is out of the
    # rollout. None means "serve nothing" (the boolean-flag behaviour).
    off_variant: str | None = None

    @property
    def is_multivariate(self) -> bool:
        return bool(self.variants)


@dataclass(frozen=True)
class Evaluation:
    """The outcome of evaluating a flag for a user, plus why.

    ``enabled`` means "served from the active distribution": True when the user
    matched a rule or fell inside the rollout, False when they got the off
    variant or nothing. ``variant``/``value`` are None on boolean flags.
    """

    enabled: bool
    reason: str
    variant: str | None = None
    value: Any = None


def bucket_for(flag_key: str, user_id: str, salt: str = "") -> int:
    """Map a (flag, user[, salt]) triple to a stable bucket in ``[0, 10000)``.

    We hash the parts with MD5 (fast, and only used for bucketing — not
    security) so the result is identical across processes and machines. That
    determinism is what makes a percentage rollout *sticky*: the same user
    always lands in the same bucket, so they never flip-flop between on and off.

    ``salt`` gives an independent bucket for the same (flag, user) pair, which
    is how variant choice is decorrelated from rollout inclusion.
    """
    material = f"{flag_key}:{salt}:{user_id}" if salt else f"{flag_key}:{user_id}"
    digest = hashlib.md5(material.encode(), usedforsecurity=False).hexdigest()
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


def _find_variant(flag: FlagSpec, key: str | None) -> VariantSpec | None:
    if key is None:
        return None
    return next((v for v in flag.variants if v.key == key), None)


def choose_variant(flag: FlagSpec, user_id: str) -> VariantSpec | None:
    """Pick a variant for a user by weighted, sticky distribution.

    Variants are walked **in key order**, not in the order they were stored, so
    that reordering them in the dashboard does not reshuffle every user. Weights
    are cumulative: a user's variant bucket falls into exactly one band.

    Note the honest limitation: *changing* a weight moves the band boundaries,
    so users near a boundary can be reassigned. That is inherent to weighted
    bucketing (LaunchDarkly behaves the same way); avoiding it entirely needs
    per-variant consistent hashing with a much larger constant factor.
    """
    if not flag.variants:
        return None
    ordered = sorted(flag.variants, key=lambda v: v.key)
    total = sum(v.weight for v in ordered)
    if total <= 0:
        return ordered[0]

    bucket = bucket_for(flag.key, user_id, salt=_VARIANT_SALT)
    # Scale the bucket into the weight space so this stays correct even if the
    # weights don't sum to exactly 100.
    position = bucket * total / _BUCKET_COUNT
    cumulative = 0.0
    for variant in ordered:
        cumulative += variant.weight
        if position < cumulative:
            return variant
    return ordered[-1]  # float dust at the top edge


def _off_result(flag: FlagSpec, reason: str) -> Evaluation:
    """The result served when a flag is off or the user is out of the rollout."""
    off = _find_variant(flag, flag.off_variant)
    if off is None:
        return Evaluation(enabled=False, reason=reason)
    return Evaluation(enabled=False, reason=reason, variant=off.key, value=off.value)


def evaluate(
    flag: FlagSpec, user_id: str, context: dict[str, str] | None = None
) -> Evaluation:
    """Evaluate a flag for a user.

    Precedence:
      1. Master switch off        -> the off variant (the kill switch).
      2. A matching targeting rule -> on, or that rule's variant if it names one.
      3. Percentage rollout by sticky bucket -> weighted variant / on / off.
    """
    context = context or {}

    if not flag.enabled:
        return _off_result(flag, "flag_disabled")

    for rule in flag.rules:
        if not _rule_matches(rule, context):
            continue
        # A rule may pin a specific variant. If it names one that no longer
        # exists (deleted variant, stale rule), fall back to the weighted
        # distribution rather than serving nothing.
        pinned = _find_variant(flag, rule.variant)
        if pinned is not None:
            return Evaluation(
                enabled=True,
                reason="targeting_match",
                variant=pinned.key,
                value=pinned.value,
            )
        if flag.is_multivariate:
            chosen = choose_variant(flag, user_id)
            return Evaluation(
                enabled=True,
                reason="targeting_match",
                variant=chosen.key if chosen else None,
                value=chosen.value if chosen else None,
            )
        return Evaluation(enabled=True, reason="targeting_match")

    if _in_rollout(flag.key, user_id, flag.rollout_percentage):
        if flag.is_multivariate:
            chosen = choose_variant(flag, user_id)
            return Evaluation(
                enabled=True,
                reason="rollout_included",
                variant=chosen.key if chosen else None,
                value=chosen.value if chosen else None,
            )
        return Evaluation(enabled=True, reason="rollout_included")

    return _off_result(flag, "rollout_excluded")
