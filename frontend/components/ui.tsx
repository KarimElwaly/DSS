export function Spinner({ label = "Loading…" }: { label?: string }) {
  return <p className="py-10 text-center text-sm text-slate-500">{label}</p>;
}

export function ErrorBox({ message }: { message: string }) {
  return (
    <p className="rounded-md bg-red-500/10 px-4 py-3 text-sm text-red-300">
      {message}
    </p>
  );
}

export function StatCard({
  label,
  value,
  hint,
  tone = "text-slate-100",
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: string;
}) {
  return (
    <div className="card p-4">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
      </p>
      <p className={`mt-1.5 text-2xl font-semibold tabular-nums ${tone}`}>{value}</p>
      {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
    </div>
  );
}

export function StockPill({ state }: { state: string }) {
  const out = state === "out_of_stock" || state === "OUT_OF_STOCK";
  return (
    <span
      className={`pill ${
        out ? "bg-red-500/15 text-red-300" : "bg-emerald-500/15 text-emerald-300"
      }`}
    >
      {out ? "OUT OF STOCK" : "IN STOCK"}
    </span>
  );
}
