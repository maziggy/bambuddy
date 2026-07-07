import { useEffect } from 'react';
import { useMutation } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  X,
  Stethoscope,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  MinusCircle,
  Loader2,
} from 'lucide-react';
import {
  multiVirtualPrinterApi,
  type VPDiagnosticCheck,
  type VPDiagnosticStatus,
  type VPDiagnosticResult,
} from '../api/client';

function StatusIcon({ status }: { status: VPDiagnosticStatus }) {
  if (status === 'pass') return <CheckCircle2 className="w-5 h-5 text-bambu-green flex-shrink-0" />;
  if (status === 'fail') return <XCircle className="w-5 h-5 text-red-600 dark:text-red-400 flex-shrink-0" />;
  if (status === 'warn') return <AlertTriangle className="w-5 h-5 text-amber-600 dark:text-amber-400 flex-shrink-0" />;
  return <MinusCircle className="w-5 h-5 text-bambu-gray flex-shrink-0" />;
}

/**
 * Setup-check modal for a single virtual printer. Opens straight into the
 * check (run on mount); "Run again" re-runs it. Each row's title and fix
 * text are localized via `vpDiagnostic.check.<id>.*`.
 */
export function VirtualPrinterDiagnosticModal({
  vpId,
  vpName,
  onClose,
}: {
  vpId: number;
  vpName: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();

  const diagnose = useMutation({
    mutationFn: (): Promise<VPDiagnosticResult> => multiVirtualPrinterApi.diagnose(vpId),
  });

  useEffect(() => {
    diagnose.mutate();
    // Run once on mount — re-running is the explicit "Run again" button.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const result = diagnose.data;

  const overallClass =
    result?.overall === 'ok'
      ? 'bg-bambu-green/10 border-bambu-green/30 text-bambu-green'
      : result?.overall === 'warnings'
        ? 'bg-amber-50 dark:bg-amber-500/10 border-amber-300 dark:border-amber-500/30 text-amber-700 dark:text-amber-300'
        : 'bg-red-50 dark:bg-red-500/10 border-red-300 dark:border-red-500/30 text-red-700 dark:text-red-300';

  const renderCheck = (check: VPDiagnosticCheck) => {
    const detail = t(`vpDiagnostic.check.${check.id}.${check.status}`, {
      ...check.params,
      defaultValue: '',
    });
    return (
      <li
        key={check.id}
        className={`flex items-start gap-3 bg-bambu-dark rounded-lg px-4 py-2.5 ${
          check.status === 'skip' ? 'opacity-60' : ''
        }`}
      >
        <div className="mt-0.5">
          <StatusIcon status={check.status} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-sm text-white">
            {t(`vpDiagnostic.check.${check.id}.title`, check.params)}
          </div>
          {detail && <div className="text-xs text-bambu-gray mt-0.5">{detail}</div>}
        </div>
      </li>
    );
  };

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div
        className="bg-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary w-full max-w-lg flex flex-col max-h-[85vh]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-bambu-dark-tertiary">
          <div className="flex items-center gap-2 min-w-0">
            <Stethoscope className="w-5 h-5 text-bambu-green flex-shrink-0" />
            <h2 className="text-lg font-semibold text-white truncate">
              {t('vpDiagnostic.title', { name: vpName })}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="text-bambu-gray hover:text-white transition-colors"
            title={t('common.close')}
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-6 space-y-4 overflow-y-auto">
          {diagnose.isPending && (
            <div className="flex items-center gap-2 text-bambu-gray">
              <Loader2 className="w-4 h-4 animate-spin" />
              <span>{t('vpDiagnostic.running')}</span>
            </div>
          )}

          {diagnose.isError && (
            <div className="rounded-lg bg-red-50 dark:bg-red-500/10 border border-red-300 dark:border-red-500/30 px-4 py-3 text-sm text-red-700 dark:text-red-300">
              {t('vpDiagnostic.runFailed', { error: (diagnose.error as Error).message })}
            </div>
          )}

          {result && (
            <div className="space-y-4">
              <ol className="space-y-2">{result.checks.map(renderCheck)}</ol>
              <div className={`rounded-lg border px-4 py-3 text-sm ${overallClass}`}>
                {t(`vpDiagnostic.overall.${result.overall}`)}
              </div>
            </div>
          )}
        </div>

        <div className="px-6 py-4 border-t border-bambu-dark-tertiary flex justify-end gap-2">
          <button
            onClick={() => diagnose.mutate()}
            disabled={diagnose.isPending}
            className="px-4 py-2 bg-bambu-dark hover:bg-bambu-dark-tertiary disabled:opacity-50 text-white text-sm rounded-lg transition-colors"
          >
            {t('vpDiagnostic.retry')}
          </button>
          <button
            onClick={onClose}
            className="px-4 py-2 bg-bambu-green hover:bg-bambu-green/90 text-white text-sm rounded-lg transition-colors"
          >
            {t('common.close')}
          </button>
        </div>
      </div>
    </div>
  );
}
