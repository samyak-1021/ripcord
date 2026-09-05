"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { AuditList } from "@/components/AuditList";
import { EvaluatePanel } from "@/components/EvaluatePanel";
import { RulesEditor } from "@/components/RulesEditor";
import { Toggle } from "@/components/Toggle";
import { api, type AuditEntry, type Flag } from "@/lib/api";

export default function FlagDetailPage() {
  const { key } = useParams<{ key: string }>();
  const router = useRouter();

  const [flag, setFlag] = useState<Flag | null>(null);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [rollout, setRollout] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);

  const load = useCallback(async () => {
    try {
      const found = await api.getFlag(key);
      setFlag(found);
      setRollout(found.rollout_percentage);
      setAudit(await api.listAudit(key));
    } catch {
      setNotFound(true);
    }
  }, [key]);

  useEffect(() => {
    load();
  }, [load]);

  async function patch(body: { enabled?: boolean; rollout_percentage?: number }) {
    if (!flag) return;
    setBusy(true);
    setError(null);
    try {
      await api.updateFlag(key, { version: flag.version, ...body });
      await load();
    } catch (e) {
      setError((e as Error).message);
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!confirm(`Delete flag "${key}"?`)) return;
    await api.deleteFlag(key);
    router.push("/");
  }

  if (notFound) {
    return (
      <div>
        <Link href="/" className="text-sm text-neutral-500 hover:underline">
          ← Flags
        </Link>
        <p className="mt-8 text-sm text-neutral-400">{`Flag "${key}" not found.`}</p>
      </div>
    );
  }

  if (!flag) return <p className="text-sm text-neutral-400">Loading…</p>;

  return (
    <div>
      <Link href="/" className="text-sm text-neutral-500 hover:underline">
        ← Flags
      </Link>

      <header className="mb-6 mt-4 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{flag.name}</h1>
          <p className="mt-1 font-mono text-xs text-neutral-500">
            {flag.key} · v{flag.version}
          </p>
        </div>
        <Toggle
          checked={flag.enabled}
          disabled={busy}
          onChange={(v) => patch({ enabled: v })}
        />
      </header>

      <div className="flex flex-col gap-4">
        <section className="rounded-2xl border border-black/5 bg-white p-5 shadow-sm">
          <div className="mb-1 flex items-center justify-between text-xs">
            <span className="font-semibold text-neutral-700">Rollout</span>
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
          {error && <p className="mt-2 text-xs text-red-500">{error}</p>}
        </section>

        <RulesEditor flag={flag} onSaved={load} />
        <EvaluatePanel flagKey={flag.key} />

        <section className="rounded-2xl border border-black/5 bg-white p-5 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold text-neutral-700">History</h2>
          <AuditList entries={audit} />
        </section>

        <div className="flex justify-end">
          <button
            type="button"
            onClick={remove}
            className="text-xs font-medium text-red-500 transition hover:text-red-600"
          >
            Delete flag
          </button>
        </div>
      </div>
    </div>
  );
}
