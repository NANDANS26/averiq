import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { apiClient } from "@/api/client";
import { formatPaise } from "@/lib/utils";
import type { RevenueRiskCase } from "@/types";

export default function RevenueRiskQueue() {
  const [searchTerm, setSearchTerm] = useState("");
  const [stateFilter, setStateFilter] = useState<string>("ALL");
  const [tierFilter, setTierFilter] = useState<string>("ALL");

  const { data, isLoading } = useQuery<{ items: RevenueRiskCase[] }>({
    queryKey: ["risk-queue-cases"],
    queryFn: () => apiClient.get("/api/v1/cases?limit=25").then((res) => res.data),
    refetchInterval: 10_000,
  });

  const rawCases = data?.items ?? [];

  // Filter cases client-side for fast operator responsiveness
  const filteredCases = rawCases.filter((c) => {
    const matchesSearch =
      searchTerm.trim() === "" ||
      c.id.toLowerCase().includes(searchTerm.toLowerCase()) ||
      (c.payment_id && c.payment_id.toLowerCase().includes(searchTerm.toLowerCase()));

    const matchesState = stateFilter === "ALL" || c.state === stateFilter;
    const matchesTier =
      tierFilter === "ALL" ||
      (c.recovery_risk_tier ?? c.exposure_tier) === tierFilter;

    return matchesSearch && matchesState && matchesTier;
  });

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-2 border-b border-slate-800">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
            Revenue Risk Queue
            <span className="px-2 py-0.5 text-[10px] uppercase font-mono tracking-wider rounded bg-slate-800 text-slate-300">
              {filteredCases.length} Cases
            </span>
          </h1>
          <p className="text-xs text-slate-400 mt-1">
            Prioritized operational queue of failed payments awaiting diagnosis, approval, or settlement
          </p>
        </div>

        <div className="flex items-center gap-2">
          <Link
            to="/demo"
            className="px-3 py-1.5 rounded-lg border border-indigo-700 bg-indigo-600/20 hover:bg-indigo-600/30 text-indigo-300 text-xs font-medium transition-colors"
          >
            + Seed Demo Scenario
          </Link>
        </div>
      </div>

      {/* Filter & Search Bar */}
      <div className="grid grid-cols-1 sm:grid-cols-3 md:grid-cols-4 gap-3 p-3.5 rounded-xl border border-slate-800 bg-slate-900/60 text-xs">
        {/* Search */}
        <div className="col-span-1 sm:col-span-2">
          <input
            type="text"
            placeholder="Search by Case ID or Payment Reference..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="w-full px-3 py-1.5 rounded-lg bg-slate-950 border border-slate-800 text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500 text-xs font-mono"
          />
        </div>

        {/* State Filter */}
        <div>
          <select
            value={stateFilter}
            onChange={(e) => setStateFilter(e.target.value)}
            className="w-full px-3 py-1.5 rounded-lg bg-slate-950 border border-slate-800 text-white focus:outline-none focus:border-indigo-500 text-xs"
          >
            <option value="ALL">All States</option>
            <option value="DETECTED">DETECTED</option>
            <option value="EVALUATED">EVALUATED</option>
            <option value="AWAITING_APPROVAL">AWAITING_APPROVAL</option>
            <option value="ACTION_APPROVED">ACTION_APPROVED</option>
            <option value="EXECUTING">EXECUTING</option>
            <option value="VERIFYING">VERIFYING</option>
            <option value="RECOVERED">RECOVERED</option>
            <option value="STOPPED">STOPPED</option>
            <option value="FAILED">FAILED</option>
            <option value="ESCALATED">ESCALATED</option>
          </select>
        </div>

        {/* Exposure Tier Filter */}
        <div>
          <select
            value={tierFilter}
            onChange={(e) => setTierFilter(e.target.value)}
            className="w-full px-3 py-1.5 rounded-lg bg-slate-950 border border-slate-800 text-white focus:outline-none focus:border-indigo-500 text-xs"
          >
            <option value="ALL">All Risk Tiers</option>
            <option value="HIGH">High Exposure (₹10k+)</option>
            <option value="MEDIUM">Medium Exposure</option>
            <option value="LOW">Low Exposure</option>
          </select>
        </div>
      </div>

      {/* Main Table */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/50 overflow-hidden shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-slate-800 bg-slate-950/40 text-slate-400 font-medium">
                <th className="py-3 px-4 font-normal">Case Identifier</th>
                <th className="py-3 px-4 font-normal">Amount at Risk</th>
                <th className="py-3 px-4 font-normal">Failure Category</th>
                <th className="py-3 px-4 font-normal">Recovery Prob (P)</th>
                <th className="py-3 px-4 font-normal">State</th>
                <th className="py-3 px-4 font-normal">Risk Tier</th>
                <th className="py-3 px-4 font-normal text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60 font-mono">
              {filteredCases.map((c) => {
                const isHighValue = c.payment_amount_at_risk >= 1_000_000; // >= 10,000 INR
                return (
                  <tr
                    key={c.id}
                    className={`hover:bg-slate-800/40 transition-colors ${
                      isHighValue ? "bg-amber-950/10" : ""
                    }`}
                  >
                    <td className="py-3 px-4">
                      <div className="flex flex-col">
                        <Link
                          to={`/cases/${c.id}`}
                          className="font-semibold text-indigo-400 hover:text-indigo-300 underline"
                        >
                          {c.id.slice(0, 8)}...
                        </Link>
                        {c.payment_id && (
                          <span className="text-[10px] text-slate-500">{c.payment_id}</span>
                        )}
                      </div>
                    </td>

                    <td className="py-3 px-4 text-slate-100 font-semibold">
                      {formatPaise(c.payment_amount_at_risk)}{" "}
                      <span className="text-[10px] text-slate-400">{c.currency}</span>
                    </td>

                    <td className="py-3 px-4 text-slate-300">
                      <span className="font-sans">{c.failure_category ?? "Unclassified"}</span>
                    </td>

                    <td className="py-3 px-4">
                      {c.recovery_probability != null ? (
                        <div className="flex items-center gap-1.5 font-sans">
                          <span className="font-mono text-slate-200">
                            {(c.recovery_probability * 100).toFixed(1)}%
                          </span>
                        </div>
                      ) : (
                        <span className="text-slate-500">—</span>
                      )}
                    </td>

                    <td className="py-3 px-4">
                      <span
                        className={`inline-block px-2 py-0.5 rounded text-[10px] font-mono font-medium ${
                          c.state === "RECOVERED"
                            ? "bg-emerald-950 text-emerald-400 border border-emerald-800/60"
                            : c.state === "STOPPED"
                              ? "bg-slate-800 text-slate-400"
                              : c.state === "AWAITING_APPROVAL"
                                ? "bg-amber-950 text-amber-400 border border-amber-800/60"
                                : c.state === "FAILED" || c.state === "ESCALATED"
                                  ? "bg-rose-950 text-rose-400 border border-rose-800/60"
                                  : "bg-indigo-950 text-indigo-400 border border-indigo-800/60"
                        }`}
                      >
                        {c.state}
                      </span>
                    </td>

                    <td className="py-3 px-4">
                      <span
                        className={`px-2 py-0.5 rounded text-[10px] uppercase ${
                          c.recovery_risk_tier === "HIGH" || c.exposure_tier === "HIGH"
                            ? "bg-rose-950 text-rose-400 border border-rose-800/50"
                            : "bg-slate-800 text-slate-400"
                        }`}
                      >
                        {c.recovery_risk_tier ?? c.exposure_tier ?? "STANDARD"}
                      </span>
                    </td>

                    <td className="py-3 px-4 text-right">
                      <Link
                        to={`/cases/${c.id}`}
                        className="px-2.5 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-sans transition-colors"
                      >
                        Inspect Trace
                      </Link>
                    </td>
                  </tr>
                );
              })}

              {filteredCases.length === 0 && !isLoading && (
                <tr>
                  <td colSpan={7} className="py-12 text-center text-slate-500 font-sans">
                    No matching cases found in queue.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
