/**
 * Tests for SliceModal.
 *
 * The modal handles preset selection + enqueueing a slice job. After
 * enqueue success it hands the job_id off to SliceJobTrackerProvider
 * (which lives at app level) and calls onClose. Polling, toasts, and
 * query invalidation all happen in the tracker — not here.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { SliceModal } from '../../components/SliceModal';
import { SliceJobTrackerProvider } from '../../contexts/SliceJobTrackerContext';
import { api } from '../../api/client';

vi.mock('../../api/client', () => ({
  api: {
    getLocalPresets: vi.fn(),
    sliceLibraryFile: vi.fn(),
    sliceArchive: vi.fn(),
    getSliceJob: vi.fn(),
    getSettings: vi.fn().mockResolvedValue({}),
    updateSettings: vi.fn().mockResolvedValue({}),
  },
}));

const mockApi = api as unknown as {
  getLocalPresets: ReturnType<typeof vi.fn>;
  sliceLibraryFile: ReturnType<typeof vi.fn>;
  sliceArchive: ReturnType<typeof vi.fn>;
  getSliceJob: ReturnType<typeof vi.fn>;
};

const samplePresets = {
  printer: [{ id: 1, name: 'X1C 0.4', preset_type: 'printer' }],
  process: [{ id: 2, name: '0.20mm Standard', preset_type: 'process' }],
  filament: [{ id: 3, name: 'Bambu PLA Basic', preset_type: 'filament' }],
};

function renderWithTracker(props: Parameters<typeof SliceModal>[0]) {
  return render(
    <SliceJobTrackerProvider>
      <SliceModal {...props} />
    </SliceJobTrackerProvider>,
  );
}

describe('SliceModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockApi.getLocalPresets.mockResolvedValue(samplePresets);
    // Tracker polls — return a still-running job so the test doesn't
    // race against terminal-state side effects (toasts, invalidation).
    mockApi.getSliceJob.mockResolvedValue({
      job_id: 42,
      status: 'running',
      kind: 'library_file',
      source_id: 100,
      source_name: 'Cube.stl',
      created_at: new Date().toISOString(),
      started_at: null,
      completed_at: null,
    });
  });

  it('disables Slice button until all three presets are picked', async () => {
    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' },
      onClose: vi.fn(),
    });

    await waitFor(() => expect(screen.getByText('X1C 0.4')).toBeDefined());

    const sliceBtn = screen.getByRole('button', { name: /^Slice$/ });
    expect((sliceBtn as HTMLButtonElement).disabled).toBe(true);

    const user = userEvent.setup();
    const selects = screen.getAllByRole('combobox');
    expect(selects).toHaveLength(3);
    await user.selectOptions(selects[0], '1');
    await user.selectOptions(selects[1], '2');
    expect((sliceBtn as HTMLButtonElement).disabled).toBe(true);
    await user.selectOptions(selects[2], '3');
    expect((sliceBtn as HTMLButtonElement).disabled).toBe(false);
  });

  it('enqueues a library-file slice job and closes the modal on success', async () => {
    const onClose = vi.fn();
    mockApi.sliceLibraryFile.mockResolvedValue({
      job_id: 42,
      status: 'pending',
      status_url: '/api/v1/slice-jobs/42',
    });

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' },
      onClose,
    });

    await waitFor(() => expect(screen.getByText('X1C 0.4')).toBeDefined());

    const user = userEvent.setup();
    const selects = screen.getAllByRole('combobox');
    await user.selectOptions(selects[0], '1');
    await user.selectOptions(selects[1], '2');
    await user.selectOptions(selects[2], '3');
    await user.click(screen.getByRole('button', { name: /^Slice$/ }));

    await waitFor(() => {
      expect(mockApi.sliceLibraryFile).toHaveBeenCalledWith(100, {
        printer_preset_id: 1,
        process_preset_id: 2,
        filament_preset_id: 3,
      });
    });
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('routes archive sources to sliceArchive instead of sliceLibraryFile', async () => {
    const onClose = vi.fn();
    mockApi.sliceArchive.mockResolvedValue({
      job_id: 7,
      status: 'pending',
      status_url: '/api/v1/slice-jobs/7',
    });

    renderWithTracker({
      source: { kind: 'archive', id: 86, filename: 'orca.3mf' },
      onClose,
    });

    await waitFor(() => expect(screen.getByText('X1C 0.4')).toBeDefined());

    const user = userEvent.setup();
    const selects = screen.getAllByRole('combobox');
    await user.selectOptions(selects[0], '1');
    await user.selectOptions(selects[1], '2');
    await user.selectOptions(selects[2], '3');
    await user.click(screen.getByRole('button', { name: /^Slice$/ }));

    await waitFor(() => {
      expect(mockApi.sliceArchive).toHaveBeenCalledWith(86, expect.any(Object));
      expect(mockApi.sliceLibraryFile).not.toHaveBeenCalled();
    });
  });

  it('surfaces enqueue errors inline and keeps the modal open', async () => {
    const onClose = vi.fn();
    mockApi.sliceLibraryFile.mockRejectedValue(new Error('Server says no'));

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' },
      onClose,
    });

    await waitFor(() => expect(screen.getByText('X1C 0.4')).toBeDefined());

    const user = userEvent.setup();
    const selects = screen.getAllByRole('combobox');
    await user.selectOptions(selects[0], '1');
    await user.selectOptions(selects[1], '2');
    await user.selectOptions(selects[2], '3');
    await user.click(screen.getByRole('button', { name: /^Slice$/ }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Server says no');
    });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('shows a friendly notice when getLocalPresets fails', async () => {
    mockApi.getLocalPresets.mockRejectedValue(new Error('500'));

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' },
      onClose: vi.fn(),
    });

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/Failed to load presets/i);
    });
  });
});
