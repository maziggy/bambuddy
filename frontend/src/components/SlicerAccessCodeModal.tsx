import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Copy, Eye, EyeOff, Loader2, Printer, RefreshCw, X } from 'lucide-react';
import { multiVirtualPrinterApi } from '../api/client';
import { Card, CardContent } from './Card';
import { Button } from './Button';
import { ConfirmModal } from './ConfirmModal';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import { copyTextToClipboard } from '../utils/clipboard';

/**
 * The signed-in user's personal access code for virtual printers (#1430).
 *
 * Entered in their slicer instead of a virtual printer's own access code, it
 * marks what they send as theirs. A virtual printer checks at every sign-in
 * whether they may send to it.
 */
export function SlicerAccessCodeModal({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [revealed, setRevealed] = useState(false);
  const [copied, setCopied] = useState(false);
  const [confirmRenew, setConfirmRenew] = useState(false);
  // Per user: on a shared PC the next person to sign in must never see the
  // code cached for the one before.
  const { user } = useAuth();
  const queryKey = ['my-slicer-access-code', user?.id ?? null];

  const { data, isLoading, isError } = useQuery({
    queryKey,
    queryFn: multiVirtualPrinterApi.getMyAccessCode,
    gcTime: 0,
  });

  const renew = useMutation({
    mutationFn: multiVirtualPrinterApi.renewMyAccessCode,
    onSuccess: (result) => {
      queryClient.setQueryData(queryKey, result);
      setRevealed(true);
      showToast(t('slicerAccessCode.renewed'));
    },
    onError: (error: Error) => showToast(error.message || t('slicerAccessCode.loadFailed'), 'error'),
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !confirmRenew) onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, confirmRenew]);

  const copy = async () => {
    if (!data) return;
    if (await copyTextToClipboard(data.code)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } else {
      showToast(t('slicerAccessCode.copyFailed'), 'error');
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center p-4 z-50" onClick={onClose}>
      <Card className="w-full max-w-md" onClick={(e: React.MouseEvent) => e.stopPropagation()}>
        <CardContent className="p-6 space-y-4">
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-center gap-2">
              <Printer className="w-5 h-5 text-bambu-green" />
              <h3 className="text-lg font-semibold text-white">{t('slicerAccessCode.title')}</h3>
            </div>
            <button
              onClick={onClose}
              aria-label={t('common.close')}
              className="p-1 text-bambu-gray hover:text-white transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          <p className="text-sm text-bambu-gray">{t('slicerAccessCode.description')}</p>

          {isLoading ? (
            <div className="flex justify-center py-4">
              <Loader2 className="w-6 h-6 animate-spin text-bambu-green" />
            </div>
          ) : isError || !data ? (
            <p className="text-sm text-red-600 dark:text-red-400">{t('slicerAccessCode.loadFailed')}</p>
          ) : (
            <div className="flex items-center gap-2">
              <code
                data-testid="slicer-access-code"
                className="flex-1 text-center text-2xl font-mono tracking-[0.3em] text-white bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-md py-2"
              >
                {revealed ? data.code : '•'.repeat(data.code.length)}
              </code>
              <button
                onClick={() => setRevealed(!revealed)}
                aria-label={revealed ? t('slicerAccessCode.hide') : t('slicerAccessCode.show')}
                className="p-2 text-bambu-gray hover:text-white transition-colors"
              >
                {revealed ? <EyeOff className="w-5 h-5" /> : <Eye className="w-5 h-5" />}
              </button>
              <button
                onClick={copy}
                aria-label={t('slicerAccessCode.copy')}
                className="p-2 text-bambu-gray hover:text-white transition-colors"
              >
                {copied ? <Check className="w-5 h-5 text-bambu-green" /> : <Copy className="w-5 h-5" />}
              </button>
            </div>
          )}

          <ul className="text-xs text-bambu-gray list-disc pl-4 space-y-1">
            <li>{t('slicerAccessCode.hintChecked')}</li>
            <li>{t('slicerAccessCode.hintCamera')}</li>
            <li>{t('slicerAccessCode.hintSecret')}</li>
          </ul>

          <div className="flex justify-end">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setConfirmRenew(true)}
              disabled={!data || renew.isPending}
            >
              {renew.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
              {t('slicerAccessCode.renew')}
            </Button>
          </div>
        </CardContent>
      </Card>

      {confirmRenew && (
        <div onClick={(e) => e.stopPropagation()}>
          <ConfirmModal
            title={t('slicerAccessCode.renewTitle')}
            message={t('slicerAccessCode.renewConfirm')}
            confirmText={t('slicerAccessCode.renew')}
            variant="warning"
            overlayZIndex="z-[60]"
            onConfirm={() => {
              setConfirmRenew(false);
              renew.mutate();
            }}
            onCancel={() => setConfirmRenew(false)}
          />
        </div>
      )}
    </div>
  );
}
