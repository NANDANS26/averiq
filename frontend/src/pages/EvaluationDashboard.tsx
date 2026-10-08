import { useQuery } from '@tanstack/react-query';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { casesApi } from '@/api/cases';
import { apiClient } from '@/api/client';

// ---------------------------------------------------------------------------
// Types for evaluation API responses
// ---------------------------------------------------------------------------

/**
 * Runtime metrics from /api/v1/analytics/metrics.
 * Shape is flexible — backend emits whatever is available at runtime.
 */
type MetricsResponse = Record<string, string | number | boolean | null>;

/**
 * Model evaluation artifact from /api/v1/models/latest-evaluation.
 * Populated after running `make evaluate`.
 */
interface ModelEvaluationArtifact {
  cohort_definition?: string;
  dataset_version?: string;
  split_boundaries?: { train_end: string; val_end: string; test_end: string };
  models?: ModelMetrics[];
}

interface ModelMetrics {
  model_name: string;
  model_version?: string;
  precision?: number | null;
  recall?: number | null;
  f1?: number | null;
  roc_auc?: number | null;
  brier_score?: number | null;
  incremental_recovered_revenue?: number | null;
  recovery_rate?: number | null;
  intervention_success_rate?: number | null;
  escalation_rate?: number | null;
  stop_rate?: number | null;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const MODEL_NAMES = ['LightGBM', 'LogisticRegression', 'DeterministicFallback'] as const;

function fmtMetric(v: number | string | null | undefined): string {
  if (v === null || v === undefined || v === 'NOT_AVAILABLE') return '—';
  if (typeof v === 'number') return v.toFixed(3);
  return String(v);
}

function fmtRate(v: number | string | null | undefined): string {
  if (v === null || v === undefined || v === 'NOT_AVAILABLE') return '—';
  if (typeof v === 'number') return `${(v * 100).toFixed(1)}%`;
  return String(v);
}

/** Build chart-friendly data from model metrics list. */
function toChartData(models: ModelMetrics[], key: keyof ModelMetrics) {
  return models.map((m) => ({
    model: m.model_name,
    value: typeof m[key] === 'number' ? (m[key] as number) : 0,
  }));
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function RunGuide() {
  return (
    <div className="rounded-lg border border-border bg-muted/30 px-4 py-3 text-xs text-muted-foreground space-y-1">
      <div>All metric values are computed at runtime — no hardcoded targets.</div>
      <div>
        Run{' '}
        <code className="bg-muted px-1 rounded">make train</code> then{' '}
        <code className="bg-muted px-1 rounded">make evaluate</code> to populate model metrics.
      </div>
      <div>
        Run <code className="bg-muted px-1 rounded">make simulate</code> to populate simulation
        metrics.
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
/**
 * EvaluationDashboard — model comparison table, calibration metrics,
 * and business simulation metrics from the most recent `make evaluate`
 * and `make simulate` outputs (FE-007, FR-MODEL-006, FR-MODEL-007, EVAL-002, EVAL-003).
 *
 * All values are fetched from the API at runtime — no hardcoded numbers.
 */
export default function EvaluationDashboard() {
  // Runtime operational metrics (available immediately)
  const { data: metrics } = useQuery<MetricsResponse>({
    queryKey: ['analytics-metrics'],
    queryFn: casesApi.getMetrics,
    staleTime: 60_000,
  });

  // Latest evaluation artifact (populated after `make evaluate`)
  const { data: evalArtifact, isLoading: evalLoading } = useQuery<ModelEvaluationArtifact>({
    queryKey: ['evaluation-latest'],
    queryFn: () => apiClient.get('/api/v1/models/latest-evaluation').then((r) => r.data),
    staleTime: 60_000,
    // Don't error loudly — endpoint may return 404 before first training run
    retry: false,
  });

  // Merge fetched models with fallback rows for models not yet evaluated
  const fetchedModels = evalArtifact?.models ?? [];
  const modelRows: ModelMetrics[] = MODEL_NAMES.map((name) => {
    return fetchedModels.find((m) => m.model_name === name) ?? { model_name: name };
  });

  // Chart data for ML metrics bar chart (only show when data is available)
  const hasMetricData = fetchedModels.some((m) => m.f1 != null);
  const f1ChartData = hasMetricData ? toChartData(modelRows, 'f1') : [];
  const brierChartData = hasMetricData ? toChartData(modelRows, 'brier_score') : [];

  // Filter out 'operating_mode' and boolean flags from the display cards
  const displayMetrics = metrics
    ? Object.entries(metrics).filter(([k, v]) => !['operating_mode'].includes(k) && v !== null)
    : [];

  return (
    <div className="p-6 space-y-6">
      <h1 className="text-2xl font-semibold">Evaluation Dashboard</h1>

      {/* Runtime operational metrics from API */}
      {displayMetrics.length > 0 && (
        <section>
          <h2 className="text-sm font-medium text-muted-foreground uppercase tracking-wide mb-3">
            Runtime Metrics
          </h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {displayMetrics.map(([key, value]) => (
              <div key={key} className="rounded-lg border border-border bg-card p-4">
                <div className="text-xs text-muted-foreground mb-1">
                  {key.replace(/_/g, ' ')}
                </div>
                <div className="text-lg font-semibold truncate">{String(value)}</div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Model comparison table (FR-MODEL-007) */}
      <section>
        <div className="rounded-lg border border-border bg-card overflow-hidden">
          <div className="px-4 py-3 bg-muted/30 border-b border-border">
            <h2 className="text-sm font-medium">Model Comparison</h2>
            {evalArtifact?.cohort_definition && (
              <p className="text-xs text-muted-foreground mt-0.5">
                Cohort: {evalArtifact.cohort_definition}
              </p>
            )}
            {evalArtifact?.dataset_version && (
              <p className="text-xs text-muted-foreground">
                Dataset: {evalArtifact.dataset_version}
              </p>
            )}
            {!evalArtifact && !evalLoading && (
              <p className="text-xs text-muted-foreground mt-0.5">
                Run <code className="bg-muted px-1 rounded">make evaluate</code> to populate
                metrics
              </p>
            )}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-muted/30">
                <tr>
                  {[
                    'Model',
                    'Precision',
                    'Recall',
                    'F1',
                    'ROC-AUC',
                    'Brier Score',
                    'IRR (primary)',
                  ].map((h) => (
                    <th
                      key={h}
                      className="px-4 py-3 text-left text-xs font-medium text-muted-foreground whitespace-nowrap"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {modelRows.map((row) => (
                  <tr key={row.model_name} className="hover:bg-muted/10">
                    <td className="px-4 py-3 font-medium whitespace-nowrap">{row.model_name}</td>
                    <td className="px-4 py-3 text-muted-foreground tabular-nums">
                      {fmtMetric(row.precision)}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground tabular-nums">
                      {fmtMetric(row.recall)}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground tabular-nums">
                      {fmtMetric(row.f1)}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground tabular-nums">
                      {fmtMetric(row.roc_auc)}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground tabular-nums">
                      {fmtMetric(row.brier_score)}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground tabular-nums">
                      {fmtMetric(row.incremental_recovered_revenue)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {/* Simulation business metrics table */}
      {fetchedModels.some((m) => m.recovery_rate != null) && (
        <section>
          <div className="rounded-lg border border-border bg-card overflow-hidden">
            <div className="px-4 py-3 bg-muted/30 border-b border-border">
              <h2 className="text-sm font-medium">Simulation Metrics</h2>
              <p className="text-xs text-muted-foreground mt-0.5">
                From most recent <code className="bg-muted px-1 rounded">make simulate</code> run
              </p>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-muted/30">
                  <tr>
                    {[
                      'Model',
                      'Recovery Rate',
                      'Intervention Success',
                      'Escalation Rate',
                      'Stop Rate',
                    ].map((h) => (
                      <th
                        key={h}
                        className="px-4 py-3 text-left text-xs font-medium text-muted-foreground whitespace-nowrap"
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {modelRows.map((row) => (
                    <tr key={row.model_name} className="hover:bg-muted/10">
                      <td className="px-4 py-3 font-medium">{row.model_name}</td>
                      <td className="px-4 py-3 text-muted-foreground tabular-nums">
                        {fmtRate(row.recovery_rate)}
                      </td>
                      <td className="px-4 py-3 text-muted-foreground tabular-nums">
                        {fmtRate(row.intervention_success_rate)}
                      </td>
                      <td className="px-4 py-3 text-muted-foreground tabular-nums">
                        {fmtRate(row.escalation_rate)}
                      </td>
                      <td className="px-4 py-3 text-muted-foreground tabular-nums">
                        {fmtRate(row.stop_rate)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </section>
      )}

      {/* ML metric bar charts — only rendered when evaluation data is present */}
      {hasMetricData && (
        <section>
          <h2 className="text-sm font-medium text-muted-foreground uppercase tracking-wide mb-3">
            Model Metric Comparison
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* F1 chart */}
            <div className="rounded-lg border border-border bg-card p-4">
              <h3 className="text-xs font-semibold mb-4 text-muted-foreground uppercase tracking-wide">
                F1 Score
              </h3>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={f1ChartData}>
                  <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                  <XAxis dataKey="model" tick={{ fontSize: 10 }} />
                  <YAxis domain={[0, 1]} tick={{ fontSize: 10 }} />
                  <Tooltip formatter={(v: number) => [v.toFixed(3), 'F1']} />
                  <Bar dataKey="value" name="F1 Score" fill="#3b82f6" />
                </BarChart>
              </ResponsiveContainer>
            </div>

            {/* Brier score chart (lower is better) */}
            <div className="rounded-lg border border-border bg-card p-4">
              <h3 className="text-xs font-semibold mb-4 text-muted-foreground uppercase tracking-wide">
                Brier Score (lower is better)
              </h3>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={brierChartData}>
                  <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                  <XAxis dataKey="model" tick={{ fontSize: 10 }} />
                  <YAxis domain={[0, 1]} tick={{ fontSize: 10 }} />
                  <Tooltip formatter={(v: number) => [v.toFixed(3), 'Brier Score']} />
                  <Bar dataKey="value" name="Brier Score" fill="#f59e0b" />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </section>
      )}

      {/* Split boundaries, if available */}
      {evalArtifact?.split_boundaries && (
        <div className="rounded-lg border border-border bg-muted/30 px-4 py-3 text-xs text-muted-foreground space-y-1">
          <div className="font-medium">Temporal Split Boundaries</div>
          <div>Train end: {evalArtifact.split_boundaries.train_end}</div>
          <div>Validation end: {evalArtifact.split_boundaries.val_end}</div>
          <div>Test end: {evalArtifact.split_boundaries.test_end}</div>
        </div>
      )}

      <RunGuide />
    </div>
  );
}
