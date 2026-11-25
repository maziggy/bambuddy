import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ThumbsUp, ThumbsDown, X, RotateCcw, ImageOff } from 'lucide-react';
import { api } from '../api/client';
import { FAILURE_REASON_KEYS } from './EditArchiveModal';
import { verdictSourceKey } from '../utils/verdictSource';
import { PrintModal } from './PrintModal';
import { useToast } from '../contexts/ToastContext';

interface ConfirmOutcomeDialogProps {
  archiveId: number;
  // The run asked about: plates of one archive each have their own question.
  // Omitted (an archive card's menu, an older link) means its latest run.
  runId?: number | null;
  // Opened to change a verdict already given (the archive window's "Change
  // outcome"): the choice is offered again instead of only stating it.
  change?: boolean;
  onClose: () => void;
}

/**
 * Post-print outcome confirmation (#1898), Formlabs-style: finish photo plus
 * Good / Reject. Opens from the print_confirm_request WebSocket event (via
 * Layout), from the pending badge on an archive card, and from the
 * ?confirm=<id> deep link a push notification carries. Rejecting offers an
 * optional reason and a one-click reprint through the regular PrintModal.
 */
export function ConfirmOutcomeDialog({ archiveId, runId, change = false, onClose }: ConfirmOutcomeDialogProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [rejecting, setRejecting] = useState(false);
  const [rejectReason, setRejectReason] = useState('');
  const [showReprint, setShowReprint] = useState(false);

  const { data: archive } = useQuery({
    queryKey: ['archive', archiveId, runId ?? null],
    queryFn: () => api.getArchive(archiveId, runId),
  });
  // The archive the answer goes to: a link to one merged into another
  // (archive redesign P2) loads that one, and the merged id writes nothing.
  const targetId = archive?.id ?? archiveId;

  const verdictMutation = useMutation({
    mutationFn: (data: { user_verdict: 'good' | 'reject'; failure_reason?: string }) =>
      api.updateArchive(targetId, {
        ...data,
        user_verdict_source: 'dialog',
        ...(runId != null ? { run_id: runId } : {}),
      }),
    onSuccess: (_updated, variables) => {
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      queryClient.invalidateQueries({ queryKey: ['archive', archiveId] });
      queryClient.invalidateQueries({ queryKey: ['archive', targetId] });
      // The archive page lists each print with its verdict.
      queryClient.invalidateQueries({ queryKey: ['archive-runs', targetId] });
      showToast(
        variables.user_verdict === 'good'
          ? t('confirmOutcome.savedGood')
          : t('confirmOutcome.savedReject'),
        'success',
      );
      if (variables.user_verdict === 'good') {
        onClose();
      }
    },
    onError: () => showToast(t('confirmOutcome.saveFailed'), 'error'),
  });

  const name = archive?.print_name || archive?.filename || '';
  // The finish photo is prepended to archive.photos at print end (#1397);
  // fall back to the thumbnail when no camera shot exists.
  const photoFilename = archive?.photos?.[0] ?? null;
  const photoUrl = photoFilename ? api.getArchivePhotoUrl(targetId, photoFilename) : null;
  const verdict = archive?.user_verdict ?? null;
  const alreadyDecided = verdict != null && !change;
  // Why this print already has a verdict — the plate-clear default answers
  // prompts on its own, and without this the answer looks like it appeared
  // from nowhere (#1898).
  const sourceKey = verdictSourceKey(archive?.user_verdict_source);

  const handleGood = () => verdictMutation.mutate({ user_verdict: 'good' });
  const handleRejectSave = (reprint: boolean) => {
    verdictMutation.mutate(
      { user_verdict: 'reject', ...(rejectReason ? { failure_reason: rejectReason } : {}) },
      {
        onSuccess: () => {
          if (reprint) {
            setShowReprint(true);
          } else {
            onClose();
          }
        },
      },
    );
  };

  if (showReprint && archive) {
    return (
      <PrintModal
        mode="create"
        archiveId={archiveId}
        archiveName={name}
        preselectPlateId={archive.plate_id}
        onClose={onClose}
      />
    );
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" data-testid="confirm-outcome-dialog">
      <div className="bg-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary w-full max-w-md overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-bambu-dark-tertiary">
          <h3 className="text-white font-semibold">{change ? t('archivePage.changeOutcome') : t('confirmOutcome.title')}</h3>
          <button onClick={onClose} className="text-bambu-gray hover:text-white transition-colors" title={t('common.close')}>
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Photo */}
        <div className="bg-bambu-dark aspect-video flex items-center justify-center overflow-hidden">
          {photoUrl ? (
            <img src={photoUrl} alt={name} className="w-full h-full object-contain" />
          ) : (
            <div className="flex flex-col items-center gap-2 text-bambu-gray/60">
              <ImageOff className="w-10 h-10" />
              <span className="text-xs">{t('confirmOutcome.noPhoto')}</span>
            </div>
          )}
        </div>

        <div className="p-4">
          <p className="text-sm text-white text-center mb-1 truncate" title={name}>{name}</p>

          {alreadyDecided ? (
            <>
              <p className="text-sm text-bambu-gray text-center">
                {verdict === 'good'
                  ? t('confirmOutcome.alreadyGood')
                  : t('confirmOutcome.alreadyRejected')}
              </p>
              {sourceKey && (
                <p className="text-xs text-bambu-gray/70 text-center mt-1" data-testid="verdict-source-hint">
                  {t(sourceKey)}
                </p>
              )}
            </>
          ) : !rejecting ? (
            <div className="flex items-center justify-center gap-6 mt-3">
              <button
                type="button"
                onClick={() => setRejecting(true)}
                disabled={verdictMutation.isPending}
                aria-pressed={change ? verdict === 'reject' : undefined}
                className={`flex flex-col items-center gap-1.5 px-6 py-3 rounded-xl border border-red-500/40 text-red-500 hover:bg-red-500/10 transition-colors ${change && verdict === 'reject' ? 'bg-red-500/10 ring-2 ring-red-500/60' : ''}`}
              >
                <ThumbsDown className="w-7 h-7" />
                <span className="text-xs">{t('confirmOutcome.reject')}</span>
              </button>
              <button
                type="button"
                onClick={handleGood}
                disabled={verdictMutation.isPending}
                aria-pressed={change ? verdict === 'good' : undefined}
                className={`flex flex-col items-center gap-1.5 px-6 py-3 rounded-xl border border-bambu-green/50 text-bambu-green hover:bg-bambu-green/10 transition-colors ${change && verdict === 'good' ? 'bg-bambu-green/10 ring-2 ring-bambu-green/60' : ''}`}
              >
                <ThumbsUp className="w-7 h-7" />
                <span className="text-xs">{t('confirmOutcome.good')}</span>
              </button>
            </div>
          ) : (
            <div className="mt-3">
              <label className="block text-xs text-bambu-gray mb-1">{t('confirmOutcome.rejectReason')}</label>
              <select
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green"
              >
                <option value="">{t('confirmOutcome.noReason')}</option>
                {FAILURE_REASON_KEYS.map((key) => (
                  <option key={key} value={key}>{t(`editArchive.failureReasons.${key}`)}</option>
                ))}
              </select>
              <div className="flex gap-2 mt-3">
                <button
                  type="button"
                  onClick={() => handleRejectSave(false)}
                  disabled={verdictMutation.isPending}
                  className="flex-1 px-3 py-2 rounded-lg bg-red-500/80 hover:bg-red-500 text-white text-sm transition-colors"
                >
                  {t('confirmOutcome.saveReject')}
                </button>
                <button
                  type="button"
                  onClick={() => handleRejectSave(true)}
                  disabled={verdictMutation.isPending}
                  className="flex-1 px-3 py-2 rounded-lg bg-bambu-dark border border-bambu-dark-tertiary hover:border-bambu-green/60 text-white text-sm transition-colors flex items-center justify-center gap-1.5"
                >
                  <RotateCcw className="w-4 h-4" />
                  {t('confirmOutcome.rejectAndReprint')}
                </button>
              </div>
              <button
                type="button"
                onClick={() => setRejecting(false)}
                className="w-full mt-2 text-xs text-bambu-gray hover:text-white transition-colors"
              >
                {t('common.back')}
              </button>
            </div>
          )}

          {!alreadyDecided && !rejecting && (
            <button
              type="button"
              onClick={onClose}
              className="w-full mt-4 text-xs text-bambu-gray hover:text-white transition-colors"
            >
              {change ? t('common.cancel') : t('confirmOutcome.later')}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
