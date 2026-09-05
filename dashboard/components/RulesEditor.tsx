"use client";

import { useState } from "react";

import { api, type Flag, type Rule } from "@/lib/api";

const OPERATORS = ["in", "not_in", "eq", "neq"];
const inputClass =
  "rounded-xl border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm outline-none transition focus:border-neutral-400";

// The editor works on a "draft" where values are a single comma-separated
// string (easy to type); we split it into a list on save.
type DraftRule = { attribute: string; operator: string; values: string };

const toDraft = (rules: Rule[]): DraftRule[] =>
  rules.map((r) => ({
    attribute: r.attribute,
    operator: r.operator,
    values: r.values.join(", "),
  }));

export function RulesEditor({
  flag,
  onSaved,
}: {
  flag: Flag;
  onSaved: () => void;
}) {
  const [rules, setRules] = useState<DraftRule[]>(toDraft(flag.rules));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const update = (i: number, patch: Partial<DraftRule>) =>
    setRules((rs) => rs.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  const add = () =>
    setRules((rs) => [...rs, { attribute: "", operator: "in", values: "" }]);
  const remove = (i: number) =>
    setRules((rs) => rs.filter((_, idx) => idx !== i));

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const payload = rules
        .filter((r) => r.attribute.trim() && r.values.trim())
        .map((r, idx) => ({
          attribute: r.attribute.trim(),
          operator: r.operator,
          values: r.values
            .split(",")
            .map((v) => v.trim())
            .filter(Boolean),
          priority: idx,
        }));
      await api.updateFlag(flag.key, { version: flag.version, rules: payload });
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-2xl border border-black/5 bg-white p-5 shadow-sm">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-neutral-700">Targeting rules</h2>
        <button
          type="button"
          onClick={add}
          className="text-xs font-medium text-neutral-500 transition hover:text-neutral-800"
        >
          + Add rule
        </button>
      </div>
      <p className="mt-1 text-xs text-neutral-400">
        A matching rule turns the flag on for that user, regardless of rollout.
      </p>

      <div className="mt-4 flex flex-col gap-3">
        {rules.length === 0 && (
          <p className="text-sm text-neutral-400">
            No rules — the flag uses its rollout percentage only.
          </p>
        )}
        {rules.map((rule, i) => (
          <div key={i} className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <input
              value={rule.attribute}
              onChange={(e) => update(i, { attribute: e.target.value })}
              placeholder="attribute (e.g. country)"
              className={`flex-1 ${inputClass}`}
            />
            <select
              value={rule.operator}
              onChange={(e) => update(i, { operator: e.target.value })}
              className={inputClass}
            >
              {OPERATORS.map((op) => (
                <option key={op} value={op}>
                  {op}
                </option>
              ))}
            </select>
            <input
              value={rule.values}
              onChange={(e) => update(i, { values: e.target.value })}
              placeholder="values (comma-separated)"
              className={`flex-1 ${inputClass}`}
            />
            <button
              type="button"
              onClick={() => remove(i)}
              className="text-xs font-medium text-red-500 transition hover:text-red-600"
            >
              Remove
            </button>
          </div>
        ))}
      </div>

      {error && <p className="mt-2 text-xs text-red-500">{error}</p>}

      <div className="mt-4">
        <button
          type="button"
          onClick={save}
          disabled={busy}
          className="rounded-xl bg-neutral-900 px-4 py-2 text-sm font-medium text-white transition hover:bg-neutral-700 disabled:opacity-40"
        >
          {busy ? "Saving…" : "Save rules"}
        </button>
      </div>
    </section>
  );
}
