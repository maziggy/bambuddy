/**
 * Tests for RunWithPipelineModal (#1425 PR B).
 *
 * Pin the two-step flow: pick → optional confirmation → run. Verify the
 * fast path skips the modal step and the slow path renders the eligibility
 * issues with the "Run anyway" button.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { RunWithPipelineModal } from '../../components/RunWithPipelineModal';
import { SliceJobTrackerProvider } from '../../contexts/SliceJobTrackerContext';
import { api, type Printer, type SlicerPipeline } from '../../api/client';

vi.mock('../../api/client', () => ({
  api: {
    listSlicerPipelines: vi.fn(),
    getPrinters: vi.fn(),
    checkPipelineEligibility: vi.fn(),
    runPipeline: vi.fn(),
    // ThemeContext / AuthContext bootstrap touches these on mount.
    getSettings: vi.fn().mockResolvedValue({}),
    updateSettings: vi.fn().mockResolvedValue({}),
  },
}));

const mockApi = api as unknown as {
  listSlicerPipelines: ReturnType<typeof vi.fn>;
  getPrinters: ReturnType<typeof vi.fn>;
  checkPipelineEligibility: ReturnType<typeof vi.fn>;
  runPipeline: ReturnType<typeof vi.fn>;
};

function makePipeline(overrides: Partial<SlicerPipeline> = {}): SlicerPipeline {
  return {
    id: 1,
    name: 'Production Batch',
    description: null,
    printer_preset: { source: 'local', id: '1' },
    process_preset: { source: 'local', id: '2' },
    filament_presets: [{ source: 'local', id: '3' }],
    bed_type: null,
    target_kind: 'specific_printer',
    target_printer_id: 42,
    target_model_class: null,
    fanout_strategy: 'max_parallel',
    created_by: null,
    created_at: '2026-06-27T00:00:00Z',
    updated_at: '2026-06-27T00:00:00Z',
    ...overrides,
  };
}

function makePrinter(overrides: Partial<Printer> = {}): Printer {
  return {
    id: 42,
    name: 'X1C #2',
    serial_number: 'TEST',
    ip_address: '192.0.2.1',
    access_code: '****',
    model: 'X1C',
    location: null,
    is_active: true,
    nozzle_count: 1,
    ...overrides,
  } as unknown as Printer;
}

describe('RunWithPipelineModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockApi.getPrinters.mockResolvedValue([makePrinter()]);
  });

  it('renders the empty state when no pipelines exist', async () => {
    mockApi.listSlicerPipelines.mockResolvedValue({ pipelines: [] });
    render(
      <SliceJobTrackerProvider>
        <RunWithPipelineModal source={{ kind: 'libraryFile', id: 99, filename: 'cube.3mf' }} onClose={vi.fn()} />
      </SliceJobTrackerProvider>,
    );
    await waitFor(() => {
      expect(screen.getByText(/No pipelines saved yet/i)).toBeInTheDocument();
    });
  });

  it('disables pipelines with no target printer', async () => {
    mockApi.listSlicerPipelines.mockResolvedValue({
      pipelines: [makePipeline({ target_printer_id: null })],
    });
    render(
      <SliceJobTrackerProvider>
        <RunWithPipelineModal source={{ kind: 'libraryFile', id: 99, filename: 'cube.3mf' }} onClose={vi.fn()} />
      </SliceJobTrackerProvider>,
    );
    await waitFor(() => {
      expect(screen.getByText(/No target printer set/i)).toBeInTheDocument();
    });
    const button = screen.getByRole('button', { name: /Production Batch/i });
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });

  it('fires the run immediately when eligibility is ok (fast path)', async () => {
    mockApi.listSlicerPipelines.mockResolvedValue({ pipelines: [makePipeline()] });
    mockApi.checkPipelineEligibility.mockResolvedValue({
      ok: true,
      target_printer_id: 42,
      target_printer_name: 'X1C #2',
      issues: [],
    });
    mockApi.runPipeline.mockResolvedValue({});
    const onClose = vi.fn();
    render(
      <SliceJobTrackerProvider>
        <RunWithPipelineModal source={{ kind: 'libraryFile', id: 99, filename: 'cube.3mf' }} onClose={onClose} />
      </SliceJobTrackerProvider>,
    );
    await waitFor(() => expect(screen.getByText('Production Batch')).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /Production Batch/i }));

    await waitFor(() => {
      expect(mockApi.checkPipelineEligibility).toHaveBeenCalledWith(1, {
        kind: 'libraryFile',
        id: 99,
      });
      expect(mockApi.runPipeline).toHaveBeenCalledWith(
        1,
        { kind: 'libraryFile', id: 99 },
        false,
        1,
      );
      expect(onClose).toHaveBeenCalled();
    });
  });

  it('threads source kind="archive" through eligibility + run', async () => {
    mockApi.listSlicerPipelines.mockResolvedValue({ pipelines: [makePipeline()] });
    mockApi.checkPipelineEligibility.mockResolvedValue({
      ok: true,
      target_printer_id: 42,
      target_printer_name: 'X1C #2',
      issues: [],
    });
    mockApi.runPipeline.mockResolvedValue({ slice_job_id: 9001 });
    render(
      <SliceJobTrackerProvider>
        <RunWithPipelineModal
          source={{ kind: 'archive', id: 7, filename: 'archive.3mf' }}
          onClose={vi.fn()}
        />
      </SliceJobTrackerProvider>,
    );
    await waitFor(() => expect(screen.getByText('Production Batch')).toBeInTheDocument());
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /Production Batch/i }));
    await waitFor(() => {
      expect(mockApi.checkPipelineEligibility).toHaveBeenCalledWith(1, {
        kind: 'archive',
        id: 7,
      });
      expect(mockApi.runPipeline).toHaveBeenCalledWith(
        1,
        { kind: 'archive', id: 7 },
        false,
        1,
      );
    });
  });

  it('shows the eligibility report + Run-anyway button when issues exist', async () => {
    mockApi.listSlicerPipelines.mockResolvedValue({ pipelines: [makePipeline()] });
    mockApi.checkPipelineEligibility.mockResolvedValue({
      ok: false,
      target_printer_id: 42,
      target_printer_name: 'X1C #2',
      issues: [
        {
          kind: 'filament_type_mismatch',
          slot_index: 0,
          expected: 'PLA',
          actual: 'PETG',
        },
      ],
    });
    mockApi.runPipeline.mockResolvedValue({});
    render(
      <SliceJobTrackerProvider>
        <RunWithPipelineModal source={{ kind: 'libraryFile', id: 99, filename: 'cube.3mf' }} onClose={vi.fn()} />
      </SliceJobTrackerProvider>,
    );
    await waitFor(() => expect(screen.getByText('Production Batch')).toBeInTheDocument());

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /Production Batch/i }));

    // Confirmation view appears with the issue listed.
    await waitFor(() => {
      expect(screen.getByText(/expected PLA/i)).toBeInTheDocument();
    });
    // Clicking Run anyway fires the run with force=true and the new source-kind shape.
    await user.click(screen.getByRole('button', { name: /Run anyway/i }));
    await waitFor(() => {
      expect(mockApi.runPipeline).toHaveBeenCalledWith(1, { kind: 'libraryFile', id: 99 }, true, 1);
    });
  });
});
