/**
 * Background slice-job tracker.
 *
 * SliceModal calls `trackJob(id, kind)` after enqueuing and closes
 * immediately. This context keeps the job-id list, polls each one, and
 * shows toasts on terminal state. Lives at app level so polling continues
 * across navigation — slice can run in the background while the user does
 * other things.
 *
 * Each tracked job also gets a persistent toast (`slice-job-{id}`) with a
 * spinner + elapsed-time counter that updates every second so the user has
 * a continuous visual indicator while a long slice is running. The toast
 * is replaced by a transient success/error toast on terminal state.
 */
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { api, type SliceJobState, type SliceJobStatus } from '../api/client';
import { useToast } from './ToastContext';

interface TrackedJob {
  id: number;
  kind: 'libraryFile' | 'archive';
  sourceName: string;
}

interface SliceJobTrackerContextValue {
  trackJob: (id: number, kind: 'libraryFile' | 'archive', sourceName: string) => void;
  activeJobs: TrackedJob[];
}

const SliceJobTrackerContext = createContext<SliceJobTrackerContextValue | null>(null);

const POLL_INTERVAL_MS = 1500;
const TICK_INTERVAL_MS = 1000;

const toastIdFor = (jobId: number) => `slice-job-${jobId}`;

function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const remS = s % 60;
  if (m < 60) return `${m}m ${remS}s`;
  const h = Math.floor(m / 60);
  const remM = m % 60;
  return `${h}h ${remM}m`;
}

export function SliceJobTrackerProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const { showToast, showPersistentToast, dismissToast } = useToast();
  const queryClient = useQueryClient();
  const [activeJobs, setActiveJobs] = useState<TrackedJob[]>([]);

  // Stable mutable ref so the polling effect can read the current list
  // without re-subscribing every time it changes.
  const activeJobsRef = useRef<TrackedJob[]>([]);
  activeJobsRef.current = activeJobs;

  // Per-job start time + latest phase, kept in refs so the 1s tick
  // doesn't need to re-render on every update. Keyed by job id.
  const startedAtRef = useRef<Map<number, number>>(new Map());
  const phaseRef = useRef<Map<number, SliceJobStatus>>(new Map());

  const renderProgressToast = useCallback(
    (job: TrackedJob) => {
      const startedAt = startedAtRef.current.get(job.id);
      if (startedAt == null) return;
      const elapsedSecs = (Date.now() - startedAt) / 1000;
      const phase = phaseRef.current.get(job.id) ?? 'pending';
      const messageKey = phase === 'pending' ? 'slice.queuedToast' : 'slice.runningToast';
      const fallback =
        phase === 'pending'
          ? 'Queued: {{name}} — {{elapsed}}'
          : 'Slicing {{name}} — {{elapsed}}';
      showPersistentToast(
        toastIdFor(job.id),
        t(messageKey, fallback, { name: job.sourceName, elapsed: formatElapsed(elapsedSecs) }),
        'loading',
      );
    },
    [showPersistentToast, t],
  );

  const trackJob = useCallback(
    (id: number, kind: 'libraryFile' | 'archive', sourceName: string) => {
      setActiveJobs((prev) => (prev.some((j) => j.id === id) ? prev : [...prev, { id, kind, sourceName }]));
      startedAtRef.current.set(id, Date.now());
      phaseRef.current.set(id, 'pending');
      // Render the initial frame immediately so the user sees the toast
      // before the first tick lands (~1s delay otherwise).
      renderProgressToast({ id, kind, sourceName });
    },
    [renderProgressToast],
  );

  const completeJob = useCallback(
    (job: TrackedJob, state: SliceJobState) => {
      setActiveJobs((prev) => prev.filter((j) => j.id !== job.id));
      startedAtRef.current.delete(job.id);
      phaseRef.current.delete(job.id);

      // Replace the persistent progress toast with a transient
      // success/error toast (auto-dismisses after 3s, same as showToast).
      dismissToast(toastIdFor(job.id));

      if (state.status === 'completed') {
        // `used_embedded_settings` still comes back on the result for tests
        // and observability, but the warning toast that surfaced it was
        // firing on essentially every slice (3MF inputs trigger the
        // embedded-settings fallback as a normal path) and just added
        // noise — see the trailing yellow toast complaint, removed.
        showToast(
          t('slice.completedToast', 'Sliced {{name}}', { name: job.sourceName }),
          'success',
        );
      } else if (state.status === 'failed') {
        const detail = state.error_detail || t('slice.failed');
        showToast(t('slice.failedToast', 'Slicing {{name}} failed: {{detail}}', { name: job.sourceName, detail }), 'error');
      }

      // Refresh whichever list owns the result. Both are cheap to invalidate.
      queryClient.invalidateQueries({ queryKey: ['library-files'] });
      queryClient.invalidateQueries({ queryKey: ['archives'] });
    },
    [dismissToast, queryClient, showToast, t],
  );

  // Status polling. Updates phase on each successful poll and triggers
  // completeJob on terminal states.
  useEffect(() => {
    if (activeJobs.length === 0) return;
    let cancelled = false;
    const interval = setInterval(async () => {
      if (cancelled) return;
      const snapshot = [...activeJobsRef.current];
      for (const job of snapshot) {
        try {
          const state = await api.getSliceJob(job.id);
          phaseRef.current.set(job.id, state.status);
          if (state.status === 'completed' || state.status === 'failed') {
            completeJob(job, state);
          }
        } catch {
          // Transient poll failure — stay tracked, retry next tick.
        }
      }
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [activeJobs.length, completeJob]);

  // 1Hz tick that re-renders each persistent progress toast with the
  // current elapsed time. Independent of the status poll so the counter
  // stays smooth even while the backend is slow to respond.
  useEffect(() => {
    if (activeJobs.length === 0) return;
    const tick = setInterval(() => {
      for (const job of activeJobsRef.current) {
        renderProgressToast(job);
      }
    }, TICK_INTERVAL_MS);
    return () => clearInterval(tick);
  }, [activeJobs.length, renderProgressToast]);

  return (
    <SliceJobTrackerContext.Provider value={{ trackJob, activeJobs }}>
      {children}
    </SliceJobTrackerContext.Provider>
  );
}

export function useSliceJobTracker(): SliceJobTrackerContextValue {
  const ctx = useContext(SliceJobTrackerContext);
  if (!ctx) {
    throw new Error('useSliceJobTracker must be used inside SliceJobTrackerProvider');
  }
  return ctx;
}
