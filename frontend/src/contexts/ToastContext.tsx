import { AlertCircle, CheckCircle, ChevronDown, ChevronUp, Info, Loader2, X, XCircle } from 'lucide-react';
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { api } from '../api/client';

type ToastType = 'success' | 'error' | 'warning' | 'info' | 'loading';

interface Toast {
  id: string;
  message: string;
  type: ToastType;
  persistent?: boolean;
  dispatchData?: DispatchToastData;
}

type DispatchJobStatus = 'dispatched' | 'processing' | 'completed' | 'failed' | 'cancelled';

interface DispatchToastJob {
  jobId: number;
  sourceName: string;
  printerName: string;
  status: DispatchJobStatus;
  message?: string;
  uploadBytes?: number;
  uploadTotalBytes?: number;
  uploadProgressPct?: number;
}

interface DispatchToastData {
  total: number;
  dispatched: number;
  processing: number;
  completed: number;
  failed: number;
  jobs: DispatchToastJob[];
}

interface ToastContextType {
  showToast: (message: string, type?: ToastType) => void;
  showPersistentToast: (id: string, message: string, type?: ToastType) => void;
  dismissToast: (id: string) => void;
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

export function ToastProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [isDispatchCollapsed, setIsDispatchCollapsed] = useState(false);
  const [cancellingDispatchJobIds, setCancellingDispatchJobIds] = useState<Set<number>>(new Set());
  const timeoutRefs = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  const dispatchToastId = 'background-dispatch';
  const lastDispatchSummaryRef = useRef<string | null>(null);

  const formatBytes = useCallback((bytes: number) => {
    if (!Number.isFinite(bytes) || bytes < 0) return '0 B';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
  }, []);

  // Clean up all timeouts on unmount
  useEffect(() => {
    const timeouts = timeoutRefs.current;
    return () => {
      timeouts.forEach((timeout) => clearTimeout(timeout));
      timeouts.clear();
    };
  }, []);

  const showToast = useCallback((message: string, type: ToastType = 'success') => {
    const id = Math.random().toString(36).substr(2, 9);
    setToasts((prev) => [...prev, { id, message, type }]);

    // Auto-dismiss after 3 seconds
    const timeout = setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
      timeoutRefs.current.delete(id);
    }, 3000);
    timeoutRefs.current.set(id, timeout);
  }, []);

  const showPersistentToast = useCallback((id: string, message: string, type: ToastType = 'info') => {
    setToasts((prev) => {
      // Update existing toast if same id, otherwise add new one
      const exists = prev.find((t) => t.id === id);
      if (exists) {
        return prev.map((t) => (t.id === id ? { ...t, message, type, persistent: true } : t));
      }
      return [...prev, { id, message, type, persistent: true }];
    });
  }, []);

  const dismissToast = useCallback((id: string) => {
    // Clear any pending auto-dismiss timeout
    const timeout = timeoutRefs.current.get(id);
    if (timeout) {
      clearTimeout(timeout);
      timeoutRefs.current.delete(id);
    }
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const cancelDispatchJob = useCallback(async (jobId: number) => {
    setCancellingDispatchJobIds((prev) => {
      const next = new Set(prev);
      next.add(jobId);
      return next;
    });

    try {
      const result = await api.cancelBackgroundDispatchJob(jobId);
      showToast(
        result.status === 'cancelling'
          ? t('backgroundDispatch.toast.cancellingUpload')
          : t('backgroundDispatch.toast.cancelled'),
        'info'
      );
    } catch (error) {
      const message = error instanceof Error ? error.message : t('backgroundDispatch.toast.cancelFailed');
      showToast(message, 'error');
    } finally {
      setCancellingDispatchJobIds((prev) => {
        const next = new Set(prev);
        next.delete(jobId);
        return next;
      });
    }
  }, [showToast, t]);

  useEffect(() => {
    interface DispatchEventDetail {
      total?: number;
      dispatched?: number;
      processing?: number;
      completed?: number;
      failed?: number;
      dispatched_jobs?: Array<{
        job_id: number;
        source_name?: string;
        printer_name?: string;
      }>;
      active_job?: {
        job_id?: number;
        printer_name?: string;
        source_name?: string;
        message?: string;
        upload_bytes?: number;
        upload_total_bytes?: number;
        upload_progress_pct?: number;
      } | null;
      active_jobs?: Array<{
        job_id?: number;
        printer_name?: string;
        source_name?: string;
        message?: string;
        upload_bytes?: number;
        upload_total_bytes?: number;
        upload_progress_pct?: number;
      }>;
      recent_event?: {
        status?: string;
        job_id?: number;
        source_name?: string;
        printer_name?: string;
        message?: string;
      };
    }

    const updateJob = (
      jobs: DispatchToastJob[],
      jobId: number,
      next: Partial<DispatchToastJob> & {
        status: DispatchJobStatus;
        sourceName: string;
        printerName: string;
      }
    ) => {
      const index = jobs.findIndex((job) => job.jobId === jobId);
      if (index === -1) {
        return [...jobs, { jobId, ...next }];
      }
      const copy = [...jobs];
      copy[index] = {
        ...copy[index],
        ...next,
      };
      return copy;
    };

    const statusWeight = (status: DispatchJobStatus) => {
      switch (status) {
        case 'failed':
          return 0;
        case 'processing':
          return 1;
        case 'dispatched':
          return 2;
        case 'completed':
          return 3;
        case 'cancelled':
          return 4;
      }
    };

    const onDispatchEvent = (event: Event) => {
      const detail = (event as CustomEvent<DispatchEventDetail>).detail || {};
      const total = detail.total ?? 0;
      const dispatched = detail.dispatched ?? 0;
      const processing = detail.processing ?? 0;
      const completed = detail.completed ?? 0;
      const failed = detail.failed ?? 0;

      const hasActiveWork = dispatched + processing > 0;
      const allDone = total > 0 && completed + failed >= total && !hasActiveWork;

      if (hasActiveWork) {
        setToasts((prev) => {
          const existing = prev.find((t) => t.id === dispatchToastId);
          const existingJobs = existing?.dispatchData?.jobs || [];

          const dispatchedJobs: DispatchToastJob[] = (detail.dispatched_jobs || []).map((job) => ({
            jobId: job.job_id,
            sourceName: job.source_name || t('backgroundDispatch.unknownFile'),
            printerName: job.printer_name || t('backgroundDispatch.unknownPrinter'),
            status: 'dispatched',
          }));

          const activeJobsPayload =
            detail.active_jobs && detail.active_jobs.length > 0
              ? detail.active_jobs
              : detail.active_job?.job_id
                ? [detail.active_job]
                : [];

          const activeJobs: DispatchToastJob[] = activeJobsPayload
            .filter((job) => typeof job.job_id === 'number')
            .map((job) => ({
              jobId: job.job_id as number,
              sourceName: job.source_name || t('backgroundDispatch.unknownFile'),
              printerName: job.printer_name || t('backgroundDispatch.unknownPrinter'),
              status: 'processing',
              message: job.message,
              uploadBytes: job.upload_bytes,
              uploadTotalBytes: job.upload_total_bytes,
              uploadProgressPct: job.upload_progress_pct,
            }));

          const activeIds = new Set([...dispatchedJobs, ...activeJobs].map((job) => job.jobId));
          const historicalJobs = existingJobs.filter(
            (job) => !activeIds.has(job.jobId) && ['completed', 'failed', 'cancelled'].includes(job.status)
          );

          let jobs = [...dispatchedJobs, ...activeJobs, ...historicalJobs];

          if (detail.recent_event?.job_id && detail.recent_event?.status) {
            const rawStatus = detail.recent_event.status;
            const eventStatus = (
              rawStatus === 'cancelled' ? 'cancelled' : rawStatus === 'cancelling' ? 'processing' : rawStatus
            ) as DispatchJobStatus;
            const sourceName = detail.recent_event.source_name || t('backgroundDispatch.unknownFile');
            const printerName = detail.recent_event.printer_name || t('backgroundDispatch.unknownPrinter');
            jobs = updateJob(jobs, detail.recent_event.job_id, {
              status: eventStatus,
              sourceName,
              printerName,
              message: detail.recent_event.message,
            });
          }

          activeJobs.forEach((activeJob) => {
            jobs = updateJob(jobs, activeJob.jobId, {
              status: 'processing',
              sourceName: activeJob.sourceName,
              printerName: activeJob.printerName,
              message: activeJob.message,
              uploadBytes: activeJob.uploadBytes,
              uploadTotalBytes: activeJob.uploadTotalBytes,
              uploadProgressPct: activeJob.uploadProgressPct,
            });
          });

          const dispatchData: DispatchToastData = {
            total,
            dispatched,
            processing,
            completed,
            failed,
            jobs: [...jobs].sort((a, b) => {
              const byStatus = statusWeight(a.status) - statusWeight(b.status);
              if (byStatus !== 0) {
                return byStatus;
              }
              return a.jobId - b.jobId;
            }),
          };

          const exists = prev.find((t) => t.id === dispatchToastId);
          if (exists) {
            return prev.map((t) =>
              t.id === dispatchToastId
                ? {
                    ...t,
                    message: t('backgroundDispatch.startingPrints'),
                    type: 'loading',
                    persistent: true,
                    dispatchData,
                  }
                : t
            );
          }
          return [
            ...prev,
            {
              id: dispatchToastId,
              message: t('backgroundDispatch.startingPrints'),
              type: 'loading',
              persistent: true,
              dispatchData,
            },
          ];
        });
        return;
      }

      const recentStatus = detail.recent_event?.status;
      if (!hasActiveWork && recentStatus && ['cancelled', 'failed', 'completed', 'idle'].includes(recentStatus)) {
        setToasts((prev) => prev.filter((t) => t.id !== dispatchToastId));
      }

      if (allDone) {
        const summaryKey = `${completed}:${failed}`;
        if (lastDispatchSummaryRef.current === summaryKey) {
          return;
        }
        lastDispatchSummaryRef.current = summaryKey;

        setToasts((prev) => prev.filter((t) => t.id !== dispatchToastId));
        const doneMessage = failed > 0
          ? t('backgroundDispatch.toast.completeWithFailures', { completed, failed })
          : t('backgroundDispatch.toast.completeSuccess', { completed });
        const id = Math.random().toString(36).substr(2, 9);
        setToasts((prev) => [...prev, { id, message: doneMessage, type: failed > 0 ? 'warning' : 'success' }]);
        const timeout = setTimeout(() => {
          setToasts((prev) => prev.filter((t) => t.id !== id));
          timeoutRefs.current.delete(id);
        }, 4000);
        timeoutRefs.current.set(id, timeout);
      }

      if (detail.recent_event?.status === 'idle' && !hasActiveWork) {
        setToasts((prev) => prev.filter((t) => t.id !== dispatchToastId));
      }

      if (!hasActiveWork) {
        setCancellingDispatchJobIds(new Set());
      }

      if (detail.dispatched_jobs) {
        const dispatchedIds = new Set(detail.dispatched_jobs.map((job) => job.job_id));
        setCancellingDispatchJobIds((prev) => {
          const next = new Set<number>();
          prev.forEach((id) => {
            if (dispatchedIds.has(id)) {
              next.add(id);
            }
          });
          return next;
        });
      }
    };

    window.addEventListener('background-dispatch', onDispatchEvent);
    return () => window.removeEventListener('background-dispatch', onDispatchEvent);
  }, [t]);

  return (
    <ToastContext.Provider value={{ showToast, showPersistentToast, dismissToast }}>
      {children}

      {/* Toast Container */}
      <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={`rounded-lg border shadow-lg backdrop-blur-sm animate-slide-in ${bgColors[toast.type]} ${
              toast.dispatchData ? 'w-[420px] p-3' : 'flex items-center gap-3 px-4 py-3'
            }`}
          >
            {toast.dispatchData ? (
              <>
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-start gap-2">
                    {icons[toast.type]}
                    <div>
                      <p className="text-white text-sm font-medium">{t('backgroundDispatch.startingPrints')}</p>
                      <p className="text-xs text-bambu-gray mt-0.5">
                        {t('backgroundDispatch.progressSummary', {
                          complete: toast.dispatchData.completed + toast.dispatchData.failed,
                          total: toast.dispatchData.total,
                          dispatched: toast.dispatchData.dispatched,
                          processing: toast.dispatchData.processing,
                        })}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => setIsDispatchCollapsed((prev) => !prev)}
                      className="text-bambu-gray hover:text-white transition-colors"
                      aria-label={
                        isDispatchCollapsed
                          ? t('backgroundDispatch.expandDetails')
                          : t('backgroundDispatch.collapseDetails')
                      }
                    >
                      {isDispatchCollapsed ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                    </button>
                    <button
                      onClick={() => dismissToast(toast.id)}
                      className="text-bambu-gray hover:text-white transition-colors"
                      aria-label={t('backgroundDispatch.dismissToast')}
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {!isDispatchCollapsed && (
                  <div className="mt-3 space-y-2 max-h-64 overflow-y-auto pr-1">
                    {toast.dispatchData.jobs.map((job) => {
                      const progressByStatus: Record<DispatchJobStatus, number> = {
                        dispatched: 15,
                        processing: 60,
                        completed: 100,
                        failed: 100,
                        cancelled: 100,
                      };
                      const barColorByStatus: Record<DispatchJobStatus, string> = {
                        dispatched: 'bg-bambu-gray/60',
                        processing: 'bg-bambu-green',
                        completed: 'bg-green-500',
                        failed: 'bg-red-500',
                        cancelled: 'bg-yellow-500',
                      };
                      return (
                        <div key={job.jobId} className="rounded border border-white/10 bg-black/15 p-2">
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-xs text-white truncate" title={job.sourceName}>
                              {job.sourceName}
                            </span>
                            <div className="flex items-center gap-2">
                              {(job.status === 'dispatched' || job.status === 'processing') && (
                                <button
                                  onClick={() => void cancelDispatchJob(job.jobId)}
                                  disabled={cancellingDispatchJobIds.has(job.jobId)}
                                  className="text-[11px] text-red-300 hover:text-red-200 disabled:opacity-50 disabled:cursor-not-allowed"
                                  title={t('backgroundDispatch.cancelDispatchJob')}
                                >
                                  {cancellingDispatchJobIds.has(job.jobId)
                                    ? t('backgroundDispatch.cancelling')
                                    : t('backgroundDispatch.cancel')}
                                </button>
                              )}
                              <span className="text-[11px] uppercase tracking-wide text-bambu-gray">
                                {t(`backgroundDispatch.status.${job.status}`)}
                              </span>
                            </div>
                          </div>
                          <div className="text-[11px] text-bambu-gray truncate" title={job.printerName}>
                            {job.printerName}
                          </div>
                          {job.message && (
                            <div className="text-[11px] text-bambu-gray truncate" title={job.message}>
                              {job.message}
                            </div>
                          )}
                          {job.status === 'processing' && typeof job.uploadBytes === 'number' && typeof job.uploadTotalBytes === 'number' && job.uploadTotalBytes > 0 && (
                            <div className="text-[11px] text-bambu-gray truncate">
                              {formatBytes(job.uploadBytes)} / {formatBytes(job.uploadTotalBytes)}
                              {typeof job.uploadProgressPct === 'number' ? ` (${job.uploadProgressPct.toFixed(1)}%)` : ''}
                            </div>
                          )}
                          <div className="mt-1 h-1.5 w-full rounded bg-white/10 overflow-hidden">
                            <div
                              className={`h-full ${barColorByStatus[job.status]} transition-all duration-300`}
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
