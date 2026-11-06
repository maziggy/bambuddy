/**
 * The "Last dried" field in the spool form (#2863).
 *
 * An AMS drying run stamps the date in the background, so the form must only
 * send it when the user changed it — otherwise saving an unrelated edit would
 * put back the date the form loaded with.
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

const driedSpool: InventorySpool = {
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
  last_dried_at: '2026-09-01T12:00:00',
  last_dried_temp: 55,
  last_dried_hours: 8,
} as unknown as InventorySpool;

async function openAdditional(mode: 'edit' | 'copy' | 'create' = 'edit') {
  render(
    <SpoolFormModal
      isOpen={true}
      onClose={vi.fn()}
      spool={mode === 'create' ? undefined : driedSpool}
      mode={mode}
      currencySymbol="$"
    />,
  );
  await waitFor(() => expect(screen.getAllByRole('heading').length).toBeGreaterThan(0));
  fireEvent.click(screen.getByRole('button', { name: /Color & Cost/ }));
}

describe('SpoolFormModal last dried (#2863)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads the stored date into the field', async () => {
    await openAdditional();
    const input = await screen.findByLabelText('Last dried');
    const expected = new Date('2026-09-01T12:00:00Z');
    const pad = (n: number) => String(n).padStart(2, '0');
    expect(input).toHaveValue(
      `${expected.getFullYear()}-${pad(expected.getMonth() + 1)}-${pad(expected.getDate())}T${pad(expected.getHours())}:${pad(expected.getMinutes())}`,
    );
  });

  it('does not send the date when it was not changed', async () => {
    await openAdditional();
    await screen.findByLabelText('Last dried');
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(api.updateSpool).toHaveBeenCalled());
    const payload = vi.mocked(api.updateSpool).mock.calls[0][1] as Record<string, unknown>;
    expect(payload).not.toHaveProperty('last_dried_at');
  });

  it('sends the time "Now" sets, as UTC', async () => {
    await openAdditional();
    await screen.findByLabelText('Last dried');
    const before = Date.now();
    fireEvent.click(screen.getByRole('button', { name: 'Now' }));
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(api.updateSpool).toHaveBeenCalled());
    const payload = vi.mocked(api.updateSpool).mock.calls[0][1] as Record<string, unknown>;
    const sent = new Date(payload.last_dried_at as string).getTime();
    expect(payload.last_dried_at as string).toMatch(/Z$/);
    // datetime-local has minute precision.
    expect(Math.abs(sent - before)).toBeLessThan(61_000);
  });

  it('sends null when the field is cleared', async () => {
    await openAdditional();
    const input = await screen.findByLabelText('Last dried');
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(api.updateSpool).toHaveBeenCalled());
    const payload = vi.mocked(api.updateSpool).mock.calls[0][1] as Record<string, unknown>;
    expect(payload.last_dried_at).toBeNull();
  });

  it('is not offered when copying a spool', async () => {
    await openAdditional('copy');
    await screen.findByText('Note');
    expect(screen.queryByLabelText('Last dried')).not.toBeInTheDocument();
  });
});
