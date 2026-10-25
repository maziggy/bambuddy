/**
 * The spool form and the product's profile default (#1326).
 *
 * - a new spool's Printers tab is filled in from the default, and says so;
 * - an untouched create does not save the tab: the backend applies the
 *   default itself, and an empty save would clear it;
 * - "Save as default" stores the tab for all colours unless told otherwise;
 * - an edit never asks for the default.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { SpoolFormModal } from '../../components/SpoolFormModal';
import { api } from '../../api/client';
import type { InventorySpool } from '../../api/client';

vi.mock('../../api/client', () => ({
  api: {
    getSettings: vi.fn().mockResolvedValue({}),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
    getCloudStatus: vi.fn().mockResolvedValue({ is_authenticated: false }),
    getFilamentPresets: vi.fn().mockResolvedValue([]),
    getSpoolCatalog: vi.fn().mockResolvedValue([]),
    getLocations: vi.fn().mockResolvedValue([]),
    getColorCatalog: vi.fn().mockResolvedValue([]),
    getLocalPresets: vi.fn().mockResolvedValue({ filament: [] }),
    getBuiltinFilaments: vi.fn().mockResolvedValue([]),
    getPrinters: vi.fn().mockResolvedValue([]),
    getSpoolUsageHistory: vi.fn().mockResolvedValue([]),
    createSpool: vi.fn().mockResolvedValue({ id: 99 }),
    bulkCreateSpools: vi.fn().mockResolvedValue([{ id: 100, k_profiles: [] }, { id: 101, k_profiles: [] }]),
    updateSpool: vi.fn().mockResolvedValue({ id: 1 }),
    saveSpoolKProfiles: vi.fn().mockResolvedValue([]),
    getSpoolFilamentPresets: vi.fn().mockResolvedValue([]),
    saveSpoolFilamentPresets: vi.fn().mockResolvedValue([]),
    resolveProfileDefault: vi.fn(),
    saveProfileDefault: vi.fn().mockResolvedValue({ id: 1 }),
  },
}));

const mockShowToast = vi.fn();
vi.mock('../../contexts/ToastContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/ToastContext')>();
  return { ...actual, useToast: () => ({ showToast: mockShowToast }) };
});

const mApi = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

// The product fields a create starts from; handed in as `spool` so the test
// does not have to type them into the comboboxes.
const product = {
  id: 1,
  material: 'PLA',
  subtype: 'Basic',
  brand: 'Polymaker',
  color_name: 'Red',
  rgba: 'FF0000FF',
  extra_colors: null,
  effect_type: null,
  label_weight: 1000,
  core_weight: 250,
  core_weight_catalog_id: null,
  weight_used: 0,
  slicer_filament: 'GFL99',
  slicer_filament_name: 'Generic PLA',
  nozzle_temp_min: null,
  nozzle_temp_max: null,
  note: null,
  added_full: null,
  last_used: null,
  encode_time: null,
  tag_uid: null,
  tray_uuid: null,
  data_origin: null,
  tag_type: null,
  archived_at: null,
  created_at: '2025-01-01T00:00:00Z',
  updated_at: '2025-01-01T00:00:00Z',
  k_profiles: [],
  cost_per_kg: null,
} as InventorySpool;

const resolved = {
  slicer_filament: 'PFUS01',
  slicer_filament_name: 'My PLA',
  presets: [{ printer_model: 'X1C', nozzle_diameter: '0.4', slicer_filament: 'PFUS02', slicer_filament_name: 'My PLA @X1C' }],
  k_profiles: [{ printer_id: 3, extruder: 0, nozzle_diameter: '0.4', nozzle_type: null, k_value: 0.02, name: 'PLA', cali_idx: 5, setting_id: null }],
  default_ids: [7],
};

function renderForm(mode: 'create' | 'edit' = 'create', overrides: Partial<InventorySpool> = {}, extra = {}) {
  return render(
    <SpoolFormModal
      isOpen={true}
      onClose={vi.fn()}
      spool={{ ...product, ...overrides }}
      mode={mode}
      currencySymbol="$"
      {...extra}
    />,
  );
}

// One printer the user can pick a K profile on.
const bench = [
  {
    printer: { id: 4, name: 'Bench', model: 'X1C', nozzle_count: 1, connected: true },
    calibrations: [
      { cali_idx: 6, filament_id: 'PFUS77', setting_id: '', name: 'Own PLA', k_value: 0.03, n_coef: 0, extruder_id: 0, nozzle_diameter: '0.4', nozzle_id: '' },
    ],
    nozzles: [{ nozzle_diameter: '0.4' }],
  },
];

async function openPrintersTab() {
  fireEvent.click(await screen.findByText('Printers'));
}

function submit(label = 'Add Spool') {
  const buttons = screen.getAllByRole('button', { name: label });
  fireEvent.click(buttons[buttons.length - 1]);
}

describe('SpoolFormModal profile defaults (#1326)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mApi.resolveProfileDefault.mockResolvedValue(resolved);
  });

  it('fills a new spool from the default and says so', async () => {
    renderForm();
    await waitFor(() =>
      expect(mApi.resolveProfileDefault).toHaveBeenCalledWith({
        brand: 'Polymaker',
        material: 'PLA',
        subtype: 'Basic',
        color_name: 'Red',
      }),
    );
    await openPrintersTab();
    expect(await screen.findByText('Filled in from the profile default for Polymaker PLA Basic')).toBeInTheDocument();
  });

  it('an untouched create leaves the Printers tab to the backend', async () => {
    renderForm();
    await waitFor(() => expect(mApi.resolveProfileDefault).toHaveBeenCalled());
    submit();
    await waitFor(() => expect(mApi.createSpool).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith('Spool created', 'success'));
    expect(mApi.saveSpoolKProfiles).not.toHaveBeenCalled();
    expect(mApi.saveSpoolFilamentPresets).not.toHaveBeenCalled();
    expect(mApi.saveProfileDefault).not.toHaveBeenCalled();
  });

  it('an untouched bulk add leaves every copy to the backend', async () => {
    renderForm();
    await waitFor(() => expect(mApi.resolveProfileDefault).toHaveBeenCalled());
    const quickAddToggle = screen
      .getAllByRole('button')
      .find((btn) => btn.className.includes('rounded-full') && btn.closest('div')?.textContent?.includes('Quick Add'));
    fireEvent.click(quickAddToggle!);
    const quantity = (await screen.findByText('Quantity')).parentElement!.querySelector('input') as HTMLInputElement;
    fireEvent.change(quantity, { target: { value: '2' } });
    submit();
    await waitFor(() => expect(mApi.bulkCreateSpools).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith('2 spools created', 'success'));
    expect(mApi.saveSpoolKProfiles).not.toHaveBeenCalled();
    expect(mApi.saveSpoolFilamentPresets).not.toHaveBeenCalled();
  });

  it('saves the tab as the default for all colours by default', async () => {
    renderForm();
    await waitFor(() => expect(mApi.resolveProfileDefault).toHaveBeenCalled());
    await openPrintersTab();
    fireEvent.click(await screen.findByLabelText('Save as default for new Polymaker PLA Basic spools'));
    expect(screen.getByLabelText('All colours')).toBeChecked();
    submit();
    await waitFor(() => expect(mApi.saveProfileDefault).toHaveBeenCalledTimes(1));
    expect(mApi.saveProfileDefault.mock.calls[0][0]).toMatchObject({
      brand: 'Polymaker',
      material: 'PLA',
      subtype: 'Basic',
      color_name: '',
      k_profiles: [expect.objectContaining({ printer_id: 3, cali_idx: 5 })],
      presets: [expect.objectContaining({ printer_model: 'X1C', slicer_filament: 'PFUS02' })],
    });
  });

  it('saves a default for one colour when asked', async () => {
    renderForm();
    await waitFor(() => expect(mApi.resolveProfileDefault).toHaveBeenCalled());
    await openPrintersTab();
    fireEvent.click(await screen.findByLabelText('Save as default for new Polymaker PLA Basic spools'));
    fireEvent.click(screen.getByLabelText('Only Red'));
    submit();
    await waitFor(() => expect(mApi.saveProfileDefault).toHaveBeenCalledTimes(1));
    expect(mApi.saveProfileDefault.mock.calls[0][0].color_name).toBe('Red');
  });

  it('an edit shows the spool, not the default', async () => {
    renderForm('edit');
    await screen.findByText('Edit Spool');
    await openPrintersTab();
    await screen.findByLabelText('Save as default for new Polymaker PLA Basic spools');
    // Past the lookup's debounce.
    await new Promise((r) => setTimeout(r, 500));
    expect(mApi.resolveProfileDefault).not.toHaveBeenCalled();
    expect(screen.queryByText(/Filled in from the profile default/)).not.toBeInTheDocument();
  });

  it('a tab touched before the default arrives still gets the rest of it', async () => {
    // The first product's lookup never answers; the user picks a profile, then
    // changes the product, whose default arrives with the tab already touched.
    mApi.resolveProfileDefault.mockImplementation((key: { subtype: string }) =>
      key.subtype === 'Matte' ? Promise.resolve(resolved) : new Promise(() => {}),
    );
    renderForm('create', { slicer_filament: 'PFUS77' }, { printersWithCalibrations: bench });
    await openPrintersTab();
    fireEvent.change(await screen.findByLabelText(/Bench .*0\.4mm/), { target: { value: '6' } });
    fireEvent.click(screen.getByText('Filament Info'));
    fireEvent.focus(screen.getByPlaceholderText('Basic, Matte, Silk...'));
    fireEvent.click(await screen.findByRole('button', { name: 'Matte' }));
    await openPrintersTab();
    await screen.findByText('Filled in from the profile default for Polymaker PLA Matte');
    fireEvent.click(screen.getByLabelText('Save as default for new Polymaker PLA Matte spools'));
    submit();
    await waitFor(() => expect(mApi.saveProfileDefault).toHaveBeenCalledTimes(1));
    const saved = mApi.saveProfileDefault.mock.calls[0][0].k_profiles.map((k: { printer_id: number; cali_idx: number }) => [k.printer_id, k.cali_idx]);
    expect(saved).toEqual(expect.arrayContaining([[3, 5], [4, 6]]));
    // The touched tab is saved, with the default's profile in it.
    expect(mApi.saveSpoolKProfiles.mock.calls[0][1].map((k: { printer_id: number }) => k.printer_id).sort()).toEqual([3, 4]);
  });

  it('the next product takes back the preset the previous default put in', async () => {
    mApi.resolveProfileDefault.mockImplementation(async (key: { subtype: string }) =>
      key.subtype === 'Basic' ? resolved : { ...resolved, slicer_filament: null, slicer_filament_name: null },
    );
    renderForm('create', { slicer_filament: '', slicer_filament_name: '' });
    expect(await screen.findByDisplayValue('My PLA')).toBeInTheDocument();
    fireEvent.focus(screen.getByPlaceholderText('Basic, Matte, Silk...'));
    fireEvent.click(await screen.findByRole('button', { name: 'Matte' }));
    await waitFor(() => expect(mApi.resolveProfileDefault).toHaveBeenCalledWith(expect.objectContaining({ subtype: 'Matte' })));
    await waitFor(() => expect(screen.queryByDisplayValue('My PLA')).not.toBeInTheDocument());
  });

  it('a colour-only default falls back to all colours when the colour is cleared', async () => {
    renderForm();
    await waitFor(() => expect(mApi.resolveProfileDefault).toHaveBeenCalled());
    await openPrintersTab();
    fireEvent.click(await screen.findByLabelText('Save as default for new Polymaker PLA Basic spools'));
    fireEvent.click(screen.getByLabelText('Only Red'));
    fireEvent.click(screen.getByText('Color & Cost'));
    fireEvent.change(screen.getByDisplayValue('Red'), { target: { value: '' } });
    await openPrintersTab();
    expect(screen.getByLabelText('All colours')).toBeChecked();
    expect(screen.getByLabelText('Only this colour: this spool has no colour name. Add one on Color & Cost to save a default for this colour alone.')).toBeDisabled();
  });

  it('a hotend the user cleared stays cleared when the product changes', async () => {
    const withBench = { ...resolved, k_profiles: [{ ...resolved.k_profiles[0], printer_id: 4, cali_idx: 6 }] };
    mApi.resolveProfileDefault.mockResolvedValue(withBench);
    renderForm('create', { slicer_filament: 'PFUS77' }, { printersWithCalibrations: bench });
    await openPrintersTab();
    const select = (await screen.findByLabelText(/Bench .*0\.4mm/)) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe('6'));
    fireEvent.change(select, { target: { value: '' } });
    fireEvent.click(screen.getByText('Filament Info'));
    fireEvent.focus(screen.getByPlaceholderText('Basic, Matte, Silk...'));
    fireEvent.click(await screen.findByRole('button', { name: 'Matte' }));
    await waitFor(() => expect(mApi.resolveProfileDefault).toHaveBeenCalledWith(expect.objectContaining({ subtype: 'Matte' })));
    await openPrintersTab();
    await screen.findByText('Filled in from the profile default for Polymaker PLA Matte');
    expect(((await screen.findByLabelText(/Bench .*0\.4mm/)) as HTMLSelectElement).value).toBe('');
  });

  it('a preset picked while the lookup is running is kept', async () => {
    let answer: (value: typeof resolved) => void = () => {};
    mApi.resolveProfileDefault.mockReturnValue(new Promise((r) => { answer = r; }));
    renderForm('create', { slicer_filament: '', slicer_filament_name: '' });
    await waitFor(() => expect(mApi.resolveProfileDefault).toHaveBeenCalled());
    fireEvent.focus(screen.getByPlaceholderText('Search filament presets...'));
    fireEvent.click(await screen.findByRole('button', { name: /Generic PLA/ }));
    expect(screen.getByDisplayValue('Generic PLA')).toBeInTheDocument();
    answer(resolved);
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.getByDisplayValue('Generic PLA')).toBeInTheDocument();
    expect(screen.queryByDisplayValue('My PLA')).not.toBeInTheDocument();
  });
});
