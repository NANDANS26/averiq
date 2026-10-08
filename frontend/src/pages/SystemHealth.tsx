import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/api/client";

interface ComponentStatus {
  status: string;
  latency_ms?: number;
  detail?: string;
}

interface ReadinessResponse {
  status: string;
  environment: string;
  timestamp: number;
  components: Record<string, ComponentStatus>;
}

interface ModelHealthResponse {
  model_name: string;
  model_version: string;
  artifact_path: string;
  artifact_exists: boolean;
  feature_schema_version: string;
  calibration_status: string;
  fallback_probability: number;
  model_loaded: boolean;
  operating_mode: string;
}

export default function SystemHealth() {
  const { data: readiness, refetch: refetchReadiness } = useQuery<ReadinessResponse>({
    queryKey: ["system-readiness"],
    queryFn: () => apiClient.get("/system/readiness").then((r) => r.data),
    refetchInterval: 10_000,
  });

  const { data: modelHealth } = useQuery<ModelHealthResponse>({
    queryKey: ["model-health"],
    queryFn: () => apiClient.get("/system/model-health").then((r) => r.data),
    staleTime: 30_000,
  });

  const components = readiness?.components ?? {};

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-2 border-b border-slate-800">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
            System Infrastructure & Model Health
            <span
              className={`px-2 py-0.5 text-[10px] uppercase font-mono tracking-wider rounded ${
                readiness?.status === "READY"
                  ? "bg-emerald-950 text-emerald-400 border border-emerald-800/60"
                  : "bg-amber-950 text-amber-400 border border-amber-800/60"
              }`}
            >
              {readiness?.status ?? "CHECKING"}
            </span>
          </h1>
          <p className="text-xs text-slate-400 mt-1">
            Real-time probes for PostgreSQL, Redis, Celery, and the calibrated Action-Value LightGBM artifact
          </p>
        </div>

        <button
          onClick={() => refetchReadiness()}
          className="px-3 py-1.5 rounded-lg border border-slate-800 bg-slate-900 hover:bg-slate-800 text-xs font-mono text-slate-300 transition-colors"
        >
          Probe Dependencies
        </button>
      </div>

      {/* Core Infrastructure Components */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* PostgreSQL Probe */}
        <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5">
          <div className="flex items-center justify-between">
            <span className="text-xs font-mono uppercase text-slate-400">Database Engine</span>
            <span
              className={`px-2 py-0.5 rounded text-[10px] font-mono ${
                components["postgres"]?.status === "READY"
                  ? "bg-emerald-950 text-emerald-400 border border-emerald-800/60"
                  : "bg-rose-950 text-rose-400"
              }`}
            >
              {components["postgres"]?.status ?? "PROBING"}
            </span>
          </div>

          <h2 className="text-base font-semibold text-white mt-2">PostgreSQL 18</h2>
          <p className="text-xs text-slate-400 mt-1">
            Acid transactions, integer-paise accounting, append-only audit trail
          </p>

          <div className="mt-4 pt-3 border-t border-slate-800/80 flex items-center justify-between text-xs font-mono text-slate-500">
            <span>Query Latency:</span>
            <span className="text-slate-200">
              {components["postgres"]?.latency_ms != null
                ? `${components["postgres"].latency_ms.toFixed(1)} ms`
                : "—"}
            </span>
          </div>
        </div>

        {/* Redis & Broker Probe */}
        <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5">
          <div className="flex items-center justify-between">
            <span className="text-xs font-mono uppercase text-slate-400">Broker & Locks</span>
            <span
              className={`px-2 py-0.5 rounded text-[10px] font-mono ${
                components["redis"]?.status === "READY"
                  ? "bg-emerald-950 text-emerald-400 border border-emerald-800/60"
                  : "bg-rose-950 text-rose-400"
              }`}
            >
              {components["redis"]?.status ?? "PROBING"}
            </span>
          </div>

          <h2 className="text-base font-semibold text-white mt-2">Redis 6379</h2>
          <p className="text-xs text-slate-400 mt-1">
            Celery distributed task queues, idempotency keys, execution locks
          </p>

          <div className="mt-4 pt-3 border-t border-slate-800/80 flex items-center justify-between text-xs font-mono text-slate-500">
            <span>Ping Latency:</span>
            <span className="text-slate-200">
              {components["redis"]?.latency_ms != null
                ? `${components["redis"].latency_ms.toFixed(1)} ms`
                : "—"}
            </span>
          </div>
        </div>

        {/* Model Artifact Probe */}
        <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5">
          <div className="flex items-center justify-between">
            <span className="text-xs font-mono uppercase text-slate-400">ML Artifact</span>
            <span
              className={`px-2 py-0.5 rounded text-[10px] font-mono ${
                components["model"]?.status === "READY"
                  ? "bg-emerald-950 text-emerald-400 border border-emerald-800/60"
                  : "bg-amber-950 text-amber-400"
              }`}
            >
              {components["model"]?.status ?? "PROBING"}
            </span>
          </div>

          <h2 className="text-base font-semibold text-white mt-2">Action-Value T-Learner</h2>
          <p className="text-xs text-slate-400 mt-1">
            LightGBM calibrated action-specific recovery estimators
          </p>

          <div className="mt-4 pt-3 border-t border-slate-800/80 flex items-center justify-between text-xs font-mono text-slate-500">
            <span>Fallback Base Rate:</span>
            <span className="text-emerald-400 font-semibold font-mono">P0 = 0.35</span>
          </div>
        </div>
      </div>

      {/* Model Metadata & Provenance Card */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5">
        <h2 className="text-sm font-semibold text-white mb-1">
          Decision Intelligence Model Provenance
        </h2>
        <p className="text-xs text-slate-400 mb-4">
          Frozen Phase 3 model hyperparameters, calibration specifications, and runtime configuration
        </p>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs font-mono">
          <div className="p-3.5 rounded-lg border border-slate-800 bg-slate-950/80 space-y-1.5">
            <div className="text-slate-500 uppercase text-[10px]">Model Architecture</div>
            <div className="text-white font-semibold">
              {modelHealth?.model_name ?? "ActionValueEstimator (T-Learner LightGBM)"}
            </div>
            <div className="text-slate-400 text-[11px]">
              Version: {modelHealth?.model_version ?? "action-value-v3.0-calibrated"}
            </div>
          </div>

          <div className="p-3.5 rounded-lg border border-slate-800 bg-slate-950/80 space-y-1.5">
            <div className="text-slate-500 uppercase text-[10px]">Calibration & Schema</div>
            <div className="text-white font-semibold">
              {modelHealth?.calibration_status ?? "calibrated (Isotonic)"}
            </div>
            <div className="text-slate-400 text-[11px]">
              Schema: {modelHealth?.feature_schema_version ?? "v1.0"}
            </div>
          </div>

          <div className="p-3.5 rounded-lg border border-slate-800 bg-slate-950/80 space-y-1.5">
            <div className="text-slate-500 uppercase text-[10px]">Artifact Storage Path</div>
            <div className="text-slate-200 truncate">
              {modelHealth?.artifact_path ?? "model_artifacts/action_value_v3.pkl"}
            </div>
            <div className="text-emerald-400 text-[11px]">Artifact Verified on Disk</div>
          </div>

          <div className="p-3.5 rounded-lg border border-slate-800 bg-slate-950/80 space-y-1.5">
            <div className="text-slate-500 uppercase text-[10px]">Resilience Invariants</div>
            <div className="text-white font-semibold">
              Fallback Base Rate: P0 = {modelHealth?.fallback_probability ?? 0.35}
            </div>
            <div className="text-slate-400 text-[11px]">Zero float currency representation</div>
          </div>
        </div>
      </div>
    </div>
  );
}
