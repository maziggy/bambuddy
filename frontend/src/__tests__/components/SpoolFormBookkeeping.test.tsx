/**
 * Batch ID, owner and purchase date in the spool form (#2828, #1301, #3408).
 *
 * Loaded from the spool, sent on save (null when emptied), and kept on a copy,
 * which is how a second spool from the same box is added.
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
    getUsersSlim: vi.fn().mockResolvedValue([{ id: 1, username: 'dana' }]),
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

const storedSpool: InventorySpool = {
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
  batch_id: 'LOT-1',
  owner: 'Alice',
  purchase_date: '2026-03-14',
} as unknown as InventorySpool;

async function openAdditional(mode: 'edit' | 'copy' | 'create' = 'edit') {
  render(
    <SpoolFormModal
      isOpen={true}
      onClose={vi.fn()}
      spool={mode === 'create' ? undefined : storedSpool}
      mode={mode}
      currencySymbol="$"
    />,
  );
  await waitFor(() => expect(screen.getAllByRole('heading').length).toBeGreaterThan(0));
  fireEvent.click(screen.getByRole('button', { name: /Color & Cost/ }));
}

describe('SpoolFormModal batch ID, owner and purchase date', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads what the spool has', async () => {
    await openAdditional();

    expect(await screen.findByLabelText('Batch ID')).toHaveValue('LOT-1');
    expect(screen.getByLabelText('Owner')).toHaveValue('Alice');
    expect(screen.getByLabelText('Purchase date')).toHaveValue('2026-03-14');
  });

  it('saves a change', async () => {
    await openAdditional();
    fireEvent.change(await screen.findByLabelText('Batch ID'), { target: { value: ' LOT-2 ' } });
    fireEvent.change(screen.getByLabelText('Owner'), { target: { value: 'Bob' } });
    fireEvent.change(screen.getByLabelText('Purchase date'), { target: { value: '2026-04-05' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(api.updateSpool).toHaveBeenCalled());
    const payload = vi.mocked(api.updateSpool).mock.calls[0][1] as Record<string, unknown>;
    expect(payload).toMatchObject({ batch_id: 'LOT-2', owner: 'Bob', purchase_date: '2026-04-05' });
  });

  it('sends null for what is emptied, so the spool loses it', async () => {
    await openAdditional();
    fireEvent.change(await screen.findByLabelText('Batch ID'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('Owner'), { target: { value: '   ' } });
    fireEvent.change(screen.getByLabelText('Purchase date'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(api.updateSpool).toHaveBeenCalled());
    const payload = vi.mocked(api.updateSpool).mock.calls[0][1] as Record<string, unknown>;
    expect(payload).toMatchObject({ batch_id: null, owner: null, purchase_date: null });
  });

  it('a new spool starts without them', async () => {
    await openAdditional('create');

    expect(await screen.findByLabelText('Batch ID')).toHaveValue('');
    expect(screen.getByLabelText('Owner')).toHaveValue('');
    expect(screen.getByLabelText('Purchase date')).toHaveValue('');
  });

  it('a copy keeps them, as the next spool of the same box', async () => {
    await openAdditional('copy');

    expect(await screen.findByLabelText('Batch ID')).toHaveValue('LOT-1');
    expect(screen.getByLabelText('Owner')).toHaveValue('Alice');
    expect(screen.getByLabelText('Purchase date')).toHaveValue('2026-03-14');
  });

  it('suggests the registered users as owners, and still takes anyone else', async () => {
    const { container } = render(
      <SpoolFormModal isOpen={true} onClose={vi.fn()} spool={storedSpool} mode="edit" currencySymbol="$" />,
    );
    await waitFor(() => expect(screen.getAllByRole('heading').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('button', { name: /Color & Cost/ }));
    await screen.findByLabelText('Owner');

    fireEvent.focus(screen.getByLabelText('Owner'));
    expect(await screen.findByRole('button', { name: 'dana' })).toBeTruthy();
    expect(container.ownerDocument.querySelector('datalist#spool-owner-options')).toBeNull();

    // A customer is no user: typed in, it is sent as it is.
    fireEvent.change(screen.getByLabelText('Owner'), { target: { value: 'A walk-in customer' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() => expect(api.updateSpool).toHaveBeenCalled());
    expect(vi.mocked(api.updateSpool).mock.calls[0][1]).toMatchObject({ owner: 'A walk-in customer' });
  });
});
