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

  // The backend now exposes a `progress` field on each slice-job poll
  // result, fed by the sidecar's --pipe channel. When a useful frame
  // is present the toast must show "{name} — {stage} ({percent}%) —
  // {elapsed}" so the user sees concrete progress instead of a wall of
  // elapsed time.
  it('weaves stage + percent into the toast when the sidecar reports progress', async () => {
    let pollCount = 0;
    mockApi.getSliceJob.mockImplementation(async () => {
      pollCount += 1;
      // First poll: running with a useful progress frame.
      if (pollCount === 1) {
        return {
          job_id: 7,
          status: 'running',
          kind: 'library_file',
          source_id: 200,
          source_name: 'Helmet.3mf',
          created_at: new Date().toISOString(),
          started_at: new Date().toISOString(),
          completed_at: null,
          progress: {
            stage: 'Generating G-code',
            total_percent: 75,
            plate_percent: 80,
            plate_index: 1,
            plate_count: 1,
            updated_at: Date.now(),
          },
        };
      }
      // Subsequent polls keep the same frame so the test loop stays stable.
      return {
        job_id: 7,
        status: 'running',
        kind: 'library_file',
        source_id: 200,
        source_name: 'Helmet.3mf',
        created_at: new Date().toISOString(),
        started_at: new Date().toISOString(),
        completed_at: null,
        progress: {
          stage: 'Generating G-code',
          total_percent: 75,
          plate_percent: 80,
          plate_index: 1,
          plate_count: 1,
          updated_at: Date.now(),
        },
      };
    });

    render(
      <Wrapper>
        <TrackTrigger id={7} name="Helmet.3mf" />
      </Wrapper>,
    );

    act(() => {
      screen.getByText('track-7').click();
    });

    // Drive the 1.5s poll so the progress frame lands in the ref, then
    // tick the 1s renderer so the toast picks it up.
    await act(async () => {
      vi.advanceTimersByTime(1500);
    });
    await act(async () => {
      await Promise.resolve();
    });
    act(() => {
      vi.advanceTimersByTime(1000);
    });

    // Toast contains the stage + percent + filename.
    const text = screen.getByText(/Generating G-code/);
    expect(text.textContent).toMatch(/Helmet\.3mf/);
    expect(text.textContent).toMatch(/75%/);
  });

  it('falls back to elapsed-time message when progress is null', async () => {
    // Sidecar without --pipe support / pre-progress feature: state.progress
    // stays null and the toast shows the existing "Slicing X — 47s" text.
    mockApi.getSliceJob.mockResolvedValue({
      job_id: 8,
      status: 'running',
      kind: 'library_file',
      source_id: 201,
      source_name: 'OldSidecar.3mf',
      created_at: new Date().toISOString(),
      started_at: new Date().toISOString(),
      completed_at: null,
      progress: null,
    });

    render(
      <Wrapper>
        <TrackTrigger id={8} name="OldSidecar.3mf" />
      </Wrapper>,
    );

    act(() => {
      screen.getByText('track-8').click();
    });

    await act(async () => {
      vi.advanceTimersByTime(1500);
    });
    await act(async () => {
      await Promise.resolve();
    });
    act(() => {
      vi.advanceTimersByTime(1000);
    });

    // The "Slicing X — Ns" / "Queued: X — Ns" fallback must still render
    // — the absence of progress mustn't blank the toast.
    expect(screen.getByText(/OldSidecar\.3mf/)).toBeDefined();
    // No progress percent is shown when null.
    expect(screen.queryByText(/%/)).toBeNull();
  });
});
