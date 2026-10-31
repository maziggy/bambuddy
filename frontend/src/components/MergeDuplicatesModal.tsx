import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Combine, Loader2, X } from 'lucide-react';

import { api } from '../api/client';
import type { ArchiveMergeGroup, ArchiveMergeMember, ArchiveMergeResult } from '../api/client';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';

interface MergeDuplicatesModalProps {
  onClose: () => void;
}

function memberName(member: ArchiveMergeMember): string {
  return member.print_name || member.filename;
}

/**
 * "Merge duplicates" (archive redesign P2): the archives that hold the same
 * file merge into the oldest. The server finds the groups and says which it
 * leaves out for now; the user picks the groups, confirms, and gets a report.
 */
export function MergeDuplicatesModal({ onClose }: MergeDuplicatesModalProps) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const previewQuery = useQuery({
    queryKey: ['archive-merge-preview'],
    queryFn: () => api.previewArchiveMerge(),
    // Each opening asks again: groups change as prints start and end. Not
    // behind the user's back while it is open: what they confirm is what
    // they saw.
    staleTime: 0,
    gcTime: 0,
    refetchOnWindowFocus: false,
  });
  const groups = useMemo(() => previewQuery.data?.groups ?? [], [previewQuery.data]);
  const mergeable = useMemo(() => groups.filter((g) => g.skipped === null), [groups]);
  const waiting = useMemo(() => groups.filter((g) => g.skipped !== null), [groups]);

  // Every mergeable group is ticked once the preview arrives.
  const [unticked, setUnticked] = useState<Set<number>>(new Set());
  const selected = mergeable.filter((g) => !unticked.has(g.survivor.id));
  const [confirming, setConfirming] = useState(false);
  // The groups the merge itself left out, and why, shown before closing.
  const [result, setResult] = useState<ArchiveMergeResult | null>(null);

  const mergeMutation = useMutation({
    mutationFn: () => api.mergeArchives(selected.map((g) => g.survivor.id)),
    onSuccess: (result: ArchiveMergeResult) => {
      const merged = result.merged.length;
      const left = result.skipped.length;
      // Runs moved between archives: every list of archives and runs.
      for (const key of ['archives', 'archive', 'archiveStats', 'archives-stats', 'print-log', 'archive-runs', 'archive-merge-count']) {
        queryClient.invalidateQueries({ queryKey: [key] });
      }
      if (left === 0) {
        showToast(t('archiveMerge.toast.merged', { count: merged }), 'success');
        onClose();
      } else {
        showToast(t('archiveMerge.toast.mergedSome', { count: merged, left }), merged > 0 ? 'warning' : 'error');
        setResult(result);
      }
    },
    onError: (e: Error) => showToast(e.message || t('archiveMerge.toast.failed'), 'error'),
  });

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !mergeMutation.isPending) onClose();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onClose, mergeMutation.isPending]);

  const toggle = (group: ArchiveMergeGroup) => {
    setConfirming(false);
    setUnticked((current) => {
      const next = new Set(current);
      if (next.has(group.survivor.id)) next.delete(group.survivor.id);
      else next.add(group.survivor.id);
      return next;
    });
  };

  const formatDate = (iso: string | null) =>
    iso ? new Date(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`).toLocaleDateString(i18n.language) : '';

  const renderGroup = (group: ArchiveMergeGroup) => {
    const ticked = group.skipped === null && !unticked.has(group.survivor.id);
    return (
      <li
        key={group.survivor.id}
        className={`rounded border border-gray-200 dark:border-gray-700 p-3 ${group.skipped ? 'opacity-60' : ''}`}
        data-testid={`merge-group-${group.survivor.id}`}
      >
        <label className={`flex gap-2 items-start ${group.skipped ? '' : 'cursor-pointer'}`}>
          {group.skipped === null && (
            <input
              type="checkbox"
              checked={ticked}
              onChange={() => toggle(group)}
              disabled={mergeMutation.isPending}
              className="mt-1 shrink-0"
              aria-label={t('archiveMerge.selectGroup', { name: memberName(group.survivor) })}
            />
          )}
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">
              {memberName(group.survivor)}
            </div>
            <div className="text-xs text-gray-500 dark:text-gray-400">
              {t('archiveMerge.keeps', {
                date: formatDate(group.survivor.created_at),
                count: group.survivor.run_count,
              })}
            </div>
            <ul className="mt-1 text-xs text-gray-600 dark:text-gray-400 space-y-0.5 list-disc pl-4">
              {group.merged.map((member) => (
                <li key={member.id} className="truncate">
                  {t('archiveMerge.mergesIn', {
                    name: memberName(member),
                    date: formatDate(member.created_at),
                    count: member.run_count,
                  })}
                </li>
              ))}
            </ul>
            {group.skipped && (
              <div className="mt-1 text-xs text-amber-700 dark:text-amber-400">
                {t(`archiveMerge.skipped.${group.skipped}`)}
              </div>
            )}
          </div>
        </label>
      </li>
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="bg-white dark:bg-gray-900 rounded-lg shadow-xl max-w-lg w-full max-h-[90vh] flex flex-col">
        <div className="flex items-center justify-between p-4 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-2">
            <Combine className="w-5 h-5" />
            {t('archiveMerge.title')}
          </h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
            aria-label={t('common.close')}
            disabled={mergeMutation.isPending}
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-4 space-y-4 overflow-y-auto">
          <div className="rounded border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/30 p-3">
            <div className="text-xs font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-300 mb-2">
              {t('archiveMerge.effectsTitle')}
            </div>
            <ul className="text-xs text-gray-700 dark:text-gray-300 space-y-1 list-disc pl-4">
              <li>{t('archiveMerge.effect1')}</li>
              <li>{t('archiveMerge.effect2')}</li>
              <li>{t('archiveMerge.effect3')}</li>
            </ul>
          </div>

          {result ? (
            <div data-testid="merge-result">
              <div className="text-xs font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-300 mb-2">
                {t('archiveMerge.leftOutTitle')}
              </div>
              <ul className="space-y-2">
                {result.skipped.map((skip) => {
                  const group = groups.find((g) => g.survivor.id === skip.survivor_id);
                  return (
                    <li
                      key={skip.survivor_id}
                      className="rounded border border-gray-200 dark:border-gray-700 p-3 text-sm text-gray-900 dark:text-gray-100"
                    >
                      <div className="font-medium truncate">{group ? memberName(group.survivor) : `#${skip.survivor_id}`}</div>
                      <div className="mt-1 text-xs text-amber-700 dark:text-amber-400">
                        {t(`archiveMerge.skipped.${skip.reason}`)}
                      </div>
                    </li>
                  );
                })}
              </ul>
            </div>
          ) : previewQuery.isLoading ? (
            <div className="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
              <Loader2 className="w-4 h-4 animate-spin" /> {t('archiveMerge.previewLoading')}
            </div>
          ) : previewQuery.isError ? (
            <div className="text-sm text-red-600 dark:text-red-400">
              {(previewQuery.error as Error | null)?.message ?? t('archiveMerge.previewFailed')}
            </div>
          ) : groups.length === 0 ? (
            <div className="text-sm text-gray-600 dark:text-gray-400">{t('archiveMerge.none')}</div>
          ) : (
            <>
              {mergeable.length > 0 && <ul className="space-y-2">{mergeable.map(renderGroup)}</ul>}
              {waiting.length > 0 && (
                <div>
                  <div className="text-xs font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-300 mb-2">
                    {t('archiveMerge.waitingTitle')}
                  </div>
                  <ul className="space-y-2">{waiting.map(renderGroup)}</ul>
                </div>
              )}
            </>
          )}

          {confirming && !result && (
            <div className="flex gap-2 items-start text-xs text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 rounded px-3 py-2">
              <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
              <span>{t('archiveMerge.confirmWarning', { count: selected.length })}</span>
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 p-4 border-t border-gray-200 dark:border-gray-700">
          <Button variant="secondary" onClick={onClose} disabled={mergeMutation.isPending}>
            {result ? t('common.close') : t('common.cancel')}
          </Button>
          {!result && (
          <Button
            disabled={selected.length === 0 || mergeMutation.isPending}
            onClick={() => (confirming ? mergeMutation.mutate() : setConfirming(true))}
          >
            {mergeMutation.isPending ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin mr-1" />
                {t('archiveMerge.merging')}
              </>
            ) : confirming ? (
              t('archiveMerge.confirmCta', { count: selected.length })
            ) : (
              t('archiveMerge.mergeCta', { count: selected.length })
            )}
          </Button>
          )}
        </div>
      </div>
    </div>
  );
}
