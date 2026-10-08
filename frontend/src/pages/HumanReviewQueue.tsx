import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import { useAuth } from '@/contexts/AuthContext';
import { formatDateTime } from '@/lib/utils';

interface ReviewItem {
  id: string;
  case_id: string;
  intervention_id: string;
  status: 'OPEN' | 'ASSIGNED' | 'RESOLVED' | string;
  created_at: string;
  assigned_to?: string | null;
  notes?: string | null;
}

const STATUS_COLORS: Record<string, string> = {
  OPEN: 'bg-amber-100 text-amber-700',
  ASSIGNED: 'bg-blue-100 text-blue-700',
  RESOLVED: 'bg-green-100 text-green-700',
};

export default function HumanReviewQueue() {
  const { role } = useAuth();
  const qc = useQueryClient();
  const canAct = role === 'ADMIN' || role === 'OPERATOR';

  const {
    data: reviews,
    isLoading,
    isError,
    refetch,
  } = useQuery<ReviewItem[]>({
    queryKey: ['human-reviews'],
    queryFn: () => apiClient.get('/api/v1/review').then((r) => r.data),
    refetchInterval: 15_000,
  });

  const approveMutation = useMutation({
    mutationFn: (interventionId: string) =>
      apiClient.post(`/api/v1/interventions/${interventionId}/approve`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['human-reviews'] }),
  });

  const rejectMutation = useMutation({
    mutationFn: (interventionId: string) =>
      apiClient.post(`/api/v1/interventions/${interventionId}/reject`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['human-reviews'] }),
  });

  const isPending = approveMutation.isPending || rejectMutation.isPending;

  const colCount = canAct ? 5 : 4;

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Human Review Queue</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Interventions pending manual approval
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-muted-foreground">Auto-refreshes every 15s</span>
          <button
            onClick={() => refetch()}
            className="text-xs px-3 py-1 rounded border border-border hover:bg-muted/50 transition-colors"
          >
            Refresh
          </button>
        </div>
      </div>

      {(approveMutation.isError || rejectMutation.isError) && (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          Action failed. Please try again.
        </div>
      )}

      {isError && (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive flex items-center justify-between">
          <span>Failed to load review queue.</span>
          <button
            onClick={() => refetch()}
            className="text-xs underline underline-offset-2 hover:no-underline"
          >
            Retry
          </button>
        </div>
      )}

      {!canAct && (
        <div className="rounded-lg border border-border bg-muted/30 px-4 py-2 text-xs text-muted-foreground">
          You have read-only access. Approve/reject actions require OPERATOR or ADMIN role.
        </div>
      )}

      <div className="rounded-lg border border-border overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">
                Review ID
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">
                Case ID
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">
                Status
              </th>
              <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">
                Created
              </th>
              {canAct && (
                <th className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">
                  Actions
                </th>
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {isLoading && (
              <tr>
                <td colSpan={colCount} className="px-4 py-8 text-center text-muted-foreground">
                  Loading…
                </td>
              </tr>
            )}
            {!isLoading &&
              Array.isArray(reviews) &&
              reviews.map((r) => (
                <tr key={r.id} className="hover:bg-muted/20">
                  <td className="px-4 py-3 font-mono text-xs" title={r.id}>
                    {r.id.slice(0, 12)}…
                  </td>
                  <td className="px-4 py-3 font-mono text-xs" title={r.case_id}>
                    {r.case_id.slice(0, 12)}…
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                        STATUS_COLORS[r.status] ?? 'bg-secondary text-secondary-foreground'
                      }`}
                    >
                      {r.status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs text-muted-foreground">
                    {formatDateTime(r.created_at)}
                  </td>
                  {canAct && (
                    <td className="px-4 py-3">
                      <div className="flex gap-2">
                        <button
                          onClick={() => approveMutation.mutate(r.intervention_id)}
                          disabled={isPending || r.status === 'RESOLVED'}
                          className="text-xs px-2 py-1 bg-green-100 text-green-700 rounded hover:bg-green-200 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                        >
                          Approve
                        </button>
                        <button
                          onClick={() => rejectMutation.mutate(r.intervention_id)}
                          disabled={isPending || r.status === 'RESOLVED'}
                          className="text-xs px-2 py-1 bg-red-100 text-red-700 rounded hover:bg-red-200 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                        >
                          Reject
                        </button>
                      </div>
                    </td>
                  )}
                </tr>
              ))}
            {!isLoading && (!Array.isArray(reviews) || reviews.length === 0) && (
              <tr>
                <td
                  colSpan={colCount}
                  className="px-4 py-8 text-center text-muted-foreground"
                >
                  No pending reviews
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {!isLoading && Array.isArray(reviews) && reviews.length > 0 && (
        <p className="text-xs text-muted-foreground text-right">
          {reviews.filter((r) => r.status === 'OPEN').length} open /{' '}
          {reviews.length} total
        </p>
      )}
    </div>
  );
}
