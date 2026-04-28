/**
 * Tests for SliceJobTrackerProvider's persistent progress toast.
 *
 * The tracker shows a persistent loading toast (`slice-job-{id}`) that
 * updates every second with elapsed time + phase label, then is replaced
 * by a transient success/error toast on terminal state. Without the
 * persistent toast, long slices on large models produce a "is it still
 * running?" UX gap between the start toast and the completion toast.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { type ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ToastProvider } from '../../contexts/ToastContext';
import { SliceJobTrackerProvider, useSliceJobTracker } from '../../contexts/SliceJobTrackerContext';
import { api } from '../../api/client';

vi.mock('../../api/client', () => ({
  api: {
    getSliceJob: vi.fn(),
  },
}));

const mockApi = api as unknown as { getSliceJob: ReturnType<typeof vi.fn> };

function Wrapper({ children }: { children: ReactNode }) {
  // A fresh QueryClient per test so invalidateQueries calls don't leak
  // between tests.
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <SliceJobTrackerProvider>{children}</SliceJobTrackerProvider>
      </ToastProvider>
    </QueryClientProvider>
  );
}

function TrackTrigger({ id, name }: { id: number; name: string }) {
  const { trackJob } = useSliceJobTracker();
  return (
    <button onClick={() => trackJob(id, 'libraryFile', name)}>
      track-{id}
    </button>
  );
}

describe('SliceJobTrackerProvider — persistent progress toast', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders a persistent toast immediately when a job is tracked', () => {
    // Job stays running indefinitely — completion path is its own test.
    mockApi.getSliceJob.mockResolvedValue({
      job_id: 1,
      status: 'running',
      kind: 'library_file',
      source_id: 100,
      source_name: 'BigCube.stl',
      created_at: new Date().toISOString(),
      started_at: new Date().toISOString(),
      completed_at: null,
    });

    render(
      <Wrapper>
        <TrackTrigger id={1} name="BigCube.stl" />
      </Wrapper>,
    );

    act(() => {
      screen.getByText('track-1').click();
    });

    // Initial frame: the "Queued" phase before the first poll lands. The
    // toast must be on screen at t=0 without waiting for any tick.
    expect(screen.getByText(/BigCube\.stl/)).toBeDefined();
    expect(screen.getByText(/0s/)).toBeDefined();
  });

  it('updates elapsed time each second while the job is running', async () => {
    let resolveFirstPoll: () => void = () => {};
    const firstPollLanded = new Promise<void>((resolve) => {
      resolveFirstPoll = resolve;
    });

    mockApi.getSliceJob.mockImplementation(async () => {
      resolveFirstPoll();
      return {
        job_id: 2,
        status: 'running',
        kind: 'library_file',
        source_id: 101,
        source_name: 'TallTower.stl',
        created_at: new Date().toISOString(),
        started_at: new Date().toISOString(),
        completed_at: null,
      };
    });

    render(
      <Wrapper>
        <TrackTrigger id={2} name="TallTower.stl" />
      </Wrapper>,
    );

    act(() => {
      screen.getByText('track-2').click();
    });

    // Advance fake time past the 1.5s poll so the phase flips
    // pending→running before we check the message.
    await act(async () => {
      vi.advanceTimersByTime(1500);
    });
    await firstPollLanded;
    // Let any pending promise microtasks drain on the test loop.
    await act(async () => {
      await Promise.resolve();
    });

    // Tick another 5 seconds — elapsed should now be ~6s total since
    // the start, and the toast must reflect it.
    act(() => {
      vi.advanceTimersByTime(5000);
    });

    // 1500ms (poll) + 5000ms (tick) = 6500ms ≈ 6s rounded down.
    expect(screen.getByText(/6s/)).toBeDefined();
    expect(screen.getByText(/TallTower\.stl/)).toBeDefined();
  });

  it('replaces the persistent toast with a transient success toast on completion', async () => {
    let pollCount = 0;
    mockApi.getSliceJob.mockImplementation(async () => {
      pollCount += 1;
      // First poll: running. Second poll: completed.
      if (pollCount === 1) {
        return {
          job_id: 3,
          status: 'running',
          kind: 'library_file',
          source_id: 102,
          source_name: 'Done.stl',
          created_at: new Date().toISOString(),
          started_at: new Date().toISOString(),
          completed_at: null,
        };
      }
      return {
        job_id: 3,
        status: 'completed',
        kind: 'library_file',
        source_id: 102,
        source_name: 'Done.stl',
        created_at: new Date().toISOString(),
        started_at: new Date().toISOString(),
        completed_at: new Date().toISOString(),
      };
    });

    render(
      <Wrapper>
        <TrackTrigger id={3} name="Done.stl" />
      </Wrapper>,
    );

    act(() => {
      screen.getByText('track-3').click();
    });

    // Drive both polls (each 1.5s). After the second, completeJob should
    // dismiss the persistent toast and show the transient success toast.
    await act(async () => {
      vi.advanceTimersByTime(1500);
    });
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      vi.advanceTimersByTime(1500);
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    // The progress toast text "Slicing Done.stl —" / "Queued: Done.stl"
    // should be gone; the success toast "Sliced Done.stl" should be up.
    expect(screen.queryByText(/Slicing Done\.stl —/)).toBeNull();
    expect(screen.queryByText(/Queued: Done\.stl/)).toBeNull();
    expect(screen.getByText(/Sliced Done\.stl/)).toBeDefined();
  });

  it('replaces the persistent toast with a transient error toast on failure', async () => {
    let pollCount = 0;
    mockApi.getSliceJob.mockImplementation(async () => {
      pollCount += 1;
      if (pollCount === 1) {
        return {
          job_id: 4,
          status: 'running',
          kind: 'library_file',
          source_id: 103,
          source_name: 'Broken.stl',
          created_at: new Date().toISOString(),
          started_at: new Date().toISOString(),
          completed_at: null,
        };
      }
      return {
        job_id: 4,
        status: 'failed',
        kind: 'library_file',
        source_id: 103,
        source_name: 'Broken.stl',
        created_at: new Date().toISOString(),
        started_at: new Date().toISOString(),
        completed_at: new Date().toISOString(),
        error_status: 500,
        error_detail: 'sidecar segfault',
      };
    });

    render(
      <Wrapper>
        <TrackTrigger id={4} name="Broken.stl" />
      </Wrapper>,
    );

    act(() => {
      screen.getByText('track-4').click();
    });

    await act(async () => {
      vi.advanceTimersByTime(1500);
    });
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      vi.advanceTimersByTime(1500);
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.queryByText(/Slicing Broken\.stl —/)).toBeNull();
    // The failure detail must surface in the toast — generic "failed"
    // strings stripped of the sidecar reason were the original UX
    // complaint that motivated `_format_sidecar_error` on the backend.
    expect(screen.getByText(/sidecar segfault/)).toBeDefined();
  });
});
