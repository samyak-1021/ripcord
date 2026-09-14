"use client";

import { useEffect, useMemo, useState } from "react";

import { api, type Flag, type Variant } from "@/lib/api";

type Draft = { key: string; weight: number; value: string };

const inputClass =
  "rounded-lg border border-neutral-200 bg-neutral-50 px-2 py-1.5 text-sm outline-none transition focus:border-neutral-400";

function toDraft(v: Variant): Draft {
  return {
    key: v.key,
    weight: v.weight,
    // Values are arbitrary JSON, edited as text so a user can type an object.
    value: v.value === undefined || v.value === null ? "" : JSON.stringify(v.value),
  };
}

/** Parse a draft's value field. Empty means null; invalid JSON is kept as a string. */
function parseValue(raw: string): unknown {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    // A bare word like `blue` isn't valid JSON but is an obvious intent.
    return trimmed;
  }
}

export function VariantsEditor({
  flag,
  onSaved,
}: {
  flag: Flag;
  onSaved: () => void;
}) {
  const [drafts, setDrafts] = useState<Draft[]>(flag.variants.map(toDraft));
  const [offVariant, setOffVariant] = useState<string>(flag.off_variant ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDrafts(flag.variants.map(toDraft));
    setOffVariant(flag.off_variant ?? "");
  }, [flag.variants, flag.off_variant]);

  const total = useMemo(
    () => drafts.reduce((sum, d) => sum + (Number(d.weight) || 0), 0),
    [drafts],
  );
  const weightsOk = drafts.length === 0 || total === 100;
  const enoughVariants = drafts.length === 0 || drafts.length >= 2;

  function update(index: number, patch: Partial<Draft>) {
    setDrafts((prev) =>
      prev.map((d, i) => (i === index ? { ...d, ...patch } : d)),
    );
  }

  function add() {
    setDrafts((prev) => [...prev, { key: "", weight: 0, value: "" }]);
  }

  function remove(index: number) {
    setDrafts((prev) => prev.filter((_, i) => i !== index));
  }

  /** Split the remaining weight evenly — the common case when adding an arm. */
  function balance() {
    if (!drafts.length) return;
    const each = Math.floor(100 / drafts.length);
    const remainder = 100 - each * drafts.length;
    setDrafts((prev) =>
      prev.map((d, i) => ({ ...d, weight: each + (i < remainder ? 1 : 0) })),
    );
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api.updateFlag(flag.key, {
        version: flag.version,
        variants: drafts.map((d) => ({
          key: d.key.trim(),
          weight: Number(d.weight) || 0,
          value: parseValue(d.value),
        })),
        off_variant: offVariant || null,
      });
      onSaved();
    } catch (e) {
      setError((e as Error).message);
      onSaved(); // resync — a version conflict means our copy is stale
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-2xl border border-black/5 bg-white p-5 shadow-sm">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-neutral-700">Variants</h2>
        <span className="text-xs text-neutral-400">
          {drafts.length === 0
            ? "boolean flag"
            : `${drafts.length} arms · ${total}%`}
        </span>
      </div>

      {drafts.length === 0 && (
        <p className="mt-2 text-xs text-neutral-500">
          No variants — this is a plain on/off flag. Add two or more to turn it
          into a multivariate experiment.
        </p>
      )}

      <div className="mt-3 flex flex-col gap-2">
        {drafts.map((draft, i) => (
          <div key={i} className="flex items-center gap-2">
            <input
              value={draft.key}
              onChange={(e) => update(i, { key: e.target.value })}
              placeholder="variant key"
              className={`${inputClass} w-32 font-mono`}
            />
            <input
              value={draft.value}
              onChange={(e) => update(i, { value: e.target.value })}
              placeholder='value (JSON, e.g. {"color":"blue"})'
              className={`${inputClass} flex-1 font-mono text-xs`}
            />
            <input
              type="number"
              min={0}
              max={100}
              value={draft.weight}
              onChange={(e) => update(i, { weight: Number(e.target.value) })}
              className={`${inputClass} w-20 text-right`}
            />
            <span className="text-xs text-neutral-400">%</span>
            <button
              type="button"
              onClick={() => remove(i)}
              className="px-1 text-xs text-neutral-400 transition hover:text-red-500"
              aria-label={`Remove variant ${draft.key || i + 1}`}
            >
              ✕
            </button>
          </div>
        ))}
      </div>

      {drafts.length > 0 && (
        <div className="mt-3 flex items-center gap-3 text-xs">
          <label className="text-neutral-500">
            Off variant
            <select
              value={offVariant}
              onChange={(e) => setOffVariant(e.target.value)}
              className={`${inputClass} ml-2`}
            >
              <option value="">(serve nothing)</option>
              {drafts
                .filter((d) => d.key.trim())
                .map((d) => (
                  <option key={d.key} value={d.key}>
                    {d.key}
                  </option>
                ))}
            </select>
          </label>
          <span className="text-neutral-400">
            served when the flag is off or the user is outside the rollout
          </span>
        </div>
      )}

      <div className="mt-4 flex items-center gap-2">
        <button
          type="button"
          onClick={add}
          className="rounded-lg border border-neutral-200 px-3 py-1.5 text-xs font-medium text-neutral-600 transition hover:border-neutral-400"
        >
          Add variant
        </button>
        {drafts.length > 0 && (
          <button
            type="button"
            onClick={balance}
            className="rounded-lg border border-neutral-200 px-3 py-1.5 text-xs font-medium text-neutral-600 transition hover:border-neutral-400"
          >
            Split evenly
          </button>
        )}
        <button
          type="button"
          onClick={save}
          disabled={busy || !weightsOk || !enoughVariants}
          className="ml-auto rounded-lg bg-neutral-900 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-neutral-700 disabled:opacity-40"
        >
          Save variants
        </button>
      </div>

      {!weightsOk && (
        <p className="mt-2 text-xs text-amber-600">
          Weights must sum to 100 — currently {total}%.
        </p>
      )}
      {!enoughVariants && (
        <p className="mt-2 text-xs text-amber-600">
          A multivariate flag needs at least 2 variants (or none at all).
        </p>
      )}
      {error && <p className="mt-2 text-xs text-red-500">{error}</p>}
    </section>
  );
}
