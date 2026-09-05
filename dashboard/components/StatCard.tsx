export function StatCard({
  label,
  value,
}: {
  label: string;
  value: number | string;
}) {
  return (
    <div className="rounded-2xl border border-black/5 bg-white p-5 shadow-sm">
      <div className="text-xs font-medium uppercase tracking-wide text-neutral-400">
        {label}
      </div>
      <div className="mt-2 text-3xl font-semibold tracking-tight">{value}</div>
    </div>
  );
}
