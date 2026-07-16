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
import { pickFilamentForSlot } from '../../utils/slicePresetPicker';
import { buildCompatibilityIndex } from '../../utils/slicerPrinterMatch';
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
    // Slicer Pipelines (#1425)
    listSlicerPipelines: vi.fn(),
    createSlicerPipeline: vi.fn(),
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
  listSlicerPipelines: ReturnType<typeof vi.fn>;
  createSlicerPipeline: ReturnType<typeof vi.fn>;
};

function makeUnified(overrides: Partial<UnifiedPresetsResponse> = {}): UnifiedPresetsResponse {
  return {
    orca_cloud: { printer: [], process: [], filament: [] },
    cloud: { printer: [], process: [], filament: [] },
    local: { printer: [], process: [], filament: [] },
    standard: { printer: [], process: [], filament: [] },
    cloud_status: 'ok',
    orca_cloud_status: 'ok',
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

// SliceModal renders one extra combobox for the Slicer Pipelines (#1425)
// "Apply pipeline" dropdown above the preset slots. Tests written before
// pipelines landed assume selects[0] = printer; this helper drops the
// pipeline combobox so those indices stay stable.
function presetSelects(): HTMLSelectElement[] {
  return (screen.getAllByRole('combobox') as HTMLSelectElement[]).filter(
    (el) => el.getAttribute('aria-label') !== 'Apply pipeline',
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
    // Default: no saved pipelines. Tests opt in by overriding this.
    mockApi.listSlicerPipelines.mockResolvedValue({ pipelines: [] });
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
    // 4 selects: printer, process, bed-type (#1337), filament. bed-type sits
    // between process and filament — it overrides curr_bed_type on the
    // process preset so the related controls cluster — and defaults to "".
    const selects = presetSelects();
    expect(selects).toHaveLength(4);
    expect(selects[0].value).toBe('local:1');
    expect(selects[1].value).toBe('local:2');
    expect(selects[2].value).toBe('');
    expect(selects[3].value).toBe('local:3');

    // Slice button is enabled because all three slots auto-defaulted and
    // the preview-slice query has resolved (mock returns immediately).
    const sliceBtn = screen.getByRole('button', { name: /^Slice$/ });
    expect((sliceBtn as HTMLButtonElement).disabled).toBe(false);
  });

  it('renders Imported / Cloud / Standard sections via <optgroup>', async () => {
    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' },
      onClose: vi.fn(),
    });

    await waitFor(() => expect(screen.getByText('Imported X1C 0.4')).toBeDefined());

    const printerSelect = presetSelects()[0];
    const groups = printerSelect.querySelectorAll('optgroup');
    expect(Array.from(groups).map((g) => g.label)).toEqual([
      'Imported',
      'Bambu Cloud',
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
    const selects = presetSelects();
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
    const selects = presetSelects();
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

  it('includes bed_type in the request when the user picks a non-auto plate (#1337)', async () => {
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
    // Order with the dropdown now sits between Process and Filament:
    // printer (0), process (1), bed-type (2), filament (3+). Find the
    // bed-type select by name rather than positional index so this stays
    // green if the layout adds another control around it.
    const bedSelect = presetSelects().find((el) =>
      (el as HTMLSelectElement).options[0]?.textContent?.toLowerCase().includes('auto'),
    ) as HTMLSelectElement;
    expect(bedSelect).toBeDefined();
    await user.selectOptions(bedSelect, 'Textured PEI Plate');
    await user.click(screen.getByRole('button', { name: /^Slice$/ }));

    await waitFor(() => {
      expect(mockApi.sliceLibraryFile).toHaveBeenCalledWith(
        100,
        expect.objectContaining({ bed_type: 'Textured PEI Plate' }),
      );
    });
  });

  it('omits bed_type when the user leaves it on Auto (no override)', async () => {
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
      const [, body] = vi.mocked(mockApi.sliceLibraryFile).mock.calls[0];
      expect(body).not.toHaveProperty('bed_type');
    });
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
    const selects = presetSelects();
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

  it('omits the cloud banner when status is not_authenticated (#1712)', async () => {
    // A signed-out user (Bambu or Orca) shouldn't get a permanent "sign in"
    // nag at the top of every slice. Sign-in lives on the Profiles page; the
    // modal stays silent unless a previously-signed-in session actually broke
    // (expired / unreachable).
    mockApi.getSlicerPresets.mockResolvedValue(
      makeUnified({
        cloud_status: 'not_authenticated',
        orca_cloud_status: 'not_authenticated',
        local: fullThreeTier.local,
        standard: fullThreeTier.standard,
      }),
    );
    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' },
      onClose: vi.fn(),
    });

    await waitFor(() => expect(screen.getByText('Imported X1C 0.4')).toBeDefined());
    expect(screen.queryByRole('status')).toBeNull();
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

  it('"Slice all plates" toggle sends plate=0 sentinel to the backend (#1493)', async () => {
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
    const plate1Button = await screen.findByRole('button', { name: /Plate 1.*Cube/ });
    await user.click(plate1Button);

    await waitFor(() => expect(screen.getByText('My Custom X1C')).toBeDefined());

    // The "Slice all plates" checkbox only appears for multi-plate sources.
    const toggle = await screen.findByRole('checkbox', { name: /Slice all 2 plates/i });
    await user.click(toggle);

    // The action button's label flips to the "Slice all" form. Click it.
    await user.click(screen.getByRole('button', { name: /Slice all 2 plates/i }));

    await waitFor(() => {
      expect(mockApi.sliceLibraryFile).toHaveBeenCalledTimes(1);
    });
    const [, body] = mockApi.sliceLibraryFile.mock.calls[0];
    // ``plate=0`` is the BS CLI's all-plates sentinel — one slice call,
    // one output 3MF with every plate's gcode inside, one archive.
    expect((body as { plate?: number }).plate).toBe(0);
  });

  it('"Slice all plates" toggle is hidden for single-plate sources', async () => {
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

    await waitFor(() => expect(screen.getByText('My Custom X1C')).toBeDefined());
    expect(screen.queryByRole('checkbox', { name: /Slice all/i })).toBeNull();
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
      orca_cloud: { printer: [], process: [], filament: [] },
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
      orca_cloud_status: 'ok',
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
    // 1 printer + 1 process + 2 filament + 1 bed-type (#1337) = 5 dropdowns.
    expect(presetSelects()).toHaveLength(5);
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
    const selects = presetSelects();
    // Order: 0 printer, 1 process, 2 bed-type, 3 filament-1, 4 filament-2
    // (#1337). Auto-picks land on printer/process/filaments; bed-type
    // defaults to "". Swap filament-1 (index 3) from the auto-picked black
    // to white.
    await user.selectOptions(selects[3], 'cloud:F-WHITE');
    await user.click(screen.getByRole('button', { name: /^Slice$/ }));

    await waitFor(() => {
      const [, body] = mockApi.sliceLibraryFile.mock.calls[0];
      expect(body.filament_presets[0]).toEqual({ source: 'cloud', id: 'F-WHITE' });
      // Slot 1 stayed at the auto-picked white.
      expect(body.filament_presets[1]).toEqual({ source: 'cloud', id: 'F-WHITE' });
    });
  });

  // Cross-printer re-slicing is a normal, supported operation as of
  // 2026-05-20 (Step 0 empirical test: sidecar overrides printer / process
  // / bed / kinematics from the picked profile triplet, producing valid
  // target-printer G-code). No banner, no warning — the picker UI already
  // shows which printer the user picked, and that's enough.
  it('does not surface any cross-printer banner and keeps Slice enabled when models differ', async () => {
    mockApi.getLibraryFilePlates.mockResolvedValue({
      file_id: 100,
      filename: 'A1Original.3mf',
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
    // Standard tier offers an X1C profile — the user picks (auto-picks) it.
    mockApi.getSlicerPresets.mockResolvedValue(makeUnified({
      standard: {
        printer: [{ id: 'Bambu Lab X1 Carbon 0.4 nozzle', name: 'Bambu Lab X1 Carbon 0.4 nozzle', source: 'standard' }],
        process: [{ id: '0.20mm Standard', name: '0.20mm Standard', source: 'standard' }],
        filament: [{ id: 'Bambu PLA Basic', name: 'Bambu PLA Basic', source: 'standard' }],
      },
    }));

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'A1Original.3mf' },
      onClose: vi.fn(),
    });

    await waitFor(() =>
      expect(screen.getByText('Bambu Lab X1 Carbon 0.4 nozzle')).toBeDefined(),
    );

    // No banner, no alert — re-slicing across printers is just a normal slice now.
    expect(screen.queryByRole('alert')).toBeNull();
    const sliceButton = screen.getByRole('button', { name: /^Slice$/ }) as HTMLButtonElement;
    expect(sliceButton.disabled).toBe(false);
  });

  // The `used_in_plate` flag tells the modal which AMS slots are
  // actually consumed by the picked plate. Slots flagged as unused
  // are still rendered (the slicer CLI needs a profile per project
  // slot, otherwise it silently fills the gap from embedded defaults
  // and unwanted colours leak into the output) but disabled in the UI
  // so the user only interacts with the dropdowns that matter.
  it('disables filament dropdowns for slots not used by the picked plate', async () => {
    mockApi.getLibraryFilePlates.mockResolvedValue({
      file_id: 100,
      filename: 'Helmet.3mf',
      is_multi_plate: false,
      plates: [
        {
          index: 1,
          name: 'Plate 1',
          objects: ['Helmet'],
          has_thumbnail: false,
          thumbnail_url: null,
          print_time_seconds: 1200,
          filament_used_grams: 80,
          filaments: [],
        },
      ],
    });
    // Project has 2 AMS slots configured (white + grey support), but
    // plate 1 only paints with white (slot 1). The backend now returns
    // BOTH slots with used_in_plate flagging the difference.
    mockApi.getLibraryFileFilamentRequirements.mockResolvedValue({
      file_id: 100,
      filename: 'Helmet.3mf',
      plate_id: 1,
      filaments: [
        { slot_id: 1, type: 'PLA', color: '#FFFFFF', used_grams: 80, used_meters: 27, used_in_plate: true },
        { slot_id: 2, type: 'PLA', color: '#808080', used_grams: 0, used_meters: 0, used_in_plate: false },
      ],
    });
    mockApi.getSlicerPresets.mockResolvedValue({
      cloud: {
        printer: [{ id: 'P1', name: 'X1C', source: 'cloud' }],
        process: [{ id: 'PR1', name: '0.20mm', source: 'cloud' }],
        filament: [
          { id: 'F-WHITE', name: 'Cloud PLA White', source: 'cloud', filament_type: 'PLA', filament_colour: '#FFFFFF' },
          { id: 'F-GREY', name: 'Cloud PLA Grey', source: 'cloud', filament_type: 'PLA', filament_colour: '#808080' },
        ],
      },
      local: { printer: [], process: [], filament: [] },
      standard: { printer: [], process: [], filament: [] },
      cloud_status: 'ok',
      orca_cloud: { printer: [], process: [], filament: [] },
      orca_cloud_status: 'ok',
    });

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Helmet.3mf' },
      onClose: vi.fn(),
    });

    await waitFor(() => expect(screen.getByText('X1C')).toBeDefined());

    // Both filament rows render — 1 printer + 1 process + 1 bed-type +
    // 2 filament (#1337) = 5. bed-type sits at index 2, filament slots
    // follow at 3 and 4.
    const selects = presetSelects();
    expect(selects).toHaveLength(5);
    // Slot 1 (used) is editable, slot 2 (not used) is disabled.
    expect(selects[3].disabled).toBe(false);
    expect(selects[4].disabled).toBe(true);
    // The disabled row's label calls out why it's disabled.
    expect(screen.getByText(/not used by this plate/i)).toBeDefined();
  });

  it('still sends both filaments to the backend even when one slot is disabled', async () => {
    // The auto-pick scoring fills the disabled slot from project
    // metadata — the slicer CLI requires a profile for every project
    // slot, otherwise it silently fills the gap. The disabled UI is
    // purely cosmetic; the wire format must include the full list.
    mockApi.getLibraryFilePlates.mockResolvedValue({
      file_id: 100,
      filename: 'Helmet.3mf',
      is_multi_plate: false,
      plates: [
        {
          index: 1,
          name: 'Plate 1',
          objects: ['Helmet'],
          has_thumbnail: false,
          thumbnail_url: null,
          print_time_seconds: 1200,
          filament_used_grams: 80,
          filaments: [],
        },
      ],
    });
    mockApi.getLibraryFileFilamentRequirements.mockResolvedValue({
      file_id: 100,
      filename: 'Helmet.3mf',
      plate_id: 1,
      filaments: [
        { slot_id: 1, type: 'PLA', color: '#FFFFFF', used_grams: 80, used_meters: 27, used_in_plate: true },
        { slot_id: 2, type: 'PLA', color: '#808080', used_grams: 0, used_meters: 0, used_in_plate: false },
      ],
    });
    mockApi.getSlicerPresets.mockResolvedValue({
      cloud: {
        printer: [{ id: 'P1', name: 'X1C', source: 'cloud' }],
        process: [{ id: 'PR1', name: '0.20mm', source: 'cloud' }],
        filament: [
          { id: 'F-WHITE', name: 'Cloud PLA White', source: 'cloud', filament_type: 'PLA', filament_colour: '#FFFFFF' },
          { id: 'F-GREY', name: 'Cloud PLA Grey', source: 'cloud', filament_type: 'PLA', filament_colour: '#808080' },
        ],
      },
      local: { printer: [], process: [], filament: [] },
      standard: { printer: [], process: [], filament: [] },
      cloud_status: 'ok',
      orca_cloud: { printer: [], process: [], filament: [] },
      orca_cloud_status: 'ok',
    });
    mockApi.sliceLibraryFile.mockResolvedValue({
      job_id: 50,
      status: 'pending',
      status_url: '/api/v1/slice-jobs/50',
    });

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Helmet.3mf' },
      onClose: vi.fn(),
    });

    await waitFor(() => expect(screen.getByText('X1C')).toBeDefined());

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /^Slice$/ }));

    await waitFor(() => {
      const [, body] = mockApi.sliceLibraryFile.mock.calls[0];
      // Both slots populated: slot 1 with the user's white pick, slot
      // 2 auto-picked with grey from the colour-match scoring.
      expect(body.filament_presets).toHaveLength(2);
      expect(body.filament_presets[0]).toEqual({ source: 'cloud', id: 'F-WHITE' });
      expect(body.filament_presets[1]).toEqual({ source: 'cloud', id: 'F-GREY' });
    });
  });

  // ------------------------------------------------------------------
  // Slicer Pipelines (#1425) — Apply / Save integration in SliceModal
  // ------------------------------------------------------------------

  it('Apply pipeline dropdown is disabled and shows empty hint when no pipelines exist', async () => {
    mockApi.listSlicerPipelines.mockResolvedValue({ pipelines: [] });
    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' },
      onClose: vi.fn(),
    });
    await waitFor(() => {
      const select = screen.getByLabelText(/Apply pipeline/i) as HTMLSelectElement;
      expect(select.disabled).toBe(true);
      expect(select.querySelector('option')?.textContent).toMatch(/No saved pipelines/i);
    });
  });

  it('applies a saved pipeline to printer, process, and bed_type slots on selection', async () => {
    mockApi.listSlicerPipelines.mockResolvedValue({
      pipelines: [
        {
          id: 7,
          name: 'Production Batch',
          description: null,
          printer_preset: { source: 'local', id: '1' },
          process_preset: { source: 'local', id: '2' },
          filament_presets: [{ source: 'local', id: '3' }],
          bed_type: 'Textured PEI Plate',
          target_kind: 'printer_class',
          target_printer_id: null,
          target_model_class: null,
          fanout_strategy: 'max_parallel',
          created_by: null,
          created_at: '2026-06-27T00:00:00Z',
          updated_at: '2026-06-27T00:00:00Z',
        },
      ],
    });

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' },
      onClose: vi.fn(),
    });

    // Wait for presets + pipelines listing to populate the modal.
    await waitFor(() => {
      const select = screen.getByLabelText(/Apply pipeline/i) as HTMLSelectElement;
      expect(select.disabled).toBe(false);
      expect(within(select).getByText('Production Batch')).toBeDefined();
    });

    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText(/Apply pipeline/i), '7');

    // After applying, submitting the slice request should carry the
    // pipeline's preset refs end-to-end.
    mockApi.sliceLibraryFile.mockResolvedValue({
      job_id: 42,
      status: 'queued',
      status_url: '/api/v1/slice-jobs/42',
    });

    await user.click(screen.getByRole('button', { name: /^Slice$/ }));

    await waitFor(() => {
      expect(mockApi.sliceLibraryFile).toHaveBeenCalled();
      const [, body] = mockApi.sliceLibraryFile.mock.calls[0];
      expect(body.printer_preset).toEqual({ source: 'local', id: '1' });
      expect(body.process_preset).toEqual({ source: 'local', id: '2' });
      expect(body.filament_presets[0]).toEqual({ source: 'local', id: '3' });
      expect(body.bed_type).toBe('Textured PEI Plate');
    });
  });

  it('saves the current four-slot selection as a new pipeline when the user clicks Save as pipeline', async () => {
    mockApi.listSlicerPipelines.mockResolvedValue({ pipelines: [] });
    mockApi.createSlicerPipeline.mockResolvedValue({
      id: 99,
      name: 'My Default',
      description: null,
      printer_preset: { source: 'local', id: '1' },
      process_preset: { source: 'local', id: '2' },
      filament_presets: [{ source: 'local', id: '3' }],
      bed_type: null,
      target_kind: 'printer_class',
      target_printer_id: null,
      target_model_class: null,
      fanout_strategy: 'max_parallel',
      created_by: null,
      created_at: '2026-06-27T00:00:00Z',
      updated_at: '2026-06-27T00:00:00Z',
    });

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' },
      onClose: vi.fn(),
    });

    // Wait for auto-pick to populate all four slots from the fullThreeTier
    // listing — then Save as pipeline becomes enabled.
    const user = userEvent.setup();
    let saveBtn: HTMLButtonElement;
    await waitFor(() => {
      saveBtn = screen.getByRole('button', { name: /^Save as pipeline$/ }) as HTMLButtonElement;
      expect(saveBtn.disabled).toBe(false);
    });
    await user.click(saveBtn!);

    const nameInput = screen.getByLabelText(/New pipeline name/i);
    await user.type(nameInput, 'My Default');
    await user.click(screen.getByRole('button', { name: /^Save$/ }));

    await waitFor(() => {
      expect(mockApi.createSlicerPipeline).toHaveBeenCalledTimes(1);
      const body = mockApi.createSlicerPipeline.mock.calls[0][0];
      expect(body.name).toBe('My Default');
      // The four slots come from the auto-picked unified-presets listing —
      // local tier wins per SLICE_MODAL_TIER_ORDER.
      expect(body.printer_preset.source).toBe('local');
      expect(body.process_preset.source).toBe('local');
      expect(body.filament_presets[0].source).toBe('local');
    });
  });

});

// Pure-function tests for the filament slot picker. Pinned as a separate
// describe so the contract is visible without needing the modal mount.
describe('pickFilamentForSlot — printer-compat contract (#1851)', () => {
  // Index that recognises @BBL H2C / @BBL A1 tokens via the canonical
  // PRINTER_MODEL_MAP. Real production data comes through
  // ``api.getSlicerPrinterModels`` — the H2C / A1 fragments are the ones
  // the production registry ships.
  const index = buildCompatibilityIndex({
    'Bambu Lab A1': 'A1',
    'Bambu Lab H2C': 'H2C',
  });

  it('prefers a printer-compatible preset over a printer-mismatched one even with better colour match', () => {
    // The OP scenario for #1851: a Bambu Lab A1 is selected; the unused-slot
    // requirement carries the original H2C plate's PLA colour. With the
    // legacy soft-penalty scoring an H2C-bound preset whose colour matches
    // exactly could still rise above the A1-compatible PLA Basic whose
    // colour doesn't, and then the unused-slot substitution propagated the
    // H2C-bound preset across every unused slot — the CLI rejected with
    // ``filament preset Generic PLA @BBL H2C (slot 1) is not compatible
    // with printer Bambu Lab A1 0.4 nozzle``. The hard-skip contract makes
    // sure a mismatched preset is never chosen while any compatible
    // alternative exists, irrespective of metadata-score arithmetic.
    const presets = makeUnified({
      standard: {
        printer: [],
        process: [],
        filament: [
          {
            id: 'Generic PLA @BBL H2C',
            name: 'Generic PLA @BBL H2C',
            source: 'standard',
            filament_type: 'PLA',
            filament_colour: '#FF0000',
          },
          {
            id: 'Bambu PLA Basic @BBL A1',
            name: 'Bambu PLA Basic @BBL A1',
            source: 'standard',
            filament_type: 'PLA',
            filament_colour: '#FFFFFF',
          },
        ],
      },
    });
    const pick = pickFilamentForSlot(
      presets,
      { type: 'PLA', color: '#FF0000' },
      'Bambu Lab A1 0.4 nozzle',
      index,
    );
    expect(pick).toEqual({ source: 'standard', id: 'Bambu PLA Basic @BBL A1' });
  });

  it('falls back to a mismatched preset when no compatible alternative exists', () => {
    // Graceful degrade: when every available preset is printer-mismatched,
    // returning ``null`` would block the slice entirely. The picker keeps
    // its old behaviour of returning the best-scoring mismatch so the user
    // sees a populated dropdown they can correct, not an empty one.
    const presets = makeUnified({
      standard: {
        printer: [],
        process: [],
        filament: [
          {
            id: 'Generic PLA @BBL H2C',
            name: 'Generic PLA @BBL H2C',
            source: 'standard',
            filament_type: 'PLA',
            filament_colour: '#FF0000',
          },
        ],
      },
    });
    const pick = pickFilamentForSlot(
      presets,
      { type: 'PLA', color: '#FF0000' },
      'Bambu Lab A1 0.4 nozzle',
      index,
    );
    expect(pick).toEqual({ source: 'standard', id: 'Generic PLA @BBL H2C' });
  });

  it('treats a no-printer-context call as no-mismatch (every preset eligible)', () => {
    // ``printerName === null`` happens transiently on first render before the
    // printer pre-pick effect has run. ``presetCompatibility`` returns
    // ``unknown`` for every preset in that case, so the picker should just
    // pick by metadata score with no compatibility filter active.
    const presets = makeUnified({
      standard: {
        printer: [],
        process: [],
        filament: [
          {
            id: 'Generic PLA @BBL H2C',
            name: 'Generic PLA @BBL H2C',
            source: 'standard',
            filament_type: 'PLA',
            filament_colour: '#FF0000',
          },
        ],
      },
    });
    const pick = pickFilamentForSlot(
      presets,
      { type: 'PLA', color: '#FF0000' },
      null,
      index,
    );
    expect(pick).toEqual({ source: 'standard', id: 'Generic PLA @BBL H2C' });
  });
});
