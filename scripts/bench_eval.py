#!/usr/bin/env python3
"""Measure local SDK evaluation latency.

The README claims the SDK evaluates "in microseconds". That was an inference
from the implementation — an MD5 and a short list walk — not a measurement, and
an unmeasured performance claim on a resume project is a question waiting to be
asked. This answers it.

What is measured is the pure decision: rules, rollout bucket, variant band. No
network, no cache, no serialisation, because that is exactly what the SDK does
on an `is_enabled` call once its ruleset is loaded.

    python scripts/bench_eval.py
    python scripts/bench_eval.py --iterations 100000
"""

from __future__ import annotations

import argparse
import statistics
import time

from ripcord.engine import FlagSpec, RuleSpec, VariantSpec, evaluate

# Deliberately not the trivial case: a targeting rule that has to be evaluated
# and missed, a partial rollout, and a two-way variant split. A benchmark of a
# flag with no rules would flatter the number.
SPEC = FlagSpec(
    key="checkout",
    enabled=True,
    rollout_percentage=50,
    rules=[
        RuleSpec(
            attribute="country", operator="in", values=["IN", "US"], variant=None
        )
    ],
    variants=[
        VariantSpec(key="control", value={"color": "grey"}, weight=50),
        VariantSpec(key="blue", value={"color": "blue"}, weight=50),
    ],
)
CONTEXT = {"country": "GB", "plan": "pro"}


def main(iterations: int, warmup: int) -> None:
    for i in range(warmup):
        evaluate(SPEC, f"warmup-{i}", CONTEXT)

    samples: list[int] = []
    for i in range(iterations):
        started = time.perf_counter_ns()
        evaluate(SPEC, f"user-{i}", CONTEXT)
        samples.append(time.perf_counter_ns() - started)
    samples.sort()

    def at(q: float) -> float:
        return samples[min(int(q * len(samples)), len(samples) - 1)] / 1000

    print(f"  iterations : {len(samples):,}")
    print(f"  median     : {statistics.median(samples) / 1000:.2f} us")
    print(f"  p95        : {at(0.95):.2f} us")
    print(f"  p99        : {at(0.99):.2f} us")
    print(f"  throughput : {1e9 / statistics.mean(samples):,.0f} evaluations/sec")
    print(
        "\n  Single core, one process. The point is the order of magnitude: a\n"
        "  local evaluation is thousands of times cheaper than the network\n"
        "  round-trip it replaces, which is the entire argument for the SDK\n"
        "  holding the ruleset instead of calling /evaluate per check."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=20_000)
    parser.add_argument("--warmup", type=int, default=2_000)
    args = parser.parse_args()
    main(args.iterations, args.warmup)
