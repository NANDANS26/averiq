import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { apiClient } from "@/api/client";

interface DemoScenario {
  id: string;
  code: string;
  name: string;
  description: string;
  expected_action: string;
  expected_final_state: string;
  category: string;
}

interface DemoRunResult {
  scenario_id: string;
  case_id: string;
  expected_final_state: string;
  message: string;
}

export default function DemoCenter() {
  const queryClient = useQueryClient();
  const [runningId, setRunningId] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<DemoRunResult | null>(null);

  // Fetch scenarios list
  const { data: scenarios } = useQuery<DemoScenario[]>({
    queryKey: ["demo-scenarios"],
    queryFn: () => apiClient.get("/api/v1/demo/scenarios").then((res) => res.data),
  });

  // Fetch recent demo cases
  const { data: demoCases, refetch: refetchDemoCases } = useQuery<any[]>({
    queryKey: ["demo-cases"],
    queryFn: () => apiClient.get("/api/v1/demo/cases").then((res) => res.data),
    refetchInterval: 5_000,
  });

  // Run scenario mutation
  const runMutation = useMutation({
    mutationFn: (scenarioId: string) =>
      apiClient.post(`/api/v1/demo/run/${scenarioId}`).then((res) => res.data),
    onSuccess: (data: DemoRunResult) => {
      setLastResult(data);
      setRunningId(null);
      refetchDemoCases();
      queryClient.invalidateQueries({ queryKey: ["risk-queue-cases"] });
      queryClient.invalidateQueries({ queryKey: ["overview-recent-cases"] });
      queryClient.invalidateQueries({ queryKey: ["canonical-analytics"] });
    },
    onError: () => {
      setRunningId(null);
    },
  });

  const handleRun = (id: string) => {
    setRunningId(id);
    runMutation.mutate(id);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-2 border-b border-slate-800">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
            Executive Demo Center
            <span className="px-2 py-0.5 text-[10px] uppercase font-mono tracking-wider rounded bg-violet-950 text-violet-300 border border-violet-800/60">
              Interactive Testbed
            </span>
          </h1>
          <p className="text-xs text-slate-400 mt-1">
            Trigger deterministic end-to-end scenarios to inspect state machine transitions, policy enforcement, and fallback behavior
          </p>
        </div>

        <div className="flex items-center gap-2 text-xs font-mono text-slate-400 bg-slate-900 border border-slate-800 px-3 py-1.5 rounded-lg">
          <span className="w-2 h-2 rounded-full bg-violet-400" />
          <span>Demo Mode Active</span>
        </div>
      </div>

      {/* Last Result Callout Banner */}
      {lastResult && (
        <div className="p-4 rounded-xl border border-emerald-800/60 bg-emerald-950/20 text-xs flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <span className="font-semibold text-emerald-300 font-mono">
              Scenario Executed Successfully:
            </span>
            <span className="text-slate-300 ml-2">
              Case <span className="font-mono text-white font-semibold">{lastResult.case_id}</span> reached expected state{" "}
              <span className="font-mono text-emerald-400 font-bold">{lastResult.expected_final_state}</span>.
            </span>
          </div>

          <Link
            to={`/cases/${lastResult.case_id}`}
            className="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-medium text-xs whitespace-nowrap transition-colors"
          >
            Inspect Case Decision Trace →
          </Link>
        </div>
      )}

      {/* Scenarios Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {(scenarios ?? []).map((sc) => (
          <div
            key={sc.id}
            className="rounded-xl border border-slate-800 bg-slate-900/50 p-5 flex flex-col justify-between hover:border-slate-700 transition-colors"
          >
            <div>
              <div className="flex items-center justify-between">
                <span className="px-2 py-0.5 rounded text-[10px] font-mono uppercase bg-slate-800 text-slate-300">
                  {sc.code} • {sc.category}
                </span>
                <span className="text-[11px] font-mono text-indigo-400">
                  Target: {sc.expected_final_state}
                </span>
              </div>

              <h2 className="text-base font-semibold text-white mt-2.5">{sc.name}</h2>
              <p className="text-xs text-slate-400 mt-1 leading-relaxed">{sc.description}</p>
            </div>

            <div className="mt-5 pt-3 border-t border-slate-800/80 flex items-center justify-between">
              <span className="text-[11px] font-mono text-slate-500">
                Action: <span className="text-slate-300">{sc.expected_action}</span>
              </span>

              <button
                disabled={runningId !== null}
                onClick={() => handleRun(sc.id)}
                className="px-3.5 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-xs font-medium font-sans transition-colors flex items-center gap-2"
              >
                {runningId === sc.id ? (
                  <>
                    <span className="w-3 h-3 border-2 border-white border-t-transparent rounded-full animate-spin" />
                    Executing...
                  </>
                ) : (
                  "Execute Scenario"
                )}
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* Recent Demo Executions Table */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/50 overflow-hidden">
        <div className="px-5 py-3.5 border-b border-slate-800 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-white">Recent Demo Executions</h2>
          <span className="text-xs font-mono text-slate-500">is_demo_fixture = True</span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs font-mono">
            <thead>
              <tr className="border-b border-slate-800 bg-slate-950/40 text-slate-400">
                <th className="py-3 px-4">Case ID</th>
                <th className="py-3 px-4">Final State</th>
                <th className="py-3 px-4">Amount</th>
                <th className="py-3 px-4">Mode</th>
                <th className="py-3 px-4 text-right">Inspect</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {(demoCases ?? []).map((c) => (
                <tr key={c.id} className="hover:bg-slate-800/30">
                  <td className="py-2.5 px-4 text-indigo-400 font-semibold">
                    <Link to={`/cases/${c.id}`} className="hover:underline">
                      {c.id.slice(0, 8)}...
                    </Link>
                  </td>
                  <td className="py-2.5 px-4 text-slate-200">{c.state}</td>
                  <td className="py-2.5 px-4 text-slate-300">
                    {c.amount} {c.currency}
                  </td>
                  <td className="py-2.5 px-4 text-slate-400 text-[11px]">{c.operating_mode}</td>
                  <td className="py-2.5 px-4 text-right">
                    <Link
                      to={`/cases/${c.id}`}
                      className="px-2 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-sans transition-colors"
                    >
                      View Trace
                    </Link>
                  </td>
                </tr>
              ))}

              {(!demoCases || demoCases.length === 0) && (
                <tr>
                  <td colSpan={5} className="py-8 text-center text-slate-500 font-sans">
                    No demo fixtures recorded in session. Click 'Execute Scenario' above to run.
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
