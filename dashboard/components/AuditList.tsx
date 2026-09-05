"use client";

import type { AuditEntry } from "@/lib/api";

const actionColor: Record<string, string> = {
  created: "bg-green-100 text-green-700",
  updated: "bg-blue-100 text-blue-700",
  deleted: "bg-red-100 text-red-600",
};

export function AuditList({ entries }: { entries: AuditEntry[] }) {
  if (entries.length === 0) {
    return <p className="text-sm text-neutral-400">No activity yet.</p>;
  }
  return (
    <ul className="flex flex-col gap-2">
      {entries.map((entry) => (
        <li
          key={entry.id}
          className="flex items-center justify-between gap-4 rounded-xl border border-black/5 bg-white px-4 py-3 text-sm"
        >
          <div className="flex items-center gap-3">
            <span
              className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                actionColor[entry.action] ?? "bg-neutral-100 text-neutral-600"
              }`}
            >
              {entry.action}
            </span>
            <span className="font-mono text-xs text-neutral-500">
              {entry.flag_key}
            </span>
          </div>
          <span className="text-xs text-neutral-400">
            {new Date(entry.created_at).toLocaleString()}
          </span>
        </li>
      ))}
    </ul>
  );
}
