/**
 * Picking a brand on a new spool puts on its default empty-spool weight (#3727).
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
    orcaCloudStatus: vi.fn().mockResolvedValue({ connected: false }),
    getFilamentPresets: vi.fn().mockResolvedValue([]),
    getSpoolCatalog: vi.fn(),
    getDefaultSpool: vi.fn(),
    getLocations: vi.fn(),
    getSuppliers: vi.fn(),
    getColorCatalog: vi.fn().mockResolvedValue([]),
    getLocalPresets: vi.fn().mockResolvedValue({ filament: [] }),
    getBuiltinFilaments: vi.fn().mockResolvedValue([]),
    getPrinters: vi.fn().mockResolvedValue([]),
    getSpoolUsageHistory: vi.fn().mockResolvedValue([]),
    getSpools: vi.fn().mockResolvedValue([]),
    getSpoolmanInventoryFilaments: vi.fn(),
    createSpool: vi.fn().mockResolvedValue({ id: 99 }),
    bulkCreateSpools: vi.fn().mockResolvedValue([{ id: 100 }, { id: 101 }]),
    createSpoolmanInventorySpool: vi.fn().mockResolvedValue({ id: 77 }),
    updateSpool: vi.fn().mockResolvedValue({ id: 1 }),
    saveSpoolKProfiles: vi.fn().mockResolvedValue([]),
    saveSpoolFilamentPresets: vi.fn().mockResolvedValue([]),
    getSpoolFilamentPresets: vi.fn().mockResolvedValue([]),
    getThreedfpSpoolLink: vi.fn().mockResolvedValue({ filament_id: null }),
    setThreedfpSpoolLink: vi.fn().mockResolvedValue({ filament_id: null }),
    threedfpStatus: vi.fn().mockResolvedValue({ connected: false, email: null }),
    setSpoolSuppliers: vi.fn().mockResolvedValue([]),
    setSpoolmanSpoolSuppliers: vi.fn().mockResolvedValue([]),
    saveSpoolmanKProfiles: vi.fn().mockResolvedValue([]),
    saveSpoolmanFilamentPresets: vi.fn().mockResolvedValue([]),
    resolveProfileDefault: vi.fn().mockResolvedValue(null),
    saveProfileDefault: vi.fn().mockResolvedValue({ id: 1 }),
  },
}));

vi.mock('../../contexts/ToastContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/ToastContext')>();
  return { ...actual, useToast: () => ({ showToast: vi.fn() }) };
});

const mApi = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const catalog = [
  { id: 1, name: 'Bambu Lab - Plastic Low Temp', weight: 250, is_default: true, manufacturer: 'Bambu Lab', default_for_brand: true },
  { id: 2, name: 'Sunlu - Plastic V3', weight: 179, is_default: true, manufacturer: 'Sunlu', default_for_brand: false },
  { id: 3, name: 'Sunlu - Plastic V3 + Cardboard Core', weight: 217, is_default: true, manufacturer: 'Sunlu', default_for_brand: true },
  { id: 4, name: 'Mine', weight: 205, is_default: false, manufacturer: null, default_for_brand: false },
  { id: 5, name: 'Bambu Lab - Cardboard', weight: 196, is_default: true, manufacturer: 'Bambu Lab', default_for_brand: false },
];

async function openForm(props: Partial<React.ComponentProps<typeof SpoolFormModal>> = {}) {
  render(<SpoolFormModal isOpen onClose={vi.fn()} mode="create" currencySymbol="$" {...props} />);
  await waitFor(() => expect(mApi.getSpoolCatalog).toHaveBeenCalled());
  // Let the catalogue land on the form
  await new Promise((r) => setTimeout(r, 50));
}

function pickBrand(brand: string) {
  fireEvent.click(screen.getByRole('button', { name: 'Filament Info' }));
  const input = screen.getByPlaceholderText('Search brand...');
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: brand } });
  fireEvent.click(screen.getByText(`Use "${brand}"`));
}

function weightPicker(): HTMLInputElement {
  fireEvent.click(screen.getByText('Color & Cost'));
  return screen.getAllByPlaceholderText(/search/i).find((el) =>
    el.getAttribute('placeholder')?.toLowerCase().includes('spool'),
  ) as HTMLInputElement;
}

const SUNLU_CORE = 'Sunlu - Plastic V3 + Cardboard Core';

describe('SpoolFormModal brand default spool (#3727)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(localStorage.getItem).mockReturnValue(null);
    mApi.getSpoolCatalog.mockResolvedValue(catalog);
    // The server's answer: Sunlu's default row, nothing for other brands
    mApi.getDefaultSpool.mockImplementation(async (brand: string, material?: string) => {
      if (brand.trim().toLowerCase() === 'sunlu') return { weight: 217, catalog_id: 3 };
      if (brand === 'Bambu Lab' && material === 'PPA-CF') return { weight: 196, catalog_id: 5 };
      return null;
    });
    mApi.getLocations.mockResolvedValue([]);
    mApi.getSuppliers.mockResolvedValue([]);
    mApi.resolveProfileDefault.mockResolvedValue(null);
    mApi.getSpoolmanInventoryFilaments.mockResolvedValue([]);
  });

  it("puts on the brand's default spool", async () => {
    await openForm();
    pickBrand('Sunlu');
    await waitFor(() => expect(weightPicker().value).toBe(SUNLU_CORE));
  });

  it('matches the brand in any case', async () => {
    await openForm();
    pickBrand('sunlu');
    await waitFor(() => expect(weightPicker().value).toBe(SUNLU_CORE));
  });

  it('takes it back off for a brand without a default', async () => {
    await openForm();
    pickBrand('Sunlu');
    await waitFor(() => expect(weightPicker().value).toBe(SUNLU_CORE));
    pickBrand('Acme');
    await waitFor(() => expect(weightPicker().value).not.toContain('Sunlu'));
  });

  it('drops an answer for a brand that has since been replaced', async () => {
    let answerSunlu: (v: unknown) => void = () => {};
    mApi.getDefaultSpool.mockImplementation((brand: string) =>
      brand === 'Sunlu' ? new Promise((resolve) => { answerSunlu = resolve; }) : Promise.resolve(null));
    await openForm();
    pickBrand('Sunlu');
    pickBrand('Acme');
    answerSunlu({ weight: 217, catalog_id: 3 });
    await new Promise((r) => setTimeout(r, 20));
    expect(weightPicker().value).not.toContain('Sunlu');
  });

  it('never replaces an empty-spool weight the user picked', async () => {
    await openForm();
    const picker = weightPicker();
    fireEvent.focus(picker);
    fireEvent.click(await screen.findByText('Mine'));
    pickBrand('Sunlu');
    await new Promise((r) => setTimeout(r, 20));
    expect(weightPicker().value).toBe('Mine');
  });

  it('leaves an edited spool alone', async () => {
    const spool = {
      id: 1, material: 'PLA', subtype: '', brand: 'eSUN', color_name: 'Blue', rgba: '0000FFFF',
      extra_colors: null, effect_type: null, label_weight: 1000, core_weight: 240, core_weight_catalog_id: null,
      weight_used: 0, slicer_filament: '', slicer_filament_name: '', note: null,
      tag_uid: null, tray_uuid: null, k_profiles: [], cost_per_kg: null, suppliers: [],
    } as unknown as InventorySpool;
    await openForm({ mode: 'edit', spool });
    pickBrand('Sunlu');
    await new Promise((r) => setTimeout(r, 20));
    expect(mApi.getDefaultSpool).not.toHaveBeenCalled();
    expect(weightPicker().value).not.toBe(SUNLU_CORE);
  });

  it('asks again when the material changes, for a Bambu cardboard-spool line', async () => {
    await openForm();
    pickBrand('Bambu Lab');
    await waitFor(() => expect(weightPicker().value).toBe('Bambu Lab - Plastic Low Temp'));
    fireEvent.click(screen.getByRole('button', { name: 'Filament Info' }));
    const material = screen.getByPlaceholderText('Select material...');
    fireEvent.focus(material);
    fireEvent.change(material, { target: { value: 'PPA-CF' } });
    fireEvent.click(await screen.findByRole('button', { name: /PPA-CF/ }));
    await waitFor(() => expect(weightPicker().value).toBe('Bambu Lab - Cardboard'));
  });
});
