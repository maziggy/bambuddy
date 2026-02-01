import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { X, Check, AlertTriangle, Loader2 } from 'lucide-react';
import { api } from '../api/client';
import type { ArchiveComparison } from '../api/client';
import { Button } from './Button';

interface CompareArchivesModalProps {
  archiveIds: number[];
  onClose: () => void;
}

export function CompareArchivesModal({ archiveIds, onClose }: CompareArchivesModalProps) {
  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const { data: comparison, isLoading, error } = useQuery({
    queryKey: ['archive-comparison', archiveIds],
    queryFn: () => api.compareArchives(archiveIds),
  });

  return (
    <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div className="bg-bambu-dark-secondary rounded-lg max-w-4xl w-full max-h-[90vh] flex flex-col border border-bambu-dark-tertiary" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
          <h3 className="text-lg font-semibold text-white">
            Compare Archives ({archiveIds.length})
          </h3>
          <button
            onClick={onClose}
            className="text-bambu-gray hover:text-white p-1"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-auto p-4 bg-bambu-dark-secondary">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
            </div>
          ) : error ? (
            <div className="text-center py-12 text-red-400">
              <AlertTriangle className="w-12 h-12 mx-auto mb-4 opacity-50" />
              <p>Failed to load comparison</p>
              <p className="text-sm text-bambu-gray mt-2">
                {error instanceof Error ? error.message : 'Unknown error'}
              </p>
            </div>
          ) : comparison ? (
            <ComparisonContent comparison={comparison} />
          ) : null}
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-bambu-dark-tertiary">
          <Button variant="secondary" onClick={onClose} className="w-full">
            Close
          </Button>
        </div>
      </div>
    </div>
  );
}

function ComparisonContent({ comparison }: { comparison: ArchiveComparison }) {
  return (
    <div className="space-y-6">
      {/* Archive Headers */}
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr>
              <th className="text-left text-sm text-bambu-gray font-medium pb-2 pr-4 min-w-[150px]">
                Setting
              </th>
              {comparison.archives.map((archive) => (
                <th
                  key={archive.id}
                  className="text-left text-sm font-medium pb-2 px-2 min-w-[120px]"
                >
                  <div className="text-white truncate max-w-[150px]" title={archive.print_name}>
                    {archive.print_name}
                  </div>
                  <div className={`text-xs ${
                    archive.status === 'completed' ? 'text-status-ok' :
                    archive.status === 'failed' ? 'text-status-error' : 'text-bambu-gray'
                  }`}>
                    {archive.status}
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-bambu-gray/20">
            {comparison.comparison.map((field) => (
              <tr
                key={field.field}
                className={field.has_difference ? 'bg-yellow-500/5' : ''}
              >
                <td className="py-2 pr-4 text-sm">
                  <div className="flex items-center gap-2">
                    {field.has_difference && (
                      <AlertTriangle className="w-3 h-3 text-yellow-400 flex-shrink-0" />
                    )}
                    <span className={field.has_difference ? 'text-yellow-400' : 'text-bambu-gray'}>
                      {field.label}
                    </span>
                  </div>
                </td>
                {field.values.map((value, idx) => (
                  <td key={idx} className="py-2 px-2 text-sm text-white">
                    {value ?? <span className="text-bambu-gray/50">-</span>}
                    {field.unit && value !== null && (
                      <span className="text-bambu-gray ml-1">{field.unit}</span>
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Differences Summary */}
      {comparison.differences.length > 0 && (
        <div className="p-4 bg-yellow-500/10 border border-yellow-500/30 rounded-lg">
          <h4 className="text-sm font-medium text-yellow-400 mb-2 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4" />
            {comparison.differences.length} Difference{comparison.differences.length > 1 ? 's' : ''} Found
          </h4>
          <ul className="text-sm text-white/80 space-y-1">
            {comparison.differences.slice(0, 5).map((diff) => (
              <li key={diff.field}>
                <span className="text-yellow-400">{diff.label}</span>: {diff.values.join(' vs ')} {diff.unit || ''}
              </li>
            ))}
            {comparison.differences.length > 5 && (
              <li className="text-bambu-gray">
                ...and {comparison.differences.length - 5} more
              </li>
            )}
          </ul>
        </div>
      )}

      {/* Success Correlation */}
      {comparison.success_correlation.has_both_outcomes ? (
        <div className="p-4 bg-bambu-dark rounded-lg">
          <h4 className="text-sm font-medium text-white mb-3 flex items-center gap-2">
            <Check className="w-4 h-4 text-bambu-green" />
            Success/Failure Analysis
          </h4>
          <div className="flex items-center gap-4 text-sm mb-3">
            <span className="text-bambu-green">
              {comparison.success_correlation.successful_count} successful
            </span>
            <span className="text-red-400">
              {comparison.success_correlation.failed_count} failed
            </span>
          </div>
          {comparison.success_correlation.insights && comparison.success_correlation.insights.length > 0 ? (
            <div className="space-y-2">
              {comparison.success_correlation.insights.map((insight) => (
                <div key={insight.field} className="text-sm p-2 bg-bambu-dark-secondary rounded">
                  <span className="text-white font-medium">{insight.label}:</span>{' '}
                  <span className="text-white/80">{insight.insight}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-bambu-gray">No clear correlations found between settings and outcomes.</p>
          )}
        </div>
      ) : (
        <div className="p-4 bg-bambu-dark rounded-lg text-sm text-bambu-gray">
          <p>{comparison.success_correlation.message || 'Need both successful and failed prints for correlation analysis.'}</p>
        </div>
      )}
    </div>
  );
}
