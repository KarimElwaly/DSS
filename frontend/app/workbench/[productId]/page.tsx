"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ErrorBox, Spinner, StatCard } from "@/components/ui";
import {
  apiFetch,
  type SkuForecast,
  type SkuWorkbench,
} from "@/lib/api";
import { formatMoney, formatPct, formatSignedPct } from "@/lib/format";
import { useRequireAuth } from "@/lib/useRequireAuth";

export default function SkuWorkbenchPage() {
  const ready = useRequireAuth();
  const params = useParams<{ productId: string }>();
  const productId = params.productId;

  const [data, setData] = useState<SkuWorkbench | null>(null);
  const [forecast, setForecast] = useState<SkuForecast | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [simPrice, setSimPrice] = useState<number | null>(null);

  useEffect(() => {
    if (!ready || !productId) return;
    Promise.all([
      apiFetch<SkuWorkbench>(`/api/analytics/elasticity/${productId}`),
      apiFetch<SkuForecast>(`/api/analytics/forecast/${productId}`).catch(() => null),
    ])
      .then(([wb, fc]) => {
        setData(wb);
        setSimPrice(wb.current_price);
        setForecast(fc);
      })
      .catch((err) => setError(err.message));
  }, [ready, productId]);

  const simMetrics = useMemo(() => {
    if (!data || !simPrice || !data.estimate) return null;
    const curP = data.current_price;
    const cost = data.unit_cost;
    const elast = data.estimate.elasticity;

    const relP = simPrice / curP;
    const unitsRatio = Math.pow(relP, elast);

    // Approximate baseline volume from demand curve middle point
    const baseUnits =
      data.demand_curve.length > 0
        ? data.demand_curve[Math.floor(data.demand_curve.length / 2)].units
        : 50;

    const expUnits = baseUnits * unitsRatio;
    const curRev = curP * baseUnits;
    const expRev = simPrice * expUnits;
    const curMargin = (curP - cost) * baseUnits;
    const expMargin = (simPrice - cost) * expUnits;

    return {
      priceChangePct: (simPrice - curP) / curP,
      expectedUnits: expUnits,
      unitsChangePct: (expUnits - baseUnits) / baseUnits,
      expectedRevenue: expRev,
      revenueChangePct: (expRev - curRev) / curRev,
      expectedMargin: expMargin,
      marginChangePct: curMargin > 0 ? (expMargin - curMargin) / curMargin : 0,
      marginRate: (simPrice - cost) / simPrice,
    };
  }, [data, simPrice]);

  if (!ready) return null;
  if (error) return <ErrorBox message={error} />;
  if (!data) return <Spinner />;

  const est = data.estimate;
  const floorPrice = data.unit_cost / (1 - data.min_margin_pct);

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <Link
              href="/workbench"
              className="text-xs text-sky-400 hover:underline"
            >
              ← All SKUs
            </Link>
            <span className="text-slate-600">/</span>
            <span className="font-mono text-xs text-slate-400">{data.sku}</span>
          </div>
          <h1 className="mt-1 text-xl font-semibold">{data.name}</h1>
          <p className="text-xs text-slate-400">
            {data.brand} {data.category_name ? `· ${data.category_name}` : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href={`/catalog/${data.product_id}`}
            className="rounded border border-[#243044] bg-[#121929] px-3 py-1.5 text-xs text-slate-300 hover:bg-[#1a243a]"
          >
            Catalog View →
          </Link>
        </div>
      </div>

      {/* Top metrics */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          label="Estimated Elasticity (ε)"
          value={est ? est.elasticity.toFixed(3) : "—"}
          hint={
            est?.ci_low !== null && est?.ci_high !== null
              ? `90% CI: [${est?.ci_low?.toFixed(2)}, ${est?.ci_high?.toFixed(2)}]`
              : "No confidence interval available"
          }
          tone={
            est && est.elasticity < -1.6
              ? "text-red-400"
              : est && est.elasticity < -1.1
              ? "text-amber-400"
              : "text-emerald-400"
          }
        />
        <StatCard
          label="Model Confidence & R²"
          value={est?.r_squared !== null ? `${(Number(est?.r_squared) * 100).toFixed(0)}% R²` : "—"}
          hint={est?.is_reliable ? "Passes econometric gating" : "Sparse data / prior fallback"}
          tone={est?.is_reliable ? "text-emerald-400" : "text-amber-400"}
        />
        <StatCard
          label="Current Price & Cost"
          value={formatMoney(data.current_price)}
          hint={`Unit cost ${formatMoney(data.unit_cost)} · Floor ${formatMoney(floorPrice)}`}
        />
        <StatCard
          label="Forecast Backtest (WAPE)"
          value={
            forecast?.backtest_wape !== null && forecast?.backtest_wape !== undefined
              ? `${(forecast.backtest_wape * 100).toFixed(1)}%`
              : "—"
          }
          hint={
            forecast && forecast.horizon_days > 0
              ? `${forecast.horizon_days}-day forward horizon`
              : "Run forecasts to view"
          }
          tone="text-sky-400"
        />
      </div>

      {/* Interactive Price Simulation Sandbox */}
      <section className="card p-5">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-sm font-semibold">Interactive Demand Simulator</h2>
            <p className="text-xs text-slate-400">
              Simulate price movements and evaluate expected revenue and margin response based on $\hat\varepsilon$.
            </p>
          </div>
          {simPrice !== null && (
            <div className="flex items-center gap-3">
              <span className="font-mono text-sm font-bold text-sky-400">
                {formatMoney(simPrice)}
              </span>
              <button
                type="button"
                onClick={() => setSimPrice(data.current_price)}
                className="text-xs text-slate-400 hover:text-slate-200"
              >
                Reset
              </button>
            </div>
          )}
        </div>

        {simPrice !== null && (
          <div className="mt-4 space-y-4">
            <div className="flex items-center gap-4">
              <span className="text-xs text-slate-500">
                {formatMoney(data.current_price * 0.7)}
              </span>
              <input
                type="range"
                min={Math.round(data.current_price * 0.7)}
                max={Math.round(data.current_price * 1.3)}
                step={0.5}
                value={simPrice}
                onChange={(e) => setSimPrice(parseFloat(e.target.value))}
                className="h-2 w-full cursor-pointer appearance-none rounded-lg bg-[#1a2334] accent-sky-400"
              />
              <span className="text-xs text-slate-500">
                {formatMoney(data.current_price * 1.3)}
              </span>
            </div>

            {simMetrics && (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 rounded-lg border border-[#1e2836] bg-[#0b0f1a] p-3 text-center">
                <div>
                  <div className="text-[11px] text-slate-400">Price Shift</div>
                  <div
                    className={`text-sm font-semibold tabular-nums ${
                      simMetrics.priceChangePct >= 0 ? "text-emerald-400" : "text-amber-400"
                    }`}
                  >
                    {formatSignedPct(simMetrics.priceChangePct)}
                  </div>
                </div>
                <div>
                  <div className="text-[11px] text-slate-400">Expected Volume</div>
                  <div
                    className={`text-sm font-semibold tabular-nums ${
                      simMetrics.unitsChangePct >= 0 ? "text-emerald-400" : "text-red-400"
                    }`}
                  >
                    {formatSignedPct(simMetrics.unitsChangePct)}
                  </div>
                </div>
                <div>
                  <div className="text-[11px] text-slate-400">Expected Revenue</div>
                  <div
                    className={`text-sm font-semibold tabular-nums ${
                      simMetrics.revenueChangePct >= 0 ? "text-emerald-400" : "text-red-400"
                    }`}
                  >
                    {formatSignedPct(simMetrics.revenueChangePct)}
                  </div>
                </div>
                <div>
                  <div className="text-[11px] text-slate-400">Margin Shift (Rate)</div>
                  <div
                    className={`text-sm font-semibold tabular-nums ${
                      simMetrics.marginChangePct >= 0 ? "text-emerald-400" : "text-red-400"
                    }`}
                  >
                    {formatSignedPct(simMetrics.marginChangePct)} ({formatPct(simMetrics.marginRate)})
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </section>

      {/* Demand Curve Chart */}
      <section className="card p-4">
        <h2 className="text-sm font-semibold">Econometric Demand Curve with 90% Confidence Band</h2>
        <p className="mb-3 text-xs text-slate-500">
          Estimated response $q(p) = q_0 \cdot (p/p_0)^\varepsilon$ with bounds representing parameter uncertainty.
        </p>
        <div className="h-72">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={data.demand_curve}>
              <CartesianGrid stroke="#1b2434" vertical={false} />
              <XAxis
                dataKey="price"
                tick={{ fill: "#64748b", fontSize: 11 }}
                tickFormatter={(p) => `$${p}`}
              />
              <YAxis
                yAxisId="units"
                tick={{ fill: "#64748b", fontSize: 11 }}
                width={48}
              />
              <Tooltip
                contentStyle={{
                  background: "#111826",
                  border: "1px solid #243044",
                  borderRadius: 8,
                  fontSize: 12,
                }}
                formatter={(val: unknown) => [
                  typeof val === "number" ? val.toFixed(1) : String(val),
                  "",
                ]}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Area
                yAxisId="units"
                type="monotone"
                dataKey="upper_units"
                name="90% Upper Bound"
                stroke="none"
                fill="#38bdf8"
                fillOpacity={0.12}
              />
              <Area
                yAxisId="units"
                type="monotone"
                dataKey="lower_units"
                name="90% Lower Bound"
                stroke="none"
                fill="#0d1220"
                fillOpacity={0.8}
              />
              <Line
                yAxisId="units"
                type="monotone"
                dataKey="units"
                name="Expected Units"
                stroke="#38bdf8"
                strokeWidth={2}
                dot={false}
              />
              <ReferenceLine
                x={data.current_price}
                yAxisId="units"
                stroke="#f59e0b"
                strokeDasharray="3 3"
                label={{
                  value: "Current",
                  fill: "#f59e0b",
                  fontSize: 11,
                  position: "top",
                }}
              />
              {simPrice && simPrice !== data.current_price && (
                <ReferenceDot
                  x={simPrice}
                  y={simMetrics?.expectedUnits ?? 0}
                  yAxisId="units"
                  r={5}
                  fill="#10b981"
                  stroke="#fff"
                />
              )}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      </section>

      {/* 30-Day Demand Forecast */}
      {forecast && forecast.points.length > 0 && (
        <section className="card p-4">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-sm font-semibold">30-Day Demand Forecast with Prediction Intervals</h2>
              <p className="text-xs text-slate-500">
                Holt-Winters ETS model with 80% prediction interval bands.
              </p>
            </div>
            {forecast.backtest_wape !== null && (
              <span className="rounded bg-sky-500/10 px-2.5 py-1 text-xs font-mono text-sky-400">
                Backtest WAPE: {(forecast.backtest_wape * 100).toFixed(1)}%
              </span>
            )}
          </div>
          <div className="mt-3 h-64">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart
                data={forecast.points.map((pt) => ({
                  date: pt.target_date.slice(5),
                  predicted: pt.predicted_units,
                  lower: pt.lower_units,
                  upper: pt.upper_units,
                }))}
              >
                <CartesianGrid stroke="#1b2434" vertical={false} />
                <XAxis
                  dataKey="date"
                  tick={{ fill: "#64748b", fontSize: 11 }}
                  minTickGap={20}
                />
                <YAxis
                  tick={{ fill: "#64748b", fontSize: 11 }}
                  width={40}
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
                <Area
                  type="monotone"
                  dataKey="upper"
                  name="Upper Bound (80%)"
                  stroke="none"
                  fill="#a855f7"
                  fillOpacity={0.15}
                />
                <Area
                  type="monotone"
                  dataKey="lower"
                  name="Lower Bound (80%)"
                  stroke="none"
                  fill="#0d1220"
                  fillOpacity={0.8}
                />
                <Line
                  type="monotone"
                  dataKey="predicted"
                  name="Forecast Units"
                  stroke="#a855f7"
                  strokeWidth={2}
                  dot={{ r: 2 }}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </section>
      )}

      {/* Cross Elasticity / Cannibalization Matrix */}
      <section className="card">
        <div className="border-b border-[#1e2836] px-4 py-3">
          <h2 className="text-sm font-semibold">Sibling Cross-Price Elasticities (Cannibalization)</h2>
          <p className="text-xs text-slate-500">
            Measures how price shifts on this SKU affect sibling demand in the same category ($\kappa = \partial \ln q_i / \partial \ln p_j$).
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="data">
            <thead>
              <tr>
                <th>Sibling SKU</th>
                <th>Product Name</th>
                <th className="text-right">Cross Elasticity (κ)</th>
                <th className="text-right">Std Error</th>
                <th className="text-right">p-value</th>
                <th>Substitution Effect</th>
              </tr>
            </thead>
            <tbody>
              {data.cross_elasticities.map((row) => (
                <tr key={row.id}>
                  <td className="font-mono text-xs text-sky-400">
                    <Link href={`/workbench/${row.related_product_id}`} className="hover:underline">
                      {row.related_sku}
                    </Link>
                  </td>
                  <td className="text-slate-200">{row.related_name}</td>
                  <td className="text-right font-mono font-medium tabular-nums text-slate-300">
                    {row.elasticity.toFixed(3)}
                  </td>
                  <td className="text-right font-mono text-xs tabular-nums text-slate-400">
                    {row.std_error !== null ? row.std_error.toFixed(3) : "—"}
                  </td>
                  <td className="text-right font-mono text-xs tabular-nums text-slate-400">
                    {row.p_value !== null ? row.p_value.toFixed(3) : "—"}
                  </td>
                  <td>
                    {row.is_significant ? (
                      <span className="rounded bg-emerald-500/15 px-2 py-0.5 text-xs text-emerald-400">
                        Significant Rival Substitute
                      </span>
                    ) : (
                      <span className="text-xs text-slate-500">Neutral / Inconclusive</span>
                    )}
                  </td>
                </tr>
              ))}
              {data.cross_elasticities.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-8 text-center text-slate-500">
                    No sibling products in this category or insufficient shared history.
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
