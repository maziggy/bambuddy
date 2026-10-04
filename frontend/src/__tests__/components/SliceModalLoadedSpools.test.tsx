/**
 * The Slice dialog's "online printers" / "loaded spools" filters and the
 * Pick screen (#3172).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { render } from '../utils';
import { SliceModal } from '../../components/SliceModal';
import { SliceJobTrackerProvider } from '../../contexts/SliceJobTrackerContext';
import { api, type LoadedSpoolsResponse, type UnifiedPresetsResponse } from '../../api/client';

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
    listSlicerPipelines: vi.fn(),
    createSlicerPipeline: vi.fn(),
    getSlicerPrinterModels: vi.fn(),
    getSlicerPresetValues: vi.fn(),
    getSlicerLoadedSpools: vi.fn(),
  },
}));

const mockApi = api as unknown as Record<string, ReturnType<typeof vi.fn>>;
const storage = window.localStorage as unknown as {
  getItem: ReturnType<typeof vi.fn>;
  setItem: ReturnType<typeof vi.fn>;
};

const PRESETS: UnifiedPresetsResponse = {
  orca_cloud: { printer: [], process: [], filament: [] },
  cloud: { printer: [], process: [], filament: [] },
  local: { printer: [], process: [], filament: [] },
  standard: {
    printer: [
      { id: 'Bambu Lab H2D 0.4 nozzle', name: 'Bambu Lab H2D 0.4 nozzle', source: 'standard' },
      { id: 'Bambu Lab X1 Carbon 0.4 nozzle', name: 'Bambu Lab X1 Carbon 0.4 nozzle', source: 'standard' },
      { id: 'My farm printer', name: 'My farm printer', source: 'standard' },
    ],
    process: [{ id: '0.20mm Standard @BBL H2D', name: '0.20mm Standard @BBL H2D', source: 'standard' }],
    filament: [
      // Alphabetically first, so it is what the plain auto-pick lands on.
      { id: 'Bambu PLA Basic @BBL H2D', name: 'Bambu PLA Basic @BBL H2D', source: 'standard', filament_type: 'PLA' },
      { id: 'Bambu PLA Matte @BBL H2D', name: 'Bambu PLA Matte @BBL H2D', source: 'standard', filament_type: 'PLA' },
      { id: 'Generic PETG @BBL H2D', name: 'Generic PETG @BBL H2D', source: 'standard', filament_type: 'PETG' },
    ],
  },
  cloud_status: 'ok',
  orca_cloud_status: 'ok',
};

const LOADED: LoadedSpoolsResponse = {
  printers: [
    {
      id: 1,
      name: 'Farm H2D',
      model: 'H2D',
      ams: [
        {
          id: 0,
          is_ams_ht: false,
          trays: [
            {
              ams_id: 0,
              tray_id: 0,
              tray_type: 'PLA',
              tray_sub_brands: 'PLA Matte',
              tray_color: '00FF00FF',
              tray_info_idx: 'GFA01',
              exists: true,
              state: 11,
              saved_preset: null,
            },
            {
              ams_id: 0,
              tray_id: 1,
              tray_type: null,
              tray_sub_brands: null,
              tray_color: null,
              tray_info_idx: null,
              exists: false,
              state: 9,
              saved_preset: null,
            },
            {
              ams_id: 0,
              tray_id: 2,
              tray_type: 'ABS',
              tray_sub_brands: 'Mystery ABS',
              tray_color: '000000FF',
              tray_info_idx: 'P1234567',
              exists: true,
              state: 11,
              saved_preset: null,
            },
          ],
        },
      ],
      external: [],
      external_holders: 2,
    },
  ],
};

function flags(values: Record<string, string>) {
  storage.getItem.mockImplementation((key: string) => values[key] ?? null);
}

function renderModal() {
  return render(
    <SliceJobTrackerProvider>
      <SliceModal source={{ kind: 'libraryFile', id: 100, filename: 'Cube.stl' }} onClose={vi.fn()} />
    </SliceJobTrackerProvider>,
  );
}

function selectFor(label: string): HTMLSelectElement {
  return screen.getByLabelText(label) as HTMLSelectElement;
}

function optionLabels(select: HTMLSelectElement): string[] {
  return Array.from(select.options).map((o) => o.textContent ?? '');
}

describe('SliceModal — online printers and loaded spools (#3172)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    flags({});
    mockApi.getSlicerPresets.mockResolvedValue(PRESETS);
    mockApi.getSlicerPresetValues.mockResolvedValue({ resolved: true, values: {}, reason: 'ok' });
    mockApi.getSlicerPrinterModels.mockResolvedValue({ 'Bambu Lab H2D': 'H2D', 'Bambu Lab X1 Carbon': 'X1C' });
    mockApi.getLibraryFilePlates.mockResolvedValue({ file_id: 100, filename: 'Cube.stl', plates: [], is_multi_plate: false });
    mockApi.getLibraryFileFilamentRequirements.mockResolvedValue({
      file_id: 100,
      filename: 'Cube.stl',
      plate_id: 1,
      filaments: [{ slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 1, used_meters: 1 }],
    });
    mockApi.listSlicerPipelines.mockResolvedValue({ pipelines: [] });
    mockApi.getSlicerLoadedSpools.mockResolvedValue(LOADED);
  });

  afterEach(() => {
    storage.getItem.mockReset();
  });

  it('offers nothing new to a caller who may not read printer status', async () => {
    mockApi.getSlicerLoadedSpools.mockRejectedValue(new Error('403'));
    renderModal();
    await waitFor(() => expect(selectFor('Filament profile').value).not.toBe(''));
    expect(screen.queryByLabelText(/Only printers that are online/)).toBeNull();
    expect(screen.queryByRole('button', { name: /Pick/ })).toBeNull();
  });

  it('starts with both filters off and every profile listed', async () => {
    renderModal();
    const online = (await screen.findByLabelText(/Only printers that are online/)) as HTMLInputElement;
    expect(online.checked).toBe(false);
    expect((screen.getByLabelText(/Only spools that are loaded/) as HTMLInputElement).checked).toBe(false);
    await waitFor(() => expect(selectFor('Filament profile').value).toBe('standard:Bambu PLA Basic @BBL H2D'));
    expect(optionLabels(selectFor('Printer profile'))).toContain('Bambu Lab X1 Carbon 0.4 nozzle');
  });

  it('remembers a filter in the browser', async () => {
    renderModal();
    fireEvent.click(await screen.findByLabelText(/Only spools that are loaded/));
    expect(storage.setItem).toHaveBeenCalledWith('bambuddy.slice.onlyLoadedSpools', 'true');
  });

  it('lists only the models that are online, keeping profiles it cannot place', async () => {
    flags({ 'bambuddy.slice.onlyConnectedModels': 'true' });
    renderModal();
    await waitFor(() => expect(selectFor('Printer profile').value).toBe('standard:Bambu Lab H2D 0.4 nozzle'));
    const labels = optionLabels(selectFor('Printer profile'));
    expect(labels).toContain('Bambu Lab H2D 0.4 nozzle');
    expect(labels).toContain('My farm printer');
    expect(labels).not.toContain('Bambu Lab X1 Carbon 0.4 nozzle');

    // "Show all" brings the offline model back, in a group of its own.
    fireEvent.click(screen.getAllByRole('button', { name: 'Show all' })[0]);
    const group = selectFor('Printer profile').querySelector('optgroup[label="Not online"]');
    expect(group?.textContent).toContain('Bambu Lab X1 Carbon 0.4 nozzle');
  });

  it('moves an auto-picked printer to an online model, but not a chosen one', async () => {
    flags({ 'bambuddy.slice.onlyConnectedModels': 'true' });
    // X1C first, so it is what the plain auto-pick lands on; only an H2D is online.
    const standard = PRESETS.standard;
    mockApi.getSlicerPresets.mockResolvedValue({
      ...PRESETS,
      standard: {
        ...standard,
        printer: [
          { id: 'Bambu Lab X1 Carbon 0.4 nozzle', name: 'Bambu Lab X1 Carbon 0.4 nozzle', source: 'standard' },
          { id: 'Bambu Lab H2D 0.6 nozzle', name: 'Bambu Lab H2D 0.6 nozzle', source: 'standard' },
          { id: 'Bambu Lab H2D 0.4 nozzle', name: 'Bambu Lab H2D 0.4 nozzle', source: 'standard' },
        ],
      },
    });
    renderModal();
    await waitFor(() => expect(selectFor('Printer profile').value).toBe('standard:Bambu Lab H2D 0.4 nozzle'));

    fireEvent.click(screen.getAllByRole('button', { name: 'Show all' })[0]);
    fireEvent.change(selectFor('Printer profile'), { target: { value: 'standard:Bambu Lab X1 Carbon 0.4 nozzle' } });
    await new Promise((r) => setTimeout(r, 50));
    expect(selectFor('Printer profile').value).toBe('standard:Bambu Lab X1 Carbon 0.4 nozzle');
  });

  it('auto-picks from the loaded spools and holds the rest back', async () => {
    flags({ 'bambuddy.slice.onlyLoadedSpools': 'true' });
    renderModal();
    // The plain auto-pick would take PLA Basic; the AMS holds PLA Matte.
    await waitFor(() => expect(selectFor('Filament profile').value).toBe('standard:Bambu PLA Matte @BBL H2D'));
    const labels = optionLabels(selectFor('Filament profile'));
    expect(labels).not.toContain('Bambu PLA Basic @BBL H2D');
    expect(labels).not.toContain('Generic PETG @BBL H2D');
  });

  it('never auto-picks a loaded spool of another material', async () => {
    flags({ 'bambuddy.slice.onlyLoadedSpools': 'true' });
    mockApi.getLibraryFileFilamentRequirements.mockResolvedValue({
      file_id: 100,
      filename: 'Cube.stl',
      plate_id: 1,
      filaments: [{ slot_id: 1, type: 'PETG', color: '#FF0000', used_grams: 1, used_meters: 1 }],
    });
    renderModal();
    // Only PLA is loaded; the PETG plate keeps a PETG profile from the full list.
    await waitFor(() => expect(selectFor('Filament profile').value).toBe('standard:Generic PETG @BBL H2D'));
  });

  it('keeps every profile when nothing is online', async () => {
    flags({ 'bambuddy.slice.onlyLoadedSpools': 'true', 'bambuddy.slice.onlyConnectedModels': 'true' });
    mockApi.getSlicerLoadedSpools.mockResolvedValue({ printers: [] });
    renderModal();
    expect(await screen.findByText('No printer is online, so every profile is shown.')).toBeDefined();
    await waitFor(() => expect(selectFor('Filament profile').value).toBe('standard:Bambu PLA Basic @BBL H2D'));
    expect(optionLabels(selectFor('Printer profile'))).toContain('Bambu Lab X1 Carbon 0.4 nozzle');
    expect(screen.queryByRole('button', { name: /Pick/ })).toBeNull();
  });

  it('fills a row and its colour from a picked slot', async () => {
    renderModal();
    await waitFor(() => expect(selectFor('Filament profile').value).toBe('standard:Bambu PLA Basic @BBL H2D'));
    fireEvent.click(screen.getByRole('button', { name: /Pick/ }));

    const dialog = await screen.findByRole('dialog', { name: 'Loaded spools' });
    expect(within(dialog).getByText('Farm H2D')).toBeDefined();
    expect(within(dialog).getByText('Empty')).toBeDefined();
    // A spool with no profile for this printer is shown but can't be picked.
    const mystery = within(dialog).getByText('A3').closest('button') as HTMLButtonElement;
    expect(mystery.disabled).toBe(true);

    fireEvent.click(within(dialog).getByText('A1').closest('button') as HTMLButtonElement);

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Loaded spools' })).toBeNull());
    expect(selectFor('Filament profile').value).toBe('standard:Bambu PLA Matte @BBL H2D');
    expect((screen.getByLabelText('Filament colour') as HTMLInputElement).value).toBe('#00ff00');
  });
});
