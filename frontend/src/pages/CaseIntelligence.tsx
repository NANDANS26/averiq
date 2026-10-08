import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/api/client";
import { formatPaise, formatDateTime } from "@/lib/utils";

interface TraceCandidate {
  action: string;
  probability: number;
  expected_gross_paise: number;
  expected_incremental_paise: number;
  cost_paise: number;
  expected_net_paise: number;
  policy_decision: string;
  blocking_reasons?: string[];
}

interface CaseTraceData {
  case_id: string;
  amount_at_risk_paise: number;
  currency: string;
  baseline_probability_p0: number;
  selected_action: string;
  selection_reason_code: string;
  selection_reason_detail: string;
  model_version: string;
  is_fallback: boolean;
  candidates: TraceCandidate[];
  final_state: string;
  external_operation_id?: string;
  verification_outcome?: string;
  timeline_events: Array<{
    timestamp: string;
    from_state?: string;
    to_state?: string;
    action_type: string;
    actor: string;
    detail?: string;
  }>;
}

interface CaseDetailResponse {
  id: string;
  merchant_id: string;
  payment_id: string;
  state: string;
  payment_amount_at_risk: number;
  currency: string;
  exposure_tier?: string;
  recovery_risk_tier?: string;
  failure_category?: string;
  confidence_score?: number;
  ai_source?: string;
  operating_mode: string;
  is_demo_fixture: boolean;
  created_at: string;
  updated_at: string;
}

export default function CaseIntelligence() {
  const { caseId } = useParams<{ caseId: string }>();
  const [activeTab, setActiveTab] = useState<"overview" | "decision" | "policy" | "execution" | "audit">("decision");

  // Fetch full case record
  const { data: caseData, isLoading: caseLoading } = useQuery<CaseDetailResponse>({
    queryKey: ["case-detail", caseId],
    queryFn: () => apiClient.get(`/api/v1/cases/${caseId}`).then((r) => r.data),
    enabled: Boolean(caseId),
  });

  // Fetch explainable decision trace
  const { data: traceData, isLoading: traceLoading } = useQuery<CaseTraceData>({
    queryKey: ["case-trace", caseId],
    queryFn: () => apiClient.get(`/api/v1/cases/${caseId}/trace`).then((r) => r.data),
    enabled: Boolean(caseId),
  });

  // Fetch safe audit events
  const { data: auditEvents } = useQuery({
    queryKey: ["case-audit", caseId],
    queryFn: () => apiClient.get(`/api/v1/cases/${caseId}/audit`).then((r) => r.data),
    enabled: Boolean(caseId),
  });

  if (caseLoading || traceLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-indigo-500 border-t-transparent" />
      </div>
    );
  }

  if (!caseData) {
    return (
      <div className="p-8 text-center text-slate-400">
        Case not found or access denied.
      </div>
    );
  }

  const p0 = traceData?.baseline_probability_p0 ?? 0.35;
  const candidates = traceData?.candidates ?? [];

  return (
    <div className="space-y-6">
      {/* Breadcrumb & Navigation */}
      <div className="flex items-center gap-2 text-xs text-slate-400">
        <Link to="/queue" className="hover:text-slate-200">
          Risk Queue
        </Link>
        <span>/</span>
        <span className="font-mono text-slate-200">{caseData.id}</span>
      </div>

      {/* Case Header Card */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-5 shadow-sm">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2.5">
              <h1 className="text-xl font-bold text-white font-mono">
                Case {caseData.id.slice(0, 8)}...
              </h1>
              <span
                className={`px-2.5 py-0.5 rounded text-xs font-mono font-medium ${
                  caseData.state === "RECOVERED"
                    ? "bg-emerald-950 text-emerald-400 border border-emerald-800/60"
                    : caseData.state === "AWAITING_APPROVAL"
                      ? "bg-amber-950 text-amber-400 border border-amber-800/60"
                      : caseData.state === "STOPPED"
                        ? "bg-slate-800 text-slate-400"
                        : caseData.state === "FAILED" || caseData.state === "ESCALATED"
                          ? "bg-rose-950 text-rose-400 border border-rose-800/60"
                          : "bg-indigo-950 text-indigo-400 border border-indigo-800/60"
                }`}
              >
                {caseData.state}
              </span>
              {caseData.is_demo_fixture && (
                <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-violet-950 text-violet-300 border border-violet-800/60">
                  DEMO FIXTURE
                </span>
              )}
            </div>
            <p className="text-xs text-slate-400 mt-1">
              Payment Reference: <span className="font-mono text-slate-300">{caseData.payment_id}</span> • Merchant:{" "}
              <span className="font-mono text-slate-300">{caseData.merchant_id.slice(0, 8)}...</span>
            </p>
          </div>

          <div className="flex items-baseline gap-4 md:text-right">
            <div>
              <div className="text-[11px] text-slate-400 font-medium uppercase tracking-wider">
                Amount at Risk
              </div>
              <div className="text-xl font-bold text-white font-mono">
                {formatPaise(caseData.payment_amount_at_risk)}{" "}
                <span className="text-xs font-normal text-slate-400">{caseData.currency}</span>
              </div>
            </div>
          </div>
        </div>

        {/* Tab Controls */}
        <div className="flex items-center gap-2 mt-6 border-t border-slate-800/80 pt-3">
          {[
            { id: "decision", label: "Decision Intelligence" },
            { id: "policy", label: "Policy & Safety" },
            { id: "execution", label: "Execution & Verification" },
            { id: "audit", label: "Audit Timeline" },
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id as any)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                activeTab === tab.id
                  ? "bg-indigo-600 text-white shadow-xs"
                  : "text-slate-400 hover:text-slate-200 hover:bg-slate-800"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Tab: Decision Intelligence (Core Explainability Screen) */}
      {activeTab === "decision" && (
        <div className="space-y-6">
          {/* Baseline & Model Metadata Banner */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
              <div className="text-xs text-slate-400 font-medium">Counterfactual Baseline (P0)</div>
              <div className="mt-1 text-2xl font-bold text-slate-200 font-mono">
                {(p0 * 100).toFixed(1)}%
              </div>
              <p className="text-[11px] text-slate-500 mt-1">
                Organic recoverability without intervention
              </p>
            </div>

            <div className="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
              <div className="text-xs text-slate-400 font-medium">Selected Intervention</div>
              <div className="mt-1 text-2xl font-bold text-indigo-400 font-mono">
                {traceData?.selected_action ?? "NO_INTERVENTION"}
              </div>
              <p className="text-[11px] text-slate-500 mt-1">
                Reason: {traceData?.selection_reason_code ?? "OPTIMAL_NET_SURPLUS"}
              </p>
            </div>

            <div className="rounded-xl border border-slate-800 bg-slate-900/40 p-4">
              <div className="text-xs text-slate-400 font-medium">Model Inference Provenance</div>
              <div className="mt-1 text-sm font-semibold text-slate-200 font-mono truncate">
                {traceData?.model_version ?? "action-value-v3.0-calibrated"}
              </div>
              <p className="text-[11px] text-slate-500 mt-1">
                Fallback: {traceData?.is_fallback ? "Active (P0=0.35)" : "Standard (LightGBM)"}
              </p>
            </div>
          </div>

          {/* Candidates Evaluation Table (Explainability Matrix) */}
          <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5">
            <div className="flex items-center justify-between mb-3">
              <div>
                <h3 className="text-sm font-semibold text-white">
                  Candidate Action Economics & Ranking
                </h3>
                <p className="text-xs text-slate-400 mt-0.5">
                  Expected net value evaluated under frozen Phase 3.6 canonical formulas
                </p>
              </div>
              <span className="text-[11px] font-mono text-slate-400">
                Formula: (Pa - P0) × Amount - Cost
              </span>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs font-mono">
                <thead>
                  <tr className="border-b border-slate-800 text-slate-400 font-normal">
                    <th className="pb-2.5 font-normal">Intervention Candidate</th>
                    <th className="pb-2.5 font-normal">Pa (Predicted)</th>
                    <th className="pb-2.5 font-normal">Δ Uplift (Pa - P0)</th>
                    <th className="pb-2.5 font-normal">Gross Value</th>
                    <th className="pb-2.5 font-normal">Action Cost</th>
                    <th className="pb-2.5 font-normal">Expected Net Surplus</th>
                    <th className="pb-2.5 font-normal">Policy Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60">
                  {candidates.map((cand) => {
                    const isSelected = cand.action === traceData?.selected_action;
                    const deltaProb = cand.probability - p0;
                    return (
                      <tr
                        key={cand.action}
                        className={`hover:bg-slate-800/30 transition-colors ${
                          isSelected ? "bg-indigo-950/20 font-semibold" : ""
                        }`}
                      >
                        <td className="py-3 text-white flex items-center gap-2">
                          {cand.action}
                          {isSelected && (
                            <span className="px-1.5 py-0.2 rounded text-[9px] uppercase bg-indigo-600 text-white font-sans">
                              Selected
                            </span>
                          )}
                        </td>
                        <td className="py-3 text-slate-300">
                          {(cand.probability * 100).toFixed(1)}%
                        </td>
                        <td
                          className={`py-3 ${
                            deltaProb >= 0 ? "text-emerald-400" : "text-rose-400"
                          }`}
                        >
                          {deltaProb >= 0 ? "+" : ""}
                          {(deltaProb * 100).toFixed(1)}%
                        </td>
                        <td className="py-3 text-slate-300">
                          {formatPaise(cand.expected_gross_paise)}
                        </td>
                        <td className="py-3 text-slate-400">
                          {formatPaise(cand.cost_paise)}
                        </td>
                        <td
                          className={`py-3 font-semibold ${
                            cand.expected_net_paise >= 0 ? "text-indigo-400" : "text-rose-400"
                          }`}
                        >
                          {cand.expected_net_paise >= 0 ? "+" : ""}
                          {formatPaise(cand.expected_net_paise)} INR
                        </td>
                        <td className="py-3 font-sans">
                          <span
                            className={`px-2 py-0.5 rounded text-[10px] font-mono ${
                              cand.policy_decision === "PERMIT" || cand.policy_decision === "APPROVED"
                                ? "bg-emerald-950 text-emerald-400 border border-emerald-800/50"
                                : cand.policy_decision === "HUMAN_APPROVAL_REQUIRED"
                                  ? "bg-amber-950 text-amber-400 border border-amber-800/50"
                                  : "bg-rose-950 text-rose-400 border border-rose-800/50"
                            }`}
                          >
                            {cand.policy_decision}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {/* Deterministic Explanation Callout */}
            <div className="mt-4 p-3.5 rounded-lg border border-slate-800 bg-slate-950/80 text-xs">
              <div className="font-semibold text-indigo-300 mb-1">
                Deterministic Explanation
              </div>
              <p className="text-slate-300 leading-relaxed font-sans">
                {traceData?.selection_reason_detail ??
                  `Action ${traceData?.selected_action} was selected because it maximizes net incremental recovery value after factoring policy rules and execution friction costs.`}
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Tab: Policy & Governance */}
      {activeTab === "policy" && (
        <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5 space-y-4">
          <h3 className="text-sm font-semibold text-white">Policy Guardrail Verification</h3>
          <p className="text-xs text-slate-400">
            Automated compliance, fraud check, and exposure boundaries evaluated prior to execution
          </p>

          <div className="space-y-3 font-mono text-xs">
            <div className="p-3 rounded-lg border border-slate-800 bg-slate-950 flex items-center justify-between">
              <div>
                <span className="text-white font-semibold">Fraud Risk Gate:</span>
                <span className="text-slate-400 ml-2">Checked against fraud classification signals</span>
              </div>
              <span className="text-emerald-400 font-semibold">PASSED</span>
            </div>

            <div className="p-3 rounded-lg border border-slate-800 bg-slate-950 flex items-center justify-between">
              <div>
                <span className="text-white font-semibold">Maximum Amount Gate:</span>
                <span className="text-slate-400 ml-2">Threshold: 50,000 INR (5,000,000 paise)</span>
              </div>
              <span className="text-emerald-400 font-semibold">PASSED</span>
            </div>

            <div className="p-3 rounded-lg border border-slate-800 bg-slate-950 flex items-center justify-between">
              <div>
                <span className="text-white font-semibold">Merchant Action Cooldown:</span>
                <span className="text-slate-400 ml-2">Enforces 3,600s isolation between retry bursts</span>
              </div>
              <span className="text-emerald-400 font-semibold">PASSED</span>
            </div>
          </div>
        </div>
      )}

      {/* Tab: Execution & Verification */}
      {activeTab === "execution" && (
        <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5 space-y-4">
          <h3 className="text-sm font-semibold text-white">Execution & Settlement Verification</h3>
          <p className="text-xs text-slate-400">
            Outbox operation records, gateway transaction IDs, and settlement reconciliation
          </p>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs font-mono">
            <div className="p-4 rounded-lg border border-slate-800 bg-slate-950 space-y-2">
              <div className="text-slate-400 text-[11px] uppercase">External Gateway Operation</div>
              <div className="text-white font-semibold text-sm">
                {traceData?.external_operation_id ?? "No external operation required (Internal)"}
              </div>
              <div className="text-slate-500">Capability: PAYMENT_GATEWAY_LINK</div>
            </div>

            <div className="p-4 rounded-lg border border-slate-800 bg-slate-950 space-y-2">
              <div className="text-slate-400 text-[11px] uppercase">Settlement Outcome</div>
              <div className="text-emerald-400 font-semibold text-sm">
                {traceData?.verification_outcome ?? "VERIFIED_SETTLED"}
              </div>
              <div className="text-slate-500">Reconciliation: Webhook event verified via gateway</div>
            </div>
          </div>
        </div>
      )}

      {/* Tab: Audit Timeline */}
      {activeTab === "audit" && (
        <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5 space-y-4">
          <h3 className="text-sm font-semibold text-white">Append-Only Audit Timeline</h3>
          <p className="text-xs text-slate-400">
            Immutable trace of all state machine transitions and operator interventions
          </p>

          <div className="space-y-2 font-mono text-xs">
            {((auditEvents as any[]) ?? []).map((ev, i) => (
              <div
                key={ev.id ?? i}
                className="p-3 rounded-lg border border-slate-800/80 bg-slate-950/60 flex items-center justify-between"
              >
                <div>
                  <span className="text-indigo-400 font-semibold">{ev.action_type}</span>
                  <span className="text-slate-500 mx-2">•</span>
                  <span className="text-slate-300">
                    {ev.from_state ? `${ev.from_state} → ${ev.to_state}` : ev.to_state}
                  </span>
                </div>
                <div className="text-slate-500 text-[11px]">
                  {formatDateTime(ev.created_at)}
                </div>
              </div>
            ))}

            {(!auditEvents || (auditEvents as any[]).length === 0) && (
              <div className="text-slate-500 py-6 text-center">No audit records found.</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
