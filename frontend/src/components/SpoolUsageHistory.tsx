import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Loader2, Trash2, Clock } from 'lucide-react';
import { api } from '../api/client';
import type { SpoolUsageRecord } from '../api/client';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';

interface SpoolUsageHistoryProps {
  spoolId: number;
}

function formatDate(dateStr: string): string {
  const date = new Date(dateStr);
  return date.toLocaleDateString('en-GB', { day: '2-digit', month: '2-digit', year: '2-digit' }) +
    ' ' + date.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
}

const STATUS_COLORS: Record<string, string> = {
  completed: 'text-bambu-green',
  failed: 'text-red-400',
  aborted: 'text-yellow-400',
};

export function SpoolUsageHistory({ spoolId }: SpoolUsageHistoryProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const { data: history, isLoading } = useQuery({
    queryKey: ['spool-usage', spoolId],
    queryFn: () => api.getSpoolUsageHistory(spoolId),
  });

  const clearMutation = useMutation({
    mutationFn: () => api.clearSpoolUsageHistory(spoolId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['spool-usage', spoolId] });
      showToast(t('inventory.historyCleared'), 'success');
    },
  });

  if (isLoading) {
    return (
      <div className="flex justify-center py-4">
        <Loader2 className="w-5 h-5 animate-spin text-bambu-green" />
      </div>
    );
  }

  if (!history || history.length === 0) {
    return (
      <div className="text-center py-4 text-bambu-gray text-sm">
        <Clock className="w-5 h-5 mx-auto mb-2 opacity-50" />
        {t('inventory.noUsageHistory')}
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-medium text-white">{t('inventory.usageHistory')}</h4>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => clearMutation.mutate()}
          disabled={clearMutation.isPending}
          className="text-xs text-bambu-gray hover:text-red-400"
        >
          <Trash2 className="w-3 h-3 mr-1" />
          {t('inventory.clearHistory')}
        </Button>
      </div>
      <div className="max-h-48 overflow-y-auto space-y-1">
        {history.map((record: SpoolUsageRecord) => (
          <div
            key={record.id}
            className="flex items-center justify-between p-2 rounded bg-bambu-dark/50 text-xs"
          >
            <div className="flex-1 min-w-0">
              <span className="text-bambu-gray">{formatDate(record.created_at)}</span>
              {record.print_name && (
                <span className="text-white ml-2 truncate" title={record.print_name}>
                  {record.print_name}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2 flex-shrink-0 ml-2">
              <span className="text-white font-medium">{record.weight_used.toFixed(1)}g</span>
              <span className="text-bambu-gray">({record.percent_used}%)</span>
              <span className={STATUS_COLORS[record.status] || 'text-bambu-gray'}>
                {record.status}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
