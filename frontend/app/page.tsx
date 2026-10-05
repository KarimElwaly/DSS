"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ErrorBox, Spinner, StatCard } from "@/components/ui";
import { apiFetch, type MarketSnapshot, type Page } from "@/lib/api";
import { formatMoney, formatSignedPct, gapTone } from "@/lib/format";
import { useRequireAuth } from "@/lib/useRequireAuth";

export default function OverviewPage() {
  const ready = useRequireAuth();
  const [rows, setRows] = useState<MarketSnapshot[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    if (!ready) return;
    apiFetch<Page<MarketSnapshot>>("/api/market/snapshot?limit=200")
      .then((page) => setRows(page.items))
      .catch((err) => setError(err.message));
  }, [ready]);

  if (!ready) return null;
  if (error) return <ErrorBox message={error} />;
  if (!rows) return <Spinner />;

  const tracked = rows.filter((r) => r.n_listings > 0);
  const above = tracked.filter((r) => (r.price_gap_pct ?? 0) > 0.03);
  const below = tracked.filter((r) => (r.price_gap_pct ?? 0) < -0.03);
  const stockoutOpportunities = tracked.filter((r) => r.n_out_of_stock > 0);
  const coverage = rows.length ? tracked.length / rows.length : 0;

  // Sort by the largest absolute deviation from the market -- the SKUs where a
  // pricing decision is most likely to move the needle.
  const sortedTracked = [...tracked].sort(
    (a, b) => Math.abs(b.price_gap_pct ?? 0) - Math.abs(a.price_gap_pct ?? 0),
  );
  const attention = showAll ? sortedTracked : sortedTracked.slice(0, 10);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Overview</h1>
        <p className="mt-1 text-sm text-slate-400">
          Competitive position across {rows.length} tracked SKUs.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          label="Match coverage"
          value={`${(coverage * 100).toFixed(0)}%`}
          hint={`${tracked.length} of ${rows.length} SKUs have competitor data`}
          tone={coverage < 0.8 ? "text-amber-400" : "text-emerald-400"}
        />
        <StatCard
          label="Priced above market"
          value={String(above.length)}
          hint="Margin captured, volume at risk"
          tone="text-amber-400"
        />
        <StatCard
          label="Priced below market"
          value={String(below.length)}
          hint="Potential margin left on the table"
          tone="text-emerald-400"
        />
        <StatCard
          label="Rival stockouts"
          value={String(stockoutOpportunities.length)}
          hint="SKUs where a competitor is out of stock"
          tone="text-sky-400"
        />
      </div>

      <section className="card">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#1e2836] px-4 py-3">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold">
                {showAll ? "All Deviations from Market" : "Top 10 Largest Deviations from Market"}
              </h2>
              <span className="rounded bg-[#1a2334] px-2 py-0.5 text-xs text-slate-400">
                {attention.length} of {tracked.length} SKUs
              </span>
            </div>
            <p className="text-xs text-slate-500">
              Ranked by absolute gap between our price and the competitor index.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setShowAll(!showAll)}
              className="rounded border border-[#1e2836] bg-[#0d1220] px-2.5 py-1 text-xs font-medium text-slate-300 transition hover:bg-[#1a2334] hover:text-white"
            >
              {showAll ? "Show Top 10" : `Show All ${tracked.length}`}
            </button>
            <Link
              href="/market"
              className="text-xs font-medium text-sky-400 hover:text-sky-300 hover:underline"
            >
              Full Market Watch →
            </Link>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="data">
            <thead>
              <tr>
                <th>SKU</th>
                <th>Product</th>
                <th className="text-right">Our price</th>
                <th className="text-right">Market index</th>
                <th className="text-right">Gap</th>
                <th className="text-right">Listings</th>
                <th className="text-right">OOS</th>
              </tr>
            </thead>
            <tbody>
              {attention.map((row) => (
                <tr key={row.product_id}>
                  <td className="font-mono text-xs">
                    <Link
                      href={`/catalog/${row.product_id}`}
                      className="text-sky-400 hover:underline"
                    >
                      {row.sku}
                    </Link>
                  </td>
                  <td className="text-slate-300">{row.name}</td>
                  <td className="text-right tabular-nums">
                    {formatMoney(row.our_price)}
                  </td>
                  <td className="text-right tabular-nums text-slate-400">
                    {formatMoney(row.competitor_price_index)}
                  </td>
                  <td
                    className={`text-right font-medium tabular-nums ${gapTone(row.price_gap_pct)}`}
                  >
                    {formatSignedPct(row.price_gap_pct)}
                  </td>
                  <td className="text-right tabular-nums text-slate-400">
                    {row.n_listings}
                  </td>
                  <td className="text-right tabular-nums text-slate-400">
                    {row.n_out_of_stock || "—"}
                  </td>
                </tr>
              ))}
              {attention.length === 0 && (
                <tr>
                  <td colSpan={7} className="py-8 text-center text-slate-500">
                    No matched competitor listings yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
