"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import {
  CartesianGrid,
  Line,
  ComposedChart,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ErrorBox, Spinner, StatCard, StockPill } from "@/components/ui";
import {
  apiFetch,
  type MarketSnapshot,
  type Page,
  type ProductDetail,
  type SalesPoint,
} from "@/lib/api";
import { formatMoney, formatPct, formatSignedPct, gapTone } from "@/lib/format";
import { useRequireAuth } from "@/lib/useRequireAuth";

export default function ProductPage() {
  const ready = useRequireAuth();
  const params = useParams<{ productId: string }>();
  const productId = params.productId;

  const [product, setProduct] = useState<ProductDetail | null>(null);
  const [sales, setSales] = useState<SalesPoint[] | null>(null);
  const [snapshot, setSnapshot] = useState<MarketSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ready || !productId) return;
    Promise.all([
      apiFetch<ProductDetail>(`/api/catalog/products/${productId}`),
      apiFetch<SalesPoint[]>(`/api/catalog/products/${productId}/sales?days=180`),
      apiFetch<Page<MarketSnapshot>>("/api/market/snapshot?limit=500"),
    ])
      .then(([detail, salesPoints, page]) => {
        setProduct(detail);
        setSales(salesPoints);
        setSnapshot(page.items.find((i) => i.product_id === productId) ?? null);
      })
      .catch((err) => setError(err.message));
  }, [ready, productId]);

  const chartData = useMemo(
    () =>
      (sales ?? []).map((p) => ({
        date: p.sale_date.slice(5),
        units: p.units,
        price: p.unit_price,
      })),
    [sales],
  );

  if (!ready) return null;
  if (error) return <ErrorBox message={error} />;
  if (!product || !sales) return <Spinner />;

  const price = Number(product.current_price);
  const cost = Number(product.unit_cost);
  const margin = price > 0 ? (price - cost) / price : 0;
  const floor = cost / (1 - Number(product.min_margin_pct));

  return (
    <div className="space-y-6">
      <div>
        <p className="font-mono text-xs text-slate-500">{product.sku}</p>
        <h1 className="text-xl font-semibold">{product.name}</h1>
        <p className="mt-1 text-sm text-slate-400">
          {product.brand}
          {product.category ? ` · ${product.category.name}` : ""}
          {product.gtin ? ` · GTIN ${product.gtin}` : ""}
        </p>
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Current price" value={formatMoney(price)} />
        <StatCard
          label="Gross margin"
          value={formatPct(margin)}
          hint={`Floor ${formatMoney(floor)} at ${formatPct(product.min_margin_pct, 0)} min margin`}
          tone={margin < Number(product.min_margin_pct) ? "text-red-400" : "text-emerald-400"}
        />
        <StatCard
          label="Market index"
          value={formatMoney(product.market?.competitor_price_index)}
          hint={`${product.market?.n_listings ?? 0} rival listings`}
        />
        <StatCard
          label="Gap vs market"
          value={formatSignedPct(product.market?.price_gap_pct)}
          hint={
            product.market?.n_out_of_stock
              ? `${product.market.n_out_of_stock} rival(s) out of stock`
              : "All tracked rivals in stock"
          }
          tone={gapTone(product.market?.price_gap_pct)}
        />
      </div>

      <section className="card p-4">
        <h2 className="text-sm font-semibold">Units and price, last 180 days</h2>
        <p className="mb-3 text-xs text-slate-500">
          The visible co-movement of price and volume is what the elasticity model
          estimates in Module B.
        </p>
        <div className="h-72">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={chartData}>
              <CartesianGrid stroke="#1b2434" vertical={false} />
              <XAxis
                dataKey="date"
                tick={{ fill: "#64748b", fontSize: 11 }}
                minTickGap={40}
              />
              <YAxis
                yAxisId="units"
                tick={{ fill: "#64748b", fontSize: 11 }}
                width={40}
              />
              <YAxis
                yAxisId="price"
                orientation="right"
                tick={{ fill: "#64748b", fontSize: 11 }}
                width={56}
                domain={["auto", "auto"]}
              />
              <Tooltip
                contentStyle={{
                  background: "#111826",
                  border: "1px solid #243044",
                  borderRadius: 8,
                  fontSize: 12,
                }}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line
                yAxisId="units"
                type="monotone"
                dataKey="units"
                name="Units"
                stroke="#38bdf8"
                dot={false}
                strokeWidth={1.5}
              />
              <Line
                yAxisId="price"
                type="monotone"
                dataKey="price"
                name="Price"
                stroke="#f59e0b"
                dot={false}
                strokeWidth={1.5}
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </section>

      <section className="card">
        <div className="border-b border-[#1e2836] px-4 py-3">
          <h2 className="text-sm font-semibold">Competitor listings</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="data">
            <thead>
              <tr>
                <th>Competitor</th>
                <th className="text-right">Price</th>
                <th className="text-right">Landed</th>
                <th className="text-right">vs us</th>
                <th>Availability</th>
                <th>Observed</th>
              </tr>
            </thead>
            <tbody>
              {(snapshot?.points ?? []).map((point) => (
                <tr key={`${point.competitor_slug}-${point.observed_at}`}>
                  <td className="text-slate-300">{point.competitor_name}</td>
                  <td className="text-right tabular-nums">
                    {formatMoney(point.price)}
                  </td>
                  <td className="text-right tabular-nums text-slate-400">
                    {formatMoney(point.landed_price)}
                  </td>
                  <td
                    className={`text-right tabular-nums ${gapTone(
                      (price - point.landed_price) / point.landed_price,
                    )}`}
                  >
                    {formatSignedPct((price - point.landed_price) / point.landed_price)}
                  </td>
                  <td>
                    <StockPill state={point.stock_state} />
                  </td>
                  <td className="text-slate-500">
                    {new Date(point.observed_at).toLocaleDateString("en-GB")}
                  </td>
                </tr>
              ))}
              {(snapshot?.points.length ?? 0) === 0 && (
                <tr>
                  <td colSpan={6} className="py-8 text-center text-slate-500">
                    No competitor listing has been matched to this SKU yet.
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
