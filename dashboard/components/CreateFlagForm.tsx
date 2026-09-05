"use client";

import { type FormEvent, useState } from "react";

import { api } from "@/lib/api";

const inputClass =
  "flex-1 rounded-xl border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm outline-none transition focus:border-neutral-400";

export function CreateFlagForm({ onCreated }: { onCreated: () => void }) {
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.createFlag({
        key,
        name: name || key,
        enabled: false,
        rollout_percentage: 0,
      });
      setKey("");
      setName("");
      onCreated();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      className="rounded-2xl border border-black/5 bg-white p-5 shadow-sm"
    >
      <h2 className="text-sm font-semibold text-neutral-700">New flag</h2>
      <div className="mt-3 flex flex-col gap-3 sm:flex-row">
        <input
          value={key}
          onChange={(e) => setKey(e.target.value)}
          required
          placeholder="flag-key"
          pattern="[a-z0-9][a-z0-9._-]*"
          title="lowercase letters, digits, dot, underscore or hyphen"
          className={inputClass}
        />
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Display name (optional)"
          className={inputClass}
        />
        <button
          type="submit"
          disabled={busy || !key}
          className="rounded-xl bg-neutral-900 px-4 py-2 text-sm font-medium text-white transition hover:bg-neutral-700 disabled:opacity-40"
        >
          {busy ? "Creating…" : "Create"}
        </button>
      </div>
      {error && <p className="mt-2 text-xs text-red-500">{error}</p>}
    </form>
  );
}
