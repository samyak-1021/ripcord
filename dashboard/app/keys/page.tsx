"use client";

import { type FormEvent, useCallback, useEffect, useState } from "react";

import { api, type ApiKey, isAuthError } from "@/lib/api";

const ALL_SCOPES = ["flags:read", "flags:write", "sdk", "admin"] as const;

function formatDate(value: string | null): string {
  if (!value) return "never";
  return new Date(value).toLocaleString();
}

export default function KeysPage() {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [forbidden, setForbidden] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>(["flags:read"]);
  const [created, setCreated] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setKeys(await api.listKeys());
      setForbidden(false);
      setError(null);
    } catch (e) {
      // Managing keys needs `admin`; a dashboard key may legitimately lack it.
      if (isAuthError(e)) setForbidden(true);
      else setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function create(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api.createKey({ name: name.trim(), scopes });
      setCreated(result.key);
      setName("");
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function revoke(keyId: string, keyName: string) {
    if (!confirm(`Revoke "${keyName}"? Anything using it stops working now.`)) {
      return;
    }
    try {
      await api.revokeKey(keyId);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (loading) return <p className="text-sm text-neutral-400">Loading…</p>;

  if (forbidden) {
    return (
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">API keys</h1>
        <p className="mt-4 rounded-xl bg-amber-50 p-4 text-sm text-amber-700">
          Your key doesn&apos;t have the <code className="font-mono">admin</code>{" "}
          scope, so it can&apos;t manage other keys. That&apos;s working as
          intended — sign in with an admin key to use this page.
        </p>
      </div>
    );
  }

  return (
    <div>
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">API keys</h1>
        <p className="mt-1 text-sm text-neutral-500">
          {keys.filter((k) => !k.revoked_at).length} active
        </p>
      </header>

      {created && (
        <div className="mb-6 rounded-2xl border border-green-200 bg-green-50 p-4">
          <p className="text-sm font-medium text-green-800">
            Copy this key now — it is never shown again.
          </p>
          <code className="mt-2 block overflow-x-auto rounded-lg bg-white px-3 py-2 font-mono text-xs">
            {created}
          </code>
          <button
            type="button"
            onClick={() => setCreated(null)}
            className="mt-2 text-xs text-green-700 underline"
          >
            Done
          </button>
        </div>
      )}

      <form
        onSubmit={create}
        className="mb-6 rounded-2xl border border-black/5 bg-white p-5 shadow-sm"
      >
        <h2 className="text-sm font-semibold text-neutral-700">Create a key</h2>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            placeholder="name, e.g. checkout-service"
            className="flex-1 rounded-xl border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm outline-none transition focus:border-neutral-400"
          />
          <button
            type="submit"
            disabled={busy || !name.trim() || scopes.length === 0}
            className="rounded-xl bg-neutral-900 px-4 py-2 text-sm font-medium text-white transition hover:bg-neutral-700 disabled:opacity-40"
          >
            Create
          </button>
        </div>
        <div className="mt-3 flex flex-wrap gap-3">
          {ALL_SCOPES.map((scope) => (
            <label key={scope} className="flex items-center gap-1.5 text-xs">
              <input
                type="checkbox"
                checked={scopes.includes(scope)}
                onChange={(e) =>
                  setScopes((prev) =>
                    e.target.checked
                      ? [...prev, scope]
                      : prev.filter((s) => s !== scope),
                  )
                }
              />
              <code className="font-mono text-neutral-600">{scope}</code>
            </label>
          ))}
        </div>
        <p className="mt-2 text-xs text-neutral-400">
          Give an application key only <code className="font-mono">sdk</code> —
          it can then read the ruleset but not change a flag.
        </p>
      </form>

      {error && (
        <p className="mb-4 rounded-xl bg-red-50 p-3 text-sm text-red-600">{error}</p>
      )}

      <div className="overflow-x-auto rounded-2xl border border-black/5 bg-white shadow-sm">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-black/5 text-xs text-neutral-500">
            <tr>
              <th className="px-4 py-3 font-medium">Name</th>
              <th className="px-4 py-3 font-medium">Scopes</th>
              <th className="px-4 py-3 font-medium">Last used</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody>
            {keys.map((key) => (
              <tr
                key={key.key_id}
                className={`border-b border-black/5 last:border-0 ${
                  key.revoked_at ? "opacity-40" : ""
                }`}
              >
                <td className="px-4 py-3">
                  <div className="font-medium">{key.name}</div>
                  <div className="font-mono text-xs text-neutral-400">
                    {key.key_id}
                  </div>
                </td>
                <td className="px-4 py-3">
                  <div className="flex flex-wrap gap-1">
                    {key.scopes.map((s) => (
                      <span
                        key={s}
                        className="rounded-full bg-neutral-100 px-2 py-0.5 font-mono text-[11px] text-neutral-600"
                      >
                        {s}
                      </span>
                    ))}
                  </div>
                </td>
                <td className="px-4 py-3 text-xs text-neutral-500">
                  {formatDate(key.last_used_at)}
                </td>
                <td className="px-4 py-3 text-right">
                  {key.revoked_at ? (
                    <span className="text-xs text-neutral-400">revoked</span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => revoke(key.key_id, key.name)}
                      className="text-xs font-medium text-red-500 transition hover:text-red-600"
                    >
                      Revoke
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {keys.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-sm text-neutral-400">
                  No keys yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
