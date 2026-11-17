/**
 * The Slicer Preset field of the spool form (#3596).
 *
 * The spool's preset is a Bambu Cloud one that is not in the list (the cloud
 * list failed to load), so only the stored name can show it. Focusing the
 * field and leaving it must keep that name, search text must never become the
 * name, and the preset can be removed so the spool is Stock again.
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
    ]),
    getLocalPresets: vi.fn().mockResolvedValue({ filament: [] }),
    getBuiltinFilaments: vi.fn().mockResolvedValue([
      { filament_id: 'GFA05', name: 'Generic ASA' },
    ]),
    getPrinters: vi.fn().mockResolvedValue([]),
    getPrinterStatus: vi.fn().mockResolvedValue(null),
    getSpoolUsageHistory: vi.fn().mockResolvedValue([]),
    createSpool: vi.fn().mockResolvedValue({ id: 99 }),
    updateSpool: vi.fn().mockResolvedValue({ id: 7 }),
    saveSpoolKProfiles: vi.fn().mockResolvedValue([]),
    getSpoolFilamentPresets: vi.fn().mockResolvedValue([]),
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

const presetSpool: InventorySpool = {
  id: 7,
  material: 'PLA',
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
  slicer_filament: 'P90fd24f',
  slicer_filament_name: 'My PLA',
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

function openForm(mode: 'edit' | 'copy') {
  render(<SpoolFormModal isOpen={true} onClose={vi.fn()} spool={presetSpool} mode={mode} currencySymbol="$" />);
}

const presetField = () => screen.getByPlaceholderText('Search filament presets...');

function sentPreset(mode: 'edit' | 'copy') {
  const data =
    mode === 'edit'
      ? vi.mocked(api.updateSpool).mock.calls[0][1]
      : vi.mocked(api.createSpool).mock.calls[0][0];
  const { slicer_filament, slicer_filament_name } = data as Record<string, unknown>;
  return { slicer_filament, slicer_filament_name };
}

describe('SpoolFormModal slicer preset field (#3596)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('keeps the preset and its name when the field is opened and left', async () => {
    openForm('edit');
    await waitFor(() => expect(presetField()).toHaveValue('My PLA'));

    fireEvent.focus(presetField());
    expect(presetField()).toHaveValue('');
    fireEvent.mouseDown(document.body);
    expect(presetField()).toHaveValue('My PLA');

    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() => expect(api.updateSpool).toHaveBeenCalled());
    expect(sentPreset('edit')).toEqual({ slicer_filament: 'P90fd24f', slicer_filament_name: 'My PLA' });
  });

  it('does not save typed search text as the preset name', async () => {
    openForm('edit');
    await waitFor(() => expect(presetField()).toHaveValue('My PLA'));

    fireEvent.focus(presetField());
    fireEvent.change(presetField(), { target: { value: 'Generic' } });
    fireEvent.mouseDown(document.body);

    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() => expect(api.updateSpool).toHaveBeenCalled());
    expect(sentPreset('edit')).toEqual({ slicer_filament: 'P90fd24f', slicer_filament_name: 'My PLA' });
  });

  it.each(['edit', 'copy'] as const)('removes the preset in %s, so the spool is Stock', async (mode) => {
    openForm(mode);
    await waitFor(() => expect(presetField()).toHaveValue('My PLA'));

    fireEvent.click(screen.getByRole('button', { name: 'Remove preset' }));
    expect(presetField()).toHaveValue('');
    expect(screen.queryByRole('button', { name: 'Remove preset' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: mode === 'edit' ? /save/i : 'Copy Spool' }));
    await waitFor(() => expect(mode === 'edit' ? api.updateSpool : api.createSpool).toHaveBeenCalled());
    expect(sentPreset(mode)).toEqual({ slicer_filament: null, slicer_filament_name: null });
  });
});
