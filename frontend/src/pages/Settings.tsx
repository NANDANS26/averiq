import type { ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import { useAuth } from '@/contexts/AuthContext';
import { useOperatingMode } from '@/contexts/OperatingModeContext';
import type { SystemStatus } from '@/types';

const MODE_COLORS: Record<string, string> = {
  SIMULATED: 'text-blue-700 bg-blue-50 border-blue-200',
  REAL_TEST_MODE: 'text-amber-700 bg-amber-50 border-amber-200',
  DEMO_FIXTURE: 'text-violet-700 bg-violet-50 border-violet-200',
};

const MODE_DESCRIPTIONS: Record<string, string> = {
  SIMULATED:
    'All payment operations use fake gateway. No real funds move.',
  REAL_TEST_MODE:
    'Connected to Razorpay test-mode credentials. No live payments.',
  DEMO_FIXTURE:
    'Static fixture data. Actions do not persist between sessions.',
};

function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-center justify-between py-2 border-b border-border/50 last:border-0">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="text-sm font-medium">{value}</span>
    </div>
  );
}

export default function Settings() {
  const { user } = useAuth();
  const { operatingMode } = useOperatingMode();

  const { data: status, isError: statusError } = useQuery<SystemStatus>({
    queryKey: ['system-status'],
    queryFn: () => apiClient.get('/api/v1/status').then((r) => r.data),
    staleTime: 60_000,
  });

  const effectiveMode = operatingMode ?? status?.operating_mode ?? null;

  return (
    <div className="p-6 space-y-6 max-w-2xl">
      <h1 className="text-2xl font-semibold">Settings</h1>

      {/* Operating Mode banner */}
      {effectiveMode && (
        <div
          className={`rounded-lg border px-4 py-3 text-sm font-medium ${
            MODE_COLORS[effectiveMode] ?? 'text-secondary-foreground bg-secondary border-border'
          }`}
        >
          <div className="flex items-center justify-between">
            <span>Operating Mode: {effectiveMode}</span>
            {effectiveMode === 'SIMULATED' && (
              <span className="text-xs font-normal opacity-70">Safe to test</span>
            )}
          </div>
          {MODE_DESCRIPTIONS[effectiveMode] && (
            <p className="mt-1 text-xs font-normal opacity-80">
              {MODE_DESCRIPTIONS[effectiveMode]}
            </p>
          )}
        </div>
      )}

      {/* System Status */}
      <section className="rounded-lg border border-border bg-card p-5 space-y-1">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-3">
          System Status
        </h2>

        {statusError ? (
          <p className="text-sm text-destructive">Failed to load system status.</p>
        ) : (
          <>
            <Row
              label="Version"
              value={status?.version ?? '—'}
            />
            <Row
              label="Overall Health"
              value={
                status?.status ? (
                  <span
                    className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                      status.status === 'ok'
                        ? 'bg-green-100 text-green-700'
                        : 'bg-amber-100 text-amber-700'
                    }`}
                  >
                    {status.status.toUpperCase()}
                  </span>
                ) : (
                  '—'
                )
              }
            />
            <Row
              label="Operating Mode"
              value={
                effectiveMode ? (
                  <span
                    className={`px-2 py-0.5 rounded-full text-xs font-semibold border ${
                      MODE_COLORS[effectiveMode] ??
                      'bg-secondary text-secondary-foreground border-border'
                    }`}
                  >
                    {effectiveMode}
                  </span>
                ) : (
                  '—'
                )
              }
            />
            <Row
              label="Demo Fixture"
              value={
                status?.is_demo_fixture !== undefined ? (
                  <span
                    className={`text-xs font-medium ${
                      status.is_demo_fixture ? 'text-violet-600' : 'text-muted-foreground'
                    }`}
                  >
                    {status.is_demo_fixture ? 'Enabled' : 'Disabled'}
                  </span>
                ) : (
                  '—'
                )
              }
            />
          </>
        )}
      </section>

      {/* Account Info */}
      {user && (
        <section className="rounded-lg border border-border bg-card p-5 space-y-1">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-3">
            Account
          </h2>
          <Row
            label="User ID"
            value={
              <span className="font-mono text-xs" title={user.id}>
                {user.id.slice(0, 16)}…
              </span>
            }
          />
          <Row
            label="Email"
            value={user.email || <span className="text-muted-foreground italic">—</span>}
          />
          <Row
            label="Role"
            value={
              <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-secondary text-secondary-foreground">
                {user.role}
              </span>
            }
          />
          <Row
            label="Merchant ID"
            value={
              <span className="font-mono text-xs" title={user.merchantId}>
                {user.merchantId.slice(0, 16)}…
              </span>
            }
          />
        </section>
      )}

      {/* Merchant Configuration */}
      <section className="rounded-lg border border-border bg-card p-5 space-y-1">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-3">
          Merchant Configuration
        </h2>
        <Row label="Currency" value="INR" />
        <Row
          label="Payment Gateway"
          value={
            <span className="text-xs text-muted-foreground">
              {effectiveMode === 'SIMULATED' ? 'FakeRazorpayGateway (simulated)' : 'Razorpay (test-mode)'}
            </span>
          }
        />
        <Row
          label="AI Provider"
          value={
            <span className="text-xs text-muted-foreground">
              Configured via backend environment
            </span>
          }
        />
      </section>
    </div>
  );
}
