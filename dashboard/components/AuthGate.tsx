"use client";

import { type FormEvent, type ReactNode, useEffect, useState } from "react";

import { api, getApiKey, onApiKeyChange, setApiKey } from "@/lib/api";

/**
 * Gates the dashboard behind an API key.
 *
 * The key is verified against the API before it is accepted, so a typo fails
 * here with a clear message rather than surfacing as a broken page three clicks
 * later. Verification uses `GET /flags`, which needs only `flags:read` — the
 * dashboard shouldn't demand an admin key just to render.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [authed, setAuthed] = useState(false);
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Validate whatever is already stored, once, on mount.
  useEffect(() => {
    let cancelled = false;
    const stored = getApiKey();
    if (!stored) {
      setReady(true);
      return;
    }
    api
      .listFlags()
      .then(() => !cancelled && setAuthed(true))
      .catch(() => {
        // Stored key is wrong or revoked — drop it and ask again.
        if (!cancelled) setApiKey(null);
      })
      .finally(() => !cancelled && setReady(true));
    return () => {
      cancelled = true;
    };
  }, []);

  // Any request that 401s elsewhere clears the key; re-gate when that happens.
  useEffect(() => onApiKeyChange((key) => setAuthed(Boolean(key))), []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const candidate = input.trim();
    setApiKey(candidate);
    try {
      await api.listFlags();
      setAuthed(true);
      setInput("");
    } catch {
      setApiKey(null);
      setError("That key was rejected. Check it has the 'flags:read' scope.");
    } finally {
      setBusy(false);
    }
  }

  if (!ready) {
    return <p className="p-10 text-sm text-neutral-400">Loading…</p>;
  }

  if (authed) return <>{children}</>;

  return (
    <div className="mx-auto mt-24 max-w-md px-6">
      <h1 className="text-xl font-semibold tracking-tight">Ripcord</h1>
      <p className="mt-2 text-sm text-neutral-500">
        The management API requires a key. Paste one with at least the{" "}
        <code className="rounded bg-neutral-100 px-1 py-0.5 font-mono text-xs">
          flags:read
        </code>{" "}
        scope.
      </p>

      <form onSubmit={submit} className="mt-6 flex flex-col gap-3">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          type="password"
          autoComplete="off"
          spellCheck={false}
          placeholder="rpc_…"
          className="rounded-xl border border-neutral-200 bg-white px-3 py-2 font-mono text-sm outline-none transition focus:border-neutral-400"
        />
        <button
          type="submit"
          disabled={busy || !input.trim()}
          className="rounded-xl bg-neutral-900 px-4 py-2 text-sm font-medium text-white transition hover:bg-neutral-700 disabled:opacity-40"
        >
          {busy ? "Checking…" : "Continue"}
        </button>
        {error && <p className="text-xs text-red-500">{error}</p>}
      </form>

      <details className="mt-8 text-xs text-neutral-500">
        <summary className="cursor-pointer">Don&apos;t have a key?</summary>
        <pre className="mt-2 overflow-x-auto rounded-xl bg-neutral-50 p-3 font-mono text-[11px] leading-relaxed">
{`python -m ripcord.cli create-key \\
    --name dashboard \\
    --scopes flags:read,flags:write,sdk`}
        </pre>
      </details>
    </div>
  );
}

/** Clears the stored credential — rendered in the sidebar. */
export function SignOutButton() {
  const [key, setKey] = useState<string | null>(null);
  useEffect(() => {
    setKey(getApiKey());
    return onApiKeyChange(setKey);
  }, []);

  if (!key) return null;
  return (
    <button
      type="button"
      onClick={() => setApiKey(null)}
      className="text-xs text-neutral-400 transition hover:text-neutral-700"
    >
      Sign out
    </button>
  );
}
