"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ErrorBox, Spinner } from "@/components/ui";
import { apiFetch, type Page, type Product } from "@/lib/api";
import { formatMoney, formatPct } from "@/lib/format";
import { useRequireAuth } from "@/lib/useRequireAuth";

export default function CatalogPage() {
  const ready = useRequireAuth();
  const [search, setSearch] = useState("");
  const [rows, setRows] = useState<Product[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ready) return;
    const handle = setTimeout(() => {
      const query = new URLSearchParams({ limit: "200" });
      if (search.trim()) query.set("search", search.trim());
      apiFetch<Page<Product>>(`/api/catalog/products?${query}`)
        .then((page) => {
          setRows(page.items);
          setTotal(page.total);
          setError(null);
        })
        .catch((err) => setError(err.message));
    }, 250);
    return () => clearTimeout(handle);
  }, [ready, search]);

  const enriched = useMemo(
    () =>
      (rows ?? []).map((p) => {
        const price = Number(p.current_price);
        const cost = Number(p.unit_cost);
        return { ...p, margin: price > 0 ? (price - cost) / price : 0 };
      }),
    [rows],
  );

  if (!ready) return null;

  return (
    <div className="space-y-5">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">Catalog</h1>
          <p className="mt-1 text-sm text-slate-400">{total} SKUs</p>
        </div>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search SKU, name or brand…"
          className="w-72 rounded-md border border-[#243044] bg-[#0d1424] px-3 py-2 text-sm outline-none focus:border-emerald-500"
        />
      </div>

      {error && <ErrorBox message={error} />}
      {!rows && !error && <Spinner />}

      {rows && (
        <div className="card overflow-x-auto">
          <table className="data">
            <thead>
              <tr>
                <th>SKU</th>
                <th>Product</th>
                <th>Brand</th>
                <th>Category</th>
                <th className="text-right">Price</th>
                <th className="text-right">Cost</th>
                <th className="text-right">Margin</th>
                <th className="text-right">Min margin</th>
              </tr>
            </thead>
            <tbody>
              {enriched.map((p) => (
                <tr key={p.id}>
                  <td className="font-mono text-xs">
                    <Link
                      href={`/catalog/${p.id}`}
                      className="text-sky-400 hover:underline"
                    >
                      {p.sku}
                    </Link>
                  </td>
                  <td className="text-slate-300">{p.name}</td>
                  <td className="text-slate-400">{p.brand}</td>
                  <td className="text-slate-400">{p.category?.name ?? "—"}</td>
                  <td className="text-right tabular-nums">
                    {formatMoney(p.current_price)}
                  </td>
                  <td className="text-right tabular-nums text-slate-400">
                    {formatMoney(p.unit_cost)}
                  </td>
                  <td
                    className={`text-right tabular-nums ${
                      p.margin < Number(p.min_margin_pct)
                        ? "text-red-400"
                        : "text-slate-200"
                    }`}
                  >
                    {formatPct(p.margin)}
                  </td>
                  <td className="text-right tabular-nums text-slate-500">
                    {formatPct(p.min_margin_pct)}
                  </td>
                </tr>
              ))}
              {enriched.length === 0 && (
                <tr>
                  <td colSpan={8} className="py-8 text-center text-slate-500">
                    No products match “{search}”.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
