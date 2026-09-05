"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Toggle } from "@/components/Toggle";
import { api, type Flag } from "@/lib/api";

export function FlagCard({
  flag,
  onChanged,
}: {
  flag: Flag;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [rollout, setRollout] = useState(flag.rollout_percentage);
  const [error, setError] = useState<string | null>(null);

  // Keep the slider in sync when the flag updates elsewhere (e.g. via SSE).
  useEffect(() => setRollout(flag.rollout_percentage), [flag.rollout_percentage]);

  async function patch(body: { enabled?: boolean; rollout_percentage?: number }) {
    setBusy(true);
    setError(null);
    try {
      await api.updateFlag(flag.key, { version: flag.version, ...body });
      onChanged();
    } catch (e) {
      setError((e as Error).message);
      onChanged(); // resync — a version conflict means our copy is stale
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-2xl border border-black/5 bg-white p-5 shadow-sm">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Link
              href={`/flags/${flag.key}`}
              className="truncate text-base font-semibold hover:underline"
            >
              {flag.name}
            </Link>
            <span className="rounded-full bg-neutral-100 px-2 py-0.5 font-mono text-xs text-neutral-500">
              {flag.key}
            </span>
          </div>
          <p className="mt-1 text-xs text-neutral-400">
            v{flag.version} · {flag.rules.length} rule
            {flag.rules.length === 1 ? "" : "s"}
          </p>
        </div>
        <Toggle
          checked={flag.enabled}
          disabled={busy}
          onChange={(v) => patch({ enabled: v })}
        />
      </div>

      <div className="mt-4">
        <div className="mb-1 flex items-center justify-between text-xs text-neutral-500">
          <span>Rollout</span>
          <span className="font-medium text-neutral-700">{rollout}%</span>
        </div>
        <input
          type="range"
          min={0}
          max={100}
          value={rollout}
          disabled={busy}
          onChange={(e) => setRollout(Number(e.target.value))}
          onPointerUp={() => {
            if (rollout !== flag.rollout_percentage) {
              patch({ rollout_percentage: rollout });
            }
          }}
          className="w-full accent-neutral-800"
        />
      </div>

      {error && <p className="mt-2 text-xs text-red-500">{error}</p>}

      <div className="mt-4 flex justify-end">
        <Link
          href={`/flags/${flag.key}`}
          className="text-xs font-medium text-neutral-500 transition hover:text-neutral-800"
        >
          Details &amp; rules →
        </Link>
      </div>
    </div>
  );
}
