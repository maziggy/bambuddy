/**
 * Tests for SliceModal.
 *
 * The modal handles preset selection across three tiers (cloud / local /
 * standard) + enqueueing a slice job. After enqueue success it hands the
 * job_id off to SliceJobTrackerProvider (which lives at app level) and
 * calls onClose. Polling, toasts, and query invalidation all happen in
 * the tracker — not here.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
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
    getSlicerPrinterModels: vi.fn(),
    getSlicerPresetValues: vi.fn(),
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
  getSlicerPrinterModels: ReturnType<typeof vi.fn>;
  getSlicerPresetValues: ReturnType<typeof vi.fn>;
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
    mockApi.getSlicerPresetValues.mockResolvedValue({ resolved: true, values: {}, reason: 'ok' });
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
        // An STL has no designed colour and the swatch was not touched, so
        // the slot is handed back to the backend's fallback chain rather
        // than being pinned to the picker's displayed default (#2977).
        filament_colours: [''],
      });
    });
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('offers "use the file\'s built-in settings" when the printer matches the design, and sends the flag (#2611)', async () => {
    const onClose = vi.fn();
    mockApi.sliceLibraryFile.mockResolvedValue({
      job_id: 42,
      status: 'pending',
      status_url: '/api/v1/slice-jobs/42',
    });
    // A project 3MF whose embedded printer matches a listed preset — the
    // printer pre-pick lands on it, so selectedPrinterName === embedded and
    // the "slice as designed" toggle is offered.
    mockApi.getLibraryFilePlates.mockResolvedValue({
      file_id: 100,
      filename: 'Designed.3mf',
      plates: [],
      is_multi_plate: false,
      embedded_printer: 'Bambu Lab X1 Carbon 0.4 nozzle',
      embedded_process: '0.20mm Standard',
    });

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Designed.3mf' },
      onClose,
    });

    const user = userEvent.setup();
    const toggle = (await screen.findByLabelText(
      /Use the file's built-in settings/,
    )) as HTMLInputElement;
    expect(toggle.checked).toBe(false);

    // All preset dropdowns are live until the toggle is on, then bypassed —
    // the printer included, so changing it can't silently drop the mode.
    const printerSelect = presetSelects()[0];
    const processSelect = presetSelects()[1];
    expect(printerSelect.disabled).toBe(false);
    expect(processSelect.disabled).toBe(false);
    await user.click(toggle);
    expect(printerSelect.disabled).toBe(true);
    expect(processSelect.disabled).toBe(true);

    await user.click(screen.getByRole('button', { name: /^Slice$/ }));
    await waitFor(() => {
      expect(mockApi.sliceLibraryFile).toHaveBeenCalledWith(
        100,
        expect.objectContaining({ use_embedded_settings: true }),
      );
    });
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('hides the embedded-settings toggle when the picked printer differs from the design (#2611)', async () => {
    // Embedded target is a model with no matching preset in the listing, so
    // the printer pre-pick falls back to the local default (Imported X1C),
    // which does not match — honouring embedded settings would risk the
    // wrong bed, so the toggle stays hidden.
    mockApi.getLibraryFilePlates.mockResolvedValue({
      file_id: 100,
      filename: 'Designed.3mf',
      plates: [],
      is_multi_plate: false,
      embedded_printer: 'Bambu Lab P1S 0.4 nozzle',
      embedded_process: '0.20mm Standard',
    });

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Designed.3mf' },
      onClose: vi.fn(),
    });

    await waitFor(() => expect(screen.getByText('Imported X1C 0.4')).toBeDefined());
    expect(screen.queryByLabelText(/Use the file's built-in settings/)).toBeNull();
  });

  // #2622: a MakerWorld 3MF designed for another printer carries the author's
  // own process tweaks. BambuStudio records which keys deviate from the stock
  // preset inside the file, so a cross-printer re-slice can carry them instead
  // of losing them to the picked process profile.
  const designedFor = {
    file_id: 100,
    filename: 'Designed.3mf',
    plates: [],
    is_multi_plate: false,
    embedded_printer: 'Bambu Lab A1 0.4 nozzle',
    embedded_process: '0.20mm Standard @BBL A1',
    design_overrides: [
      { key: 'wall_loops', value: '5', printer_coupled: false, preset_defining: false },
      { key: 'sparse_infill_density', value: '100%', printer_coupled: false, preset_defining: false },
      { key: 'outer_wall_speed', value: '200', printer_coupled: true, preset_defining: false },
    ],
  };

  // The designer's settings are shown inside the process-settings panel now,
  // against the options they belong to, rather than in a list of their own.
  // The payload contract below is unchanged: their *values* still travel as
  // design_overrides keys, read from the file by the backend.
  async function openDesignSection() {
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /Process settings/ }));
    await screen.findByPlaceholderText('Search settings');
    // Every designer key must be reachable, including expert-tier ones.
    await user.click(screen.getByRole('button', { name: 'Expert' }));
    return user;
  }

  /** The panel's per-option "use the file's value" checkbox, by option key. */
  function sourceCheckbox(key: string): HTMLInputElement {
    const boxes = screen.getAllByRole('checkbox') as HTMLInputElement[];
    const found = boxes.find((b) => (b.getAttribute('aria-label') ?? '').includes(key));
    if (!found) throw new Error(`no source checkbox for ${key}`);
    return found;
  }

  it('carries nothing out of the file until it is asked to (#2942)', async () => {
    mockApi.sliceLibraryFile.mockResolvedValue({
      job_id: 42,
      status: 'pending',
      status_url: '/api/v1/slice-jobs/42',
    });
    mockApi.getLibraryFilePlates.mockResolvedValue(designedFor);

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Designed.3mf' },
      onClose: vi.fn(),
    });

    // Nothing pre-ticked: "Use the file's built-in settings" is off, so the
    // slice runs on the picked preset and the file's own values wait to be
    // asked for by name.
    await waitFor(() => expect(screen.getByRole('button', { name: /^Slice$/ })).toBeEnabled());

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /^Slice$/ }));

    await waitFor(() => expect(mockApi.sliceLibraryFile).toHaveBeenCalled());
    const payload = mockApi.sliceLibraryFile.mock.calls[0][1] as { design_overrides?: string[] };
    // Empty, not absent: the backend reads the difference. A list that is
    // there and empty says the user was shown the file's settings and took
    // none of them, which also stands the support carry-over down (#1881).
    expect(payload.design_overrides).toEqual([]);
  });

  // The file's layer height is the one deviation that must not ride along: it
  // *is* the process preset the user picked, so carrying it silently sliced a
  // file at 0.2 while the process dropdown still read "0.08mm High Quality".
  const designedWithLayerHeight = {
    ...designedFor,
    design_overrides: [
      { key: 'wall_loops', value: '5', printer_coupled: false, preset_defining: false },
      { key: 'layer_height', value: '0.2', printer_coupled: false, preset_defining: true },
    ],
  };

  it("leaves the file's layer height off even in bulk, so the picked preset wins", async () => {
    mockApi.sliceLibraryFile.mockResolvedValue({
      job_id: 42,
      status: 'pending',
      status_url: '/api/v1/slice-jobs/42',
    });
    mockApi.getLibraryFilePlates.mockResolvedValue(designedWithLayerHeight);

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Designed.3mf' },
      onClose: vi.fn(),
    });

    await waitFor(() => expect(screen.getByRole('button', { name: /^Slice$/ })).toBeEnabled());

    const user = await openDesignSection();
    await user.click(screen.getByRole('button', { name: /Use the designer's settings/ }));
    await user.click(screen.getByRole('button', { name: /^Slice$/ }));

    await waitFor(() => expect(mockApi.sliceLibraryFile).toHaveBeenCalled());
    const payload = mockApi.sliceLibraryFile.mock.calls[0][1] as { design_overrides?: string[] };
    // The bulk action takes the keys that carry across printers. Layer height
    // *is* the preset that was picked, so it stays a per-key decision.
    expect(payload.design_overrides).toEqual(['wall_loops']);
  });

  it('still applies the file\'s layer height when the user ticks it', async () => {
    mockApi.sliceLibraryFile.mockResolvedValue({
      job_id: 42,
      status: 'pending',
      status_url: '/api/v1/slice-jobs/42',
    });
    mockApi.getLibraryFilePlates.mockResolvedValue(designedWithLayerHeight);

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Designed.3mf' },
      onClose: vi.fn(),
    });

    await waitFor(() => expect(screen.getByRole('button', { name: /^Slice$/ })).toBeEnabled());

    const user = await openDesignSection();
    // Search rather than page-hop: it cuts across every page. The tick's
    // aria-label carries the option's schema label, not its key.
    await user.type(screen.getByPlaceholderText('Search settings'), 'layer height');
    await waitFor(() => expect(sourceCheckbox('Layer height')).toBeInTheDocument());
    await user.click(sourceCheckbox('Layer height'));
    await user.click(screen.getByRole('button', { name: /^Slice$/ }));

    await waitFor(() => expect(mockApi.sliceLibraryFile).toHaveBeenCalled());
    const payload = mockApi.sliceLibraryFile.mock.calls[0][1] as { design_overrides?: string[] };
    expect(payload.design_overrides).toEqual(['layer_height']);
  });

  it('lists every changed setting with its value and flags the machine-coupled ones (#2622)', async () => {
    mockApi.getLibraryFilePlates.mockResolvedValue(designedFor);

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Designed.3mf' },
      onClose: vi.fn(),
    });

    const user = await openDesignSection();

    // Offered but not taken: the row is flagged and the control still shows
    // the baseline, until the tick says the file's value should win.
    await user.type(screen.getByPlaceholderText('Search settings'), 'wall loops');
    await waitFor(() => expect(sourceCheckbox('Wall loops')).toBeInTheDocument());
    expect(sourceCheckbox('Wall loops').checked).toBe(false);
    expect(screen.getByLabelText(/^Wall loops/)).not.toHaveValue(5);
    await user.click(sourceCheckbox('Wall loops'));
    await waitFor(() => expect(screen.getByLabelText(/^Wall loops/)).toHaveValue(5));

    await user.clear(screen.getByPlaceholderText('Search settings'));
    await user.type(screen.getByPlaceholderText('Search settings'), 'outer wall speed');
    // The machine-coupled one is present and flagged, just not pre-ticked.
    await waitFor(() => expect(screen.getAllByText("designer's printer").length).toBeGreaterThan(0));
    expect(sourceCheckbox('Outer wall').checked).toBe(false);
  });

  it('lets the user opt a machine-coupled setting in on its own (#2622)', async () => {
    mockApi.sliceLibraryFile.mockResolvedValue({
      job_id: 42,
      status: 'pending',
      status_url: '/api/v1/slice-jobs/42',
    });
    mockApi.getLibraryFilePlates.mockResolvedValue(designedFor);

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Designed.3mf' },
      onClose: vi.fn(),
    });

    const user = await openDesignSection();

    await user.type(screen.getByPlaceholderText('Search settings'), 'outer wall speed');
    await waitFor(() => expect(sourceCheckbox('Outer wall')).toBeInTheDocument());
    await user.click(sourceCheckbox('Outer wall'));

    await user.click(screen.getByRole('button', { name: /^Slice$/ }));

    await waitFor(() => expect(mockApi.sliceLibraryFile).toHaveBeenCalled());
    const payload = mockApi.sliceLibraryFile.mock.calls[0][1] as { design_overrides?: string[] };
    // Only the key that was asked for -- the two printer-independent ones are
    // still on offer and still untouched.
    expect(payload.design_overrides).toEqual(['outer_wall_speed']);
  });

  it('omits design_overrides entirely for a file that offered nothing (#2942)', async () => {
    // The other half of the distinction the backend reads: no list at all
    // means there was nothing to decide, which leaves #1881's support
    // carry-over unconditional for sources -- an OrcaSlicer export, say --
    // that record no deviations to tick in the first place.
    mockApi.sliceLibraryFile.mockResolvedValue({
      job_id: 42,
      status: 'pending',
      status_url: '/api/v1/slice-jobs/42',
    });
    mockApi.getLibraryFilePlates.mockResolvedValue({ ...designedFor, design_overrides: [] });

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Designed.3mf' },
      onClose: vi.fn(),
    });

    await waitFor(() => expect(screen.getByRole('button', { name: /^Slice$/ })).toBeEnabled());
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /^Slice$/ }));

    await waitFor(() => expect(mockApi.sliceLibraryFile).toHaveBeenCalled());
    expect(mockApi.sliceLibraryFile.mock.calls[0][1]).not.toHaveProperty('design_overrides');
  });

  // #2942: the per-key ticks answer the same question the toggle above them
  // does -- where do this slice's settings come from -- so one governs the
  // other. They used to be pre-ticked whatever it said, which is how a slice
  // with the toggle deliberately off still took sixteen values from the file.
  const designedForThisPrinter = {
    ...designedFor,
    embedded_printer: 'Bambu Lab X1 Carbon 0.4 nozzle',
    embedded_process: '0.20mm Standard',
    // Seam position sits on the panel's opening page at the simple tier, so
    // its tick can be read without driving a panel the toggle has disabled.
    design_overrides: [
      ...designedFor.design_overrides,
      { key: 'seam_position', value: 'rear', printer_coupled: false, preset_defining: false },
    ],
  };

  it('shows every setting as coming from the file while the built-in toggle is on (#2942)', async () => {
    mockApi.sliceLibraryFile.mockResolvedValue({
      job_id: 42,
      status: 'pending',
      status_url: '/api/v1/slice-jobs/42',
    });
    mockApi.getLibraryFilePlates.mockResolvedValue(designedForThisPrinter);

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Designed.3mf' },
      onClose: vi.fn(),
    });

    const user = userEvent.setup();
    await user.click(await screen.findByLabelText(/Use the file's built-in settings/));
    await user.click(await screen.findByRole('button', { name: /Process settings/ }));
    await screen.findByPlaceholderText('Search settings');

    // A readout, not a control: on this path the file drives the whole slice,
    // so a tick that said otherwise would be describing the wrong run. The
    // panel is inactive throughout -- nothing here is sent.
    await waitFor(() => expect(sourceCheckbox('Seam position').checked).toBe(true));
    expect(sourceCheckbox('Seam position').disabled).toBe(true);

    await user.click(screen.getByRole('button', { name: /^Slice$/ }));
    await waitFor(() => expect(mockApi.sliceLibraryFile).toHaveBeenCalled());
    const payload = mockApi.sliceLibraryFile.mock.calls[0][1] as {
      design_overrides?: string[];
      use_embedded_settings?: boolean;
    };
    expect(payload.use_embedded_settings).toBe(true);
    expect(payload).not.toHaveProperty('design_overrides');
  });

  it('clears them again when the built-in toggle goes back off (#2942)', async () => {
    mockApi.sliceLibraryFile.mockResolvedValue({
      job_id: 42,
      status: 'pending',
      status_url: '/api/v1/slice-jobs/42',
    });
    mockApi.getLibraryFilePlates.mockResolvedValue(designedForThisPrinter);

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Designed.3mf' },
      onClose: vi.fn(),
    });

    const user = userEvent.setup();
    const toggle = await screen.findByLabelText(/Use the file's built-in settings/);
    await user.click(toggle);
    await user.click(toggle);

    await openDesignSection();
    await user.type(screen.getByPlaceholderText('Search settings'), 'wall loops');
    await waitFor(() => expect(sourceCheckbox('Wall loops').checked).toBe(false));

    await user.click(screen.getByRole('button', { name: /^Slice$/ }));
    await waitFor(() => expect(mockApi.sliceLibraryFile).toHaveBeenCalled());
    const payload = mockApi.sliceLibraryFile.mock.calls[0][1] as {
      design_overrides?: string[];
      use_embedded_settings?: boolean;
    };
    expect(payload.design_overrides).toEqual([]);
    expect(payload).not.toHaveProperty('use_embedded_settings');
  });

  it("takes the designer's settings in bulk, without the machine-coupled ones (#2942)", async () => {
    mockApi.sliceLibraryFile.mockResolvedValue({
      job_id: 42,
      status: 'pending',
      status_url: '/api/v1/slice-jobs/42',
    });
    mockApi.getLibraryFilePlates.mockResolvedValue(designedFor);

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Designed.3mf' },
      onClose: vi.fn(),
    });

    const user = await openDesignSection();
    // Without this the file's settings would be reachable only by hunting for
    // chips across six pages of 348 options.
    expect(screen.getByText(/The designer changed 3 process settings/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Use the designer's settings/ }));

    await user.click(screen.getByRole('button', { name: /^Slice$/ }));
    await waitFor(() => expect(mockApi.sliceLibraryFile).toHaveBeenCalled());
    const payload = mockApi.sliceLibraryFile.mock.calls[0][1] as { design_overrides?: string[] };
    expect([...(payload.design_overrides ?? [])].sort()).toEqual([
      'sparse_infill_density',
      'wall_loops',
    ]);
  });

  it('hides the section for a file that changes nothing (#2622)', async () => {
    mockApi.getLibraryFilePlates.mockResolvedValue({ ...designedFor, design_overrides: [] });

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Designed.3mf' },
      onClose: vi.fn(),
    });

    await waitFor(() => expect(screen.getByRole('button', { name: /^Slice$/ })).toBeEnabled());
    // The panel still exists — it is the editor — but nothing is marked as
    // coming from the file.
    expect(screen.queryByText('from file')).toBeNull();
    expect(screen.queryByText("designer's printer")).toBeNull();
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

  it('sends the layout flags only for the boxes the user ticked (#2548)', async () => {
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
    await user.click(screen.getByRole('checkbox', { name: /Auto-orient objects/ }));
    await user.click(screen.getByRole('button', { name: /^Slice$/ }));

    await waitFor(() => {
      const [, body] = vi.mocked(mockApi.sliceLibraryFile).mock.calls[0];
      expect(body).toHaveProperty('auto_orient', true);
      // The untouched box is omitted, not sent as false. The sidecar reads
      // any present value as truthy, so a literal false would arrange every
      // slice — the flag has to travel by absence.
      expect(body).not.toHaveProperty('auto_arrange');
    });
  });

  it('omits both layout flags when neither box is ticked (#2548)', async () => {
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

    await userEvent.setup().click(screen.getByRole('button', { name: /^Slice$/ }));

    await waitFor(() => {
      const [, body] = vi.mocked(mockApi.sliceLibraryFile).mock.calls[0];
      expect(body).not.toHaveProperty('auto_orient');
      expect(body).not.toHaveProperty('auto_arrange');
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

  // #2712: the filament list is positional the whole way down — index 0 is
  // slot 1, and the backend forwards it as filament_1.json..filament_N.json.
  // A MakerWorld source that ships slice_info and paints with slot 4 alone
  // used to yield a one-row list, so the user's only pick was bound to slot 1
  // and slot 4 sliced with the source's embedded default: picking PETG gave a
  // PLA print. The modal now asks for every project slot so the positions
  // line up.
  it('requests every project slot, not just the ones the plate prints with', async () => {
    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Tunnel.3mf' },
      onClose: vi.fn(),
    });

    await waitFor(() => expect(mockApi.getLibraryFileFilamentRequirements).toHaveBeenCalled());
    const [, plateArg, , fullSlots] = mockApi.getLibraryFileFilamentRequirements.mock.calls[0];
    expect(plateArg).toBe(1);
    expect(fullSlots).toBe(true);
  });

  it('sends the pick for a high-numbered slot at its own index', async () => {
    // Four project slots, only slot 4 printed. The pick must arrive as the
    // FOURTH entry; anywhere else and the slicer binds it to the wrong slot.
    mockApi.getLibraryFilePlates.mockResolvedValue({
      file_id: 100,
      filename: 'Tunnel.3mf',
      is_multi_plate: false,
      plates: [
        {
          index: 1,
          name: 'Plate 1',
          objects: ['Tunnel'],
          has_thumbnail: false,
          thumbnail_url: null,
          print_time_seconds: 1200,
          filament_used_grams: 105,
          filaments: [],
        },
      ],
    });
    mockApi.getLibraryFileFilamentRequirements.mockResolvedValue({
      file_id: 100,
      filename: 'Tunnel.3mf',
      plate_id: 1,
      filaments: [
        { slot_id: 1, type: 'PLA', color: '#38CC0A', used_grams: 0, used_meters: 0, used_in_plate: false },
        { slot_id: 2, type: 'PLA', color: '#161616', used_grams: 0, used_meters: 0, used_in_plate: false },
        { slot_id: 3, type: 'PLA', color: '#898989', used_grams: 0, used_meters: 0, used_in_plate: false },
        { slot_id: 4, type: 'PLA', color: '#898989', used_grams: 105, used_meters: 35, used_in_plate: true },
      ],
    });
    mockApi.getSlicerPresets.mockResolvedValue({
      cloud: {
        printer: [{ id: 'P1', name: 'X1C', source: 'cloud' }],
        process: [{ id: 'PR1', name: '0.20mm', source: 'cloud' }],
        filament: [
          { id: 'F-PLA', name: 'Cloud PLA Grey', source: 'cloud', filament_type: 'PLA', filament_colour: '#898989' },
          { id: 'F-PETG', name: 'Cloud PETG', source: 'cloud', filament_type: 'PETG', filament_colour: '#00FF00' },
        ],
      },
      local: { printer: [], process: [], filament: [] },
      standard: { printer: [], process: [], filament: [] },
      cloud_status: 'ok',
      orca_cloud: { printer: [], process: [], filament: [] },
      orca_cloud_status: 'ok',
    });
    mockApi.sliceLibraryFile.mockResolvedValue({
      job_id: 51,
      status: 'pending',
      status_url: '/api/v1/slice-jobs/51',
    });

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Tunnel.3mf' },
      onClose: vi.fn(),
    });

    await waitFor(() => expect(screen.getByText('X1C')).toBeDefined());

    // 1 printer + 1 process + 1 bed-type + 4 filament rows.
    const selects = presetSelects();
    expect(selects).toHaveLength(7);
    // Only slot 4 is selectable — the other three are the padding.
    expect([selects[3].disabled, selects[4].disabled, selects[5].disabled]).toEqual([true, true, true]);
    expect(selects[6].disabled).toBe(false);

    const user = userEvent.setup();
    await user.selectOptions(selects[6], 'cloud:F-PETG');
    await user.click(screen.getByRole('button', { name: /^Slice$/ }));

    await waitFor(() => {
      const [, body] = mockApi.sliceLibraryFile.mock.calls[0];
      expect(body.filament_presets).toHaveLength(4);
      expect(body.filament_presets[3]).toEqual({ source: 'cloud', id: 'F-PETG' });
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

  /**
   * Per-slot filament colour (#2977).
   *
   * A filament preset carries no colour in either slicer -- colour is a
   * per-project property their GUIs set from the plate -- so a slice that
   * supplies none records the CLI's compiled-in #00AE42 for every slot. That
   * is what made every internal-slicer thumbnail Bambu green regardless of
   * the filament picked, and what made the print dialog report a colour
   * mismatch against the AMS slot it had just correctly mapped to.
   *
   * The swatch is the only place the colour can come from for an STL, which
   * has none anywhere else, so it is offered for single-slot sources too.
   */
  describe('filament colour swatch', () => {
    function colourInputs(): HTMLInputElement[] {
      return screen
        .getAllByLabelText('Filament colour')
        .filter((el): el is HTMLInputElement => el instanceof HTMLInputElement);
    }

    it('shows the hex beside the swatch so it reads as a control, not a decoration', async () => {
      // The reason this exists: a bare dot next to a label was taken for the
      // read-only swatch multi-colour rows already had, so on the STL -- the
      // one source with no colour to inherit -- nothing said it was settable.
      renderWithTracker({
        source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' },
        onClose: vi.fn(),
      });

      await waitFor(() => expect(screen.getByText('My Custom X1C')).toBeDefined());
      expect(screen.getByText('#00AE42')).toBeDefined();
    });

    it('puts the whole control on the dropdown row, not in the label', async () => {
      // Sitting beside the <select> and styled like it is what makes it read
      // as a control. In the label row it read as a caption on the label.
      renderWithTracker({
        source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' },
        onClose: vi.fn(),
      });

      await waitFor(() => expect(screen.getByText('My Custom X1C')).toBeDefined());
      const row = colourInputs()[0].closest('div');
      expect(row?.querySelector('select')).not.toBeNull();
    });

    it('wraps the swatch and its hex in one label bound to the input', async () => {
      // So a click anywhere on the pill opens the picker, rather than only a
      // 16px dot being live.
      renderWithTracker({
        source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' },
        onClose: vi.fn(),
      });

      await waitFor(() => expect(screen.getByText('My Custom X1C')).toBeDefined());
      const pill = screen.getByText('#00AE42').closest('label');
      expect(pill?.getAttribute('for')).toBe(colourInputs()[0].id);
      expect(pill?.contains(colourInputs()[0])).toBe(true);
    });

    it('the hex follows the swatch when it is changed', async () => {
      renderWithTracker({
        source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' },
        onClose: vi.fn(),
      });

      await waitFor(() => expect(screen.getByText('My Custom X1C')).toBeDefined());
      fireEvent.change(colourInputs()[0], { target: { value: '#e8b00c' } });
      await waitFor(() => expect(screen.getByText('#E8B00C')).toBeDefined());
    });

    it('paints the colour on the input itself, not only via the native swatch', async () => {
      // An engine that does not paint ::-webkit-color-swatch would otherwise
      // leave an empty ring, which is indistinguishable from no swatch.
      renderWithTracker({
        source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' },
        onClose: vi.fn(),
      });

      await waitFor(() => expect(screen.getByText('My Custom X1C')).toBeDefined());
      expect(colourInputs()[0].style.backgroundColor).not.toBe('');
    });

    it('offers a colour swatch for a single-slot STL, which has no colour of its own', async () => {
      renderWithTracker({
        source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' },
        onClose: vi.fn(),
      });

      await waitFor(() => expect(screen.getByText('My Custom X1C')).toBeDefined());
      expect(colourInputs()).toHaveLength(1);
    });

    it("shows the slicer's own default for a source that carries no colour", async () => {
      // Not an invented placeholder: #00AE42 is exactly what the slice would
      // record if nothing were sent, so the swatch tells the truth about the
      // file that is about to be produced.
      renderWithTracker({
        source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' },
        onClose: vi.fn(),
      });

      await waitFor(() => expect(screen.getByText('My Custom X1C')).toBeDefined());
      expect(colourInputs()[0].value).toBe('#00ae42');
    });

    it("pre-fills each slot from the source plate's designed colour", async () => {
      mockApi.getLibraryFilePlates.mockResolvedValue(makeMultiColorPlateResponse());
      mockApi.getLibraryFileFilamentRequirements.mockResolvedValue(makeMultiColorRequirementsResponse());
      mockApi.getSlicerPresets.mockResolvedValue(makeColorAwarePresets());

      renderWithTracker({
        source: { kind: 'libraryFile', id: 100, filename: 'TwoColor.3mf' },
        onClose: vi.fn(),
      });

      await waitFor(() => expect(screen.getByText('X1C')).toBeDefined());
      expect(colourInputs().map((i) => i.value)).toEqual(['#000000', '#ffffff']);
    });

    it("sends the source plate's colours when the swatches are left alone", async () => {
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
      await userEvent.setup().click(screen.getByRole('button', { name: /^Slice$/ }));

      await waitFor(() => {
        expect(mockApi.sliceLibraryFile).toHaveBeenCalledWith(
          100,
          expect.objectContaining({ filament_colours: ['#000000', '#FFFFFF'] }),
        );
      });
    });

    it('sends an empty string for a slot with no colour, so the backend fallbacks still run', async () => {
      // A sent colour outranks the preset's own default_filament_colour, so
      // pinning the picker's displayed #00AE42 here would silently discard
      // the real colour of an Orca-imported profile that carries one.
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
      await userEvent.setup().click(screen.getByRole('button', { name: /^Slice$/ }));

      await waitFor(() => {
        expect(mockApi.sliceLibraryFile).toHaveBeenCalledWith(
          100,
          expect.objectContaining({ filament_colours: [''] }),
        );
      });
    });

    it('sends the user\'s pick, upper-cased, once a swatch is changed', async () => {
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
      // A colour input has no text entry; fire the change the picker would.
      fireEvent.change(colourInputs()[0], { target: { value: '#e8b00c' } });
      await waitFor(() => expect(colourInputs()[0].value).toBe('#e8b00c'));

      await user.click(screen.getByRole('button', { name: /^Slice$/ }));
      await waitFor(() => {
        expect(mockApi.sliceLibraryFile).toHaveBeenCalledWith(
          100,
          expect.objectContaining({ filament_colours: ['#E8B00C'] }),
        );
      });
    });

    it('only overrides the slot that was changed', async () => {
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
      fireEvent.change(colourInputs()[1], { target: { value: '#112233' } });

      await userEvent.setup().click(screen.getByRole('button', { name: /^Slice$/ }));
      await waitFor(() => {
        expect(mockApi.sliceLibraryFile).toHaveBeenCalledWith(
          100,
          expect.objectContaining({ filament_colours: ['#000000', '#112233'] }),
        );
      });
    });

    it('trims the alpha byte for display but submits the colour whole', async () => {
      // The AMS reports colours with an alpha byte and a source 3MF can carry
      // one; <input type="color"> accepts only the 6-digit form.
      mockApi.getLibraryFileFilamentRequirements.mockResolvedValue({
        file_id: 100,
        filename: 'Alpha.3mf',
        plate_id: 1,
        filaments: [{ slot_id: 1, type: 'PLA', color: '#E8B00CFF', used_grams: 10, used_meters: 3 }],
      });
      mockApi.sliceLibraryFile.mockResolvedValue({
        job_id: 42,
        status: 'pending',
        status_url: '/api/v1/slice-jobs/42',
      });

      renderWithTracker({
        source: { kind: 'libraryFile', id: 100, filename: 'Alpha.3mf' },
        onClose: vi.fn(),
      });

      await waitFor(() => expect(screen.getByText('My Custom X1C')).toBeDefined());
      expect(colourInputs()[0].value).toBe('#e8b00c');

      await userEvent.setup().click(screen.getByRole('button', { name: /^Slice$/ }));
      await waitFor(() => {
        expect(mockApi.sliceLibraryFile).toHaveBeenCalledWith(
          100,
          expect.objectContaining({ filament_colours: ['#E8B00CFF'] }),
        );
      });
    });

    it('is disabled in "slice as designed" mode, which sends no filament profiles', async () => {
      mockApi.getLibraryFilePlates.mockResolvedValue({
        file_id: 100,
        filename: 'Designed.3mf',
        plates: [],
        is_multi_plate: false,
        embedded_printer: 'Imported X1C 0.4',
        embedded_process: '0.20mm Standard',
      });

      renderWithTracker({
        source: { kind: 'libraryFile', id: 100, filename: 'Designed.3mf' },
        onClose: vi.fn(),
      });

      await waitFor(() => expect(screen.getByText('My Custom X1C')).toBeDefined());
      const toggle = screen.getByRole('checkbox', { name: /built-in settings/i });
      expect(colourInputs()[0].disabled).toBe(false);
      await userEvent.setup().click(toggle);
      expect(colourInputs()[0].disabled).toBe(true);
    });
  });

});

// Pure-function tests for the filament slot picker. Pinned as a separate
// describe so the contract is visible without needing the modal mount.
/**
 * The slice dialog switches to a two-column layout once there is room for it,
 * and the process-settings panel then owns the right-hand column. The global
 * test setup pins matchMedia to `matches: false`, so every other test in this
 * file exercises the narrow single-stack path; these override it.
 */
describe('SliceModal — process settings in "slice as designed" mode', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockApi.getSlicerPresets.mockResolvedValue(fullThreeTier);
    mockApi.getSlicerPresetValues.mockResolvedValue({ resolved: true, values: {}, reason: 'ok' });
    mockApi.listSlicerPipelines.mockResolvedValue({ pipelines: [] });
    mockApi.getSlicerPrinterModels.mockResolvedValue({});
    mockApi.getLibraryFilePlates.mockResolvedValue({
      file_id: 100,
      filename: 'Designed.3mf',
      plates: [],
      is_multi_plate: false,
      embedded_printer: 'Bambu Lab X1 Carbon 0.4 nozzle',
      embedded_process: '0.20mm Standard',
    });
    mockApi.getLibraryFileFilamentRequirements.mockResolvedValue({
      file_id: 100, filename: 'Designed.3mf', plate_id: 1, filaments: [],
    });
  });

  it('disables the panel rather than removing it', async () => {
    const user = userEvent.setup();
    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Designed.3mf' },
      onClose: vi.fn(),
    });

    const toggle = (await screen.findByLabelText(/Use the file's built-in settings/)) as HTMLInputElement;
    const header = await screen.findByRole('button', { name: /Process settings/ });
    await user.click(header);
    const search = await screen.findByPlaceholderText('Search settings');
    expect(search).toBeEnabled();

    await user.click(toggle);

    // Still on screen — hiding it made the dialog look like it had lost a
    // feature — but nothing in it can be operated, because nothing in it is
    // sent on this path.
    expect(screen.getByPlaceholderText('Search settings')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Expert' })).toBeDisabled();
    expect(screen.getByText(/Not used while/)).toBeInTheDocument();
    expect(screen.getByText('Inactive')).toBeInTheDocument();
  });

  it('sends no process overrides once the file drives the slice', async () => {
    mockApi.sliceLibraryFile.mockResolvedValue({ job_id: 42, status: 'pending', status_url: '/x' });
    const user = userEvent.setup();
    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Designed.3mf' },
      onClose: vi.fn(),
    });

    // Edit something, then hand the slice over to the file's own settings.
    await user.click(await screen.findByRole('button', { name: /Process settings/ }));
    const input = await screen.findByLabelText(/^Layer height/);
    await user.clear(input);
    await user.type(input, '0.16');

    await user.click(screen.getByLabelText(/Use the file's built-in settings/));
    await user.click(screen.getByRole('button', { name: /^Slice$/ }));

    await waitFor(() => expect(mockApi.sliceLibraryFile).toHaveBeenCalled());
    const payload = mockApi.sliceLibraryFile.mock.calls[0][1] as Record<string, unknown>;
    expect(payload.use_embedded_settings).toBe(true);
    expect(payload).not.toHaveProperty('process_overrides');
  });
});

describe('SliceModal — process settings layout', () => {
  const setViewport = (wide: boolean) => {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: (query: string) => ({
        matches: wide,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => true,
      }),
    });
  };

  beforeEach(() => {
    vi.clearAllMocks();
    mockApi.getSlicerPresets.mockResolvedValue(fullThreeTier);
    mockApi.getSlicerPresetValues.mockResolvedValue({ resolved: true, values: {}, reason: 'ok' });
    mockApi.listSlicerPipelines.mockResolvedValue({ pipelines: [] });
    mockApi.getLibraryFilePlates.mockResolvedValue({
      file_id: 100,
      filename: 'Cube.stl',
      plates: [],
      is_multi_plate: false,
    });
    mockApi.getLibraryFileFilamentRequirements.mockResolvedValue({
      file_id: 100,
      filename: 'Cube.stl',
      plate_id: 1,
      filaments: [],
    });
  });

  afterEach(() => setViewport(false));

  it('keeps the panel collapsed behind a disclosure in the narrow layout', async () => {
    setViewport(false);
    renderWithTracker({ source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' }, onClose: vi.fn() });

    const header = await screen.findByRole('button', { name: /Process settings/ });
    expect(header).toBeEnabled();
    expect(screen.queryByPlaceholderText('Search settings')).not.toBeInTheDocument();

    await userEvent.setup().click(header);
    await waitFor(() => expect(screen.getByPlaceholderText('Search settings')).toBeInTheDocument());
  });

  it('opens the panel without a click once it has a column of its own', async () => {
    setViewport(true);
    renderWithTracker({ source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' }, onClose: vi.fn() });

    // No disclosure to operate: the panel is the column, so its header is
    // inert rather than offering to collapse something that has room.
    await waitFor(() => expect(screen.getByPlaceholderText('Search settings')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /Process settings/ })).toBeDisabled();
  });
});

/**
 * Process and filament lists hold back presets that resolve to a *different*
 * printer, behind a per-slot "Show all". Two things must never be hidden: a
 * preset whose compatibility is merely unknown, and whatever is currently
 * selected.
 */
describe('SliceModal — presets filtered by the selected printer', () => {
  const presets: UnifiedPresetsResponse = {
    cloud: { printer: [], process: [], filament: [] },
    orca_cloud: { printer: [], process: [], filament: [] },
    local: { printer: [], process: [], filament: [] },
    standard: {
      printer: [
        { id: 'Bambu Lab X1 Carbon 0.4 nozzle', name: 'Bambu Lab X1 Carbon 0.4 nozzle', source: 'standard' },
      ],
      process: [
        { id: 'p-x1c', name: '0.20mm Standard @BBL X1C', source: 'standard' },
        { id: 'p-h2d', name: '0.20mm Standard @BBL H2D', source: 'standard' },
        { id: 'p-a1m', name: '0.20mm Standard @BBL A1M', source: 'standard' },
        // No printer tag at all — compatibility is unknown, never hidden.
        { id: 'p-custom', name: 'My own profile', source: 'standard' },
      ],
      filament: [{ id: 'f-x1c', name: 'Bambu PLA Basic @BBL X1C', source: 'standard' }],
    },
    cloud_status: 'ok',
    orca_cloud_status: 'ok',
  } as UnifiedPresetsResponse;

  const processOptionNames = () =>
    Array.from(presetSelects()[1].options).map((o) => o.textContent);

  beforeEach(() => {
    vi.clearAllMocks();
    mockApi.getSlicerPresets.mockResolvedValue(presets);
    mockApi.getSlicerPrinterModels.mockResolvedValue({ 'Bambu Lab X1 Carbon': 'X1C' });
    mockApi.listSlicerPipelines.mockResolvedValue({ pipelines: [] });
    mockApi.getLibraryFilePlates.mockResolvedValue({
      file_id: 100, filename: 'Cube.stl', plates: [], is_multi_plate: false,
    });
    mockApi.getLibraryFileFilamentRequirements.mockResolvedValue({
      file_id: 100, filename: 'Cube.stl', plate_id: 1, filaments: [],
    });
  });

  const open = async () => {
    renderWithTracker({ source: { kind: 'libraryFile', id: 100, filename: 'Cube.stl' }, onClose: vi.fn() });
    await waitFor(() => expect(presetSelects().length).toBeGreaterThan(1));
  };

  it('leaves out presets belonging to another printer', async () => {
    await open();
    await waitFor(() => expect(processOptionNames()).toContain('0.20mm Standard @BBL X1C'));
    expect(processOptionNames()).not.toContain('0.20mm Standard @BBL H2D');
    expect(processOptionNames()).not.toContain('0.20mm Standard @BBL A1M');
  });

  it('keeps a preset whose compatibility cannot be determined', async () => {
    await open();
    // An untagged preset carries no evidence either way; hiding it would make
    // a user's own imported profiles vanish.
    await waitFor(() => expect(processOptionNames()).toContain('My own profile'));
  });

  it('says how many it held back and reveals them on request', async () => {
    const user = userEvent.setup();
    await open();

    const hidden = await screen.findByText('2 hidden');
    expect(hidden).toBeInTheDocument();

    await user.click(within(hidden.parentElement as HTMLElement).getByRole('button', { name: 'Show all' }));
    await waitFor(() => expect(processOptionNames()).toContain('0.20mm Standard @BBL H2D'));
    expect(processOptionNames()).toContain('0.20mm Standard @BBL A1M');
  });

  it('collapses the list again on Show fewer', async () => {
    const user = userEvent.setup();
    await open();

    await user.click((await screen.findAllByRole('button', { name: 'Show all' }))[0]);
    await waitFor(() => expect(processOptionNames()).toContain('0.20mm Standard @BBL H2D'));

    await user.click(screen.getAllByRole('button', { name: 'Show fewer' })[0]);
    await waitFor(() => expect(processOptionNames()).not.toContain('0.20mm Standard @BBL H2D'));
  });

  it('never hides the preset that is currently selected', async () => {
    const user = userEvent.setup();
    await open();

    // Reach a cross-printer preset, pick it, then collapse the list again.
    await user.click((await screen.findAllByRole('button', { name: 'Show all' }))[0]);
    await waitFor(() => expect(processOptionNames()).toContain('0.20mm Standard @BBL H2D'));
    await user.selectOptions(presetSelects()[1], 'standard:p-h2d');
    await user.click(screen.getAllByRole('button', { name: 'Show fewer' })[0]);

    // Dropping it from the options would blank the select and silently discard
    // a deliberate cross-printer choice.
    await waitFor(() => expect(processOptionNames()).toContain('0.20mm Standard @BBL H2D'));
    expect(presetSelects()[1].value).toBe('standard:p-h2d');
    // The one still-hidden preset is counted; the selected one is not.
    expect(screen.getByText('1 hidden')).toBeInTheDocument();
  });
});

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

describe('pickFilamentForSlot — long-form printer tag (#2628)', () => {
  const index = buildCompatibilityIndex({
    'Bambu Lab A1': 'A1',
    'Bambu Lab H2D': 'H2D',
  });

  it('never auto-picks a user-saved preset scoped to another printer', () => {
    // michaelklos's registry: a cloud-tier user preset carrying the full
    // "@Bambu Lab H2D 0.4 nozzle" tag outscores the A1 preset on tier bonus
    // alone. Until the matcher learned the long form it classified 'unknown'
    // — indistinguishable from compatible — so it won the slot, landed in a
    // dropdown the modal disables (slot not used by the plate), and the CLI
    // rejected the whole slice.
    const presets = makeUnified({
      cloud: {
        printer: [],
        process: [],
        filament: [
          {
            id: 'sunlu-tpu-h2d',
            name: 'SUNLU TPU 95A @Bambu Lab H2D 0.4 nozzle',
            source: 'cloud',
            filament_type: 'PLA',
            filament_colour: '#FF0000',
          },
        ],
      },
      standard: {
        printer: [],
        process: [],
        filament: [
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

  it('still picks a long-form preset for its own printer', () => {
    const presets = makeUnified({
      cloud: {
        printer: [],
        process: [],
        filament: [
          {
            id: 'sunlu-tpu-h2d',
            name: 'SUNLU TPU 95A @Bambu Lab H2D 0.4 nozzle',
            source: 'cloud',
            filament_type: 'TPU',
            filament_colour: '#FF0000',
          },
        ],
      },
    });

    const pick = pickFilamentForSlot(
      presets,
      { type: 'TPU', color: '#FF0000' },
      'Bambu Lab H2D 0.4 nozzle',
      index,
    );

    expect(pick).toEqual({ source: 'cloud', id: 'sunlu-tpu-h2d' });
  });
});

describe('SliceModal — material and printer filtering (#2982)', () => {
  const A1 = 'Bambu Lab A1 0.4 nozzle';
  const P1S = 'Bambu Lab P1S 0.4 nozzle';

  beforeEach(() => {
    vi.clearAllMocks();
    mockApi.getSlicerPresetValues.mockResolvedValue({ resolved: true, values: {}, reason: 'ok' });
    mockApi.getSlicerPrinterModels.mockResolvedValue({
      'Bambu Lab A1': 'A1',
      'Bambu Lab P1S': 'P1S',
      'Bambu Lab X1 Carbon': 'X1C',
    });
    mockApi.listSlicerPipelines.mockResolvedValue({ pipelines: [] });
    mockApi.getLibraryFilePlates.mockResolvedValue({ file_id: 100, filename: 'Plate.3mf', plates: [] });
    mockApi.sliceLibraryFile.mockResolvedValue({
      job_id: 42,
      status: 'pending',
      status_url: '/api/v1/slice-jobs/42',
    });
    mockApi.getSliceJob.mockResolvedValue({
      job_id: 42,
      status: 'running',
      kind: 'library_file',
      source_id: 100,
      source_name: 'Plate.3mf',
      created_at: new Date().toISOString(),
      started_at: null,
      completed_at: null,
    });
  });

  // One PLA plate slot — the case the report is about.
  function plaPlate() {
    return {
      file_id: 100,
      filename: 'Plate.3mf',
      plate_id: 1,
      filaments: [{ slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 10, used_meters: 3 }],
    };
  }

  function presetsFor(printer: string, filaments: UnifiedPresetsResponse['standard']['filament']) {
    return makeUnified({
      standard: {
        printer: [{ id: printer, name: printer, source: 'standard' }],
        process: [
          {
            id: '0.20mm Standard @BBL X1C',
            name: '0.20mm Standard @BBL X1C',
            source: 'standard',
            compatible_printers: [P1S, 'Bambu Lab X1 Carbon 0.4 nozzle'],
          },
        ],
        filament: filaments,
      },
    });
  }

  it('does not auto-pick a stated PETG for a PLA plate', async () => {
    mockApi.getLibraryFileFilamentRequirements.mockResolvedValue(plaPlate());
    mockApi.getSlicerPresets.mockResolvedValue(
      presetsFor(A1, [
        { id: 'petg', name: 'eSUN PETG Basic @BBL A1', source: 'standard', filament_type: 'PETG', filament_colour: '#FF0000' },
        { id: 'pla', name: 'Bambu PLA Basic @BBL A1', source: 'standard', filament_type: 'PLA', filament_colour: '#FFFFFF' },
      ]),
    );

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Plate.3mf' },
      onClose: vi.fn(),
    });

    await waitFor(() => expect(screen.getByText(A1)).toBeDefined());
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /^Slice$/ }));

    await waitFor(() => {
      const [, body] = mockApi.sliceLibraryFile.mock.calls[0];
      expect(body.filament_presets).toEqual([{ source: 'standard', id: 'pla' }]);
    });
  });

  it('drops a wrong-material pick once the listing learns the material', async () => {
    // The sequence a user upgrading their sidecar actually goes through. The
    // first listing reports no material for anything, so the pre-pick has
    // only colour to go on and lands on the PETG. The second reports the
    // materials — and the wrong pick has to go, which it did not, because the
    // slot was held on printer-compatibility alone.
    const colourless = presetsFor(A1, [
      { id: 'petg', name: 'eSUN PETG Basic @BBL A1', source: 'standard', filament_type: null, filament_colour: '#FF0000' },
      { id: 'pla', name: 'Bambu PLA Basic @BBL A1', source: 'standard', filament_type: null, filament_colour: '#FFFFFF' },
    ]);
    const typed = presetsFor(A1, [
      { id: 'petg', name: 'eSUN PETG Basic @BBL A1', source: 'standard', filament_type: 'PETG', filament_colour: '#FF0000' },
      { id: 'pla', name: 'Bambu PLA Basic @BBL A1', source: 'standard', filament_type: 'PLA', filament_colour: '#FFFFFF' },
    ]);
    mockApi.getLibraryFileFilamentRequirements.mockResolvedValue(plaPlate());
    mockApi.getSlicerPresets.mockResolvedValueOnce(colourless).mockResolvedValue(typed);

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Plate.3mf' },
      onClose: vi.fn(),
    });

    await waitFor(() => expect(screen.getByText(A1)).toBeDefined());
    const user = userEvent.setup();

    // The colour-matched PETG is what the first listing produces.
    await waitFor(() => {
      const select = presetSelects().find((el) => el.value.startsWith('standard:p'));
      expect(select?.value).toBe('standard:petg');
    });

    await user.click(screen.getByRole('button', { name: 'Refresh' }));

    await waitFor(() => {
      const select = presetSelects().find((el) => el.value.startsWith('standard:p'));
      expect(select?.value).toBe('standard:pla');
    });
  });

  it('keeps a wrong-material preset the user picked on purpose', async () => {
    // Printing PETG on a plate a designer labelled PLA is a legitimate thing
    // to do. The material rule corrects the auto-pick; it must not overrule
    // a choice made in the dropdown.
    const pla = { id: 'pla', name: 'Bambu PLA Basic @BBL A1', source: 'standard' as const, filament_type: 'PLA', filament_colour: '#FF0000' };
    const petg = { id: 'petg', name: 'eSUN PETG Basic @BBL A1', source: 'standard' as const, filament_type: 'PETG', filament_colour: '#FFFFFF' };
    const matte = { id: 'matte', name: 'Bambu PLA Matte @BBL A1', source: 'standard' as const, filament_type: 'PLA', filament_colour: '#00FF00' };
    mockApi.getLibraryFileFilamentRequirements.mockResolvedValue(plaPlate());
    // The refresh has to return a *different* listing, or React Query's
    // structural sharing hands back the same object and the pre-pick never
    // re-runs — which would make this test pass without proving anything.
    mockApi.getSlicerPresets
      .mockResolvedValueOnce(presetsFor(A1, [pla, petg]))
      .mockResolvedValue(presetsFor(A1, [pla, petg, matte]));

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Plate.3mf' },
      onClose: vi.fn(),
    });

    await waitFor(() => expect(screen.getByText(A1)).toBeDefined());

    const user = userEvent.setup();
    const filamentSelect = presetSelects().find((el) =>
      Array.from(el.options).some((o) => o.textContent?.includes('eSUN PETG Basic')),
    );
    expect(filamentSelect).toBeDefined();
    await user.selectOptions(filamentSelect!, 'standard:petg');

    // Refreshing re-runs the pre-pick over the same slots. That is exactly
    // where an un-exempted material rule would quietly undo the choice.
    await user.click(screen.getByRole('button', { name: 'Refresh' }));

    await waitFor(() => {
      const select = presetSelects().find((el) => el.value.startsWith('standard:p'));
      expect(select?.value).toBe('standard:petg');
    });

    await user.click(screen.getByRole('button', { name: /^Slice$/ }));

    await waitFor(() => {
      const [, body] = mockApi.sliceLibraryFile.mock.calls[0];
      expect(body.filament_presets).toEqual([{ source: 'standard', id: 'petg' }]);
    });
  });

  it('shows every process when the printer filter would leave the list empty', async () => {
    // A P1S against a sidecar too old to report compatible_printers: all 198
    // processes read as another printer's, so the dropdown held one
    // auto-picked entry and a "Show all" link, with nothing saying the list
    // itself was the problem.
    mockApi.getLibraryFileFilamentRequirements.mockResolvedValue(plaPlate());
    mockApi.getSlicerPresets.mockResolvedValue(
      makeUnified({
        standard: {
          printer: [{ id: P1S, name: P1S, source: 'standard' }],
          process: [
            { id: 'x1c', name: '0.20mm Standard @BBL X1C', source: 'standard' },
            { id: 'a1', name: '0.06mm Fine @BBL A1 0.2 nozzle', source: 'standard' },
          ],
          filament: [
            { id: 'pla', name: 'Bambu PLA Basic @BBL A1', source: 'standard', filament_type: 'PLA' },
          ],
        },
      }),
    );

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Plate.3mf' },
      onClose: vi.fn(),
    });

    await waitFor(() => expect(screen.getByText(P1S)).toBeDefined());

    const processSelect = presetSelects().find((el) =>
      Array.from(el.options).some((o) => o.textContent?.includes('0.20mm Standard @BBL X1C')),
    );
    expect(processSelect).toBeDefined();
    // Both are visible rather than one hidden behind "Show all".
    const labels = Array.from(processSelect!.options).map((o) => o.textContent);
    expect(labels.some((l) => l?.includes('0.20mm Standard @BBL X1C'))).toBe(true);
    expect(labels.some((l) => l?.includes('0.06mm Fine @BBL A1 0.2 nozzle'))).toBe(true);
  });

  it('still hides other-printer presets when some do match', async () => {
    // The guard is for an empty list only — the normal filter must survive.
    mockApi.getLibraryFileFilamentRequirements.mockResolvedValue(plaPlate());
    mockApi.getSlicerPresets.mockResolvedValue(
      makeUnified({
        standard: {
          printer: [{ id: P1S, name: P1S, source: 'standard' }],
          process: [
            {
              id: 'x1c',
              name: '0.20mm Standard @BBL X1C',
              source: 'standard',
              compatible_printers: [P1S],
            },
            { id: 'a1', name: '0.06mm Fine @BBL A1 0.2 nozzle', source: 'standard' },
          ],
          filament: [
            { id: 'pla', name: 'Bambu PLA Basic @BBL A1', source: 'standard', filament_type: 'PLA' },
          ],
        },
      }),
    );

    renderWithTracker({
      source: { kind: 'libraryFile', id: 100, filename: 'Plate.3mf' },
      onClose: vi.fn(),
    });

    await waitFor(() => expect(screen.getByText(P1S)).toBeDefined());

    const processSelect = presetSelects().find((el) =>
      Array.from(el.options).some((o) => o.textContent?.includes('0.20mm Standard @BBL X1C')),
    );
    const labels = Array.from(processSelect!.options).map((o) => o.textContent);
    expect(labels.some((l) => l?.includes('0.06mm Fine @BBL A1 0.2 nozzle'))).toBe(false);
  });
});
