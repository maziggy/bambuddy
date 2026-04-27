/**
 * Background slice-job tracker.
 *
 * SliceModal calls `trackJob(id, kind)` after enqueuing and closes
 * immediately. This context keeps the job-id list, polls each one, and
 * shows toasts on terminal state. Lives at app level so polling continues
 * across navigation — slice can run in the background while the user does
 * other things.
 */
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { api, type SliceJobState } from '../api/client';
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

export function SliceJobTrackerProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [activeJobs, setActiveJobs] = useState<TrackedJob[]>([]);

  // Stable mutable ref so the polling effect can read the current list
  // without re-subscribing every time it changes.
  const activeJobsRef = useRef<TrackedJob[]>([]);
  activeJobsRef.current = activeJobs;

  const trackJob = useCallback(
    (id: number, kind: 'libraryFile' | 'archive', sourceName: string) => {
      setActiveJobs((prev) => (prev.some((j) => j.id === id) ? prev : [...prev, { id, kind, sourceName }]));
      showToast(t('slice.startedToast', 'Slicing {{name}} in the background…', { name: sourceName }), 'info');
    },
    [showToast, t],
  );

  const completeJob = useCallback(
    (job: TrackedJob, state: SliceJobState) => {
      setActiveJobs((prev) => prev.filter((j) => j.id !== job.id));

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
    [queryClient, showToast, t],
  );

  useEffect(() => {
    if (activeJobs.length === 0) return;
    let cancelled = false;
    const interval = setInterval(async () => {
      if (cancelled) return;
      // Snapshot the current list so concurrent updates don't surprise us.
      const snapshot = [...activeJobsRef.current];
      for (const job of snapshot) {
        try {
          const state = await api.getSliceJob(job.id);
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
