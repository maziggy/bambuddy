import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Copy, Eye, EyeOff, KeyRound, Loader2, RefreshCw } from 'lucide-react';
import { multiVirtualPrinterApi } from '../api/client';
import { Card, CardContent } from './Card';
import { ConfirmModal } from './ConfirmModal';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import { copyTextToClipboard } from '../utils/clipboard';

/**
 * The signed-in user's personal access code for virtual printers (#1430),
 * at the top of Settings > Virtual Printer.
 *
 * Entered in their slicer instead of a virtual printer's own access code, it
 * marks what they send as theirs. A virtual printer checks at every sign-in
 * whether they may send to it. Renders nothing while auth is off.
 */
export function SlicerAccessCodeCard() {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const { user, authEnabled } = useAuth();
  const [revealed, setRevealed] = useState(false);
  const [copied, setCopied] = useState(false);
  const [confirmRenew, setConfirmRenew] = useState(false);
  // Per user: on a shared PC the next person to sign in must never see the
  // code cached for the one before.
  const queryKey = ['my-slicer-access-code', user?.id ?? null];
  const enabled = authEnabled && !!user;

  const { data, isLoading, isError } = useQuery({
    queryKey,
    queryFn: multiVirtualPrinterApi.getMyAccessCode,
    enabled,
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

  if (!enabled) return null;

  const copy = async () => {
    if (!data) return;
    if (await copyTextToClipboard(data.code)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } else {
      showToast(t('slicerAccessCode.copyFailed'), 'error');
    }
  };

  const iconButton =
    'inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded bg-bambu-dark-secondary border border-bambu-dark-tertiary text-white hover:border-bambu-gray disabled:opacity-50 transition-colors';

  return (
    <Card>
      <CardContent className="py-3 px-4">
        <div className="flex items-start gap-3">
          <KeyRound className="w-4 h-4 text-bambu-green flex-shrink-0 mt-1" />
          <div className="flex-1 min-w-0">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="text-sm text-white font-medium">{t('slicerAccessCode.title')}</p>
              {isLoading ? (
                <Loader2 className="w-4 h-4 animate-spin text-bambu-green" />
              ) : isError || !data ? (
                <p className="text-xs text-red-600 dark:text-red-400">{t('slicerAccessCode.loadFailed')}</p>
              ) : (
                <div className="flex flex-wrap items-center gap-2">
                  <code
                    data-testid="slicer-access-code"
                    className="font-mono text-sm tracking-[0.25em] text-white bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded px-2 py-0.5"
                  >
                    {revealed ? data.code : '•'.repeat(data.code.length)}
                  </code>
                  <button
                    onClick={() => setRevealed(!revealed)}
                    aria-label={revealed ? t('slicerAccessCode.hide') : t('slicerAccessCode.show')}
                    className={iconButton}
                  >
                    {revealed ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                  </button>
                  <button onClick={copy} aria-label={t('slicerAccessCode.copy')} className={iconButton}>
                    {copied ? <Check className="w-3.5 h-3.5 text-bambu-green" /> : <Copy className="w-3.5 h-3.5" />}
                  </button>
                  <button
                    onClick={() => setConfirmRenew(true)}
                    disabled={renew.isPending}
                    className={iconButton}
                  >
                    {renew.isPending ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <RefreshCw className="w-3.5 h-3.5" />
                    )}
                    {t('slicerAccessCode.renew')}
                  </button>
                </div>
              )}
            </div>
            <p className="text-xs text-bambu-gray mt-1">{t('slicerAccessCode.description')}</p>
            <ul className="text-xs text-bambu-gray list-disc pl-4 mt-1 space-y-0.5">
              <li>{t('slicerAccessCode.hintChecked')}</li>
              <li>{t('slicerAccessCode.hintCamera')}</li>
              <li>{t('slicerAccessCode.hintSecret')}</li>
            </ul>
          </div>
        </div>
      </CardContent>

      {confirmRenew && (
        <ConfirmModal
          title={t('slicerAccessCode.renewTitle')}
          message={t('slicerAccessCode.renewConfirm')}
          confirmText={t('slicerAccessCode.renew')}
          variant="warning"
          onConfirm={() => {
            setConfirmRenew(false);
            renew.mutate();
          }}
          onCancel={() => setConfirmRenew(false)}
        />
      )}
    </Card>
  );
}
