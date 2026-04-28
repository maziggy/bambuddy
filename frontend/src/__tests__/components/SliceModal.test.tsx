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
    getLibraryFilePlates: vi.fn(),
    getArchivePlates: vi.fn(),
    getLibraryFileFilamentRequirements: vi.fn(),
    getArchiveFilamentRequirements: vi.fn(),
    getSettings: vi.fn().mockResolvedValue({}),
    updateSettings: vi.fn().mockResolvedValue({}),
  },
}));

const mockApi = api as unknown as {
  getSlicerPresets: ReturnType<typeof vi.fn>;
  sliceLibraryFile: ReturnType<typeof vi.fn>;
  sliceArchive: ReturnType<typeof vi.fn>;
  getSliceJob: ReturnType<typeof vi.fn>;
  getLibraryFilePlates: ReturnType<typeof vi.fn>;
  getArchivePlates: ReturnType<typeof vi.fn>;
  getLibraryFileFilamentRequirements: ReturnType<typeof vi.fn>;
  getArchiveFilamentRequirements: ReturnType<typeof vi.fn>;
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
    // Default: single-plate (or non-3MF). Multi-plate tests override this.
    mockApi.getLibraryFilePlates.mockResolvedValue({
      file_id: 100,
      filename: 'Cube.stl',
      plates: [],
      is_multi_plate: false,
    });
    mockApi.getArchivePlates.mockResolvedValue({
      archive_id: 100,
      filename: 'Cube.3mf',
      plates: [],
      is_multi_plate: false,
    });
    // Default: no per-plate filament metadata available (mirrors STL or
    // unsliced source). Multi-color tests override this.
    mockApi.getLibraryFileFilamentRequirements.mockResolvedValue({
      file_id: 100,
      filename: 'Cube.stl',
      plate_id: 1,
      filaments: [],
    });
    mockApi.getArchiveFilamentRequirements.mockResolvedValue({
      archive_id: 100,
      filename: 'Cube.3mf',
      plate_id: 1,
      filaments: [],
    });
  });

  it('auto-selects the highest-priority tier per slot on first load', async () => {
    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' },
      onClose: vi.fn(),
    });

    // SliceModal-specific tier priority: imported (local) wins over cloud
    // and standard so the user's curated picks come first.
    await waitFor(() => {
      expect(screen.getByText('My Custom X1C')).toBeDefined();
    });
    const selects = screen.getAllByRole('combobox') as HTMLSelectElement[];
    expect(selects).toHaveLength(3);
    expect(selects[0].value).toBe('local:1');
    expect(selects[1].value).toBe('local:2');
    expect(selects[2].value).toBe('local:3');

    // Slice button is enabled because all three slots auto-defaulted.
    const sliceBtn = screen.getByRole('button', { name: /^Slice$/ });
    expect((sliceBtn as HTMLButtonElement).disabled).toBe(false);
  });

  it('renders Imported / Cloud / Standard sections via <optgroup>', async () => {
    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' },
      onClose: vi.fn(),
    });

    await waitFor(() => expect(screen.getByText('Imported X1C 0.4')).toBeDefined());

    const printerSelect = screen.getAllByRole('combobox')[0];
    const groups = printerSelect.querySelectorAll('optgroup');
    expect(Array.from(groups).map((g) => g.label)).toEqual([
      'Imported',
      'Cloud',
      'Standard',
    ]);

    // Each entry sits inside its own tier's group — pin the assignment so
    // a future render-shape change can't quietly mix them. Order matches
    // SLICE_MODAL_TIER_ORDER (local → cloud → standard).
    const localGroup = groups[0];
    expect(within(localGroup as HTMLElement).getByText('Imported X1C 0.4')).toBeDefined();
    const cloudGroup = groups[1];
    expect(within(cloudGroup as HTMLElement).getByText('My Custom X1C')).toBeDefined();
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
      // SliceModal-specific tier priority puts imported (local) above cloud,
      // so the auto-pick lands on the local entries even when a cloud entry
      // with the same slot is also available in the listing.
      expect(mockApi.sliceLibraryFile).toHaveBeenCalledWith(100, {
        printer_preset: { source: 'local', id: '1' },
        process_preset: { source: 'local', id: '2' },
        filament_preset: { source: 'local', id: '3' },
        filament_presets: [{ source: 'local', id: '3' }],
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

  // ----- Multi-plate flow -----------------------------------------------

  function makeMultiPlateLibraryResponse() {
    return {
      file_id: 100,
      filename: 'Multi.3mf',
      is_multi_plate: true,
      plates: [
        {
          index: 1,
          name: 'Plate 1',
          objects: ['Cube'],
          object_count: 1,
          has_thumbnail: false,
          thumbnail_url: null,
          print_time_seconds: 600,
          filament_used_grams: 10,
          filaments: [],
        },
        {
          index: 2,
          name: 'Plate 2',
          objects: ['Pyramid'],
          object_count: 1,
          has_thumbnail: false,
          thumbnail_url: null,
          print_time_seconds: 800,
          filament_used_grams: 12,
          filaments: [],
        },
      ],
    };
  }

  it('shows the plate picker first for multi-plate library files', async () => {
    mockApi.getLibraryFilePlates.mockResolvedValue(makeMultiPlateLibraryResponse());
    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Multi.3mf' },
      onClose: vi.fn(),
    });

    // Plate picker renders one button per plate — the accessible name
    // joins the heading ("Plate N — name") with the object summary line.
    await screen.findByRole('button', { name: /Plate 1.*Cube/ });
    expect(screen.getByRole('button', { name: /Plate 2.*Pyramid/ })).toBeDefined();
    // Profile dropdowns must NOT be visible yet — the user has to pick a
    // plate first.
    expect(screen.queryByRole('combobox')).toBeNull();
  });

  it('skips the plate picker for single-plate sources', async () => {
    mockApi.getLibraryFilePlates.mockResolvedValue({
      file_id: 100,
      filename: 'Single.3mf',
      is_multi_plate: false,
      plates: [
        {
          index: 1,
          name: 'Plate 1',
          objects: [],
          has_thumbnail: false,
          thumbnail_url: null,
          print_time_seconds: null,
          filament_used_grams: null,
          filaments: [],
        },
      ],
    });
    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Single.3mf' },
      onClose: vi.fn(),
    });

    // Should jump straight to the profile dropdowns.
    await waitFor(() => expect(screen.getByText('My Custom X1C')).toBeDefined());
  });

  it('passes the picked plate to the slice request', async () => {
    mockApi.getLibraryFilePlates.mockResolvedValue(makeMultiPlateLibraryResponse());
    mockApi.sliceLibraryFile.mockResolvedValue({
      job_id: 42,
      status: 'pending',
      status_url: '/api/v1/slice-jobs/42',
    });

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Multi.3mf' },
      onClose: vi.fn(),
    });

    const user = userEvent.setup();
    // Step 1: pick Plate 2.
    const plate2Button = await screen.findByRole('button', { name: /Plate 2.*Pyramid/ });
    await user.click(plate2Button);

    // Step 2: profile dropdowns are now visible.
    await waitFor(() => expect(screen.getByText('My Custom X1C')).toBeDefined());

    // Step 3: submit and verify the plate index made it into the body.
    await user.click(screen.getByRole('button', { name: /^Slice$/ }));
    await waitFor(() => {
      expect(mockApi.sliceLibraryFile).toHaveBeenCalledWith(
        100,
        expect.objectContaining({ plate: 2 }),
      );
    });
  });

  it('routes the plate fetch through getArchivePlates for archive sources', async () => {
    mockApi.getArchivePlates.mockResolvedValue({
      ...makeMultiPlateLibraryResponse(),
      archive_id: 100,
      filename: 'Multi.3mf',
    });
    renderWithTracker({
      source: { kind: 'archive', id: 100, filename: 'Multi.3mf' },
      onClose: vi.fn(),
    });

    await screen.findByRole('button', { name: /Plate 1.*Cube/ });
    expect(mockApi.getArchivePlates).toHaveBeenCalledWith(100);
    expect(mockApi.getLibraryFilePlates).not.toHaveBeenCalled();
  });

  it('cancelling the plate picker closes the entire slice flow', async () => {
    const onClose = vi.fn();
    mockApi.getLibraryFilePlates.mockResolvedValue(makeMultiPlateLibraryResponse());
    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Multi.3mf' },
      onClose,
    });

    await screen.findByRole('button', { name: /Plate 1.*Cube/ });

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /^Close$/i }));

    expect(onClose).toHaveBeenCalled();
  });

  it('omits the plate field when the source is single-plate', async () => {
    mockApi.sliceLibraryFile.mockResolvedValue({
      job_id: 42,
      status: 'pending',
      status_url: '/api/v1/slice-jobs/42',
    });

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' },
      onClose: vi.fn(),
    });

    await waitFor(() => expect(screen.getByText('My Custom X1C')).toBeDefined());

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /^Slice$/ }));

    await waitFor(() => {
      const [, body] = mockApi.sliceLibraryFile.mock.calls[0];
      expect(body).not.toHaveProperty('plate');
    });
  });

  // ----- Multi-color flow ------------------------------------------------

  function makeMultiColorPlateResponse() {
    // Single-plate 3MF that uses two filament slots — mirrors the realistic
    // "I have a multi-color file with one plate" case. Multi-plate is a
    // separate axis that's already covered above.
    return {
      file_id: 100,
      filename: 'TwoColor.3mf',
      is_multi_plate: false,
      plates: [
        {
          index: 1,
          name: 'Plate 1',
          objects: ['Logo'],
          object_count: 1,
          has_thumbnail: false,
          thumbnail_url: null,
          print_time_seconds: 600,
          filament_used_grams: 20,
          filaments: [],
        },
      ],
    };
  }

  function makeMultiColorRequirementsResponse() {
    return {
      file_id: 100,
      filename: 'TwoColor.3mf',
      plate_id: 1,
      filaments: [
        { slot_id: 1, type: 'PLA', color: '#000000', used_grams: 10, used_meters: 3 },
        { slot_id: 2, type: 'PLA', color: '#FFFFFF', used_grams: 10, used_meters: 3 },
      ],
    };
  }

  function makeColorAwarePresets(): UnifiedPresetsResponse {
    // Two filament presets in cloud: one black PLA, one white PLA. Pre-pick
    // should match each plate slot to the same-colour preset so the user
    // doesn't have to manually align them.
    return {
      cloud: {
        printer: [{ id: 'P1', name: 'X1C', source: 'cloud' }],
        process: [{ id: 'PR1', name: '0.20mm', source: 'cloud' }],
        filament: [
          { id: 'F-BLACK', name: 'Cloud PLA Black', source: 'cloud', filament_type: 'PLA', filament_colour: '#000000' },
          { id: 'F-WHITE', name: 'Cloud PLA White', source: 'cloud', filament_type: 'PLA', filament_colour: '#FFFFFF' },
        ],
      },
      local: { printer: [], process: [], filament: [] },
      standard: { printer: [], process: [], filament: [] },
      cloud_status: 'ok',
    };
  }

  it('renders one filament dropdown per plate slot when the source is multi-color', async () => {
    mockApi.getLibraryFilePlates.mockResolvedValue(makeMultiColorPlateResponse());
    mockApi.getLibraryFileFilamentRequirements.mockResolvedValue(makeMultiColorRequirementsResponse());
    mockApi.getSlicerPresets.mockResolvedValue(makeColorAwarePresets());

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'TwoColor.3mf' },
      onClose: vi.fn(),
    });

    await waitFor(() => expect(screen.getByText('X1C')).toBeDefined());
    // 1 printer + 1 process + 2 filament = 4 dropdowns.
    expect(screen.getAllByRole('combobox')).toHaveLength(4);
  });

  it('pre-picks each filament slot by matching colour metadata', async () => {
    mockApi.getLibraryFilePlates.mockResolvedValue(makeMultiColorPlateResponse());
    mockApi.getLibraryFileFilamentRequirements.mockResolvedValue(makeMultiColorRequirementsResponse());
    mockApi.getSlicerPresets.mockResolvedValue(makeColorAwarePresets());
    mockApi.sliceLibraryFile.mockResolvedValue({
      job_id: 42,
      status: 'pending',
      status_url: '/api/v1/slice-jobs/42',
    });

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'TwoColor.3mf' },
      onClose: vi.fn(),
    });

    await waitFor(() => expect(screen.getByText('X1C')).toBeDefined());

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /^Slice$/ }));

    await waitFor(() => {
      const [, body] = mockApi.sliceLibraryFile.mock.calls[0];
      // Slot 1 was black plate → cloud black preset; slot 2 was white →
      // cloud white preset. Pre-pick aligns them by metadata so the user
      // doesn't have to swap them manually.
      expect(body.filament_presets).toEqual([
        { source: 'cloud', id: 'F-BLACK' },
        { source: 'cloud', id: 'F-WHITE' },
      ]);
    });
  });

  it('still sends the legacy filament_preset for single-color flows', async () => {
    // Backwards-compat with backends / proxies that read the singular field.
    mockApi.sliceLibraryFile.mockResolvedValue({
      job_id: 42,
      status: 'pending',
      status_url: '/api/v1/slice-jobs/42',
    });

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' },
      onClose: vi.fn(),
    });

    await waitFor(() => expect(screen.getByText('My Custom X1C')).toBeDefined());

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /^Slice$/ }));

    await waitFor(() => {
      const [, body] = mockApi.sliceLibraryFile.mock.calls[0];
      // Single-color path mirrors the array's first entry into the legacy
      // singular so older backend clients that only know about
      // `filament_preset` still work.
      expect(body.filament_preset).toEqual(body.filament_presets[0]);
      expect(body.filament_presets).toHaveLength(1);
    });
  });

  it('lets the user override a pre-picked filament slot', async () => {
    mockApi.getLibraryFilePlates.mockResolvedValue(makeMultiColorPlateResponse());
    mockApi.getLibraryFileFilamentRequirements.mockResolvedValue(makeMultiColorRequirementsResponse());
    mockApi.getSlicerPresets.mockResolvedValue(makeColorAwarePresets());
    mockApi.sliceLibraryFile.mockResolvedValue({
      job_id: 42,
      status: 'pending',
      status_url: '/api/v1/slice-jobs/42',
    });

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'TwoColor.3mf' },
      onClose: vi.fn(),
    });

    await waitFor(() => expect(screen.getByText('X1C')).toBeDefined());

    const user = userEvent.setup();
    const selects = screen.getAllByRole('combobox') as HTMLSelectElement[];
    // Slots 0 (printer) and 1 (process) are auto-picked. Slots 2 and 3 are
    // the two filament dropdowns. Swap slot-2 (was black) to white.
    await user.selectOptions(selects[2], 'cloud:F-WHITE');
    await user.click(screen.getByRole('button', { name: /^Slice$/ }));

    await waitFor(() => {
      const [, body] = mockApi.sliceLibraryFile.mock.calls[0];
      expect(body.filament_presets[0]).toEqual({ source: 'cloud', id: 'F-WHITE' });
      // Slot 1 stayed at the auto-picked white.
      expect(body.filament_presets[1]).toEqual({ source: 'cloud', id: 'F-WHITE' });
    });
  });
});
