import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { policyApi } from '@/api/policy';
import { useAuth } from '@/contexts/AuthContext';
import { formatDateTime } from '@/lib/utils';

export default function PolicyCenter() {
  const { role } = useAuth();
  const qc = useQueryClient();
  const isAdmin = role === 'ADMIN';

  const { data: policies } = useQuery({ queryKey: ['policies'], queryFn: policyApi.list });
  const { data: active } = useQuery({ queryKey: ['active-policy'], queryFn: policyApi.getActive });

  const activateMutation = useMutation({
    mutationFn: (id: string) => policyApi.activate(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['policies'] });
      qc.invalidateQueries({ queryKey: ['active-policy'] });
    },
  });

  const stateBadge = (state: string) => {
    const colors: Record<string, string> = {
      active: 'bg-green-100 text-green-700',
      draft: 'bg-slate-100 text-slate-600',
      validated: 'bg-blue-100 text-blue-700',
      retired: 'bg-gray-100 text-gray-500',
    };
    return (
      <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${colors[state] ?? 'bg-secondary text-secondary-foreground'}`}>
        {state}
      </span>
    );
  };

  return (
    <div className="p-6 space-y-6">
      <h1 className="text-xl font-semibold">Policy Center</h1>

      {/* Active policy summary */}
      {active && (
        <div className="rounded-lg border border-green-200 bg-green-50 p-4">
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm font-semibold text-green-800">Active Policy — v{active.version}</h2>
            {stateBadge(active.lifecycle_state)}
          </div>
          <dl className="grid grid-cols-3 gap-2 text-xs text-green-700">
            <div><dt>Min Confidence</dt><dd className="font-medium">{((active.configuration?.min_confidence ?? 0) * 100).toFixed(0)}%</dd></div>
            <div><dt>Cooldown</dt><dd className="font-medium">{active.configuration?.cooldown_seconds ?? 0}s</dd></div>
            <div><dt>Human Approval ≥</dt><dd className="font-medium">₹{((active.configuration?.human_approval_threshold ?? 0) / 100).toLocaleString('en-IN')}</dd></div>
          </dl>
        </div>
      )}

      {/* Policy version history */}
      <div className="rounded-lg border border-border overflow-hidden">
        <div className="px-4 py-3 bg-muted/30 border-b border-border">
          <h2 className="text-sm font-medium">Version History</h2>
        </div>
        <table className="w-full text-sm">
          <thead className="bg-muted/30">
            <tr>
              {(['Version', 'State', 'Created', isAdmin ? 'Actions' : ''] as string[]).filter(Boolean).map(h => (
                <th key={h} className="px-4 py-2 text-left text-xs font-medium text-muted-foreground">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {policies?.map((p: Record<string, unknown>) => (
              <tr key={String(p.id)} className="hover:bg-muted/10">
                <td className="px-4 py-3 font-medium">v{String(p.version)}</td>
                <td className="px-4 py-3">{stateBadge(String(p.lifecycle_state))}</td>
                <td className="px-4 py-3 text-xs text-muted-foreground">{formatDateTime(String(p.created_at))}</td>
                {isAdmin && (
                  <td className="px-4 py-3">
                    {(p.lifecycle_state === 'draft' || p.lifecycle_state === 'validated') && (
                      <button
                        onClick={() => activateMutation.mutate(String(p.id))}
                        className="text-xs px-2 py-1 bg-primary text-primary-foreground rounded hover:opacity-90"
                      >
                        Activate
                      </button>
                    )}
                  </td>
                )}
              </tr>
            ))}
            {!policies?.length && (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-muted-foreground text-sm">No policies</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
