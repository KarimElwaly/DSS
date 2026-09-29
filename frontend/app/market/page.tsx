"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ErrorBox, Spinner } from "@/components/ui";
import { apiFetch, type MarketSnapshot, type Page } from "@/lib/api";
import { formatMoney, formatSignedPct, gapTone } from "@/lib/format";
import { useRequireAuth } from "@/lib/useRequireAuth";

type Filter = "all" | "above" | "below" | "stockout" | "untracked";

const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "above", label: "Above market" },
  { key: "below", label: "Below market" },
  { key: "stockout", label: "Rival stockout" },
  { key: "untracked", label: "No coverage" },
];

export default function MarketPage() {
  const ready = useRequireAuth();
  const [rows, setRows] = useState<MarketSnapshot[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");

  useEffect(() => {
    if (!ready) return;
    apiFetch<Page<MarketSnapshot>>("/api/market/snapshot?limit=500")
      .then((page) => setRows(page.items))
      .catch((err) => setError(err.message));
  }, [ready]);

  if (!ready) return null;
  if (error) return <ErrorBox message={error} />;
  if (!rows) return <Spinner />;

  const visible = rows.filter((row) => {
    switch (filter) {
      case "above":
        return (row.price_gap_pct ?? 0) > 0.03;
      case "below":
        return (row.price_gap_pct ?? 0) < -0.03;
      case "stockout":
        return row.n_out_of_stock > 0;
      case "untracked":
        return row.n_listings === 0;
      default:
        return true;
    }
  });

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold">Market Watch</h1>
        <p className="mt-1 text-sm text-slate-400">
          Our price versus the geometric-mean landed price of in-stock rivals.
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        {FILTERS.map((option) => (
          <button
            key={option.key}
            type="button"
            onClick={() => setFilter(option.key)}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
              filter === option.key
                ? "bg-emerald-500 text-slate-950"
                : "bg-[#151d2b] text-slate-400 hover:text-slate-200"
            }`}
          >
            {option.label}
          </button>
        ))}
      </div>

      <div className="card overflow-x-auto">
        <table className="data">
          <thead>
            <tr>
              <th>SKU</th>
              <th>Product</th>
              <th className="text-right">Our price</th>
              <th className="text-right">Cheapest rival</th>
              <th className="text-right">Market index</th>
              <th className="text-right">Gap</th>
              <th className="text-right">Listings</th>
              <th className="text-right">OOS</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => (
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
                  {formatMoney(row.cheapest_competitor_price)}
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
                  {row.n_listings || "—"}
                </td>
                <td
                  className={`text-right tabular-nums ${
                    row.n_out_of_stock ? "text-sky-400" : "text-slate-600"
                  }`}
                >
                  {row.n_out_of_stock || "—"}
                </td>
              </tr>
            ))}
            {visible.length === 0 && (
              <tr>
                <td colSpan={8} className="py-8 text-center text-slate-500">
                  Nothing matches this filter.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
