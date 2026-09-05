"use client";

import { type FormEvent, useState } from "react";

import { api, type Evaluation } from "@/lib/api";

const inputClass =
  "rounded-xl border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm outline-none transition focus:border-neutral-400";

export function EvaluatePanel() {
  const [flagKey, setFlagKey] = useState("");
  const [userId, setUserId] = useState("user-123");
  const [country, setCountry] = useState("");
  const [result, setResult] = useState<Evaluation | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const context: Record<string, string> = country ? { country } : {};
      setResult(await api.evaluate(flagKey, userId, context));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={run}
      className="rounded-2xl border border-black/5 bg-white p-5 shadow-sm"
    >
      <h2 className="text-sm font-semibold text-neutral-700">Evaluate</h2>
      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <input
          value={flagKey}
          onChange={(e) => setFlagKey(e.target.value)}
          required
          placeholder="flag-key"
          className={inputClass}
        />
        <input
          value={userId}
          onChange={(e) => setUserId(e.target.value)}
          placeholder="user id"
          className={inputClass}
        />
        <input
          value={country}
          onChange={(e) => setCountry(e.target.value)}
          placeholder="country (optional)"
          className={inputClass}
        />
      </div>
      <div className="mt-3 flex items-center gap-3">
        <button
          type="submit"
          disabled={busy || !flagKey}
          className="rounded-xl bg-neutral-900 px-4 py-2 text-sm font-medium text-white transition hover:bg-neutral-700 disabled:opacity-40"
        >
          Evaluate
        </button>
        {result && (
          <span className="text-sm">
            <span
              className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                result.enabled
                  ? "bg-green-100 text-green-700"
                  : "bg-neutral-100 text-neutral-500"
              }`}
            >
              {result.enabled ? "ON" : "OFF"}
            </span>
            <span className="ml-2 font-mono text-xs text-neutral-500">
              {result.reason}
            </span>
          </span>
        )}
      </div>
    </form>
  );
}
