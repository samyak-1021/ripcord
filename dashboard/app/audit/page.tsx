"use client";

import { useEffect, useState } from "react";

import { AuditList } from "@/components/AuditList";
import { api, type AuditEntry } from "@/lib/api";

export default function AuditPage() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listAudit()
      .then(setEntries)
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">Audit log</h1>
        <p className="mt-1 text-sm text-neutral-500">Every change, newest first</p>
      </header>

      {loading && <p className="text-sm text-neutral-400">Loading…</p>}
      {error && (
        <p className="rounded-xl bg-red-50 p-3 text-sm text-red-600">
          Couldn&apos;t reach the API — {error}
        </p>
      )}
      {!loading && !error && <AuditList entries={entries} />}
    </div>
  );
}
