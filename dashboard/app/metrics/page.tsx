"use client";

import { useEffect, useState } from "react";

import { StatCard } from "@/components/StatCard";
import { api, type Stats } from "@/lib/api";

export default function MetricsPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getStats().then(setStats).catch((e) => setError((e as Error).message));
  }, []);

  return (
    <div>
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">Metrics</h1>
        <p className="mt-1 text-sm text-neutral-500">
          Flag composition &amp; evaluation activity
        </p>
      </header>

      {error && (
        <p className="rounded-xl bg-red-50 p-3 text-sm text-red-600">
          Couldn&apos;t reach the API — {error}
        </p>
      )}
      {!stats && !error && <p className="text-sm text-neutral-400">Loading…</p>}

      {stats && (
        <div className="flex flex-col gap-6">
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            <StatCard label="Total flags" value={stats.flags_total} />
            <StatCard label="Enabled" value={stats.flags_enabled} />
            <StatCard label="Disabled" value={stats.flags_disabled} />
          </div>

          <section className="rounded-2xl border border-black/5 bg-white p-5 shadow-sm">
            <h2 className="text-sm font-semibold text-neutral-700">
              Evaluations by result
              <span className="ml-2 font-normal text-neutral-400">
                ({stats.evaluations_total} total)
              </span>
            </h2>
            <div className="mt-4 flex flex-col gap-3">
              {Object.keys(stats.evaluations_by_result).length === 0 && (
                <p className="text-sm text-neutral-400">
                  No evaluations yet — try the Evaluate box on a flag.
                </p>
              )}
              {Object.entries(stats.evaluations_by_result).map(
                ([reason, count]) => {
                  const pct = stats.evaluations_total
                    ? Math.round((count / stats.evaluations_total) * 100)
                    : 0;
                  return (
                    <div key={reason}>
                      <div className="mb-1 flex justify-between text-xs text-neutral-500">
                        <span className="font-mono">{reason}</span>
                        <span>{count}</span>
                      </div>
                      <div className="h-2 w-full overflow-hidden rounded-full bg-neutral-100">
                        <div
                          className="h-full rounded-full bg-neutral-800"
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                    </div>
                  );
                },
              )}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
