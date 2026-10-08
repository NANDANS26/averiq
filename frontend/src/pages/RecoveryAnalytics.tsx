import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/api/client";
import { formatPaise } from "@/lib/utils";

interface CanonicalAnalytics {
  period: string;
  total_cases: number;
  terminal_cases: number;
  recovered_cases: number;
  gross_recovered_paise: number;
  organic_baseline_paise: number;
  incremental_recovered_paise: number;
  intervention_cost_paise: number;
  net_incremental_paise: number;
  recovery_rate: number;
  incremental_recovery_rate: number;
  currency: string;
}

export default function RecoveryAnalytics() {
  const [period, setPeriod] = useState<"7d" | "30d" | "all_time">("30d");

  const { data: canonical } = useQuery<CanonicalAnalytics>({
    queryKey: ["canonical-analytics-page", period],
    queryFn: () =>
      apiClient.get(`/api/v1/analytics/recovery-canonical?period=${period}`).then((r) => r.data),
  });

  const grossPaise = canonical?.gross_recovered_paise ?? 0;
  const organicPaise = canonical?.organic_baseline_paise ?? 0;
  const incrementalPaise = canonical?.incremental_recovered_paise ?? 0;
  const costPaise = canonical?.intervention_cost_paise ?? 0;
  const netPaise = canonical?.net_incremental_paise ?? 0;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-2 border-b border-slate-800">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
            Canonical Recovery Analytics
            <span className="px-2 py-0.5 text-[10px] uppercase font-mono tracking-wider rounded bg-indigo-950 text-indigo-400 border border-indigo-800/60">
              Phase 3.6 Accounting
            </span>
          </h1>
          <p className="text-xs text-slate-400 mt-1">
            Auditable economic accounting separating organic baseline recoveries from model-attributable net surplus
          </p>
        </div>

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

      {/* Disclaimers & Methodology Note */}
      <div className="p-4 rounded-xl border border-slate-800 bg-slate-900/40 text-xs text-slate-300 space-y-1">
        <div className="font-semibold text-slate-200 flex items-center gap-2">
          <span>Production Simulation Platform Notice</span>
          <span className="px-1.5 py-0.2 rounded text-[10px] bg-slate-800 text-slate-400 font-mono">
            BENCHMARK / SIMULATION
          </span>
        </div>
        <p className="text-slate-400 leading-relaxed">
          The metrics displayed below reflect observed transactions evaluated under production-hardened simulation environments.
          All currency values are recorded in strictly minor units (integer paise) with zero floating-point arithmetic.
          Negative incremental recovery is preserved without artificial zero-clamping when an intervention arm underperforms organic base rates.
        </p>
      </div>

      {/* Canonical Metric Tiles */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <div className="p-5 rounded-xl border border-slate-800 bg-slate-900/50">
          <div className="text-xs font-mono text-slate-400 uppercase">Gross Recovered Revenue</div>
          <div className="mt-2 text-2xl font-bold font-mono text-white">
            {formatPaise(grossPaise)} <span className="text-xs text-slate-400">INR</span>
          </div>
          <div className="text-[11px] text-slate-500 mt-1">
            Total recovered volume across terminal cases
          </div>
        </div>

        <div className="p-5 rounded-xl border border-slate-800 bg-slate-900/50">
          <div className="text-xs font-mono text-slate-400 uppercase">Organic Counterfactual Baseline</div>
          <div className="mt-2 text-2xl font-bold font-mono text-slate-300">
            {formatPaise(organicPaise)} <span className="text-xs text-slate-400">INR</span>
          </div>
          <div className="text-[11px] text-slate-500 mt-1">
            Formula: Σ ⌊ P0 × Amount ⌋ without intervention
          </div>
        </div>

        <div className="p-5 rounded-xl border border-slate-800 bg-slate-900/50">
          <div className="text-xs font-mono text-slate-400 uppercase">Incremental Recovery (Δ)</div>
          <div
            className={`mt-2 text-2xl font-bold font-mono ${
              incrementalPaise >= 0 ? "text-emerald-400" : "text-rose-400"
            }`}
          >
            {incrementalPaise >= 0 ? "+" : ""}
            {formatPaise(incrementalPaise)} <span className="text-xs text-slate-400">INR</span>
          </div>
          <div className="text-[11px] text-slate-500 mt-1">
            Gross Recovered minus Organic Baseline
          </div>
        </div>

        <div className="p-5 rounded-xl border border-slate-800 bg-slate-900/50">
          <div className="text-xs font-mono text-slate-400 uppercase">Intervention Execution Costs</div>
          <div className="mt-2 text-2xl font-bold font-mono text-slate-400">
            {formatPaise(costPaise)} <span className="text-xs text-slate-400">INR</span>
          </div>
          <div className="text-[11px] text-slate-500 mt-1">
            Direct operational friction and gateway fees
          </div>
        </div>

        <div className="p-5 rounded-xl border border-slate-800 bg-slate-900/50">
          <div className="text-xs font-mono text-slate-400 uppercase">Net Incremental Surplus</div>
          <div
            className={`mt-2 text-2xl font-bold font-mono ${
              netPaise >= 0 ? "text-indigo-400" : "text-rose-400"
            }`}
          >
            {netPaise >= 0 ? "+" : ""}
            {formatPaise(netPaise)} <span className="text-xs text-slate-400">INR</span>
          </div>
          <div className="text-[11px] text-slate-500 mt-1">
            Incremental Revenue minus Execution Costs
          </div>
        </div>

        <div className="p-5 rounded-xl border border-slate-800 bg-slate-900/50">
          <div className="text-xs font-mono text-slate-400 uppercase">Incremental Lift Rate</div>
          <div className="mt-2 text-2xl font-bold font-mono text-white">
            {((canonical?.incremental_recovery_rate ?? 0) * 100).toFixed(1)}%
          </div>
          <div className="text-[11px] text-emerald-400 mt-1">
            Recovery rate over unassisted organic conversion
          </div>
        </div>
      </div>

      {/* Cohort Breakdown Table */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5">
        <h2 className="text-sm font-semibold text-white mb-1">Cohort Statistics</h2>
        <p className="text-xs text-slate-400 mb-4">Volume summary for active accounting window</p>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 font-mono text-xs">
          <div className="p-3 rounded-lg bg-slate-950 border border-slate-800">
            <div className="text-slate-500 text-[10px]">TOTAL CASES</div>
            <div className="text-white text-base font-bold mt-1">{canonical?.total_cases ?? 0}</div>
          </div>

          <div className="p-3 rounded-lg bg-slate-950 border border-slate-800">
            <div className="text-slate-500 text-[10px]">TERMINAL CASES</div>
            <div className="text-white text-base font-bold mt-1">{canonical?.terminal_cases ?? 0}</div>
          </div>

          <div className="p-3 rounded-lg bg-slate-950 border border-slate-800">
            <div className="text-slate-500 text-[10px]">RECOVERED CASES</div>
            <div className="text-emerald-400 text-base font-bold mt-1">{canonical?.recovered_cases ?? 0}</div>
          </div>

          <div className="p-3 rounded-lg bg-slate-950 border border-slate-800">
            <div className="text-slate-500 text-[10px]">BASE RECOVERY RATE</div>
            <div className="text-white text-base font-bold mt-1">
              {((canonical?.recovery_rate ?? 0) * 100).toFixed(1)}%
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
