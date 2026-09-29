"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ErrorBox, Spinner, StatCard } from "@/components/ui";
import { apiFetch, type ElasticityEstimate, type FitReport } from "@/lib/api";
import { formatMoney } from "@/lib/format";
import { useRequireAuth } from "@/lib/useRequireAuth";

export default function WorkbenchOverviewPage() {
  const ready = useRequireAuth();
  const [estimates, setEstimates] = useState<ElasticityEstimate[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);

  const loadData = () => {
    apiFetch<ElasticityEstimate[]>("/api/analytics/elasticity")
      .then((data) => setEstimates(data))
      .catch((err) => setError(err.message));
  };

  useEffect(() => {
    if (!ready) return;
    loadData();
  }, [ready]);

  const handleFitElasticity = async () => {
    try {
      setLoading(true);
      setActionMsg(null);
      const res = await apiFetch<FitReport>("/api/analytics/elasticity/fit", {
        method: "POST",
      });
      setActionMsg(
        `Fitted elasticity for ${res.n_products} SKUs. Reliable: ${res.metrics.n_reliable ?? 0}`
      );
      loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  const handleGenerateForecasts = async () => {
    try {
      setLoading(true);
      setActionMsg(null);
      const res = await apiFetch<FitReport>("/api/analytics/forecast/fit", {
        method: "POST",
      });
      const wape = res.metrics.wape_ets ? `${(Number(res.metrics.wape_ets) * 100).toFixed(1)}%` : "N/A";
      setActionMsg(`Forecast generation complete. Rolling Backtest WAPE: ${wape}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  if (!ready) return null;
  if (error) return <ErrorBox message={error} />;
  if (!estimates) return <Spinner />;

  const reliableCount = estimates.filter((e) => e.is_reliable).length;
  const avgElasticity =
    estimates.length > 0
      ? estimates.reduce((acc, e) => acc + e.elasticity, 0) / estimates.length
      : 0;
  const highlyElastic = estimates.filter((e) => e.elasticity < -1.6).length;
  const inelastic = estimates.filter((e) => e.elasticity > -1.0).length;

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-semibold">SKU Workbench</h1>
          <p className="mt-1 text-sm text-slate-400">
            Econometric demand elasticity, confidence intervals, and scenario simulations.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleFitElasticity}
            disabled={loading}
            className="rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-emerald-500 disabled:opacity-50"
          >
            {loading ? "Fitting..." : "Re-fit Elasticity Models"}
          </button>
          <button
            type="button"
            onClick={handleGenerateForecasts}
            disabled={loading}
            className="rounded-md bg-[#1f293d] px-3 py-1.5 text-xs font-medium text-slate-200 transition hover:bg-[#2b3954] disabled:opacity-50"
          >
            Run Forecasts
          </button>
        </div>
      </div>

      {actionMsg && (
        <div className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-4 py-2.5 text-xs text-emerald-300">
          {actionMsg}
        </div>
      )}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          label="Mean elasticity"
          value={avgElasticity ? avgElasticity.toFixed(2) : "—"}
          hint="Average price sensitivity across catalog"
          tone="text-sky-400"
        />
        <StatCard
          label="Econometrically reliable"
          value={`${reliableCount} / ${estimates.length}`}
          hint="Passes identification and variance gates"
          tone={reliableCount > 0 ? "text-emerald-400" : "text-amber-400"}
        />
        <StatCard
          label="Highly elastic (ε < -1.6)"
          value={String(highlyElastic)}
          hint="High volume sensitivity to price hikes"
          tone="text-amber-400"
        />
        <StatCard
          label="Inelastic (ε > -1.0)"
          value={String(inelastic)}
          hint="Pricing power, margin capture potential"
          tone="text-purple-400"
        />
      </div>

      <section className="card">
        <div className="border-b border-[#1e2836] px-4 py-3">
          <h2 className="text-sm font-semibold">Catalog Demand Elasticity Estimates</h2>
          <p className="text-xs text-slate-500">
            Log-log OLS models fitted with heteroskedasticity-robust (HC3) standard errors.
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="data">
            <thead>
              <tr>
                <th>SKU</th>
                <th>Product</th>
                <th>Category</th>
                <th className="text-right">Price</th>
                <th className="text-right">Elasticity (ε)</th>
                <th className="text-center">90% CI</th>
                <th>Method</th>
                <th className="text-right">R²</th>
                <th>Status</th>
                <th className="text-right">Action</th>
              </tr>
            </thead>
            <tbody>
              {estimates.map((row) => {
                const elast = row.elasticity;
                const elastTone =
                  elast < -1.8
                    ? "text-red-400"
                    : elast < -1.2
                    ? "text-amber-400"
                    : "text-emerald-400";

                return (
                  <tr key={row.id}>
                    <td className="font-mono text-xs text-slate-300">{row.sku}</td>
                    <td className="font-medium text-slate-200">{row.product_name}</td>
                    <td className="text-slate-400">{row.category_name ?? "—"}</td>
                    <td className="text-right tabular-nums text-slate-300">
                      {formatMoney(row.current_price)}
                    </td>
                    <td className={`text-right font-bold tabular-nums ${elastTone}`}>
                      {row.elasticity.toFixed(3)}
                    </td>
                    <td className="text-center font-mono text-xs tabular-nums text-slate-400">
                      {row.ci_low !== null && row.ci_high !== null
                        ? `[${row.ci_low.toFixed(2)}, ${row.ci_high.toFixed(2)}]`
                        : "—"}
                    </td>
                    <td>
                      <span className="rounded bg-[#1a2334] px-2 py-0.5 text-[11px] text-slate-300">
                        {row.method.replace("_", " ")}
                      </span>
                    </td>
                    <td className="text-right tabular-nums text-slate-400">
                      {row.r_squared !== null ? row.r_squared.toFixed(2) : "—"}
                    </td>
                    <td>
                      {row.is_reliable ? (
                        <span className="inline-flex items-center gap-1 text-xs text-emerald-400">
                          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                          Reliable
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-xs text-amber-400">
                          <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
                          Sparse / Prior
                        </span>
                      )}
                    </td>
                    <td className="text-right">
                      <Link
                        href={`/workbench/${row.product_id}`}
                        className="rounded bg-sky-500/10 px-2 py-1 text-xs font-medium text-sky-400 hover:bg-sky-500/20"
                      >
                        Workbench →
                      </Link>
                    </td>
                  </tr>
                );
              })}
              {estimates.length === 0 && (
                <tr>
                  <td colSpan={10} className="py-8 text-center text-slate-500">
                    No elasticity models fitted yet. Click &quot;Re-fit Elasticity Models&quot; to estimate.
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
