/**
 * 3D Filament Profiles in the spool form (#2981): a lookup fills the form and
 * links the spool, and the link is saved with the spool -- on create, edit,
 * copy and in Spoolman mode -- but only when it changed.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { SpoolFormModal } from '../../components/SpoolFormModal';
import type { InventorySpool, ThreeDfpFilament } from '../../api/client';

// Mock the API client
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
    getSpoolmanSpoolUsageHistory: vi.fn().mockResolvedValue([]),
    createSpool: vi.fn().mockResolvedValue({ id: 99 }),
    createSpoolmanInventorySpool: vi.fn().mockResolvedValue({ id: 88 }),
    updateSpool: vi.fn().mockResolvedValue({ id: 1 }),
    saveSpoolKProfiles: vi.fn().mockResolvedValue([]),
    getSpoolFilamentPresets: vi.fn().mockResolvedValue([]),
    getPresetNozzleRange: vi.fn().mockResolvedValue({ nozzle_temp_min: null, nozzle_temp_max: null, source: null }),
    // 3D Filament Profiles (#2981): the form loads the spool's link, and
    // its lookup section asks whether the user is connected.
    getThreedfpSpoolLink: vi.fn().mockResolvedValue({ filament_id: null }),
    // A filled-in product looks up its profile default (#1326).
    resolveProfileDefault: vi.fn().mockResolvedValue(null),
    setThreedfpSpoolLink: vi.fn().mockResolvedValue({ filament_id: null }),
    threedfpStatus: vi.fn().mockResolvedValue({ connected: false, email: null }),
    threedfpLookup: vi.fn().mockResolvedValue({ results: [] }),
    threedfpGetFilament: vi.fn(),
    saveSpoolFilamentPresets: vi.fn().mockResolvedValue([]),
    getSpoolmanFilamentPresets: vi.fn().mockResolvedValue([]),
    saveSpoolmanFilamentPresets: vi.fn().mockResolvedValue([]),
    saveSpoolmanKProfiles: vi.fn().mockResolvedValue([]),
    updateSpoolmanInventorySpool: vi.fn().mockResolvedValue({ id: 42 }),
    bulkCreateSpoolmanInventorySpools: vi.fn().mockResolvedValue({
      created: [{ id: 1, material: 'PLA' }],
      requested_count: 1,
      failed_count: 0,
    }),
    getSpoolmanInventoryFilaments: vi.fn().mockResolvedValue([]),
    getAssignments: vi.fn().mockResolvedValue([]),
    getSpools: vi.fn().mockResolvedValue([]),
    linkTagToSpool: vi.fn().mockResolvedValue({ id: 73 }),
    getSpoolmanSlotAssignments: vi.fn().mockResolvedValue([]),
    unassignSpool: vi.fn().mockResolvedValue({}),
    unassignSpoolmanSlot: vi.fn().mockResolvedValue({}),
    // Suppliers (#2988) — the SupplierSection inside the form loads these.
    getSuppliers: vi.fn().mockResolvedValue([]),
    createSupplier: vi.fn().mockResolvedValue({ id: 1, name: 'S' }),
    setSpoolSuppliers: vi.fn().mockResolvedValue([]),
    setSpoolmanSpoolSuppliers: vi.fn().mockResolvedValue([]),
  },
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
}));

// Mock validateForm so we can bypass validation for the create-mode test
// (editing tests pass validation naturally since the spool has material + slicer_filament)
vi.mock('../../components/spool-form/types', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../components/spool-form/types')>();
  return {
    ...actual,
    validateForm: vi.fn().mockReturnValue({ isValid: true, errors: {} }),
  };
});

// Mock the toast context
const mockShowToast = vi.fn();
vi.mock('../../contexts/ToastContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/ToastContext')>();
  return {
    ...actual,
    useToast: () => ({ showToast: mockShowToast }),
  };
});

import { api } from '../../api/client';

const existingSpool: InventorySpool = {
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
  weight_used: 300,
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
};

const JADE: ThreeDfpFilament = {
  id: 101,
  brand_name: 'Bambu Lab',
  material: 'PLA',
  material_type: 'Matte',
  color: 'Jade White',
  rgb: '#F0F0E8',
  bambu_filament_id: 'GFA01',
  discontinued: false,
  url: 'https://3dfilamentprofiles.com/filament/details/101',
  nominal_weight: 1000,
  spool_weight: 210,
  temp_min: 190,
  temp_max: 230,
  stores: [
    { region: 'eu', price: 24.99, currency: 'EUR', availability: 'in_stock', updated_at: null, url: 'https://3dfp.to/p/35' },
  ],
};

async function lookUp(query: string) {
  const input = await screen.findByPlaceholderText('Barcode, filament id or link');
  fireEvent.change(input, { target: { value: query } });
  fireEvent.keyDown(input, { key: 'Enter' });
}

function clickAdd() {
  const submit = screen
    .getAllByRole('button', { name: /add spool/i })
    .find((btn) => btn.tagName === 'BUTTON' && btn.querySelector('svg.lucide-save'));
  fireEvent.click(submit!);
}

describe('SpoolFormModal + 3D Filament Profiles', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.threedfpStatus).mockResolvedValue({ connected: true, email: 'me@example.com' });
    vi.mocked(api.threedfpLookup).mockResolvedValue({ results: [JADE] });
    vi.mocked(api.getThreedfpSpoolLink).mockResolvedValue({ filament_id: null });
  });

  it('fills the form from a lookup and links the new spool', async () => {
    render(<SpoolFormModal isOpen={true} onClose={vi.fn()} currencySymbol="$" />);
    await lookUp('4019238055001');

    await waitFor(() => expect(screen.getByText('Bambu Lab PLA Matte Jade White')).toBeInTheDocument());
    expect(api.threedfpLookup).toHaveBeenCalledWith('4019238055001');
    // The linked filament's store, with a Reorder link used as 3DFP gave it.
    expect(screen.getByRole('link', { name: /reorder/i })).toHaveAttribute('href', 'https://3dfp.to/p/35');

    clickAdd();
    await waitFor(() => expect(api.createSpool).toHaveBeenCalledTimes(1));
    const [payload] = vi.mocked(api.createSpool).mock.calls[0] as unknown as [Record<string, unknown>];
    expect(payload).toMatchObject({
      brand: 'Bambu Lab',
      material: 'PLA',
      subtype: 'Matte',
      color_name: 'Jade White',
      rgba: 'F0F0E8FF',
      label_weight: 1000,
      core_weight: 210,
      slicer_filament: 'GFA01',
    });
    await waitFor(() => expect(api.setThreedfpSpoolLink).toHaveBeenCalledWith('inventory', 99, 101));
  });

  it('keeps a preset the spool already has', async () => {
    render(<SpoolFormModal isOpen={true} onClose={vi.fn()} spool={existingSpool} mode="edit" currencySymbol="$" />);
    await lookUp('101');
    await waitFor(() => expect(screen.getByText('Bambu Lab PLA Matte Jade White')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() => expect(api.updateSpool).toHaveBeenCalledTimes(1));
    const [, payload] = vi.mocked(api.updateSpool).mock.calls[0] as unknown as [number, Record<string, unknown>];
    expect(payload.slicer_filament).toBe('GFL99');
    expect(payload.brand).toBe('Bambu Lab');
    await waitFor(() => expect(api.setThreedfpSpoolLink).toHaveBeenCalledWith('inventory', 1, 101));
  });

  it('lets the user pick when a barcode matches several filaments', async () => {
    const black = { ...JADE, id: 102, color: 'Black', stores: [], nominal_weight: null };
    vi.mocked(api.threedfpLookup).mockResolvedValue({ results: [{ ...JADE, stores: [], nominal_weight: null }, black] });
    vi.mocked(api.threedfpGetFilament).mockResolvedValue({ ...JADE, id: 102, color: 'Black' });
    render(<SpoolFormModal isOpen={true} onClose={vi.fn()} currencySymbol="$" />);
    await lookUp('4019238055001');

    fireEvent.click(await screen.findByRole('button', { name: /Black/ }));

    // A barcode answers summaries: the chosen one is fetched in full.
    await waitFor(() => expect(api.threedfpGetFilament).toHaveBeenCalledWith(102));
    await screen.findByRole('button', { name: /unlink/i });
    clickAdd();
    await waitFor(() => expect(api.setThreedfpSpoolLink).toHaveBeenCalledWith('inventory', 99, 102));
  });

  it('saves nothing when the link was left alone', async () => {
    vi.mocked(api.getThreedfpSpoolLink).mockResolvedValue({ filament_id: 101 });
    vi.mocked(api.threedfpGetFilament).mockResolvedValue(JADE);
    render(<SpoolFormModal isOpen={true} onClose={vi.fn()} spool={existingSpool} mode="edit" currencySymbol="$" />);
    await waitFor(() => expect(screen.getByText('Bambu Lab PLA Matte Jade White')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /save/i }));
    await waitFor(() => expect(api.updateSpool).toHaveBeenCalledTimes(1));
    expect(api.setThreedfpSpoolLink).not.toHaveBeenCalled();
  });

  it('unlinks an edited spool', async () => {
    vi.mocked(api.getThreedfpSpoolLink).mockResolvedValue({ filament_id: 101 });
    vi.mocked(api.threedfpGetFilament).mockResolvedValue(JADE);
    render(<SpoolFormModal isOpen={true} onClose={vi.fn()} spool={existingSpool} mode="edit" currencySymbol="$" />);

    fireEvent.click(await screen.findByRole('button', { name: /unlink/i }));
    fireEvent.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(api.setThreedfpSpoolLink).toHaveBeenCalledWith('inventory', 1, null));
  });

  it('gives a copy the link of the spool it copies', async () => {
    vi.mocked(api.getThreedfpSpoolLink).mockResolvedValue({ filament_id: 101 });
    render(<SpoolFormModal isOpen={true} onClose={vi.fn()} spool={existingSpool} mode="copy" currencySymbol="$" />);
    await waitFor(() => expect(api.getThreedfpSpoolLink).toHaveBeenCalledWith('inventory', 1));
    await screen.findByRole('button', { name: /unlink/i });

    const copy = screen
      .getAllByRole('button', { name: /copy spool/i })
      .find((btn) => btn.tagName === 'BUTTON' && btn.querySelector('svg'));
    fireEvent.click(copy!);

    await waitFor(() => expect(api.setThreedfpSpoolLink).toHaveBeenCalledWith('inventory', 99, 101));
  });

  it('links a Spoolman spool through the Spoolman inventory', async () => {
    render(<SpoolFormModal isOpen={true} onClose={vi.fn()} currencySymbol="$" spoolmanMode={true} />);
    await lookUp('101');
    await waitFor(() => expect(screen.getByText('Bambu Lab PLA Matte Jade White')).toBeInTheDocument());

    clickAdd();
    await waitFor(() => expect(api.createSpoolmanInventorySpool).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(api.setThreedfpSpoolLink).toHaveBeenCalledWith('spoolman', 88, 101));
  });

  it('shows only the link to a viewer who has not connected', async () => {
    vi.mocked(api.threedfpStatus).mockResolvedValue({ connected: false, email: null });
    vi.mocked(api.getThreedfpSpoolLink).mockResolvedValue({ filament_id: 101 });
    render(<SpoolFormModal isOpen={true} onClose={vi.fn()} spool={existingSpool} mode="edit" currencySymbol="$" />);

    expect(await screen.findByText('Linked to 3D Filament Profiles filament #101')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /view on 3d filament profiles/i })).toHaveAttribute(
      'href',
      'https://3dfilamentprofiles.com/filament/details/101',
    );
    expect(screen.queryByPlaceholderText('Barcode, filament id or link')).not.toBeInTheDocument();
    expect(api.threedfpGetFilament).not.toHaveBeenCalled();
  });
});
