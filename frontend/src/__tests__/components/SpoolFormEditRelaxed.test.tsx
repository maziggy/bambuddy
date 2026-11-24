/**
 * Tests for #1905 — editing a spool that was created without a slicer preset.
 *
 * Covers:
 * - edit/copy no longer demand a slicer preset, brand or subtype
 * - Add Spool splits a matching product name into material and subtype (#3671)
 * - picking a preset preserves other user-set identity fields
 * - the Quick Add layout can't leak from create mode into an edit
 * - brand/material dropdowns rank catalog pairings instead of filtering by them
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { SpoolFormModal } from '../../components/SpoolFormModal';
import type { InventorySpool } from '../../api/client';

vi.mock('../../api/client', () => ({
  api: {
    getSettings: vi.fn().mockResolvedValue({}),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
    getCloudStatus: vi.fn().mockResolvedValue({ is_authenticated: false }),
    orcaCloudStatus: vi.fn().mockResolvedValue({ connected: false }),
    orcaCloudListProfiles: vi.fn().mockResolvedValue({ filament: [] }),
    getFilamentPresets: vi.fn().mockResolvedValue([]),
    getSpoolCatalog: vi.fn().mockResolvedValue([]),
    getLocations: vi.fn().mockResolvedValue([]),
    // Elegoo is only known for PLA here — the pairing that used to hide it
    // from the brand list as soon as ASA was selected.
    getColorCatalog: vi.fn().mockResolvedValue([
      { manufacturer: 'Elegoo', color_name: 'Red', hex_color: 'FF0000', material: 'PLA' },
      { manufacturer: 'Polymaker', color_name: 'Blue', hex_color: '0000FF', material: 'ASA' },
      { manufacturer: 'Bambu Lab', color_name: 'Light Gray', hex_color: '#D1D3D5', material: 'PLA Basic' },
    ]),
    getLocalPresets: vi.fn().mockResolvedValue({ filament: [] }),
    getBuiltinFilaments: vi.fn().mockResolvedValue([
      { filament_id: 'GFA05', name: 'Generic ASA' },
      { filament_id: 'GFA00', name: 'Bambu PLA Basic' },
      { filament_id: 'GFA01', name: 'Bambu PLA Matte' },
      { filament_id: 'GFG02', name: 'Bambu PETG HF' },
    ]),
    getPrinters: vi.fn().mockResolvedValue([]),
    getPrinterStatus: vi.fn().mockResolvedValue(null),
    getSpoolUsageHistory: vi.fn().mockResolvedValue([]),
    createSpool: vi.fn().mockResolvedValue({ id: 99 }),
    createSpoolmanInventorySpool: vi.fn().mockResolvedValue({ id: 99 }),
    updateSpool: vi.fn().mockResolvedValue({ id: 7 }),
    saveSpoolKProfiles: vi.fn().mockResolvedValue([]),
    saveSpoolmanKProfiles: vi.fn().mockResolvedValue([]),
    getSpoolFilamentPresets: vi.fn().mockResolvedValue([]),
    // 3D Filament Profiles (#2981): the form loads the spool's link, and
    // its lookup section asks whether the user is connected.
    getThreedfpSpoolLink: vi.fn().mockResolvedValue({ filament_id: null }),
    setThreedfpSpoolLink: vi.fn().mockResolvedValue({ filament_id: null }),
    threedfpStatus: vi.fn().mockResolvedValue({ connected: false, email: null }),
    threedfpLookup: vi.fn().mockResolvedValue({ results: [] }),
    threedfpGetFilament: vi.fn(),
    saveSpoolFilamentPresets: vi.fn().mockResolvedValue([]),
    getSpoolmanFilamentPresets: vi.fn().mockResolvedValue([]),
    saveSpoolmanFilamentPresets: vi.fn().mockResolvedValue([]),
    getSpoolmanInventoryFilaments: vi.fn().mockResolvedValue([]),
    getAssignments: vi.fn().mockResolvedValue([]),
    unassignSpool: vi.fn().mockResolvedValue({}),
  },
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
}));

const mockShowToast = vi.fn();
vi.mock('../../contexts/ToastContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/ToastContext')>();
  return {
    ...actual,
    useToast: () => ({ showToast: mockShowToast }),
  };
});

import { api } from '../../api/client';

// A spool as produced by Quick Add / CSV import / an RFID scan: material only.
const quickAddedSpool: InventorySpool = {
  id: 7,
  material: 'ASA',
  subtype: null,
  brand: null,
  color_name: null,
  rgba: '808080FF',
  extra_colors: null,
  effect_type: null,
  label_weight: 1000,
  core_weight: 250,
  core_weight_catalog_id: null,
  weight_used: 0,
  slicer_filament: null,
  slicer_filament_name: null,
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
} as unknown as InventorySpool;

const elegooAsaSpool: InventorySpool = {
  ...quickAddedSpool,
  id: 8,
  brand: 'Elegoo',
  subtype: 'Basic',
} as unknown as InventorySpool;

async function pickPreset(name: RegExp) {
  const input = screen.getByPlaceholderText('Search filament presets...');
  fireEvent.mouseDown(input);
  fireEvent.focus(input);
  fireEvent.click(await screen.findByRole('button', { name }));
}

async function pickMaterial(material: string, option = material) {
  const input = screen.getByPlaceholderText('Select material...');
  fireEvent.mouseDown(input);
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: material } });
  fireEvent.click(await screen.findByRole('button', { name: option }));
}

describe('SpoolFormModal relaxed edit/copy validation (#1905)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('saves an edit of a preset-less spool without demanding a slicer preset', async () => {
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        spool={quickAddedSpool}
        mode="edit"
        currencySymbol="$"
      />,
    );

    await waitFor(() => expect(screen.getByRole('heading', { name: /Edit Spool/ })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(api.updateSpool).toHaveBeenCalled());
    expect(screen.queryByText('Slicer preset is required')).not.toBeInTheDocument();
  });

  it('copies a preset-less spool without demanding a slicer preset', async () => {
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        spool={quickAddedSpool}
        mode="copy"
        currencySymbol="$"
      />,
    );

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Copy Spool' })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Copy Spool' }));

    await waitFor(() => expect(api.createSpool).toHaveBeenCalled());
    expect(screen.queryByText('Slicer preset is required')).not.toBeInTheDocument();
  });

  it('still requires a slicer preset when creating a spool', async () => {
    render(
      <SpoolFormModal isOpen={true} onClose={vi.fn()} mode="create" currencySymbol="$" />,
    );

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Add Spool' })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Add Spool' }));

    await waitFor(() => expect(screen.getByText('Slicer preset is required')).toBeInTheDocument());
    expect(api.createSpool).not.toHaveBeenCalled();
  });

  it('keeps the spool brand when a preset is picked during an edit', async () => {
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        spool={elegooAsaSpool}
        mode="edit"
        currencySymbol="$"
      />,
    );

    await waitFor(() => expect(screen.getByRole('heading', { name: /Edit Spool/ })).toBeInTheDocument());

    const presetInput = screen.getByPlaceholderText('Search filament presets...');
    fireEvent.focus(presetInput);
    await waitFor(() => expect(screen.getByRole('button', { name: /^Generic ASA\b/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /^Generic ASA\b/ }));

    // parsePresetName('Generic ASA') yields brand "Generic" — it must not
    // replace the manufacturer the spool already carries.
    expect(screen.getByPlaceholderText('Search brand...')).toHaveValue('Elegoo');
  });

  it('auto-fills empty identity fields from the preset when creating', async () => {
    render(
      <SpoolFormModal isOpen={true} onClose={vi.fn()} mode="create" currencySymbol="$" />,
    );

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Add Spool' })).toBeInTheDocument());

    const presetInput = screen.getByPlaceholderText('Search filament presets...');
    fireEvent.focus(presetInput);
    await waitFor(() => expect(screen.getByRole('button', { name: /^Generic ASA\b/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /^Generic ASA\b/ }));

    expect(screen.getByPlaceholderText('Search brand...')).toHaveValue('Generic');
    expect(screen.getByPlaceholderText('Select material...')).toHaveValue('ASA');
  });

  it.each(['material', 'preset'] as const)('saves PLA Basic once when the %s is picked first', async (first) => {
    render(<SpoolFormModal isOpen={true} onClose={vi.fn()} mode="create" currencySymbol="$" />);

    const brand = screen.getByPlaceholderText('Search brand...');
    fireEvent.focus(brand);
    fireEvent.click(await screen.findByRole('button', { name: 'Bambu Lab' }));

    if (first === 'material') await pickMaterial('PLA Basic');
    await pickPreset(/^Bambu PLA Basic\b/);
    if (first === 'preset') await pickMaterial('PLA Basic');

    expect(screen.getByPlaceholderText('Select material...')).toHaveValue('PLA');
    expect(screen.getByPlaceholderText('Basic, Matte, Silk...')).toHaveValue('Basic');
    fireEvent.click(screen.getByRole('button', { name: 'Color & Cost' }));
    fireEvent.click(await screen.findByTitle('Light Gray (Bambu Lab — PLA Basic)'));
    fireEvent.click(screen.getByRole('button', { name: 'Add Spool' }));

    await waitFor(() => expect(api.createSpool).toHaveBeenCalledWith(expect.objectContaining({
      material: 'PLA',
      subtype: 'Basic',
      brand: 'Bambu Lab',
      color_name: 'Light Gray',
      rgba: 'D1D3D5FF',
      slicer_filament: 'GFA00',
      slicer_filament_name: 'Bambu PLA Basic',
    })));
  });

  it.each(['edit', 'copy'] as const)('preserves a saved spool’s material and subtype in %s mode', async (mode) => {
    render(<SpoolFormModal isOpen={true} onClose={vi.fn()} mode={mode} currencySymbol="$" spool={{
      ...quickAddedSpool,
      material: 'PLA Basic',
      subtype: 'Basic',
      brand: 'Bambu Lab',
      color_name: 'Light Gray',
      slicer_filament: 'GFA00',
      slicer_filament_name: 'Bambu PLA Basic',
    }} />);

    await pickPreset(/^Bambu PLA Basic\b/);
    expect(screen.getByPlaceholderText('Select material...')).toHaveValue('PLA Basic');
    await pickMaterial('PLA Basic');

    const expected = expect.objectContaining({
      material: 'PLA Basic',
      subtype: 'Basic',
      brand: 'Bambu Lab',
      color_name: 'Light Gray',
      slicer_filament: 'GFA00',
    });
    if (mode === 'edit') {
      fireEvent.click(screen.getByRole('button', { name: 'Save', exact: true }));
      await waitFor(() => expect(api.updateSpool).toHaveBeenCalledWith(quickAddedSpool.id, expected));
    } else {
      fireEvent.click(screen.getByRole('button', { name: 'Copy Spool', exact: true }));
      await waitFor(() => expect(api.createSpool).toHaveBeenCalledWith(expected));
    }
  });

  it('keeps the manually chosen product line if the slicer preset changes later', async () => {
    render(<SpoolFormModal isOpen={true} onClose={vi.fn()} mode="create" currencySymbol="$" />);

    await pickMaterial('PLA Basic');
    await pickPreset(/^Bambu PLA Basic\b/);
    await pickPreset(/^Bambu PLA Matte\b/);

    expect(screen.getByPlaceholderText('Select material...')).toHaveValue('PLA');
    expect(screen.getByPlaceholderText('Basic, Matte, Silk...')).toHaveValue('Basic');
    expect(screen.getByPlaceholderText('Search filament presets...')).toHaveValue('Bambu PLA Matte');
  });

  it.each([
    { pickedSubtype: false, expectedSubtype: 'HF' },
    { pickedSubtype: true, expectedSubtype: 'Basic' },
  ])('uses $expectedSubtype after changing material and preset (separate subtype picked: $pickedSubtype)', async ({ pickedSubtype, expectedSubtype }) => {
    render(<SpoolFormModal isOpen={true} onClose={vi.fn()} mode="create" currencySymbol="$" />);

    await pickMaterial('PLA Basic');
    if (pickedSubtype) {
      fireEvent.focus(screen.getByPlaceholderText('Basic, Matte, Silk...'));
      fireEvent.click(screen.getByRole('button', { name: 'Basic' }));
    }
    await pickPreset(/^Bambu PLA Basic\b/);

    const material = screen.getByPlaceholderText('Select material...');
    fireEvent.focus(material);
    fireEvent.click(screen.getByRole('button', { name: 'PETG' }));
    await pickPreset(/^Bambu PETG HF\b/);

    expect(material).toHaveValue('PETG');
    expect(screen.getByPlaceholderText('Basic, Matte, Silk...')).toHaveValue(expectedSubtype);
  });

  it('keeps the chosen Spoolman filament when assigning a matching preset', async () => {
    vi.mocked(api.getSpoolmanInventoryFilaments).mockResolvedValueOnce([{
      id: 21,
      name: 'Basic',
      material: 'PLA Basic',
      color_name: 'Light Gray',
      color_hex: 'D1D3D5',
      weight: 1000,
      spool_weight: null,
      vendor: { id: 1, name: 'Bambu Lab' },
    }]);
    render(<SpoolFormModal isOpen={true} onClose={vi.fn()} mode="create" currencySymbol="$" spoolmanMode />);

    fireEvent.click(await screen.findByRole('button', { name: 'Spoolman Filament Catalog' }));
    fireEvent.click(await screen.findByRole('button', { name: /Bambu Lab — Basic/ }));
    await pickPreset(/^Bambu PLA Basic\b/);
    fireEvent.click(screen.getByRole('button', { name: 'Add Spool' }));

    await waitFor(() => expect(api.createSpoolmanInventorySpool).toHaveBeenCalledWith(expect.objectContaining({
      spoolman_filament_id: 21,
      material: 'PLA Basic',
      subtype: 'Basic',
      slicer_filament: 'GFA00',
    })));
  });

  it.each([
    { material: 'PLA Basic', subtype: 'Matte', option: 'PLA Basic' },
    { material: 'Custom PLA Basic', subtype: 'Basic', option: 'Use custom material: Custom PLA Basic' },
  ])('preserves a manual $material / $subtype pair when assigning a preset during creation', async ({ material, subtype, option }) => {
    render(<SpoolFormModal isOpen={true} onClose={vi.fn()} mode="create" currencySymbol="$" />);

    await pickMaterial(material, option);
    fireEvent.focus(screen.getByPlaceholderText('Basic, Matte, Silk...'));
    fireEvent.click(screen.getByRole('button', { name: subtype }));

    await pickPreset(/^Bambu PLA Basic\b/);
    expect(screen.getByPlaceholderText('Select material...')).toHaveValue(material);
    expect(screen.getByPlaceholderText('Basic, Matte, Silk...')).toHaveValue(subtype);
  });

  it('offers brands the catalog does not pair with the selected material', async () => {
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        spool={quickAddedSpool}
        mode="edit"
        currencySymbol="$"
      />,
    );

    await waitFor(() => expect(screen.getByRole('heading', { name: /Edit Spool/ })).toBeInTheDocument());

    fireEvent.focus(screen.getByPlaceholderText('Search brand...'));

    // Polymaker is the known ASA brand, Elegoo is only catalogued for PLA —
    // both are selectable, the pairing only decides the order.
    await waitFor(() => expect(screen.getByRole('button', { name: 'Polymaker' })).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Elegoo' })).toBeInTheDocument();
    expect(screen.getByText('Suggested')).toBeInTheDocument();
    expect(screen.getByText('All')).toBeInTheDocument();
  });

  it('does not carry Quick Add layout from a create into a later edit', async () => {
    const { rerender } = render(
      <SpoolFormModal isOpen={true} onClose={vi.fn()} mode="create" currencySymbol="$" />,
    );

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Add Spool' })).toBeInTheDocument());

    // Turn Quick Add on — the preset field disappears.
    fireEvent.click(screen.getByText('Quick Add (Stock)').closest('div')!.parentElement!.querySelector('button')!);
    await waitFor(() =>
      expect(screen.queryByPlaceholderText('Search filament presets...')).not.toBeInTheDocument(),
    );

    // Close, then reopen on an existing spool. The toggle only renders in
    // create mode, so a leaked quickAdd would strand the edit form.
    rerender(<SpoolFormModal isOpen={false} onClose={vi.fn()} mode="create" currencySymbol="$" />);
    rerender(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        spool={quickAddedSpool}
        mode="edit"
        currencySymbol="$"
      />,
    );

    await waitFor(() => expect(screen.getByRole('heading', { name: /Edit Spool/ })).toBeInTheDocument());
    expect(screen.getByPlaceholderText('Search filament presets...')).toBeInTheDocument();
  });
});
