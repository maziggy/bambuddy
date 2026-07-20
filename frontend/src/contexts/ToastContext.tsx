import { AlertCircle, CheckCircle, ChevronDown, ChevronUp, Info, Loader2, X, XCircle } from 'lucide-react';
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { formatFileSize } from '../utils/file';

type ToastType = 'success' | 'error' | 'warning' | 'info' | 'loading';

// Dispatch-toast types — ported verbatim from
// 0b43ac0d:frontend/src/contexts/ToastContext.tsx. The visual rendering
// block below is the legacy code 1:1; the only swap is the event ingestion
// (now sourced from `bambuddy:dispatch-toast` window events that
// useWebSocket forwards from the four backend WS event types added in
// the #1625 follow-up). Same shape, same DOM, same styling, same i18n
// surface — guarantees the modal looks identical to the pre-scheduler
// experience that users remember.
type DispatchJobStatus = 'processing' | 'completed' | 'failed';

interface DispatchToastJob {
  jobId: number;
  sourceName: string;
  printerName: string;
  status: DispatchJobStatus;
  uploadBytes?: number;
  uploadTotalBytes?: number;
  uploadProgressPct?: number;
  failReason?: string;
}

interface DispatchToastData {
  total: number;
  processing: number;
  completed: number;
  failed: number;
  jobs: DispatchToastJob[];
}

interface ToastAction {
  label: string;
  href: string;
  onClick?: () => void;
}

type ShowPersistentToast = (
  id: string,
  message: string,
  type?: ToastType,
  options?: { action?: ToastAction },
) => void;

interface Toast {
  id: string;
  message: string;
  type: ToastType;
  persistent?: boolean;
  action?: ToastAction;
  dispatchData?: DispatchToastData;
}

interface ToastContextType {
  showToast: (message: string, type?: ToastType) => void;
  showPersistentToast: ShowPersistentToast;
  dismissToast: (id: string) => void;
  setViewportSuppressed: (suppressed: boolean) => void;
}

const ToastContext = createContext<ToastContextType | undefined>(undefined);

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error('useToast must be used within a ToastProvider');
  }
  return context;
}

const icons = {
  success: <CheckCircle className="w-5 h-5 text-green-400" />,
  error: <XCircle className="w-5 h-5 text-red-400" />,
  warning: <AlertCircle className="w-5 h-5 text-yellow-400" />,
  info: <Info className="w-5 h-5 text-blue-400" />,
  loading: <Loader2 className="w-5 h-5 text-bambu-green animate-spin" />,
};

const bgColors = {
  success: 'bg-green-500/10 border-green-500/30',
  error: 'bg-red-500/10 border-red-500/30',
  warning: 'bg-yellow-500/10 border-yellow-500/30',
  info: 'bg-blue-500/10 border-blue-500/30',
  loading: 'bg-bambu-green/10 border-bambu-green/30',
};

const DISPATCH_TOAST_ID = 'background-dispatch';
const DISPATCH_TERMINAL_DISMISS_MS = 3500;

interface DispatchEventDetail {
  type: string;
  queue_item_id: number;
  printer_id?: number | null;
  printer_name?: string | null;
  file_name?: string;
  total_bytes?: number;
  bytes_transferred?: number;
  pct?: number;
  reason?: string;
}

function isAwaitingPrinter(job: DispatchToastJob): boolean {
  // Same trick the legacy code used to derive "Awaiting printer…" without
  // a separate status. While the job is still 'processing' AND upload pct
  // has reached 99.9%, the printer hasn't yet acked our project_file.
  return (
    job.status === 'processing'
    && typeof job.uploadProgressPct === 'number'
    && job.uploadProgressPct >= 99.9
  );
}

function recomputeAggregate(jobs: DispatchToastJob[]): DispatchToastData {
  return {
    total: jobs.length,
    processing: jobs.filter((j) => j.status === 'processing').length,
    completed: jobs.filter((j) => j.status === 'completed').length,
    failed: jobs.filter((j) => j.status === 'failed').length,
    jobs,
  };
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [viewportSuppressed, setViewportSuppressed] = useState(false);
  const [isDispatchCollapsed, setIsDispatchCollapsed] = useState(false);
  const timeoutRefs = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  // Tracks whether the provider is still mounted. A toast can be triggered by
  // an async callback that resolves AFTER React has unmounted us (common in
  // tests: `cleanup()` runs while a login promise is still in flight, then
  // the error handler calls showToast). In that case, scheduling a setTimeout
  // that later calls setToasts produces "window is not defined" once the jsdom
  // environment is torn down. Guard every setToasts call behind this ref so a
  // post-unmount showToast is a no-op instead of crashing.
  const isMountedRef = useRef(true);

  // Clean up all timeouts on unmount
  useEffect(() => {
    isMountedRef.current = true;
    const timeouts = timeoutRefs.current;
    return () => {
      isMountedRef.current = false;
      timeouts.forEach((timeout) => clearTimeout(timeout));
      timeouts.clear();
    };
  }, []);

  const showToast = useCallback((message: string, type: ToastType = 'success') => {
    if (!isMountedRef.current) return;
    const id = Math.random().toString(36).substr(2, 9);
    setToasts((prev) => [...prev, { id, message, type }]);

    // Auto-dismiss after 3 seconds
    const timeout = setTimeout(() => {
      if (!isMountedRef.current) return;
      setToasts((prev) => prev.filter((t) => t.id !== id));
      timeoutRefs.current.delete(id);
    }, 3000);
    timeoutRefs.current.set(id, timeout);
  }, []);

  const showPersistentToast = useCallback(
    (id: string, message: string, type: ToastType = 'info', options?: { action?: ToastAction }) => {
      if (!isMountedRef.current) return;
      setToasts((prev) => {
        // Update existing toast if same id, otherwise add new one
        const exists = prev.find((t) => t.id === id);
        if (exists) {
          return prev.map((t) =>
            t.id === id ? { ...t, message, type, persistent: true, action: options?.action } : t,
          );
        }
        return [...prev, { id, message, type, persistent: true, action: options?.action }];
      });
    },
    [],
  );

  const dismissToast = useCallback((id: string) => {
    if (!isMountedRef.current) return;
    // Clear any pending auto-dismiss timeout
    const timeout = timeoutRefs.current.get(id);
    if (timeout) {
      clearTimeout(timeout);
      timeoutRefs.current.delete(id);
    }
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  // Dispatch-toast ingestion. The four event types from the backend
  // (queue_item_uploading / upload_progress / acked / failed) map to
  // the legacy DispatchToastJob shape, then the same auto-dismiss +
  // aggregate-recompute logic from 0b43ac0d takes over.
  useEffect(() => {
    const onDispatchEvent = (event: Event) => {
      if (!isMountedRef.current) return;
      const detail = (event as CustomEvent<DispatchEventDetail>).detail;
      if (!detail || typeof detail.queue_item_id !== 'number') return;
      const jobId = detail.queue_item_id;

      setToasts((prev) => {
        const existing = prev.find((toastItem) => toastItem.id === DISPATCH_TOAST_ID);
        const existingJobs = existing?.dispatchData?.jobs ?? [];
        const existingJobIndex = existingJobs.findIndex((j) => j.jobId === jobId);
        const existingJob = existingJobIndex >= 0 ? existingJobs[existingJobIndex] : undefined;

        let nextJob: DispatchToastJob | null = null;
        const sourceName =
          detail.file_name
          || existingJob?.sourceName
          || t('dispatchToast.untitled');
        const printerName =
          detail.printer_name
          || existingJob?.printerName
          || (detail.printer_id ? `Printer ${detail.printer_id}` : '');

        switch (detail.type) {
          case 'queue_item_uploading':
            // Materialization point — job appears here, never on queue-add.
            nextJob = {
              jobId,
              sourceName,
              printerName,
              status: 'processing',
              uploadBytes: 0,
              uploadTotalBytes: detail.total_bytes,
              uploadProgressPct: 0,
            };
            break;
          case 'queue_item_upload_progress':
            if (!existingJob) return prev;
            nextJob = {
              ...existingJob,
              uploadBytes: detail.bytes_transferred,
              uploadTotalBytes: detail.total_bytes ?? existingJob.uploadTotalBytes,
              uploadProgressPct: detail.pct,
            };
            break;
          case 'queue_item_acked':
            if (!existingJob) return prev;
            nextJob = {
              ...existingJob,
              status: 'completed',
              uploadProgressPct: 100,
            };
            break;
          case 'queue_item_failed':
            if (!existingJob) return prev;
            nextJob = {
              ...existingJob,
              status: 'failed',
              failReason: detail.reason,
            };
            break;
          default:
            return prev;
        }

        // Compose the updated jobs list
        let updatedJobs: DispatchToastJob[];
        if (existingJob) {
          updatedJobs = [...existingJobs];
          updatedJobs[existingJobIndex] = nextJob;
        } else {
          updatedJobs = [...existingJobs, nextJob];
        }

        const dispatchData = recomputeAggregate(updatedJobs);

        const toastShape: Toast = {
          id: DISPATCH_TOAST_ID,
          message: t('dispatchToast.startingPrints'),
          type: 'loading',
          persistent: true,
          dispatchData,
        };

        if (existing) {
          return prev.map((toastItem) =>
            toastItem.id === DISPATCH_TOAST_ID ? toastShape : toastItem,
          );
        }
        return [...prev, toastShape];
      });
    };

    window.addEventListener('bambuddy:dispatch-toast', onDispatchEvent);
    return () => window.removeEventListener('bambuddy:dispatch-toast', onDispatchEvent);
  }, [t]);

  // Auto-dismiss the wrapper once every job has reached a terminal state.
  useEffect(() => {
    const dispatchToast = toasts.find((tst) => tst.id === DISPATCH_TOAST_ID);
    if (!dispatchToast?.dispatchData) return;
    const data = dispatchToast.dispatchData;
    if (data.total === 0 || data.processing !== 0) return;
    const existing = timeoutRefs.current.get(DISPATCH_TOAST_ID);
    if (existing) clearTimeout(existing);
    const timeout = setTimeout(() => {
      if (!isMountedRef.current) return;
      setToasts((prev) => prev.filter((tst) => tst.id !== DISPATCH_TOAST_ID));
      timeoutRefs.current.delete(DISPATCH_TOAST_ID);
    }, DISPATCH_TERMINAL_DISMISS_MS);
    timeoutRefs.current.set(DISPATCH_TOAST_ID, timeout);
  }, [toasts]);

  return (
    <ToastContext.Provider value={{ showToast, showPersistentToast, dismissToast, setViewportSuppressed }}>
      {children}

      {/* Toast Container — to the left of the bug-report bubble (bottom-4 right-4 w-12).
          The kiosk layout suppresses this entire viewport so SpoolBuddy displays stay
          free of main-app notifications.
          Position is set via safe-area-aware calc() rather than bottom-4/right-20 so an
          installed PWA on a notched phone clears the home indicator / landscape notch
          (#2612): the 5rem right offset keeps clearance for the bug bubble. */}
      <div
        data-testid="toast-viewport"
        className={`fixed z-[60] flex flex-col items-end gap-2 ${viewportSuppressed ? 'hidden' : ''}`}
        style={{
          bottom: 'calc(1rem + env(safe-area-inset-bottom))',
          right: 'calc(5rem + env(safe-area-inset-right))',
        }}
      >
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={`rounded-lg border shadow-lg backdrop-blur-sm animate-slide-in ${bgColors[toast.type]} ${
              toast.dispatchData ? 'w-[420px] p-3' : 'flex items-center gap-3 px-4 py-3'
            }`}
            // Cap width to the viewport so the fixed-width dispatch toast (420px)
            // can't run off the left edge on a phone (#2612). At the cap the toast
            // sits 1rem + safe-area from the left; on desktop the 420px wins. The
            // 6rem = the 5rem right offset above + a 1rem left gutter.
            style={{
              maxWidth:
                'calc(100vw - 6rem - env(safe-area-inset-left) - env(safe-area-inset-right))',
            }}
            data-testid={toast.dispatchData ? 'dispatch-toast-wrapper' : undefined}
          >
            {toast.dispatchData ? (
              // Legacy dispatch-toast rendering — verbatim port from
              // 0b43ac0d:frontend/src/contexts/ToastContext.tsx lines
              // 515–650. Same DOM, same Tailwind classes, same uppercase
              // status chip, same `awaitingPrinter` derivation. Only
              // diff vs legacy: no cancel button (the BG dispatch
              // cancel endpoint doesn't exist in the scheduler model).
              <>
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-start gap-2">
                    {icons[toast.type]}
                    <div>
                      <p className="text-white text-sm font-medium">{t('dispatchToast.startingPrints')}</p>
                      <p className="text-xs text-bambu-gray mt-0.5">
                        {t('dispatchToast.progressSummary', {
                          complete: toast.dispatchData.completed + toast.dispatchData.failed,
                          total: toast.dispatchData.total,
                          processing: toast.dispatchData.processing,
                        })}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => setIsDispatchCollapsed((prev) => !prev)}
                      className="text-bambu-gray hover:text-white transition-colors"
                      aria-label={isDispatchCollapsed ? t('dispatchToast.expandDetails') : t('dispatchToast.collapseDetails')}
                      data-testid="dispatch-toast-collapse"
                    >
                      {isDispatchCollapsed ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                    </button>
                    <button
                      onClick={() => dismissToast(toast.id)}
                      className="text-bambu-gray hover:text-white transition-colors"
                      aria-label={t('dispatchToast.dismiss')}
                      data-testid="dispatch-toast-dismiss"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {!isDispatchCollapsed && (
                  <div className="mt-3 space-y-2 max-h-64 overflow-y-auto pr-1">
                    {toast.dispatchData.jobs.map((job) => {
                      const uploadDoneAwaitingPrinter = isAwaitingPrinter(job);
                      const barColorByStatus: Record<DispatchJobStatus, string> = {
                        processing: 'bg-bambu-green',
                        completed: 'bg-green-500',
                        failed: 'bg-red-500',
                      };
                      const progressByStatus: Record<DispatchJobStatus, number> = {
                        processing: 60,
                        completed: 100,
                        failed: 100,
                      };
                      return (
                        <div
                          key={job.jobId}
                          className="rounded border border-white/10 bg-black/15 p-2"
                          data-testid={`dispatch-toast-job-${job.jobId}`}
                        >
                          <div className="flex items-center justify-between gap-2">
                            {/* min-w-0 + flex-1 lets truncate actually kick in
                                when the toast is capped to a phone's width
                                (#2612); the status chip stays put with shrink-0. */}
                            <span className="text-xs text-white truncate min-w-0 flex-1" title={job.sourceName}>
                              {job.sourceName}
                            </span>
                            <span
                              className="text-[11px] uppercase tracking-wide text-bambu-gray shrink-0"
                              data-testid={`dispatch-toast-status-${job.jobId}`}
                            >
                              {t(`dispatchToast.status.${job.status}`)}
                            </span>
                          </div>
                          {job.printerName && (
                            <div className="text-[11px] text-bambu-gray truncate" title={job.printerName}>
                              {job.printerName}
                            </div>
                          )}
                          {job.status === 'processing' ? (
                            uploadDoneAwaitingPrinter ? (
                              <div className="text-[11px] text-bambu-gray truncate">
                                {t('dispatchToast.awaitingPrinter')}
                              </div>
                            ) : typeof job.uploadBytes === 'number'
                                && typeof job.uploadTotalBytes === 'number'
                                && job.uploadTotalBytes > 0 ? (
                              <div className="text-[11px] text-bambu-gray truncate">
                                {formatFileSize(job.uploadBytes)} / {formatFileSize(job.uploadTotalBytes)}
                                {typeof job.uploadProgressPct === 'number' ? ` (${job.uploadProgressPct.toFixed(1)}%)` : ''}
                              </div>
                            ) : null
                          ) : job.status === 'failed' && job.failReason ? (
                            <div className="text-[11px] text-red-400 truncate">
                              {t(`dispatchToast.failed.${job.failReason}`, { defaultValue: t('dispatchToast.failed.generic') })}
                            </div>
                          ) : null}
                          <div className="mt-1 h-1.5 w-full rounded bg-white/10 overflow-hidden">
                            <div
                              className={`h-full ${barColorByStatus[job.status]} transition-all duration-300 ${uploadDoneAwaitingPrinter ? 'animate-pulse' : ''}`}
                              style={{
                                width: `${
                                  job.status === 'processing' && typeof job.uploadProgressPct === 'number'
                                    ? Math.max(0, Math.min(100, job.uploadProgressPct))
                                    : progressByStatus[job.status]
                                }%`,
                              }}
                            />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </>
            ) : (
              <>
                {icons[toast.type]}
                <span className="text-white text-sm">{toast.message}</span>
                {toast.action && (
                  <a
                    href={toast.action.href}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={() => {
                      toast.action?.onClick?.();
                      dismissToast(toast.id);
                    }}
                    className="ml-2 px-2 py-1 rounded text-xs font-medium bg-bambu-green/20 text-bambu-green hover:bg-bambu-green/30 whitespace-nowrap"
                  >
                    {toast.action.label}
                  </a>
                )}
                <button
                  onClick={() => dismissToast(toast.id)}
                  className="ml-2 text-bambu-gray hover:text-white transition-colors"
                >
                  <X className="w-4 h-4" />
                </button>
              </>
            )}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
