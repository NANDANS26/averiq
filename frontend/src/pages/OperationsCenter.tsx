import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { apiClient } from "@/api/client";
import { formatPaise, formatDateTime } from "@/lib/utils";
import type { RevenueRiskCase } from "@/types";

export default function OperationsCenter() {
  const [activeFilter, setActiveFilter] = useState<"ALL" | "APPROVALS" | "EXECUTING" | "VERIFYING" | "ESCALATED">("ALL");

  // Fetch all active or interesting operational cases
  const { data: casesData, isLoading, refetch } = useQuery<{ items: RevenueRiskCase[] }>({
    queryKey: ["operations-cases"],
    queryFn: () => apiClient.get("/cases?limit=50").then((res) => res.data),
    refetchInterval: 10_000,
  });

  const allCases = casesData?.items ?? [];

  // Groupings
  const pendingApprovals = allCases.filter((c) => c.state === "AWAITING_APPROVAL");
  const executingOps = allCases.filter((c) => c.state === "EXECUTING" || c.state === "ACTION_APPROVED");
  const verifyingOps = allCases.filter((c) => c.state === "VERIFYING");
  const escalatedOps = allCases.filter((c) => c.state === "ESCALATED" || c.state === "FAILED");

  const filteredCases = allCases.filter((c) => {
    if (activeFilter === "APPROVALS") return c.state === "AWAITING_APPROVAL";
    if (activeFilter === "EXECUTING") return c.state === "EXECUTING" || c.state === "ACTION_APPROVED";
    if (activeFilter === "VERIFYING") return c.state === "VERIFYING";
    if (activeFilter === "ESCALATED") return c.state === "ESCALATED" || c.state === "FAILED";
    return true;
  });

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-2 border-b border-slate-800">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
            Intervention Operations Center
            <span className="px-2 py-0.5 text-[10px] uppercase font-mono tracking-wider rounded bg-indigo-950 text-indigo-400 border border-indigo-800/60">
              Orchestrator
            </span>
          </h1>
          <p className="text-xs text-slate-400 mt-1">
            Real-time control plane for human approvals, outbox dispatching, and settlement verification
          </p>
        </div>

        <button
          onClick={() => refetch()}
          className="px-3 py-1.5 rounded-lg border border-slate-800 bg-slate-900 hover:bg-slate-800 text-xs font-mono text-slate-300 transition-colors"
        >
          Refresh Queues
        </button>
      </div>

      {/* Operational Status Tiles */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <button
          onClick={() => setActiveFilter("APPROVALS")}
          className={`p-4 rounded-xl border text-left transition-all ${
            activeFilter === "APPROVALS"
              ? "border-amber-500 bg-amber-950/30"
              : "border-slate-800 bg-slate-900/50 hover:border-slate-700"
          }`}
        >
          <div className="text-[11px] uppercase font-mono text-slate-400">Pending Approvals</div>
          <div className="mt-1 text-2xl font-bold font-mono text-amber-400">{pendingApprovals.length}</div>
          <div className="text-[11px] text-slate-500 mt-0.5">High exposure cases</div>
        </button>

        <button
          onClick={() => setActiveFilter("EXECUTING")}
          className={`p-4 rounded-xl border text-left transition-all ${
            activeFilter === "EXECUTING"
              ? "border-indigo-500 bg-indigo-950/30"
              : "border-slate-800 bg-slate-900/50 hover:border-slate-700"
          }`}
        >
          <div className="text-[11px] uppercase font-mono text-slate-400">Active Executions</div>
          <div className="mt-1 text-2xl font-bold font-mono text-indigo-400">{executingOps.length}</div>
          <div className="text-[11px] text-slate-500 mt-0.5">Outbox dispatched</div>
        </button>

        <button
          onClick={() => setActiveFilter("VERIFYING")}
          className={`p-4 rounded-xl border text-left transition-all ${
            activeFilter === "VERIFYING"
              ? "border-sky-500 bg-sky-950/30"
              : "border-slate-800 bg-slate-900/50 hover:border-slate-700"
          }`}
        >
          <div className="text-[11px] uppercase font-mono text-slate-400">Verifying Settlement</div>
          <div className="mt-1 text-2xl font-bold font-mono text-sky-400">{verifyingOps.length}</div>
          <div className="text-[11px] text-slate-500 mt-0.5">Awaiting gateway proof</div>
        </button>

        <button
          onClick={() => setActiveFilter("ESCALATED")}
          className={`p-4 rounded-xl border text-left transition-all ${
            activeFilter === "ESCALATED"
              ? "border-rose-500 bg-rose-950/30"
              : "border-slate-800 bg-slate-900/50 hover:border-slate-700"
          }`}
        >
          <div className="text-[11px] uppercase font-mono text-slate-400">Escalations & Failures</div>
          <div className="mt-1 text-2xl font-bold font-mono text-rose-400">{escalatedOps.length}</div>
          <div className="text-[11px] text-slate-500 mt-0.5">Requires intervention</div>
        </button>
      </div>

      {/* Main Operations List */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/50 overflow-hidden">
        <div className="px-5 py-3.5 border-b border-slate-800 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-white">
            Operational Cases ({filteredCases.length})
          </h2>
          {activeFilter !== "ALL" && (
            <button
              onClick={() => setActiveFilter("ALL")}
              className="text-xs text-indigo-400 hover:underline"
            >
              Clear Filter
            </button>
          )}
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs font-mono">
            <thead>
              <tr className="border-b border-slate-800 bg-slate-950/40 text-slate-400 font-normal">
                <th className="py-3 px-4">Case Identifier</th>
                <th className="py-3 px-4">Current State</th>
                <th className="py-3 px-4">Amount at Risk</th>
                <th className="py-3 px-4">Failure Category</th>
                <th className="py-3 px-4">Created Time</th>
                <th className="py-3 px-4 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {filteredCases.map((c) => (
                <tr key={c.id} className="hover:bg-slate-800/40 transition-colors">
                  <td className="py-3 px-4">
                    <Link to={`/cases/${c.id}`} className="text-indigo-400 hover:underline font-semibold">
                      {c.id.slice(0, 8)}...
                    </Link>
                  </td>

                  <td className="py-3 px-4">
                    <span
                      className={`px-2 py-0.5 rounded text-[10px] font-mono ${
                        c.state === "AWAITING_APPROVAL"
                          ? "bg-amber-950 text-amber-400 border border-amber-800/60"
                          : c.state === "VERIFYING"
                            ? "bg-sky-950 text-sky-400 border border-sky-800/60"
                            : c.state === "ESCALATED" || c.state === "FAILED"
                              ? "bg-rose-950 text-rose-400 border border-rose-800/60"
                              : "bg-indigo-950 text-indigo-400 border border-indigo-800/60"
                      }`}
                    >
                      {c.state}
                    </span>
                  </td>

                  <td className="py-3 px-4 text-slate-100 font-semibold">
                    {formatPaise(c.payment_amount_at_risk)} {c.currency}
                  </td>

                  <td className="py-3 px-4 text-slate-300 font-sans">
                    {c.failure_category ?? "Unclassified"}
                  </td>

                  <td className="py-3 px-4 text-slate-500 font-sans">
                    {formatDateTime(c.created_at)}
                  </td>

                  <td className="py-3 px-4 text-right">
                    <Link
                      to={`/cases/${c.id}`}
                      className="px-2.5 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-sans transition-colors"
                    >
                      Inspect
                    </Link>
                  </td>
                </tr>
              ))}

              {filteredCases.length === 0 && !isLoading && (
                <tr>
                  <td colSpan={6} className="py-12 text-center text-slate-500 font-sans">
                    No operational cases in this category.
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
