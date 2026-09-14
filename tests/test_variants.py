"""Engine tests for multivariate flags.

These are pure-function tests (no I/O), which is the whole reason the engine has
no ORM types in it. They assert the properties a weighted rollout has to hold:
stickiness, a distribution that matches the configured weights, independence
from storage order, decorrelation from the rollout hash, and — most
importantly — that adding variants changed nothing about boolean flags.
"""

import statistics

import pytest

from ripcord.engine import (
    FlagSpec,
    RuleSpec,
    VariantSpec,
    bucket_for,
    choose_variant,
    evaluate,
)

USERS = [f"user-{i}" for i in range(20_000)]


def mv_flag(
    *,
    weights: dict[str, int],
    enabled: bool = True,
    rollout: int = 100,
    off_variant: str | None = None,
    rules: list[RuleSpec] | None = None,
) -> FlagSpec:
    return FlagSpec(
        key="experiment",
        enabled=enabled,
        rollout_percentage=rollout,
        rules=rules or [],
        variants=[
            VariantSpec(key=k, value={"label": k}, weight=w) for k, w in weights.items()
        ],
        off_variant=off_variant,
    )


# --- Back-compatibility: boolean flags must be untouched ---------------------


def test_boolean_flag_has_no_variant_or_value() -> None:
    flag = FlagSpec(key="f", enabled=True, rollout_percentage=100)
    result = evaluate(flag, "u1")
    assert result.enabled is True
    assert result.variant is None
    assert result.value is None


def test_boolean_rollout_is_still_monotonic() -> None:
    """Raising the rollout may only ever add users — the original property."""
    included: set[str] = set()
    for pct in range(0, 101, 5):
        flag = FlagSpec(key="f", enabled=True, rollout_percentage=pct)
        now = {u for u in USERS[:5000] if evaluate(flag, u).enabled}
        assert included <= now, f"a user was dropped when rollout rose to {pct}%"
        included = now


def test_disabled_boolean_flag_is_off_for_everyone() -> None:
    flag = FlagSpec(key="f", enabled=False, rollout_percentage=100)
    assert all(evaluate(flag, u).enabled is False for u in USERS[:500])


# --- Stickiness --------------------------------------------------------------


def test_variant_assignment_is_sticky() -> None:
    flag = mv_flag(weights={"control": 34, "blue": 33, "green": 33})
    for user in USERS[:500]:
        seen = {evaluate(flag, user).variant for _ in range(20)}
        assert len(seen) == 1, f"{user} flip-flopped between variants"


def test_variant_is_independent_of_storage_order() -> None:
    """Reordering variants in the dashboard must not reshuffle users.

    The engine sorts by key before laying out the cumulative bands, so the two
    flags below are the same flag as far as any user is concerned.
    """
    forward = mv_flag(weights={"control": 50, "blue": 30, "green": 20})
    shuffled = FlagSpec(
        key="experiment",
        enabled=True,
        rollout_percentage=100,
        variants=list(reversed(forward.variants)),
    )
    for user in USERS[:2000]:
        assert evaluate(forward, user).variant == evaluate(shuffled, user).variant


# --- Distribution ------------------------------------------------------------


@pytest.mark.parametrize(
    "weights",
    [
        {"a": 50, "b": 50},
        {"control": 34, "blue": 33, "green": 33},
        {"a": 10, "b": 20, "c": 70},
        {"a": 1, "b": 99},
    ],
)
def test_distribution_matches_weights(weights: dict[str, int]) -> None:
    """Observed split must track the configured weights within tolerance."""
    flag = mv_flag(weights=weights)
    counts = dict.fromkeys(weights, 0)
    for user in USERS:
        counts[evaluate(flag, user).variant] += 1

    for key, weight in weights.items():
        observed = 100 * counts[key] / len(USERS)
        assert abs(observed - weight) < 1.5, (
            f"variant {key}: expected ~{weight}%, observed {observed:.2f}%"
        )


def test_zero_weight_variant_is_never_served() -> None:
    flag = mv_flag(weights={"live": 100, "retired": 0})
    assert {evaluate(flag, u).variant for u in USERS[:5000]} == {"live"}


def test_variant_choice_is_decorrelated_from_rollout_bucket() -> None:
    """The property that makes a partial rollout of an experiment valid.

    If variant choice reused the rollout hash, the users admitted by a 10%
    rollout would all sit in the lowest buckets and therefore all land in the
    first variant — silently ruining the experiment. Different salts mean the
    split inside a 10% rollout still matches the weights.
    """
    flag = mv_flag(weights={"a": 50, "b": 50}, rollout=10)
    included = [u for u in USERS if evaluate(flag, u).enabled]
    assert len(included) > 500, "sanity: the 10% rollout should admit many users"

    a = sum(1 for u in included if evaluate(flag, u).variant == "a")
    share = 100 * a / len(included)
    assert 45 < share < 55, (
        f"variant split inside the rollout was {share:.1f}% — "
        "variant choice looks correlated with rollout inclusion"
    )


def test_bucket_salt_produces_independent_buckets() -> None:
    """The salted and unsalted buckets must not track each other."""
    pairs = [(bucket_for("f", u), bucket_for("f", u, salt="variant")) for u in USERS]
    unsalted = [p[0] for p in pairs]
    salted = [p[1] for p in pairs]
    assert statistics.correlation(unsalted, salted) < 0.05
    assert sum(1 for a, b in pairs if a == b) < len(pairs) * 0.01


# --- Off variant and rollout exclusion ---------------------------------------


def test_excluded_users_get_the_off_variant() -> None:
    flag = mv_flag(weights={"on": 100, "off": 0}, rollout=0, off_variant="off")
    result = evaluate(flag, "u1")
    assert result.enabled is False
    assert result.reason == "rollout_excluded"
    assert result.variant == "off"
    assert result.value == {"label": "off"}


def test_disabled_flag_serves_the_off_variant() -> None:
    """The kill switch on a multivariate flag still serves a defined value."""
    flag = mv_flag(weights={"a": 50, "b": 50}, enabled=False, off_variant="a")
    result = evaluate(flag, "u1")
    assert result.enabled is False
    assert result.reason == "flag_disabled"
    assert result.variant == "a"


def test_without_off_variant_excluded_users_get_nothing() -> None:
    flag = mv_flag(weights={"a": 50, "b": 50}, rollout=0)
    result = evaluate(flag, "u1")
    assert result.enabled is False
    assert result.variant is None
    assert result.value is None


# --- Targeting rules ---------------------------------------------------------


def test_rule_can_pin_a_variant() -> None:
    flag = mv_flag(
        weights={"control": 50, "blue": 50},
        rollout=0,
        rules=[
            RuleSpec(attribute="country", operator="in", values=["IN"], variant="blue")
        ],
    )
    hit = evaluate(flag, "u1", {"country": "IN"})
    assert hit.enabled is True
    assert hit.reason == "targeting_match"
    assert hit.variant == "blue"

    miss = evaluate(flag, "u1", {"country": "US"})
    assert miss.enabled is False


def test_pinned_rule_overrides_the_weighted_split_for_everyone() -> None:
    flag = mv_flag(
        weights={"control": 99, "blue": 1},
        rules=[
            RuleSpec(attribute="plan", operator="eq", values=["beta"], variant="blue")
        ],
    )
    variants = {
        evaluate(flag, u, {"plan": "beta"}).variant for u in USERS[:2000]
    }
    assert variants == {"blue"}


def test_rule_without_a_variant_falls_back_to_the_split() -> None:
    """An unpinned rule on a multivariate flag still yields a real variant."""
    flag = mv_flag(
        weights={"a": 50, "b": 50},
        rollout=0,
        rules=[RuleSpec(attribute="beta", operator="eq", values=["yes"])],
    )
    result = evaluate(flag, "u1", {"beta": "yes"})
    assert result.enabled is True
    assert result.variant in {"a", "b"}


def test_rule_pinning_a_deleted_variant_degrades_gracefully() -> None:
    """A stale rule must not make the flag serve nothing.

    Deleting a variant that a rule still references is an easy operator
    mistake. The engine is total — it never raises — so it falls back to the
    weighted split rather than returning a null variant to production traffic.
    """
    flag = mv_flag(
        weights={"a": 50, "b": 50},
        rules=[
            RuleSpec(attribute="x", operator="eq", values=["1"], variant="deleted")
        ],
    )
    result = evaluate(flag, "u1", {"x": "1"})
    assert result.enabled is True
    assert result.variant in {"a", "b"}


# --- Degenerate inputs -------------------------------------------------------


def test_all_zero_weights_does_not_divide_by_zero() -> None:
    flag = mv_flag(weights={"a": 0, "b": 0})
    assert evaluate(flag, "u1").variant == "a"  # first by key order


def test_choose_variant_on_boolean_flag_returns_none() -> None:
    assert choose_variant(FlagSpec(key="f", enabled=True, rollout_percentage=50), "u") is None


def test_weights_not_summing_to_100_still_distribute_proportionally() -> None:
    """The engine tolerates what the API validates against.

    The API rejects weights that don't sum to 100, but the engine must stay
    total for a ruleset an older SDK cached before that rule existed.
    """
    flag = FlagSpec(
        key="f",
        enabled=True,
        rollout_percentage=100,
        variants=[VariantSpec("a", weight=1), VariantSpec("b", weight=3)],
    )
    counts = {"a": 0, "b": 0}
    for user in USERS:
        counts[evaluate(flag, user).variant] += 1
    assert abs(100 * counts["a"] / len(USERS) - 25) < 2
