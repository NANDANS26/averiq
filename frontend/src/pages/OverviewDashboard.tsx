import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { apiClient } from "@/api/client";
import { formatPaise } from "@/lib/utils";
import type { RevenueRiskCase } from "@/types";

interface CanonicalCohort {
  timeframe: string;
  total_cases: number;
  recovered_cases: number;
  recovery_rate: number;
  total_at_risk_paise: number;
  organic_recovery_paise: number;
  gross_recovered_paise: number;
  incremental_recovery_paise: number;
  total_intervention_cost_paise: number;
  net_incremental_recovery_paise: number;
  average_net_recovery_per_case_paise: number;
  escalated_cases_count: number;
  failed_cases_count: number;
  human_review_cases_count: number;
  fallback_cases_count: number;
}

interface CanonicalResponse {
  currency: string;
  cohorts: Record<string, CanonicalCohort>;
  definition_notes: Record<string, string>;
}

interface RevenueRiskSummary {
  configured_currency: string;
  total_amount_at_risk: number;
  total_projected_revenue_at_risk: number;
  active_case_count: number;
  by_state: Array<{
    state: string;
    case_count: number;
    total_amount_at_risk: number;
  }>;
}

export default function OverviewDashboard() {
  const [period, setPeriod] = useState<"7d" | "30d" | "all_time">("30d");

  // Fetch canonical economics from Phase 5 hardened endpoint
  const { data: canonicalResp, error: canonicalError } = useQuery<CanonicalResponse>({
    queryKey: ["canonical-analytics", period],
    queryFn: () =>
      apiClient.get(`/analytics/recovery-canonical?period=${period}`).then((res) => res.data),
    staleTime: 30_000,
  });

  // Fetch active revenue at risk breakdown
  const { data: riskSummary } = useQuery<RevenueRiskSummary>({
    queryKey: ["revenue-risk-summary"],
    queryFn: () => apiClient.get("/analytics/revenue").then((res) => res.data),
    staleTime: 30_000,
  });

  // Fetch recent cases for operator risk queue snapshot
  const { data: recentCasesData } = useQuery<{ items: RevenueRiskCase[] }>({
    queryKey: ["overview-recent-cases"],
    queryFn: () => apiClient.get("/cases?limit=6").then((res) => res.data),
    refetchInterval: 15_000,
  });

  const recentCases = recentCasesData?.items ?? [];
  const activeCohort = canonicalResp?.cohorts?.[period];

  // Extract canonical economics safely from active cohort
  const grossPaise = activeCohort?.gross_recovered_paise ?? 0;
  const organicPaise = activeCohort?.organic_recovery_paise ?? 0;
  const incrementalPaise = activeCohort?.incremental_recovery_paise ?? 0;
  const costPaise = activeCohort?.total_intervention_cost_paise ?? 0;
  const netIncrementalPaise = activeCohort?.net_incremental_recovery_paise ?? 0;

  const totalAtRiskPaise = riskSummary?.total_amount_at_risk ?? 0;
  const activeCasesCount = riskSummary?.active_case_count ?? 0;

  // Funnel calculations derived from real state buckets
  const stateCounts = (riskSummary?.by_state ?? []).reduce(
    (acc, item) => ({ ...acc, [item.state]: item.case_count }),
    {} as Record<string, number>
  );

  const totalCohortCases = activeCohort?.total_cases ?? 0;
  const funnelAtRisk = activeCasesCount + totalCohortCases;
  const funnelDiagnosed = funnelAtRisk - (stateCounts["DETECTED"] ?? 0);
  const funnelIntervention =
    (stateCounts["ACTION_APPROVED"] ?? 0) +
    (stateCounts["EXECUTING"] ?? 0) +
    (stateCounts["VERIFYING"] ?? 0) +
    (activeCohort?.recovered_cases ?? 0);
  const funnelVerifying =
    (stateCounts["VERIFYING"] ?? 0) + (activeCohort?.recovered_cases ?? 0);
  const funnelRecovered = activeCohort?.recovered_cases ?? 0;

  return (
    <div className="space-y-6">
      {/* Header with Period Switcher */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-2 border-b border-slate-800">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
            Executive Revenue Overview
            <span className="px-2 py-0.5 text-[10px] uppercase font-mono tracking-wider rounded bg-indigo-950 text-indigo-400 border border-indigo-800/60">
              Live Economics
            </span>
          </h1>
          <p className="text-xs text-slate-400 mt-1">
            Real-time pipeline diagnostics, counterfactual recovery cohorts, and unclipped net surplus
          </p>
        </div>

        <div className="flex items-center gap-2">
          <div className="inline-flex rounded-lg border border-slate-800 bg-slate-900 p-0.5 text-xs">
            {(["7d", "30d", "all_time"] as const).map((p) => (
              <button
                key={p}
                onClick={() => setPeriod(p)}
                className={`px-3 py-1 rounded-md transition-colors ${
                  period === p
                    ? "bg-slate-800 text-white font-medium shadow-xs"
                    : "text-slate-400 hover:text-slate-200"
                }`}
              >
                {p === "all_time" ? "All Time" : p.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
      </div>

      {canonicalError && (
        <div className="p-4 rounded-lg border border-amber-900/50 bg-amber-950/20 text-amber-200 text-xs">
          Unable to fetch canonical analytics cohort data. Displaying live pipeline state where available.
        </div>
      )}

      {/* KPI Cards: Phase 3.6 Frozen Canonical Economics */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Active Revenue at Risk */}
        <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 shadow-sm hover:border-slate-700 transition-colors">
          <div className="flex items-center justify-between text-xs text-slate-400 font-medium">
            <span>Revenue at Risk</span>
            <span className="font-mono text-[11px] text-amber-400">Active</span>
          </div>
          <div className="mt-2 text-2xl font-bold tracking-tight text-white">
            {formatPaise(totalAtRiskPaise)} <span className="text-xs font-normal text-slate-400">INR</span>
          </div>
          <p className="mt-1 text-[11px] text-slate-400">
            {activeCasesCount} non-terminal failed payments in queue
          </p>
        </div>

        {/* Incremental Recovery (Unclamped) */}
        <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 shadow-sm hover:border-slate-700 transition-colors">
          <div className="flex items-center justify-between text-xs text-slate-400 font-medium">
            <span>Incremental Revenue</span>
            <span
              title="(Gross Recovered - Organic Baseline) attributable to ML actions"
              className="cursor-help text-[10px] text-slate-500 underline"
            >
              Formula
            </span>
          </div>
          <div
            className={`mt-2 text-2xl font-bold tracking-tight ${
              incrementalPaise >= 0 ? "text-emerald-400" : "text-rose-400"
            }`}
          >
            {incrementalPaise >= 0 ? "+" : ""}
            {formatPaise(incrementalPaise)}{" "}
            <span className="text-xs font-normal text-slate-400">INR</span>
          </div>
          <p className="mt-1 text-[11px] text-slate-400">
            Gross: {formatPaise(grossPaise)} • Organic P0: {formatPaise(organicPaise)}
          </p>
        </div>

        {/* Net Incremental Surplus (After Intervention Costs) */}
        <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 shadow-sm hover:border-slate-700 transition-colors">
          <div className="flex items-center justify-between text-xs text-slate-400 font-medium">
            <span>Net Incremental Surplus</span>
            <span
              title="Incremental Recovery minus Intervention Execution Costs"
              className="cursor-help text-[10px] text-slate-500 underline"
            >
              Formula
            </span>
          </div>
          <div
            className={`mt-2 text-2xl font-bold tracking-tight ${
              netIncrementalPaise >= 0 ? "text-indigo-400" : "text-rose-400"
            }`}
          >
            {netIncrementalPaise >= 0 ? "+" : ""}
            {formatPaise(netIncrementalPaise)}{" "}
            <span className="text-xs font-normal text-slate-400">INR</span>
          </div>
          <p className="mt-1 text-[11px] text-slate-400">
            Less {formatPaise(costPaise)} INR intervention costs
          </p>
        </div>

        {/* Incremental Recovery Rate */}
        <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 shadow-sm hover:border-slate-700 transition-colors">
          <div className="flex items-center justify-between text-xs text-slate-400 font-medium">
            <span>Recovery Rate</span>
            <span className="font-mono text-[11px] text-slate-500">Cohort</span>
          </div>
          <div className="mt-2 text-2xl font-bold tracking-tight text-white">
            {((activeCohort?.recovery_rate ?? 0) * 100).toFixed(1)}%
          </div>
          <p className="mt-1 text-[11px] text-emerald-400">
            {activeCohort && activeCohort.recovery_rate > 0.35
              ? `+ ${(((activeCohort.recovery_rate) - 0.35) * 100).toFixed(1)}% lift vs control`
              : "Baseline control benchmark: 35.0%"}
          </p>
        </div>
      </div>

      {/* Visual Pipeline Funnel Section */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-sm font-semibold text-white">Intervention Pipeline Funnel</h2>
            <p className="text-xs text-slate-400 mt-0.5">
              Case progression from failure detection to verified settlement
            </p>
          </div>
          <span className="text-xs font-mono text-slate-500">
            {funnelAtRisk > 0 ? `${((funnelRecovered / funnelAtRisk) * 100).toFixed(1)}% conversion` : "0% conversion"}
          </span>
        </div>

        {/* Stepped Funnel Horizontal Cards */}
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          {[
            { label: "At Risk", count: funnelAtRisk, desc: "Detected failures" },
            { label: "Diagnosed", count: funnelDiagnosed, desc: "Ranked & evaluated" },
            { label: "Intervention", count: funnelIntervention, desc: "Action approved/active" },
            { label: "Verifying", count: funnelVerifying, desc: "Awaiting settlement" },
            { label: "Recovered", count: funnelRecovered, desc: "Reconciled revenue" },
          ].map((stage, idx) => (
            <div
              key={stage.label}
              className="rounded-lg border border-slate-800/80 bg-slate-950/60 p-3.5 relative overflow-hidden"
            >
              <div className="text-[10px] uppercase font-mono tracking-wider text-slate-500">
                0{idx + 1} • {stage.label}
              </div>
              <div className="mt-2 text-xl font-bold text-white font-mono">{stage.count}</div>
              <div className="text-[11px] text-slate-400 mt-1">{stage.desc}</div>
              <div
                className="absolute bottom-0 left-0 h-1 bg-indigo-500/70"
                style={{
                  width: `${funnelAtRisk > 0 ? (stage.count / funnelAtRisk) * 100 : 0}%`,
                }}
              />
            </div>
          ))}
        </div>
      </div>

      {/* Grid: State Distribution & Recent Operator Queue */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Pipeline State Distribution */}
        <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5">
          <h2 className="text-sm font-semibold text-white mb-1">State Distribution</h2>
          <p className="text-xs text-slate-400 mb-4">Active operational non-terminal states</p>

          <div className="space-y-3">
            {(riskSummary?.by_state ?? []).map((bucket) => (
              <div key={bucket.state} className="space-y-1">
                <div className="flex items-center justify-between text-xs">
                  <span className="font-mono text-slate-300">{bucket.state}</span>
                  <div className="flex items-center gap-2">
                    <span className="text-slate-400 font-mono">{bucket.case_count}</span>
                    <span className="text-slate-500 font-mono text-[11px]">
                      ({formatPaise(bucket.total_amount_at_risk)} INR)
                    </span>
                  </div>
                </div>
                <div className="w-full h-1.5 rounded-full bg-slate-800 overflow-hidden">
                  <div
                    className="h-full bg-indigo-500 rounded-full"
                    style={{
                      width: `${
                        activeCasesCount > 0 ? (bucket.case_count / activeCasesCount) * 100 : 0
                      }%`,
                    }}
                  />
                </div>
              </div>
            ))}

            {(riskSummary?.by_state ?? []).length === 0 && (
              <div className="text-center py-8 text-xs text-slate-500">
                No active non-terminal cases in queue.
              </div>
            )}
          </div>
        </div>

        {/* Recent High-Exposure Cases */}
        <div className="lg:col-span-2 rounded-xl border border-slate-800 bg-slate-900/50 p-5">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-sm font-semibold text-white">Live Operator Queue Snapshot</h2>
              <p className="text-xs text-slate-400 mt-0.5">
                Recent failed payments ranked by risk & exposure
              </p>
            </div>
            <Link
              to="/queue"
              className="text-xs text-indigo-400 hover:text-indigo-300 font-medium transition-colors"
            >
              View Full Queue →
            </Link>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-slate-800 text-slate-400 font-medium">
                  <th className="pb-2.5 font-normal">Case ID</th>
                  <th className="pb-2.5 font-normal">Amount</th>
                  <th className="pb-2.5 font-normal">Risk Tier</th>
                  <th className="pb-2.5 font-normal">Status</th>
                  <th className="pb-2.5 font-normal text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {recentCases.map((c) => (
                  <tr key={c.id} className="hover:bg-slate-800/30 transition-colors">
                    <td className="py-2.5 font-mono text-slate-200">
                      <Link to={`/cases/${c.id}`} className="hover:underline text-indigo-400">
                        {c.id.slice(0, 8)}...
                      </Link>
                    </td>
                    <td className="py-2.5 font-mono text-slate-100">
                      {formatPaise(c.payment_amount_at_risk)} {c.currency}
                    </td>
                    <td className="py-2.5">
                      <span
                        className={`px-2 py-0.5 rounded text-[10px] font-mono uppercase ${
                          c.recovery_risk_tier === "HIGH" || c.exposure_tier === "HIGH"
                            ? "bg-rose-950 text-rose-400 border border-rose-800/50"
                            : "bg-slate-800 text-slate-300"
                        }`}
                      >
                        {c.recovery_risk_tier ?? c.exposure_tier ?? "STANDARD"}
                      </span>
                    </td>
                    <td className="py-2.5">
                      <span className="font-mono text-[11px] text-slate-300">{c.state}</span>
                    </td>
                    <td className="py-2.5 text-right">
                      <Link
                        to={`/cases/${c.id}`}
                        className="px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-200 text-[11px] transition-colors"
                      >
                        Inspect
                      </Link>
                    </td>
                  </tr>
                ))}

                {recentCases.length === 0 && (
                  <tr>
                    <td colSpan={5} className="py-8 text-center text-slate-500">
                      No cases found. Run a demo scenario or seed transactions to view live cases.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
