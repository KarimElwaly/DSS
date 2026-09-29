"use client";

import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ErrorBox, Spinner, StatCard } from "@/components/ui";
import {
  apiFetch,
  type Page,
  type Product,
  type SimulationRun,
} from "@/lib/api";
import { formatMoney, formatSignedPct } from "@/lib/format";
import { useRequireAuth } from "@/lib/useRequireAuth";

export default function SandboxPage() {
  const ready = useRequireAuth();
  const [products, setProducts] = useState<Product[] | null>(null);
  const [runs, setRuns] = useState<SimulationRun[] | null>(null);
  const [activeRun, setActiveRun] = useState<SimulationRun | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Scenario config state
  const [scenarioName, setScenarioName] = useState("Audio & Visual Selective Price Lift");
  const [horizonDays, setHorizonDays] = useState(30);
  const [competitorReaction, setCompetitorReaction] = useState(0.30);
  const [monteCarloDraws, setMonteCarloDraws] = useState(300);
  const [overrides, setOverrides] = useState<Record<string, number>>({});

  useEffect(() => {
    if (!ready) return;
    Promise.all([
      apiFetch<Page<Product>>("/api/catalog/products?limit=100"),
      apiFetch<SimulationRun[]>("/api/pricing/sandbox/runs"),
    ])
      .then(([prodPage, simRuns]) => {
        setProducts(prodPage.items);
        setRuns(simRuns);
        if (simRuns.length > 0) {
          setActiveRun(simRuns[0]);
        }
      })
      .catch((err) => setError(err.message));
  }, [ready]);

  const handleAdjustPrice = (productId: string, currentPrice: number, pct: number) => {
    const newPrice = Math.round(currentPrice * (1 + pct) * 100) / 100;
    setOverrides((prev) => ({
      ...prev,
      [productId]: newPrice,
    }));
  };

  const handleClearOverride = (productId: string) => {
    setOverrides((prev) => {
      const copy = { ...prev };
      delete copy[productId];
      return copy;
    });
  };

  const handleRunSimulation = async () => {
    try {
      setLoading(true);
      setError(null);
      const res = await apiFetch<SimulationRun>("/api/pricing/sandbox/simulate", {
        method: "POST",
        body: JSON.stringify({
          name: scenarioName,
          horizon_days: horizonDays,
          price_overrides: overrides,
          competitor_reaction_pct: competitorReaction,
          n_monte_carlo: monteCarloDraws,
        }),
      });
      setActiveRun(res);
      setRuns((prev) => [res, ...(prev ?? []).filter((r) => r.id !== res.id)]);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  if (!ready) return null;
  if (error) return <ErrorBox message={error} />;
  if (!products) return <Spinner />;

  const overridesCount = Object.keys(overrides).length;
  const simResults = activeRun?.results;

  const quantileChartData = simResults
    ? [
        {
          percentile: "P05 (Bear / VaR)",
          revenue: simResults.quantiles.revenue.p05,
          margin: simResults.quantiles.margin.p05,
        },
        {
          percentile: "P25",
          revenue: simResults.quantiles.revenue.p25,
          margin: simResults.quantiles.margin.p25,
        },
        {
          percentile: "P50 (Median)",
          revenue: simResults.quantiles.revenue.p50,
          margin: simResults.quantiles.margin.p50,
        },
        {
          percentile: "P75",
          revenue: simResults.quantiles.revenue.p75,
          margin: simResults.quantiles.margin.p75,
        },
        {
          percentile: "P95 (Bull Case)",
          revenue: simResults.quantiles.revenue.p95,
          margin: simResults.quantiles.margin.p95,
        },
      ]
    : [];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Scenario Sandbox</h1>
        <p className="mt-1 text-sm text-slate-400">
          Simulate portfolio price shifts with Monte Carlo elasticity uncertainty sampling and competitor reaction modeling.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Scenario Controls Panel */}
        <section className="card p-5 space-y-4 lg:col-span-1">
          <h2 className="text-sm font-semibold text-slate-200">Scenario Configuration</h2>

          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">Scenario Name</label>
            <input
              type="text"
              value={scenarioName}
              onChange={(e) => setScenarioName(e.target.value)}
              className="w-full rounded border border-[#243044] bg-[#121929] px-3 py-1.5 text-xs text-slate-200"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">Horizon</label>
              <select
                value={horizonDays}
                onChange={(e) => setHorizonDays(Number(e.target.value))}
                className="w-full rounded border border-[#243044] bg-[#121929] px-2.5 py-1.5 text-xs text-slate-200"
              >
                <option value={14}>14 Days</option>
                <option value={30}>30 Days</option>
                <option value={60}>60 Days</option>
                <option value={90}>90 Days</option>
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">Monte Carlo Draws</label>
              <select
                value={monteCarloDraws}
                onChange={(e) => setMonteCarloDraws(Number(e.target.value))}
                className="w-full rounded border border-[#243044] bg-[#121929] px-2.5 py-1.5 text-xs text-slate-200"
              >
                <option value={150}>150 Draws (Fast)</option>
                <option value={300}>300 Draws</option>
                <option value={500}>500 Draws (Precise)</option>
              </select>
            </div>
          </div>

          <div>
            <div className="flex justify-between text-xs mb-1">
              <span className="font-medium text-slate-400">Competitor Reaction Factor</span>
              <span className="font-mono text-sky-400">{(competitorReaction * 100).toFixed(0)}%</span>
            </div>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={competitorReaction}
              onChange={(e) => setCompetitorReaction(parseFloat(e.target.value))}
              className="h-1.5 w-full cursor-pointer appearance-none rounded-lg bg-[#1a2334] accent-sky-400"
            />
            <p className="text-[10px] text-slate-500 mt-1">
              Degree to which rivals match our price shifts, mitigating volume leakage.
            </p>
          </div>

          {/* Price Adjusters List */}
          <div className="pt-2">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-medium text-slate-300">
                Adjust SKU Prices ({overridesCount} changed)
              </span>
              {overridesCount > 0 && (
                <button
                  type="button"
                  onClick={() => setOverrides({})}
                  className="text-[11px] text-slate-400 hover:text-slate-200"
                >
                  Reset All
                </button>
              )}
            </div>

            <div className="max-h-60 overflow-y-auto space-y-2 pr-1">
              {products.slice(0, 12).map((p) => {
                const curP = Number(p.current_price);
                const overrideP = overrides[p.id];
                const active = overrideP !== undefined;

                return (
                  <div
                    key={p.id}
                    className={`rounded-lg border p-2 text-xs transition ${
                      active
                        ? "border-sky-500/40 bg-sky-500/5"
                        : "border-[#1e2836] bg-[#0d1220]"
                    }`}
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="font-mono font-medium text-slate-300">{p.sku}</span>
                      <span className="tabular-nums text-slate-400">
                        {active ? (
                          <span className="font-bold text-sky-400 font-mono">
                            {formatMoney(overrideP)}{" "}
                            <span className="text-[10px] font-normal text-slate-500">
                              ({formatSignedPct((overrideP - curP) / curP)})
                            </span>
                          </span>
                        ) : (
                          formatMoney(curP)
                        )}
                      </span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={() => handleAdjustPrice(p.id, curP, -0.10)}
                        className="rounded bg-[#1a2334] px-1.5 py-0.5 text-[10px] text-slate-300 hover:bg-[#253249]"
                      >
                        -10%
                      </button>
                      <button
                        type="button"
                        onClick={() => handleAdjustPrice(p.id, curP, -0.05)}
                        className="rounded bg-[#1a2334] px-1.5 py-0.5 text-[10px] text-slate-300 hover:bg-[#253249]"
                      >
                        -5%
                      </button>
                      <button
                        type="button"
                        onClick={() => handleAdjustPrice(p.id, curP, +0.05)}
                        className="rounded bg-[#1a2334] px-1.5 py-0.5 text-[10px] text-slate-300 hover:bg-[#253249]"
                      >
                        +5%
                      </button>
                      <button
                        type="button"
                        onClick={() => handleAdjustPrice(p.id, curP, +0.10)}
                        className="rounded bg-[#1a2334] px-1.5 py-0.5 text-[10px] text-slate-300 hover:bg-[#253249]"
                      >
                        +10%
                      </button>
                      {active && (
                        <button
                          type="button"
                          onClick={() => handleClearOverride(p.id)}
                          className="ml-auto text-[10px] text-rose-400 hover:underline"
                        >
                          Clear
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          <button
            type="button"
            disabled={loading}
            onClick={handleRunSimulation}
            className="w-full rounded-md bg-sky-600 py-2 text-xs font-semibold text-white shadow transition hover:bg-sky-500 disabled:opacity-50"
          >
            {loading ? "Simulating Monte Carlo..." : "Run Monte Carlo Simulation"}
          </button>
        </section>

        {/* Results Visualizer Panel */}
        <section className="space-y-6 lg:col-span-2">
          {activeRun && simResults ? (
            <>
              {/* Simulation KPIs */}
              <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
                <StatCard
                  label="Simulated Horizon"
                  value={`${activeRun.horizon_days} Days`}
                  hint={`${activeRun.n_monte_carlo} Monte Carlo draws`}
                />
                <StatCard
                  label="Expected Margin Δ"
                  value={formatSignedPct(simResults.delta.margin_pct)}
                  hint={`P50: ${formatMoney(simResults.simulated.margin)}`}
                  tone={simResults.delta.margin_pct >= 0 ? "text-emerald-400" : "text-red-400"}
                />
                <StatCard
                  label="Expected Revenue Δ"
                  value={formatSignedPct(simResults.delta.revenue_pct)}
                  hint={`P50: ${formatMoney(simResults.simulated.revenue)}`}
                  tone={simResults.delta.revenue_pct >= 0 ? "text-emerald-400" : "text-amber-400"}
                />
                <StatCard
                  label="Volume Shift Δ"
                  value={formatSignedPct(simResults.delta.units_pct)}
                  hint={`Expected: ${simResults.simulated.units.toLocaleString()} units`}
                  tone="text-sky-400"
                />
              </div>

              {/* Quantile Distributions Chart */}
              <div className="card p-4">
                <div className="flex items-center justify-between mb-3">
                  <div>
                    <h3 className="text-sm font-semibold">Distribution of Simulated Financial Outcomes</h3>
                    <p className="text-xs text-slate-400">
                      Uncertainty percentiles for scenario &quot;{activeRun.name}&quot;
                    </p>
                  </div>
                  <span className="rounded bg-sky-500/10 px-2 py-0.5 text-xs font-mono text-sky-400">
                    Baseline Margin: {formatMoney(simResults.baseline.margin)}
                  </span>
                </div>
                <div className="h-64">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={quantileChartData}>
                      <CartesianGrid stroke="#1b2434" vertical={false} />
                      <XAxis dataKey="percentile" tick={{ fill: "#64748b", fontSize: 11 }} />
                      <YAxis
                        tick={{ fill: "#64748b", fontSize: 11 }}
                        tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`}
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
                          typeof val === "number" ? formatMoney(val) : String(val),
                          "",
                        ]}
                      />
                      <Legend wrapperStyle={{ fontSize: 12 }} />
                      <Bar dataKey="revenue" name="Simulated Revenue" fill="#38bdf8" radius={[4, 4, 0, 0]} />
                      <Bar dataKey="margin" name="Simulated Margin" fill="#10b981" radius={[4, 4, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>

              {/* Changed SKUs Impact Table */}
              <div className="card">
                <div className="border-b border-[#1e2836] px-4 py-3">
                  <h3 className="text-sm font-semibold">Adjusted SKUs Expected Impact</h3>
                </div>
                <div className="overflow-x-auto">
                  <table className="data">
                    <thead>
                      <tr>
                        <th>SKU</th>
                        <th>Product</th>
                        <th className="text-right">Current</th>
                        <th className="text-right">Simulated</th>
                        <th className="text-right">Price Δ%</th>
                        <th className="text-right">Volume Δ%</th>
                        <th className="text-right">Exp. Revenue</th>
                        <th className="text-right">Exp. Margin</th>
                      </tr>
                    </thead>
                    <tbody>
                      {simResults.sku_impacts.map((row) => (
                        <tr key={row.product_id}>
                          <td className="font-mono text-xs text-sky-400">{row.sku}</td>
                          <td className="text-slate-200">{row.name}</td>
                          <td className="text-right tabular-nums text-slate-400">
                            {formatMoney(row.current_price)}
                          </td>
                          <td className="text-right font-mono font-bold tabular-nums text-slate-100">
                            {formatMoney(row.new_price)}
                          </td>
                          <td
                            className={`text-right font-medium tabular-nums ${
                              row.price_delta_pct >= 0 ? "text-emerald-400" : "text-amber-400"
                            }`}
                          >
                            {formatSignedPct(row.price_delta_pct)}
                          </td>
                          <td
                            className={`text-right font-medium tabular-nums ${
                              row.units_delta_pct >= 0 ? "text-emerald-400" : "text-red-400"
                            }`}
                          >
                            {formatSignedPct(row.units_delta_pct)}
                          </td>
                          <td className="text-right tabular-nums text-slate-300">
                            {formatMoney(row.expected_revenue)}
                          </td>
                          <td className="text-right font-medium tabular-nums text-emerald-400">
                            {formatMoney(row.expected_margin)}
                          </td>
                        </tr>
                      ))}
                      {simResults.sku_impacts.length === 0 && (
                        <tr>
                          <td colSpan={8} className="py-6 text-center text-slate-500">
                            No individual SKU price overrides specified; simulating current baseline portfolio.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          ) : (
            <div className="card p-12 text-center text-slate-500">
              <p>Configure price shifts on the left and click &quot;Run Monte Carlo Simulation&quot; to inspect probability distributions.</p>
            </div>
          )}

          {/* Past Simulation History */}
          {runs && runs.length > 1 && (
            <div className="card">
              <div className="border-b border-[#1e2836] px-4 py-3">
                <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
                  Saved Scenario Runs ({runs.length})
                </h3>
              </div>
              <div className="divide-y divide-[#1e2836]">
                {runs.map((r) => (
                  <button
                    key={r.id}
                    type="button"
                    onClick={() => setActiveRun(r)}
                    className={`flex w-full items-center justify-between p-3 text-left text-xs transition ${
                      activeRun?.id === r.id ? "bg-[#162032]" : "hover:bg-[#121927]"
                    }`}
                  >
                    <div>
                      <div className="font-medium text-slate-200">{r.name}</div>
                      <div className="text-[11px] text-slate-500">
                        {new Date(r.created_at).toLocaleDateString()} · {r.horizon_days}d horizon · {r.n_monte_carlo} draws
                      </div>
                    </div>
                    <div className="text-right font-mono">
                      <span
                        className={
                          r.results.delta.margin_pct >= 0 ? "text-emerald-400" : "text-amber-400"
                        }
                      >
                        Margin {formatSignedPct(r.results.delta.margin_pct)}
                      </span>
                    </div>
                  </button>
                ))}
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
