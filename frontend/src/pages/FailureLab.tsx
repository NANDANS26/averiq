import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import { useOperatingMode } from '@/contexts/OperatingModeContext';
import type { SystemStatus } from '@/types';

/**
 * All 10 failure scenarios defined in REL-FAIL-001 (task 18.1).
 * The id maps to the backend route: POST /api/v1/simulation/failures/{id}
 */
const SCENARIOS = [
  {
    id: 'duplicate-webhook',
    label: '1. Duplicate Webhook',
    desc: 'Re-send the same provider_event_id to verify deduplication (DUPLICATE status, no reprocessing).',
    risk: 'low',
  },
  {
    id: 'out-of-order-webhook',
    label: '2. Out-of-Order Webhook',
    desc: 'Send a webhook event that implies a past state transition to test defensive handling.',
    risk: 'low',
  },
  {
    id: 'ai-timeout',
    label: '3. AI Timeout',
    desc: 'Inject asyncio.sleep(15) in StubAIProvider to force a timeout and verify fallback classifier activates.',
    risk: 'low',
  },
  {
    id: 'malformed-ai-output',
    label: '4. Malformed AI Output',
    desc: 'Inject schema-invalid DiagnosisOutput to verify ai_output_validator rejects and fallback fires.',
    risk: 'low',
  },
  {
    id: 'external-api-timeout',
    label: '5. External API Timeout',
    desc: 'Inject timeout in FakeRazorpayGateway to simulate gateway non-response. Expect TIMEOUT state.',
    risk: 'medium',
  },
  {
    id: 'payment-already-recovered',
    label: '6. Payment Already Recovered',
    desc: 'Set case to RECOVERED then attempt an intervention. Verify idempotency guard blocks re-execution.',
    risk: 'medium',
  },
  {
    id: 'duplicate-action-attempt',
    label: '7. Duplicate Action Attempt',
    desc: 'Submit the same action_id twice. Verify at-most-once execution via idempotency key.',
    risk: 'low',
  },
  {
    id: 'retry-budget-exhausted',
    label: '8. Retry Budget Exhausted',
    desc: 'Set attempt count to max then attempt a RETRY intervention. Verify STOP pathway.',
    risk: 'medium',
  },
  {
    id: 'policy-rejection',
    label: '9. Policy Rejection',
    desc: 'Configure policy to block all interventions. Verify BLOCKED outcome and no execution.',
    risk: 'low',
  },
  {
    id: 'processing-crash-mid-execution',
    label: '10. Processing Crash Mid-Execution',
    desc: 'Mark execution status TIMEOUT on restart to verify reconciliation path fires and no duplicate action.',
    risk: 'high',
  },
] as const;

type ScenarioId = (typeof SCENARIOS)[number]['id'];
type ScenarioResult = { scenario: string; status: string; message?: string; detail?: unknown };

const RISK_COLORS = {
  low: 'text-green-700 bg-green-50 border-green-200',
  medium: 'text-amber-700 bg-amber-50 border-amber-200',
  high: 'text-red-700 bg-red-50 border-red-200',
};

export default function FailureLab() {
  const { operatingMode } = useOperatingMode();
  const [results, setResults] = useState<Partial<Record<ScenarioId, ScenarioResult>>>({});
  const [errors, setErrors] = useState<Partial<Record<ScenarioId, string>>>({});

  // Check system status for failure_lab_enabled flag (FE-008)
  const { data: status } = useQuery<SystemStatus>({
    queryKey: ['system-status'],
    queryFn: () => apiClient.get('/api/v1/status').then((r) => r.data),
    staleTime: 60_000,
  });

  const triggerMutation = useMutation({
    mutationFn: ({ id }: { id: ScenarioId }) =>
      apiClient
        .post<ScenarioResult>(`/api/v1/simulation/failures/${id}`, {})
        .then((r) => ({ id, data: r.data })),
    onSuccess: ({ id, data }) => {
      setResults((prev) => ({ ...prev, [id]: data }));
      setErrors((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
    },
    onError: (err: unknown, { id }) => {
      const apiErr = err as { error?: string };
      setErrors((prev) => ({
        ...prev,
        [id]: apiErr.error ?? 'Trigger failed. Check backend logs.',
      }));
    },
  });

  // FE-008: page must not be accessible when ENABLE_FAILURE_LAB=false
  // The backend enforces this with HTTP 404; we also guard on the frontend.
  const labEnabled = status?.failure_lab_enabled;
  // If status not yet loaded, show based on operating mode only
  const isAccessible = labEnabled ?? (operatingMode === 'SIMULATED');

  if (!isAccessible) {
    return (
      <div className="p-6 max-w-xl">
        <h1 className="text-2xl font-semibold mb-4">Failure Lab</h1>
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800 space-y-2">
          <p className="font-medium">Failure Lab is not enabled.</p>
          <p>
            Set <code className="font-mono bg-amber-100 px-1 rounded">ENABLE_FAILURE_LAB=true</code> in
            the backend environment to enable this page.
          </p>
          {operatingMode && (
            <p className="text-xs opacity-75">Current operating mode: {operatingMode}</p>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">Failure Lab</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Trigger deterministic failure scenarios to validate system resilience. Uses{' '}
          <code className="font-mono text-xs">FakeRazorpayGateway</code> only — no real payments.
        </p>
      </div>

      <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-800 flex gap-2 items-start">
        <span className="mt-0.5">⚠</span>
        <span>
          These actions trigger real state changes in the system database. Each scenario is designed
          to be safe in SIMULATED mode, but review the risk level before triggering.
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {SCENARIOS.map((s) => {
          const result = results[s.id];
          const error = errors[s.id];
          const isRunning =
            triggerMutation.isPending &&
            (triggerMutation.variables as { id: ScenarioId } | undefined)?.id === s.id;

          return (
            <div
              key={s.id}
              className="rounded-lg border border-border bg-card p-4 space-y-3 flex flex-col"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-sm">{s.label}</div>
                  <div className="text-xs text-muted-foreground mt-0.5 leading-relaxed">
                    {s.desc}
                  </div>
                </div>
                <span
                  className={`shrink-0 px-2 py-0.5 rounded-full text-xs font-medium border ${RISK_COLORS[s.risk]}`}
                >
                  {s.risk}
                </span>
              </div>

              {/* Result / error display */}
              {result && (
                <div className="rounded bg-muted p-2 text-xs font-mono overflow-auto max-h-28 whitespace-pre-wrap">
                  {JSON.stringify(result, null, 2)}
                </div>
              )}
              {error && (
                <div className="rounded bg-destructive/5 border border-destructive/20 p-2 text-xs text-destructive">
                  {error}
                </div>
              )}

              <button
                onClick={() => triggerMutation.mutate({ id: s.id })}
                disabled={isRunning}
                className={`mt-auto w-full px-3 py-2 text-sm rounded border transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                  s.risk === 'high'
                    ? 'bg-destructive/10 text-destructive border-destructive/30 hover:bg-destructive/20'
                    : s.risk === 'medium'
                    ? 'bg-amber-50 text-amber-800 border-amber-200 hover:bg-amber-100'
                    : 'bg-muted/50 text-foreground border-border hover:bg-muted'
                }`}
              >
                {isRunning ? 'Running…' : 'Trigger'}
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
