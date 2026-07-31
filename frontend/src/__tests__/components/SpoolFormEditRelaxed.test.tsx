/**
 * Tests for #1905 — editing a spool that was created without a slicer preset.
 *
 * Covers:
 * - edit/copy no longer demand a slicer preset, brand or subtype
 * - picking a preset never overwrites identity fields the user already set
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
    await waitFor(() => expect(screen.getByRole('button', { name: 'Generic ASA' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Generic ASA' }));

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
    await waitFor(() => expect(screen.getByRole('button', { name: 'Generic ASA' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Generic ASA' }));

    expect(screen.getByPlaceholderText('Search brand...')).toHaveValue('Generic');
    expect(screen.getByPlaceholderText('Select material...')).toHaveValue('ASA');
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
