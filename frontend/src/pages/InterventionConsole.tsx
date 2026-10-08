import { useParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { casesApi } from '@/api/cases';
import { apiClient } from '@/api/client';
import { formatPaise } from '@/lib/utils';
import { useAuth } from '@/contexts/AuthContext';
import type { Intervention } from '@/types';

export default function InterventionConsole() {
  const { caseId } = useParams<{ caseId: string }>();
  const { role } = useAuth();
  const qc = useQueryClient();

  const { data: interventions } = useQuery({
    queryKey: ['case-interventions', caseId],
    queryFn: () => casesApi.getInterventions(caseId!),
    enabled: !!caseId,
  });

  const approveMutation = useMutation({
    mutationFn: (interventionId: string) => apiClient.post(`/api/v1/interventions/${interventionId}/approve`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['case-interventions', caseId] }),
  });
  const rejectMutation = useMutation({
    mutationFn: (interventionId: string) => apiClient.post(`/api/v1/interventions/${interventionId}/reject`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['case-interventions', caseId] }),
  });

  const canAct = role === 'ADMIN' || role === 'OPERATOR';

  return (
    <div className="p-6 space-y-4">
      <h1 className="text-xl font-semibold">Intervention Console</h1>
      <div className="rounded-lg border border-border overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              {['Rank', 'Type', 'EV', 'Recovery Prob.', 'Policy Decision', 'Blocking Rules', 'Actions'].map(h => (
                <th key={h} className="px-4 py-3 text-left text-xs font-medium text-muted-foreground">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {interventions?.map((i: Intervention, idx: number) => (
              <tr key={String(i.id)} className="hover:bg-muted/20">
                <td className="px-4 py-3 text-xs text-muted-foreground">#{idx + 1}</td>
                <td className="px-4 py-3 font-mono text-xs">{String(i.intervention_type)}</td>
                <td className="px-4 py-3 font-medium tabular-nums text-xs">{typeof i.expected_recovery_value === 'number' ? formatPaise(i.expected_recovery_value) : '-'}</td>
                <td className="px-4 py-3 text-xs tabular-nums">{typeof i.recovery_probability === 'number' ? `${(i.recovery_probability * 100).toFixed(1)}%` : '-'}</td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${i.policy_decision === 'APPROVED' ? 'bg-green-100 text-green-700' : i.policy_decision === 'BLOCKED' ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-700'}`}>
                    {String(i.policy_decision ?? '-')}
                  </span>
                </td>
                <td className="px-4 py-3 text-xs text-muted-foreground">{Array.isArray(i.blocking_rules) ? i.blocking_rules.join(', ') : '-'}</td>
                <td className="px-4 py-3">
                  {canAct && i.policy_decision === 'HUMAN_REVIEW' && (
                    <div className="flex gap-2">
                      <button onClick={() => approveMutation.mutate(String(i.id))} className="text-xs px-2 py-1 bg-green-100 text-green-700 rounded hover:bg-green-200">Approve</button>
                      <button onClick={() => rejectMutation.mutate(String(i.id))} className="text-xs px-2 py-1 bg-red-100 text-red-700 rounded hover:bg-red-200">Reject</button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
            {!interventions?.length && <tr><td colSpan={7} className="px-4 py-8 text-center text-muted-foreground">No interventions</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}