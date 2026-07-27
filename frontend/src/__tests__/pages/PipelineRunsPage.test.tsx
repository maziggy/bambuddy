/**
 * Tests for PipelineRunsPage — dashboard for Slicer Pipeline runs (#1425 PR C).
 *
 * Pin the load → list → expand → cancel/retry flow. We mock the api client
 * so we can exercise the dashboard without hitting the WS subscription.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { PipelineRunsView } from '../../pages/PipelineRunsPage';
import { api, type PipelineRun } from '../../api/client';

vi.mock('../../api/client', () => ({
  api: {
    listSlicerPipelines: vi.fn(),
    listAllPipelineRuns: vi.fn(),
    cancelPipelineRun: vi.fn(),
    retryFailedPipelineRun: vi.fn(),
    // Bootstrap calls — ThemeContext / AuthContext touch these on mount.
    getSettings: vi.fn().mockResolvedValue({}),
    updateSettings: vi.fn().mockResolvedValue({}),
  },
}));

const mockApi = api as unknown as {
  listSlicerPipelines: ReturnType<typeof vi.fn>;
  listAllPipelineRuns: ReturnType<typeof vi.fn>;
  cancelPipelineRun: ReturnType<typeof vi.fn>;
  retryFailedPipelineRun: ReturnType<typeof vi.fn>;
};

function makeRun(overrides: Partial<PipelineRun> = {}): PipelineRun {
  return {
    id: 1,
    pipeline_id: 10,
    pipeline_name: 'Production Batch',
    source_library_file_id: 99,
    source_archive_id: null,
    source_filename: 'cube.3mf',
    parent_run_id: null,
    copies: 3,
    copies_completed: 1,
    copies_failed: 0,
    copies_cancelled: 0,
    copies_in_progress: 2,
    status: 'in_progress',
    slice_job_id: 4242,
    sliced_library_file_id: 100,
    eligibility_overridden: false,
    error_message: null,
    created_by: null,
    created_at: '2026-06-27T15:00:00Z',
    started_at: '2026-06-27T15:00:01Z',
    completed_at: null,
    jobs: [
      {
        id: 1,
        pipeline_run_id: 1,
        copy_index: 0,
        assigned_printer_id: 5,
        assigned_printer_name: 'X1C #1',
        queue_entry_id: 200,
        status: 'completed',
        error_message: null,
        dispatched_at: '2026-06-27T15:00:02Z',
        completed_at: '2026-06-27T15:30:00Z',
      },
      {
        id: 2,
        pipeline_run_id: 1,
        copy_index: 1,
        assigned_printer_id: 6,
        assigned_printer_name: 'X1C #2',
        queue_entry_id: 201,
        status: 'printing',
        error_message: null,
        dispatched_at: '2026-06-27T15:00:02Z',
        completed_at: null,
      },
    ],
    target_kind: 'printer_class',
    target_printer_id: null,
    target_model_class: 'X1C',
    fanout_strategy: 'max_parallel',
    ...overrides,
  };
}

describe('PipelineRunsView', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockApi.listSlicerPipelines.mockResolvedValue({ pipelines: [] });
    mockApi.listAllPipelineRuns.mockResolvedValue({ runs: [], total: 0 });
  });

  it('renders empty state when no runs exist', async () => {
    render(<PipelineRunsView />);
    await waitFor(() => {
      expect(screen.getByText(/No pipeline runs yet/i)).toBeInTheDocument();
    });
  });

  it('lists runs and surfaces the pipeline name + status chip', async () => {
    mockApi.listAllPipelineRuns.mockResolvedValue({ runs: [makeRun()], total: 1 });
    render(<PipelineRunsView />);
    await waitFor(() => {
      expect(screen.getByText(/Production Batch/i)).toBeInTheDocument();
      // Status chip uses the i18n key.
      expect(screen.getAllByText(/printing/i).length).toBeGreaterThan(0);
    });
  });

  it('shows a Cancel button on in-flight runs and fires the mutation', async () => {
    mockApi.listAllPipelineRuns.mockResolvedValue({ runs: [makeRun()], total: 1 });
    mockApi.cancelPipelineRun.mockResolvedValue(makeRun({ status: 'cancelled' }));
    render(<PipelineRunsView />);
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByText(/Production Batch/i)).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: /Cancel/i }));
    await waitFor(() => expect(mockApi.cancelPipelineRun).toHaveBeenCalledWith(1));
  });

  it('shows a Retry-failed button on partial_failure runs and fires the mutation', async () => {
    mockApi.listAllPipelineRuns.mockResolvedValue({
      runs: [
        makeRun({
          status: 'partial_failure',
          copies_completed: 1,
          copies_failed: 2,
          copies_in_progress: 0,
        }),
      ],
      total: 1,
    });
    mockApi.retryFailedPipelineRun.mockResolvedValue(makeRun({ id: 2, parent_run_id: 1, copies: 2 }));
    render(<PipelineRunsView />);
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByText(/Production Batch/i)).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: /Retry failed/i }));
    await waitFor(() => expect(mockApi.retryFailedPipelineRun).toHaveBeenCalledWith(1));
  });

  it('expands the row to show per-copy jobs', async () => {
    mockApi.listAllPipelineRuns.mockResolvedValue({ runs: [makeRun()], total: 1 });
    render(<PipelineRunsView />);
    const user = userEvent.setup();
    await waitFor(() => expect(screen.getByText(/Production Batch/i)).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: /Expand/i }));
    await waitFor(() => {
      expect(screen.getByText('X1C #1')).toBeInTheDocument();
      expect(screen.getByText('X1C #2')).toBeInTheDocument();
    });
  });
});
