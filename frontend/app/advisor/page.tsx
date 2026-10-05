"use client";

import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ErrorBox, Spinner, StatCard } from "@/components/ui";
import {
  apiFetch,
  type AdvisorReport,
  type FinancialFinding,
  type FinancialPeriod,
} from "@/lib/api";
import { formatMoney, formatPct, formatSignedPct } from "@/lib/format";
import { useRequireAuth } from "@/lib/useRequireAuth";

export default function AdvisorPage() {
  const ready = useRequireAuth();
  const [report, setReport] = useState<AdvisorReport | null>(null);
  const [periods, setPeriods] = useState<FinancialPeriod[] | null>(null);
  const [findings, setFindings] = useState<FinancialFinding[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const loadData = () => {
    Promise.all([
      apiFetch<AdvisorReport | null>("/api/finance/advisor/latest").catch(() => null),
      apiFetch<FinancialPeriod[]>("/api/finance/periods"),
      apiFetch<FinancialFinding[]>("/api/finance/findings"),
    ])
      .then(([rep, per, fin]) => {
        setReport(rep);
        setPeriods(per);
        setFindings(fin);
      })
      .catch((err) => setError(err.message));
  };

  useEffect(() => {
    if (!ready) return;
    loadData();
  }, [ready]);

  const handleGenerate = async () => {
    try {
      setLoading(true);
      setError(null);
      setMsg(null);
      const rep = await apiFetch<AdvisorReport>("/api/finance/advisor/generate", {
        method: "POST",
      });
      setReport(rep);
      setMsg("New CFO strategic advisor briefing generated.");
      loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  const handleSeed = async () => {
    try {
      setLoading(true);
      setError(null);
      await apiFetch("/api/finance/seed", { method: "POST" });
      setMsg("Seeded trailing financial statements and computed KPIs.");
      loadData();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  if (!ready) return null;
  if (error) return <ErrorBox message={error} />;
  if (!periods) return <Spinner />;

  const latestPeriod = periods.length > 0 ? periods[periods.length - 1] : null;
  const latestMetrics = latestPeriod?.metrics ?? {};

  const chartData = periods.map((p) => ({
    period: p.label,
    grossMargin: p.metrics.gross_margin ? p.metrics.gross_margin * 100 : 0,
    ebitdaMargin: p.metrics.ebitda_margin ? p.metrics.ebitda_margin * 100 : 0,
    runway: p.metrics.runway_months ?? 0,
    ccc: p.metrics.ccc ?? 0,
  }));

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-semibold">CFO Strategic Advisor</h1>
          <p className="mt-1 text-sm text-slate-400">
            AI-driven strategic finance briefings verified by numeric-grounding validation over enterprise facts.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {periods.length === 0 && (
            <button
              type="button"
              onClick={handleSeed}
              disabled={loading}
              className="rounded-md bg-[#1f293d] px-3.5 py-1.5 text-xs font-medium text-slate-200 transition hover:bg-[#2b3954]"
            >
              Seed Demo Financials
            </button>
          )}
          <button
            type="button"
            onClick={handleGenerate}
            disabled={loading}
            className="rounded-md bg-emerald-600 px-3.5 py-1.5 text-xs font-medium text-white transition hover:bg-emerald-500 disabled:opacity-50"
          >
            {loading ? "Synthesizing Briefing..." : "Generate Strategic Report"}
          </button>
        </div>
      </div>

      {msg && (
        <div className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-4 py-2.5 text-xs text-emerald-300">
          {msg}
        </div>
      )}

      {/* KPI Cards */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          label="Gross Margin"
          value={latestMetrics.gross_margin ? formatPct(latestMetrics.gross_margin) : "—"}
          hint={latestPeriod ? `Latest: ${latestPeriod.label}` : ""}
          tone={
            latestMetrics.gross_margin && latestMetrics.gross_margin < 0.28
              ? "text-amber-400"
              : "text-emerald-400"
          }
        />
        <StatCard
          label="Cash Runway"
          value={
            latestMetrics.runway_months !== undefined
              ? `${latestMetrics.runway_months.toFixed(1)} Mo`
              : "—"
          }
          hint="Coverage at net monthly burn rate"
          tone={
            latestMetrics.runway_months && latestMetrics.runway_months < 12
              ? "text-red-400"
              : "text-sky-400"
          }
        />
        <StatCard
          label="Cash Conversion Cycle"
          value={latestMetrics.ccc !== undefined ? `${latestMetrics.ccc.toFixed(0)} Days` : "—"}
          hint="Working capital liquidity lag"
          tone="text-slate-200"
        />
        <StatCard
          label="CAC vs LTV Ratio"
          value={
            latestMetrics.ltv_cac_ratio !== undefined
              ? `${latestMetrics.ltv_cac_ratio.toFixed(1)}x`
              : "—"
          }
          hint={
            latestMetrics.cac
              ? `CAC: ${formatMoney(latestMetrics.cac)} · LTV: ${formatMoney(latestMetrics.ltv ?? 0)}`
              : ""
          }
          tone={
            latestMetrics.ltv_cac_ratio && latestMetrics.ltv_cac_ratio >= 3.0
              ? "text-emerald-400"
              : "text-amber-400"
          }
        />
      </div>

      {/* Strategic Advisor Narrative */}
      {report ? (
        <section className="card p-6 space-y-4">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between border-b border-[#1e2836] pb-3">
            <div className="flex items-center gap-2">
              <span className="grid h-6 w-6 place-items-center rounded bg-emerald-500/20 text-xs font-bold text-emerald-400">
                AI
              </span>
              <h2 className="text-sm font-semibold text-slate-100">
                Executive Strategy Briefing
              </h2>
            </div>
            <div className="flex items-center gap-2">
              {report.grounding_passed ? (
                <span className="inline-flex items-center gap-1 rounded bg-emerald-500/15 border border-emerald-500/30 px-2 py-0.5 text-xs text-emerald-300 font-medium">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                  100% Grounded in Facts JSON
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 rounded bg-rose-500/15 border border-rose-500/30 px-2 py-0.5 text-xs text-rose-300 font-medium">
                  <span className="h-1.5 w-1.5 rounded-full bg-rose-400" />
                  {report.grounding_violations.length} Grounding Violations
                </span>
              )}
              <span className="text-[11px] text-slate-500 font-mono">
                {report.model_name} · {report.latency_ms ?? 0}ms
              </span>
            </div>
          </div>

          <div className="space-y-3 text-sm text-slate-300 leading-relaxed whitespace-pre-line font-sans">
            {report.narrative}
          </div>

          {report.grounding_violations.length > 0 && (
            <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 p-3 text-xs text-rose-300 space-y-1">
              <div className="font-semibold">Unverified / Hallucinated Numeric Claims:</div>
              {report.grounding_violations.map((v, i) => (
                <div key={i}>• {v}</div>
              ))}
            </div>
          )}

          {report.recommendations && report.recommendations.length > 0 && (
            <div className="pt-2">
              <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">
                Priority Capital & Pricing Directives
              </h3>
              <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                {report.recommendations.map((rec, i) => (
                  <div
                    key={i}
                    className="rounded-lg border border-[#1e2836] bg-[#0c111e] p-3 text-xs space-y-1"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-slate-400 text-[11px]">
                        {rec.category ?? "Action Item"}
                      </span>
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                          rec.priority === "High"
                            ? "bg-rose-500/20 text-rose-400"
                            : "bg-amber-500/20 text-amber-400"
                        }`}
                      >
                        {rec.priority ?? "Normal"}
                      </span>
                    </div>
                    <div className="font-medium text-slate-200">{rec.action}</div>
                    {rec.expected_impact && (
                      <div className="text-[11px] text-emerald-400">{rec.expected_impact}</div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>
      ) : (
        <div className="card p-10 text-center text-slate-500">
          <p>No advisor briefing generated yet. Click &quot;Generate Strategic Report&quot; to synthesize executive insights.</p>
        </div>
      )}

      {/* Financial Trajectory Chart */}
      {periods.length > 0 && (
        <section className="card p-5">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-sm font-semibold">Corporate Financial Trends (Trailing Quarters)</h2>
              <p className="text-xs text-slate-500">
                Gross margin, operating profitability, and working capital cycle over time.
              </p>
            </div>
          </div>
          <div className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData}>
                <CartesianGrid stroke="#1b2434" vertical={false} />
                <XAxis dataKey="period" tick={{ fill: "#64748b", fontSize: 11 }} />
                <YAxis
                  yAxisId="pct"
                  tick={{ fill: "#64748b", fontSize: 11 }}
                  tickFormatter={(v) => `${v}%`}
                  domain={[0, 60]}
                  width={42}
                />
                <YAxis
                  yAxisId="days"
                  orientation="right"
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
                  formatter={(val: unknown) => [
                    typeof val === "number" ? val.toFixed(1) : String(val),
                    "",
                  ]}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Line
                  yAxisId="pct"
                  type="monotone"
                  dataKey="grossMargin"
                  name="Gross Margin %"
                  stroke="#10b981"
                  strokeWidth={2}
                  dot={{ r: 3 }}
                />
                <Line
                  yAxisId="pct"
                  type="monotone"
                  dataKey="ebitdaMargin"
                  name="EBITDA Margin %"
                  stroke="#38bdf8"
                  strokeWidth={2}
                  dot={{ r: 3 }}
                />
                <Line
                  yAxisId="days"
                  type="monotone"
                  dataKey="ccc"
                  name="CCC (Days)"
                  stroke="#f59e0b"
                  strokeWidth={1.5}
                  strokeDasharray="3 3"
                  dot={{ r: 2 }}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </section>
      )}

      {/* Anomalies & Findings */}
      {findings && findings.length > 0 && (
        <section className="card">
          <div className="border-b border-[#1e2836] px-4 py-3">
            <h2 className="text-sm font-semibold">Financial Anomalies & Diagnostic Findings</h2>
          </div>
          <div className="divide-y divide-[#1e2836]">
            {findings.map((f) => {
              const sevColors = {
                critical: "text-rose-400 bg-rose-500/10 border-rose-500/20",
                warning: "text-amber-400 bg-amber-500/10 border-amber-500/20",
                info: "text-sky-400 bg-sky-500/10 border-sky-500/20",
              };

              return (
                <div key={f.id} className="p-4 space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-slate-200 text-xs">{f.title}</span>
                    <span
                      className={`rounded border px-2 py-0.5 text-[10px] uppercase font-semibold tracking-wider ${
                        sevColors[f.severity]
                      }`}
                    >
                      {f.severity}
                    </span>
                  </div>
                  <p className="text-xs text-slate-400">{f.detail}</p>
                </div>
              );
            })}
          </div>
        </section>
      )}
    </div>
  );
}
