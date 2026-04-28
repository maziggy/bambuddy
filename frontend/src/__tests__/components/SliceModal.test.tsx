/**
 * Tests for SliceModal.
 *
 * The modal handles preset selection across three tiers (cloud / local /
 * standard) + enqueueing a slice job. After enqueue success it hands the
 * job_id off to SliceJobTrackerProvider (which lives at app level) and
 * calls onClose. Polling, toasts, and query invalidation all happen in
 * the tracker — not here.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { SliceModal } from '../../components/SliceModal';
import { SliceJobTrackerProvider } from '../../contexts/SliceJobTrackerContext';
import { api, type UnifiedPresetsResponse } from '../../api/client';

vi.mock('../../api/client', () => ({
  api: {
    getSlicerPresets: vi.fn(),
    sliceLibraryFile: vi.fn(),
    sliceArchive: vi.fn(),
    getSliceJob: vi.fn(),
    getSettings: vi.fn().mockResolvedValue({}),
    updateSettings: vi.fn().mockResolvedValue({}),
  },
}));

const mockApi = api as unknown as {
  getSlicerPresets: ReturnType<typeof vi.fn>;
  sliceLibraryFile: ReturnType<typeof vi.fn>;
  sliceArchive: ReturnType<typeof vi.fn>;
  getSliceJob: ReturnType<typeof vi.fn>;
};

function makeUnified(overrides: Partial<UnifiedPresetsResponse> = {}): UnifiedPresetsResponse {
  return {
    cloud: { printer: [], process: [], filament: [] },
    local: { printer: [], process: [], filament: [] },
    standard: { printer: [], process: [], filament: [] },
    cloud_status: 'ok',
    ...overrides,
  };
}

const fullThreeTier: UnifiedPresetsResponse = makeUnified({
  cloud: {
    printer: [{ id: 'PFUcloud-printer', name: 'My Custom X1C', source: 'cloud' }],
    process: [{ id: 'PFUcloud-process', name: 'My 0.16mm Tweaked', source: 'cloud' }],
    filament: [{ id: 'PFUcloud-filament', name: 'My PLA Black', source: 'cloud' }],
  },
  local: {
    printer: [{ id: '1', name: 'Imported X1C 0.4', source: 'local' }],
    process: [{ id: '2', name: 'Imported 0.20mm', source: 'local' }],
    filament: [{ id: '3', name: 'Imported PLA Basic', source: 'local' }],
  },
  standard: {
    printer: [{ id: 'Bambu Lab X1 Carbon 0.4 nozzle', name: 'Bambu Lab X1 Carbon 0.4 nozzle', source: 'standard' }],
    process: [{ id: '0.20mm Standard', name: '0.20mm Standard', source: 'standard' }],
    filament: [{ id: 'Bambu PLA Basic', name: 'Bambu PLA Basic', source: 'standard' }],
  },
});

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
    mockApi.getSlicerPresets.mockResolvedValue(fullThreeTier);
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

  it('auto-selects the highest-priority tier per slot on first load', async () => {
    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' },
      onClose: vi.fn(),
    });

    // The cloud tier wins — printer dropdown should land on the cloud entry.
    await waitFor(() => {
      expect(screen.getByText('My Custom X1C')).toBeDefined();
    });
    const selects = screen.getAllByRole('combobox') as HTMLSelectElement[];
    expect(selects).toHaveLength(3);
    expect(selects[0].value).toBe('cloud:PFUcloud-printer');
    expect(selects[1].value).toBe('cloud:PFUcloud-process');
    expect(selects[2].value).toBe('cloud:PFUcloud-filament');

    // Slice button is enabled because all three slots auto-defaulted.
    const sliceBtn = screen.getByRole('button', { name: /^Slice$/ });
    expect((sliceBtn as HTMLButtonElement).disabled).toBe(false);
  });

  it('renders Cloud / Imported / Standard sections via <optgroup>', async () => {
    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' },
      onClose: vi.fn(),
    });

    await waitFor(() => expect(screen.getByText('My Custom X1C')).toBeDefined());

    const printerSelect = screen.getAllByRole('combobox')[0];
    const groups = printerSelect.querySelectorAll('optgroup');
    expect(Array.from(groups).map((g) => g.label)).toEqual([
      'Cloud',
      'Imported',
      'Standard',
    ]);

    // The cloud entry sits inside the Cloud group, the local entry inside
    // Imported, the standard entry inside Standard — pin the assignment so
    // a future render-shape change can't quietly mix them.
    const cloudGroup = groups[0];
    expect(within(cloudGroup as HTMLElement).getByText('My Custom X1C')).toBeDefined();
    const localGroup = groups[1];
    expect(within(localGroup as HTMLElement).getByText('Imported X1C 0.4')).toBeDefined();
    const standardGroup = groups[2];
    expect(within(standardGroup as HTMLElement).getByText('Bambu Lab X1 Carbon 0.4 nozzle')).toBeDefined();
  });

  it('falls back to local when cloud is empty (auto-pick respects priority)', async () => {
    mockApi.getSlicerPresets.mockResolvedValue(
      makeUnified({
        local: fullThreeTier.local,
        standard: fullThreeTier.standard,
      }),
    );
    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' },
      onClose: vi.fn(),
    });

    await waitFor(() => expect(screen.getByText('Imported X1C 0.4')).toBeDefined());
    const selects = screen.getAllByRole('combobox') as HTMLSelectElement[];
    expect(selects[0].value).toBe('local:1');
  });

  it('falls back to standard when both cloud and local are empty', async () => {
    mockApi.getSlicerPresets.mockResolvedValue(
      makeUnified({ standard: fullThreeTier.standard }),
    );
    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' },
      onClose: vi.fn(),
    });

    await waitFor(() => expect(screen.getByText('Bambu Lab X1 Carbon 0.4 nozzle')).toBeDefined());
    const selects = screen.getAllByRole('combobox') as HTMLSelectElement[];
    expect(selects[0].value).toBe('standard:Bambu Lab X1 Carbon 0.4 nozzle');
  });

  it('sends source-aware refs (not legacy bare ints) on submit', async () => {
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

    await waitFor(() => expect(screen.getByText('My Custom X1C')).toBeDefined());

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /^Slice$/ }));

    await waitFor(() => {
      expect(mockApi.sliceLibraryFile).toHaveBeenCalledWith(100, {
        printer_preset: { source: 'cloud', id: 'PFUcloud-printer' },
        process_preset: { source: 'cloud', id: 'PFUcloud-process' },
        filament_preset: { source: 'cloud', id: 'PFUcloud-filament' },
      });
    });
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('lets the user override the default and pick a Standard preset', async () => {
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

    await waitFor(() => expect(screen.getByText('My Custom X1C')).toBeDefined());

    const user = userEvent.setup();
    const selects = screen.getAllByRole('combobox');
    await user.selectOptions(selects[0], 'standard:Bambu Lab X1 Carbon 0.4 nozzle');
    await user.click(screen.getByRole('button', { name: /^Slice$/ }));

    await waitFor(() => {
      expect(mockApi.sliceLibraryFile).toHaveBeenCalledWith(
        100,
        expect.objectContaining({
          printer_preset: { source: 'standard', id: 'Bambu Lab X1 Carbon 0.4 nozzle' },
        }),
      );
    });
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

    await waitFor(() => expect(screen.getByText('My Custom X1C')).toBeDefined());

    const user = userEvent.setup();
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

    await waitFor(() => expect(screen.getByText('My Custom X1C')).toBeDefined());

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /^Slice$/ }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Server says no');
    });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('shows a friendly notice when getSlicerPresets fails', async () => {
    mockApi.getSlicerPresets.mockRejectedValue(new Error('500'));

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' },
      onClose: vi.fn(),
    });

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/Failed to load presets/i);
    });
  });

  it('renders a "sign in" banner when cloud_status is not_authenticated', async () => {
    mockApi.getSlicerPresets.mockResolvedValue(
      makeUnified({
        cloud_status: 'not_authenticated',
        local: fullThreeTier.local,
        standard: fullThreeTier.standard,
      }),
    );
    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' },
      onClose: vi.fn(),
    });

    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent(/Sign in to Bambu Cloud/i);
    });
  });

  it('renders an "expired" banner when cloud_status is expired', async () => {
    mockApi.getSlicerPresets.mockResolvedValue(
      makeUnified({
        cloud_status: 'expired',
        local: fullThreeTier.local,
      }),
    );
    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' },
      onClose: vi.fn(),
    });

    await waitFor(() => {
      expect(screen.getByRole('status')).toHaveTextContent(/expired/i);
    });
  });

  it('omits the banner entirely when cloud_status is ok', async () => {
    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' },
      onClose: vi.fn(),
    });
    await waitFor(() => expect(screen.getByText('My Custom X1C')).toBeDefined());
    // No status-role banner should be rendered on the happy path.
    expect(screen.queryByRole('status')).toBeNull();
  });
});
