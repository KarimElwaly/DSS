"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ErrorBox, Spinner, StatCard } from "@/components/ui";
import { apiFetch, type PriceRecommendation } from "@/lib/api";
import { formatMoney, formatSignedPct } from "@/lib/format";
import { useRequireAuth } from "@/lib/useRequireAuth";

type ObjectiveFilter = "all" | "margin" | "revenue" | "penetration";
type StatusFilter = "all" | "proposed" | "approved" | "applied" | "rejected";

export default function RecommendationsPage() {
  const ready = useRequireAuth();
  const [recs, setRecs] = useState<PriceRecommendation[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [objFilter, setObjFilter] = useState<ObjectiveFilter>("all");

  const [genObjective, setGenObjective] = useState<"margin" | "revenue" | "penetration">("margin");
  const [genRounding, setGenRounding] = useState(true);
  const [showGenModal, setShowGenModal] = useState(false);

  const loadData = () => {
    let url = "/api/pricing/recommendations";
    const params = new URLSearchParams();
    if (statusFilter !== "all") params.set("status", statusFilter);
    if (objFilter !== "all") params.set("objective", objFilter);
    const qs = params.toString();
    if (qs) url += `?${qs}`;

    apiFetch<PriceRecommendation[]>(url)
      .then((data) => setRecs(data))
      .catch((err) => setError(err.message));
  };

  useEffect(() => {
    if (!ready) return;
    loadData();
  }, [ready, statusFilter, objFilter]);

  const handleGenerate = async () => {
    try {
      setLoading(true);
      setError(null);
      setMsg(null);
      const res = await apiFetch<PriceRecommendation[]>("/api/pricing/recommendations/generate", {
        method: "POST",
        body: JSON.stringify({
          objective: genObjective,
          apply_charm_rounding: genRounding,
        }),
      });
      setShowGenModal(false);
      setMsg(`Successfully generated ${res.length} recommendations under ${genObjective} objective.`);
      loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  const handleDecide = async (id: string, newStatus: "approved" | "rejected" | "applied") => {
    try {
      setLoading(true);
      setError(null);
      const updated = await apiFetch<PriceRecommendation>(`/api/pricing/recommendations/${id}/decide`, {
        method: "POST",
        body: JSON.stringify({
          status: newStatus,
          note: `Marked as ${newStatus} via recommendations dashboard.`,
        }),
      });
      setMsg(`Recommendation for ${updated.sku} updated to ${newStatus}.`);
      loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  if (!ready) return null;
  if (error) return <ErrorBox message={error} />;
  if (!recs) return <Spinner />;

  const proposedCount = recs.filter((r) => r.status === "proposed").length;
  const approvedCount = recs.filter((r) => r.status === "approved").length;
  const appliedCount = recs.filter((r) => r.status === "applied").length;

  const totalBaselineMargin = recs.reduce((acc, r) => acc + (r.baseline_margin ?? 0), 0);
  const totalExpMargin = recs.reduce((acc, r) => acc + (r.expected_margin ?? 0), 0);
  const portfolioMarginLiftPct =
    totalBaselineMargin > 0 ? (totalExpMargin - totalBaselineMargin) / totalBaselineMargin : 0;

  const constrainedCount = recs.filter(
    (r) => r.binding_constraints.length > 0 && !r.binding_constraints.includes("none")
  ).length;

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-semibold">Price Recommendations</h1>
          <p className="mt-1 text-sm text-slate-400">
            Constrained mathematical price optimizations with guardrail enforcement and audit trails.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setShowGenModal(true)}
          className="rounded-md bg-emerald-600 px-3.5 py-1.5 text-xs font-medium text-white transition hover:bg-emerald-500"
        >
          + Generate Recommendations
        </button>
      </div>

      {msg && (
        <div className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-4 py-2.5 text-xs text-emerald-300">
          {msg}
        </div>
      )}

      {/* KPI Cards */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          label="Proposed Actions"
          value={String(proposedCount)}
          hint={`${approvedCount} approved · ${appliedCount} applied`}
          tone="text-amber-400"
        />
        <StatCard
          label="Projected Portfolio Margin Δ"
          value={formatSignedPct(portfolioMarginLiftPct)}
          hint="Net expected profit shift vs current prices"
          tone={portfolioMarginLiftPct >= 0 ? "text-emerald-400" : "text-red-400"}
        />
        <StatCard
          label="Guardrail Bound Items"
          value={`${constrainedCount} / ${recs.length}`}
          hint="Constrained by floor, ceiling, or max step"
          tone={constrainedCount > 0 ? "text-sky-400" : "text-slate-400"}
        />
        <StatCard
          label="Active Objective"
          value={objFilter === "all" ? "Mixed" : objFilter.toUpperCase()}
          hint="Target optimization function"
        />
      </div>

      {/* Filter Tabs */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#1e2836] pb-3">
        <div className="flex items-center gap-1.5 text-xs">
          <span className="text-slate-400 mr-1">Status:</span>
          {(["all", "proposed", "approved", "applied", "rejected"] as StatusFilter[]).map((st) => (
            <button
              key={st}
              type="button"
              onClick={() => setStatusFilter(st)}
              className={`rounded px-2.5 py-1 capitalize transition ${
                statusFilter === st
                  ? "bg-slate-700 text-white font-medium"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              {st}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1.5 text-xs">
          <span className="text-slate-400 mr-1">Objective:</span>
          {(["all", "margin", "revenue", "penetration"] as ObjectiveFilter[]).map((obj) => (
            <button
              key={obj}
              type="button"
              onClick={() => setObjFilter(obj)}
              className={`rounded px-2.5 py-1 capitalize transition ${
                objFilter === obj
                  ? "bg-slate-700 text-white font-medium"
                  : "text-slate-400 hover:text-slate-200"
              }`}
            >
              {obj}
            </button>
          ))}
        </div>
      </div>

      {/* Recommendations Table */}
      <section className="card">
        <div className="overflow-x-auto">
          <table className="data">
            <thead>
              <tr>
                <th>SKU</th>
                <th>Product</th>
                <th>Objective</th>
                <th className="text-right">Current</th>
                <th className="text-right">Recommended</th>
                <th className="text-right">Shift (Δ%)</th>
                <th className="text-right">Margin Δ%</th>
                <th>Guardrails / Binding</th>
                <th>Status</th>
                <th className="text-right">Decision Actions</th>
              </tr>
            </thead>
            <tbody>
              {recs.map((r) => {
                const deltaP = (r.recommended_price - r.current_price) / r.current_price;
                const marginDelta = Number(r.rationale.delta_margin_pct ?? 0);
                const statusColors = {
                  proposed: "bg-amber-500/10 text-amber-400 border-amber-500/20",
                  approved: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
                  applied: "bg-sky-500/10 text-sky-400 border-sky-500/20",
                  rejected: "bg-rose-500/10 text-rose-400 border-rose-500/20",
                  expired: "bg-slate-500/10 text-slate-400 border-slate-500/20",
                };

                return (
                  <tr key={r.id}>
                    <td className="font-mono text-xs text-sky-400">
                      <Link href={`/workbench/${r.product_id}`} className="hover:underline">
                        {r.sku}
                      </Link>
                    </td>
                    <td className="font-medium text-slate-200">{r.product_name}</td>
                    <td className="capitalize text-slate-400 text-xs">{r.objective}</td>
                    <td className="text-right tabular-nums text-slate-400">
                      {formatMoney(r.current_price)}
                    </td>
                    <td className="text-right font-mono font-bold tabular-nums text-slate-100">
                      {formatMoney(r.recommended_price)}
                    </td>
                    <td
                      className={`text-right font-medium tabular-nums ${
                        deltaP >= 0 ? "text-emerald-400" : "text-amber-400"
                      }`}
                    >
                      {formatSignedPct(deltaP)}
                    </td>
                    <td
                      className={`text-right font-medium tabular-nums ${
                        marginDelta >= 0 ? "text-emerald-400" : "text-amber-400"
                      }`}
                    >
                      {formatSignedPct(marginDelta)}
                    </td>
                    <td>
                      <div className="flex flex-wrap gap-1">
                        {r.binding_constraints.map((c) => (
                          <span
                            key={c}
                            className={`rounded px-1.5 py-0.5 text-[10px] font-mono ${
                              c.includes("floor")
                                ? "bg-red-500/10 text-red-400 border border-red-500/20"
                                : c.includes("ceiling")
                                ? "bg-purple-500/10 text-purple-400 border border-purple-500/20"
                                : "bg-[#182335] text-slate-300"
                            }`}
                          >
                            {c}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td>
                      <span
                        className={`inline-block rounded border px-2 py-0.5 text-[11px] capitalize font-medium ${
                          statusColors[r.status]
                        }`}
                      >
                        {r.status}
                      </span>
                    </td>
                    <td className="text-right space-x-1.5">
                      {r.status === "proposed" && (
                        <>
                          <button
                            type="button"
                            disabled={loading}
                            onClick={() => handleDecide(r.id, "approved")}
                            className="rounded bg-emerald-600/20 px-2 py-1 text-[11px] font-medium text-emerald-400 hover:bg-emerald-600/30"
                          >
                            Approve
                          </button>
                          <button
                            type="button"
                            disabled={loading}
                            onClick={() => handleDecide(r.id, "rejected")}
                            className="rounded bg-rose-600/20 px-2 py-1 text-[11px] font-medium text-rose-400 hover:bg-rose-600/30"
                          >
                            Reject
                          </button>
                        </>
                      )}
                      {r.status === "approved" && (
                        <button
                          type="button"
                          disabled={loading}
                          onClick={() => handleDecide(r.id, "applied")}
                          className="rounded bg-sky-600 px-2 py-1 text-[11px] font-medium text-white hover:bg-sky-500"
                        >
                          Apply Price
                        </button>
                      )}
                      {r.status === "applied" && (
                        <span className="text-[11px] text-slate-500">Live in catalog</span>
                      )}
                    </td>
                  </tr>
                );
              })}
              {recs.length === 0 && (
                <tr>
                  <td colSpan={10} className="py-8 text-center text-slate-500">
                    No recommendations found matching filters. Click &quot;Generate Recommendations&quot; to optimize.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* Generation Modal */}
      {showGenModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-md rounded-xl border border-[#243044] bg-[#0e1422] p-6 shadow-2xl">
            <h3 className="text-base font-semibold text-slate-100">
              Generate Price Recommendations
            </h3>
            <p className="mt-1 text-xs text-slate-400">
              Run constrained optimization across catalog products.
            </p>

            <div className="mt-4 space-y-4 text-xs">
              <div>
                <label className="block text-slate-300 font-medium mb-1">
                  Pricing Objective
                </label>
                <select
                  value={genObjective}
                  onChange={(e) => setGenObjective(e.target.value as any)}
                  className="w-full rounded border border-[#243044] bg-[#161f30] px-3 py-2 text-slate-200"
                >
                  <option value="margin">Gross Margin Maximization</option>
                  <option value="revenue">Revenue Maximization</option>
                  <option value="penetration">Market Penetration (Volume Lift)</option>
                </select>
              </div>

              <div className="flex items-center gap-2 pt-1">
                <input
                  type="checkbox"
                  id="charm"
                  checked={genRounding}
                  onChange={(e) => setGenRounding(e.target.checked)}
                  className="h-4 w-4 rounded border-slate-700 bg-[#161f30] accent-emerald-500"
                />
                <label htmlFor="charm" className="text-slate-300">
                  Enforce psychological .99 charm endings
                </label>
              </div>
            </div>

            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowGenModal(false)}
                className="rounded px-3 py-1.5 text-xs text-slate-400 hover:text-slate-200"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={loading}
                onClick={handleGenerate}
                className="rounded bg-emerald-600 px-4 py-1.5 text-xs font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
              >
                {loading ? "Optimizing..." : "Run Optimization"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
