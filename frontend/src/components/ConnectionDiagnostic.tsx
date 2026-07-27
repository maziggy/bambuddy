import { useEffect, useState } from 'react';
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
  api,
  type DiagnosticCheck,
  type DiagnosticStatus,
  type PrinterDiagnosticResult,
} from '../api/client';

function StatusIcon({ status }: { status: DiagnosticStatus }) {
  if (status === 'pass') return <CheckCircle2 className="w-5 h-5 text-bambu-green flex-shrink-0" />;
  if (status === 'fail') return <XCircle className="w-5 h-5 text-red-600 dark:text-red-400 flex-shrink-0" />;
  if (status === 'warn') return <AlertTriangle className="w-5 h-5 text-amber-600 dark:text-amber-400 flex-shrink-0" />;
  return <MinusCircle className="w-5 h-5 text-bambu-gray flex-shrink-0" />;
}

/**
 * Presentational checklist — renders one row per diagnostic check plus an
 * overall banner. Shared by the modal and the bug-report panel. The title
 * and per-status detail text are localized via `diagnostic.check.<id>.*`.
 */
export function DiagnosticChecklist({ result }: { result: PrinterDiagnosticResult }) {
  const { t } = useTranslation();

  const overallClass =
    result.overall === 'ok'
      ? 'bg-bambu-green/10 border-bambu-green/30 text-bambu-green'
      : result.overall === 'warnings'
        ? 'bg-amber-50 dark:bg-amber-500/10 border-amber-300 dark:border-amber-500/30 text-amber-700 dark:text-amber-300'
        : 'bg-red-50 dark:bg-red-500/10 border-red-300 dark:border-red-500/30 text-red-700 dark:text-red-300';

  const renderCheck = (check: DiagnosticCheck) => {
    const params =
      check.id === 'port_rtsps'
        ? { protocol: 'RTSPS', port: 322, ...check.params }
        : check.params;
    // A check may carry a `reason` to select a more specific message variant
    // (e.g. external_storage skip on P1-series → skip_unsupported_model #2524);
    // fall back to the plain per-status text when no variant key exists.
    const reason = (check.params as { reason?: string } | undefined)?.reason;
    const detail = t(
      `diagnostic.check.${check.id}.${check.status}${reason ? `_${reason}` : ''}`,
      {
        ...params,
        defaultValue: reason
          ? t(`diagnostic.check.${check.id}.${check.status}`, { ...params, defaultValue: '' })
          : '',
      },
    );
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
            {t(`diagnostic.check.${check.id}.title`, params)}
          </div>
          {detail && <div className="text-xs text-bambu-gray mt-0.5">{detail}</div>}
        </div>
      </li>
    );
  };

  return (
    <div className="space-y-4">
      <ol className="space-y-2">{result.checks.map(renderCheck)}</ol>
      <div className={`rounded-lg border px-4 py-3 text-sm ${overallClass}`}>
        {t(`diagnostic.overall.${result.overall}`)}
      </div>
    </div>
  );
}

type Connection = {
  ip_address: string;
  serial_number?: string;
  access_code?: string;
};

// Keep in sync with backend `PUBLISH_WAIT_DEFAULT` in
// backend/app/services/printer_diagnostic.py — that's the upper bound on how
// long the existing-printer route waits for the printer's first status report
// after a bridge reconnect. The countdown is purely cosmetic; if the two
// drift the worst case is the hint text being off by a couple of seconds.
const PUBLISH_WAIT_DEFAULT_SECONDS = 10;

type ConnectionDiagnosticModalProps = {
  onClose: () => void;
  printerName?: string | null;
} & ({ printerId: number } | { connection: Connection });

/**
 * Connection diagnostic modal. Opens straight into the test — used from the
 * printer card, the System page, and the Add-Printer flow on failure.
 */
export function ConnectionDiagnosticModal(props: ConnectionDiagnosticModalProps) {
  const { onClose, printerName } = props;
  const { t } = useTranslation();
  const printerId = 'printerId' in props ? props.printerId : undefined;
  const connection = 'connection' in props ? props.connection : undefined;

  const diagnose = useMutation({
    mutationFn: (): Promise<PrinterDiagnosticResult> =>
      printerId !== undefined
        ? api.diagnosePrinter(printerId)
        : api.diagnoseConnection(connection as Connection),
  });

  useEffect(() => {
    diagnose.mutate();
    // Run once on mount — re-running is the explicit "Retry" button.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  // Tick an elapsed-seconds counter while the diagnostic is running so the
  // existing-printer flow (which waits up to PUBLISH_WAIT_DEFAULT_SECONDS for
  // the printer's first status report) doesn't look hung. Resets on each
  // (re)run. No effect on the pre-add flow other than a ticking counter,
  // which is still useful feedback.
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  useEffect(() => {
    if (!diagnose.isPending) {
      setElapsedSeconds(0);
      return;
    }
    const startedAt = Date.now();
    const interval = window.setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000));
    }, 500);
    return () => window.clearInterval(interval);
  }, [diagnose.isPending]);

  const result = diagnose.data as PrinterDiagnosticResult | undefined;

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
              {t('diagnostic.modalTitle', { name: printerName || '' })}
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
            <div className="space-y-1.5">
              <div className="flex items-center gap-2 text-bambu-gray">
                <Loader2 className="w-4 h-4 animate-spin" />
                <span>
                  {elapsedSeconds > 0
                    ? t('diagnostic.runningElapsed', { elapsed: elapsedSeconds })
                    : t('diagnostic.running')}
                </span>
              </div>
              {printerId !== undefined && (
                <p className="text-xs text-bambu-gray-light pl-6">
                  {t('diagnostic.waitingForReportHint', { max: PUBLISH_WAIT_DEFAULT_SECONDS })}
                </p>
              )}
            </div>
          )}

          {diagnose.isError && (
            <div className="rounded-lg bg-red-50 dark:bg-red-500/10 border border-red-300 dark:border-red-500/30 px-4 py-3 text-sm text-red-700 dark:text-red-300">
              {t('diagnostic.runFailed', { error: (diagnose.error as Error).message })}
            </div>
          )}

          {result && <DiagnosticChecklist result={result} />}
        </div>

        <div className="px-6 py-4 border-t border-bambu-dark-tertiary flex justify-end gap-2">
          <button
            onClick={() => diagnose.mutate()}
            disabled={diagnose.isPending}
            className="px-4 py-2 bg-bambu-dark hover:bg-bambu-dark-tertiary disabled:opacity-50 text-white text-sm rounded-lg transition-colors"
          >
            {t('diagnostic.retry')}
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
