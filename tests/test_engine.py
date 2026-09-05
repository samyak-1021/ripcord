"""Unit tests for the pure flag-evaluation engine (no I/O, no database)."""

from ripcord.engine import FlagSpec, RuleSpec, bucket_for, evaluate


def _flag(*, enabled: bool = True, rollout: int = 0, rules=None) -> FlagSpec:
    return FlagSpec(
        key="test-flag", enabled=enabled, rollout_percentage=rollout, rules=rules or []
    )


def test_disabled_flag_is_off_for_everyone():
    result = evaluate(_flag(enabled=False, rollout=100), "user-1")
    assert result.enabled is False
    assert result.reason == "flag_disabled"


def test_zero_rollout_is_off():
    result = evaluate(_flag(rollout=0), "user-1")
    assert result.enabled is False
    assert result.reason == "rollout_excluded"


def test_full_rollout_is_on():
    result = evaluate(_flag(rollout=100), "user-1")
    assert result.enabled is True
    assert result.reason == "rollout_included"


def test_in_operator_matches():
    rules = [RuleSpec("country", "in", ["IN", "US"])]
    result = evaluate(_flag(rollout=0, rules=rules), "u", {"country": "IN"})
    assert result.enabled is True
    assert result.reason == "targeting_match"


def test_not_in_operator_matches_when_absent_from_list():
    rules = [RuleSpec("country", "not_in", ["US"])]
    assert evaluate(_flag(rollout=0, rules=rules), "u", {"country": "IN"}).enabled


def test_eq_and_neq_operators():
    eq_rule = [RuleSpec("plan", "eq", ["pro"])]
    assert evaluate(_flag(rollout=0, rules=eq_rule), "u", {"plan": "pro"}).enabled
    neq_rule = [RuleSpec("plan", "neq", ["free"])]
    assert evaluate(_flag(rollout=0, rules=neq_rule), "u", {"plan": "pro"}).enabled


def test_targeting_rule_overrides_zero_rollout():
    # A matching rule turns the flag on even when the rollout is 0%.
    rules = [RuleSpec("country", "in", ["IN"])]
    assert evaluate(_flag(rollout=0, rules=rules), "u", {"country": "IN"}).enabled


def test_missing_attribute_never_matches():
    rules = [RuleSpec("country", "in", ["IN"])]
    assert not evaluate(_flag(rollout=0, rules=rules), "u", {}).enabled


def test_unknown_operator_fails_closed():
    rules = [RuleSpec("country", "??", ["IN"])]
    assert not evaluate(_flag(rollout=0, rules=rules), "u", {"country": "IN"}).enabled


def test_bucketing_is_deterministic():
    assert bucket_for("flag", "user-1") == bucket_for("flag", "user-1")


def test_bucket_is_in_range():
    assert 0 <= bucket_for("flag", "user-xyz") < 10_000


def test_evaluation_is_sticky():
    flag = _flag(rollout=50)
    assert evaluate(flag, "user-42").enabled == evaluate(flag, "user-42").enabled


def test_rollout_is_monotonic():
    # A user included at a lower percentage must stay included as it grows.
    for i in range(2000):
        user = f"user-{i}"
        if evaluate(_flag(rollout=10), user).enabled:
            assert evaluate(_flag(rollout=20), user).enabled
            assert evaluate(_flag(rollout=100), user).enabled


def test_rollout_distribution_is_approximately_uniform():
    users = [f"user-{i}" for i in range(10_000)]
    included = sum(1 for u in users if evaluate(_flag(rollout=50), u).enabled)
    # Expect ~5000 included; the band is very wide (~6 sigma) to avoid flakiness.
    assert 4700 <= included <= 5300
