"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { CreateFlagForm } from "@/components/CreateFlagForm";
import { FlagCard } from "@/components/FlagCard";
import { api, type Flag } from "@/lib/api";

export default function FlagsPage() {
  const [flags, setFlags] = useState<Flag[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState(false);
  const [query, setQuery] = useState("");

  const load = useCallback(async () => {
    try {
      setFlags(await api.listFlags());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    // Live updates: the backend pushes a "flag-change" SSE event on any change.
    const events = new EventSource(api.streamUrl());
    events.onopen = () => setLive(true);
    events.onerror = () => setLive(false);
    events.addEventListener("flag-change", () => load());
    return () => events.close();
  }, [load]);

  const filtered = useMemo(
    () =>
      flags.filter((f) =>
        `${f.key} ${f.name}`.toLowerCase().includes(query.toLowerCase()),
      ),
    [flags, query],
  );

  return (
    <div>
      <header className="mb-8 flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Flags</h1>
          <p className="mt-1 text-sm text-neutral-500">
            {flags.length} flag{flags.length === 1 ? "" : "s"}
          </p>
        </div>
        <span className="flex items-center gap-2 text-xs text-neutral-500">
          <span
            className={`h-2 w-2 rounded-full ${live ? "bg-green-500" : "bg-neutral-300"}`}
          />
          {live ? "Live" : "Offline"}
        </span>
      </header>

      <div className="flex flex-col gap-4">
        <CreateFlagForm onCreated={load} />

        {flags.length > 0 && (
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search flags…"
            className="rounded-xl border border-neutral-200 bg-white px-3 py-2 text-sm outline-none transition focus:border-neutral-400"
          />
        )}

        {loading && <p className="text-sm text-neutral-400">Loading…</p>}
        {error && (
          <p className="rounded-xl bg-red-50 p-3 text-sm text-red-600">
            Couldn&apos;t reach the API — {error}
          </p>
        )}
        {!loading && !error && flags.length === 0 && (
          <p className="rounded-2xl border border-dashed border-neutral-300 p-8 text-center text-sm text-neutral-400">
            No flags yet. Create one above.
          </p>
        )}
        {filtered.map((flag) => (
          <FlagCard key={flag.key} flag={flag} onChanged={load} />
        ))}
      </div>
    </div>
  );
}
