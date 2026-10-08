import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import { formatDateTime } from '@/lib/utils';
import type { WebhookEvent } from '@/types';

const STATUS_COLORS: Record<string, string> = {
  PROCESSED: 'bg-green-100 text-green-700',
  PROCESSING: 'bg-blue-100 text-blue-700',
  PERSISTED: 'bg-slate-100 text-slate-600',
  FAILED_RETRYABLE: 'bg-amber-100 text-amber-700',
  DEAD_LETTERED: 'bg-red-100 text-red-700',
  RECEIVED: 'bg-gray-100 text-gray-600',
  DUPLICATE: 'bg-purple-100 text-purple-700',
  FAILED: 'bg-red-100 text-red-700',
};

const COLUMNS = ['Event ID', 'Provider Event ID', 'Event Type', 'Status', 'Dedup', 'Created'];

export default function WebhookMonitor() {
  const { data, isLoading, isError, refetch } = useQuery<{ items: WebhookEvent[] }>({
    queryKey: ['webhook-events'],
    queryFn: () =>
      apiClient.get('/api/v1/webhooks?limit=50').then((r) => r.data),
    refetchInterval: 10_000,
  });

  const events = data?.items ?? [];

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Webhook Monitor</h1>
        <div className="flex items-center gap-3">
          <span className="text-xs text-muted-foreground">Auto-refreshes every 10s</span>
          <button
            onClick={() => refetch()}
            className="text-xs px-3 py-1 rounded border border-border hover:bg-muted/50 transition-colors"
          >
            Refresh
          </button>
        </div>
      </div>

      {isError && (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive flex items-center justify-between">
          <span>Failed to load webhook events.</span>
          <button
            onClick={() => refetch()}
            className="text-xs underline underline-offset-2 hover:no-underline"
          >
            Retry
          </button>
        </div>
      )}

      <div className="rounded-lg border border-border overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              {COLUMNS.map((h) => (
                <th
                  key={h}
                  className="px-4 py-3 text-left text-xs font-medium text-muted-foreground"
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {isLoading && (
              <tr>
                <td colSpan={COLUMNS.length} className="px-4 py-8 text-center text-muted-foreground">
                  Loading…
                </td>
              </tr>
            )}
            {!isLoading && events.map((evt) => {
              const isDuplicate = evt.processing_status === 'DUPLICATE';
              const isErrorState =
                evt.processing_status === 'DEAD_LETTERED' ||
                evt.processing_status === 'FAILED' ||
                evt.processing_status === 'FAILED_RETRYABLE';

              return (
                <tr
                  key={evt.id}
                  className={`hover:bg-muted/20 ${isErrorState ? 'bg-red-50/30' : ''}`}
                >
                  <td className="px-4 py-3 font-mono text-xs" title={evt.id}>
                    {evt.id.slice(0, 12)}…
                  </td>
                  <td className="px-4 py-3 font-mono text-xs" title={evt.provider_event_id}>
                    {evt.provider_event_id.slice(0, 16)}…
                  </td>
                  <td className="px-4 py-3 text-xs">{evt.event_type}</td>
                  <td className="px-4 py-3">
                    <span
                      className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                        STATUS_COLORS[evt.processing_status] ?? 'bg-secondary text-secondary-foreground'
                      }`}
                    >
                      {evt.processing_status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs">
                    {isDuplicate ? (
                      <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-purple-100 text-purple-700">
                        DEDUP
                      </span>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs text-muted-foreground">
                    {formatDateTime(evt.created_at)}
                  </td>
                </tr>
              );
            })}
            {!isLoading && events.length === 0 && (
              <tr>
                <td
                  colSpan={COLUMNS.length}
                  className="px-4 py-8 text-center text-muted-foreground"
                >
                  No recent webhook activity
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {!isLoading && events.length > 0 && (
        <p className="text-xs text-muted-foreground text-right">
          Showing {events.length} event{events.length !== 1 ? 's' : ''}
        </p>
      )}
    </div>
  );
}
