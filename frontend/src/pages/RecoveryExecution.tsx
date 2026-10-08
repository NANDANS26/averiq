import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { apiClient } from '@/api/client';
import { formatDateTime } from '@/lib/utils';

export default function RecoveryExecution() {
  const { caseId } = useParams<{ caseId: string }>();
  const { data: caseData } = useQuery({
    queryKey: ['case', caseId],
    queryFn: () => apiClient.get(`/api/v1/cases/${caseId}`).then(r => r.data),
    enabled: !!caseId,
  });

  return (
    <div className="p-6 space-y-4">
      <h1 className="text-xl font-semibold">Recovery Execution</h1>
      {caseData && (
        <div className="rounded-lg border border-border bg-card p-4 space-y-3 text-sm">
          <dl className="grid grid-cols-2 gap-3">
            <div><dt className="text-xs text-muted-foreground">State</dt><dd className="font-medium">{caseData.state}</dd></div>
            <div><dt className="text-xs text-muted-foreground">Operating Mode</dt><dd className="font-medium">{caseData.operating_mode}</dd></div>
            <div><dt className="text-xs text-muted-foreground">Updated</dt><dd className="font-medium">{formatDateTime(caseData.updated_at)}</dd></div>
          </dl>
        </div>
      )}
    </div>
  );
}
