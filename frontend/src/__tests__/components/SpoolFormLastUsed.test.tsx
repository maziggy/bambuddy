/**
 * The create form starts from the last created spool (#3619).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent, act } from '@testing-library/react';
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

// The test setup replaces localStorage with bare mocks; back them with a map.
const store = new Map<string, string>();

const KEY = 'bambuddy-last-spool';
const SPOOLMAN_KEY = 'bambuddy-last-spool-spoolman';

const remembered = {
  v: 1,
  brand: 'Polymaker',
  material: 'PLA',
  subtype: 'Basic',
  slicer_filament: 'GFL99',
  slicer_filament_name: 'Generic PLA',
  label_weight: 750,
  core_weight: 200,
  core_weight_catalog_id: 6,
  location_id: 3,
  cost_per_kg: 19.5,
  category: 'Stock',
  supplier_ids: [8],
  // Never carried, even when present in storage.
  rgba: 'FF0000FF',
  color_name: 'Red',
  note: 'old note',
};

const lists = () => {
  mApi.getLocations.mockResolvedValue([{ id: 3, name: 'Shelf' }]);
  mApi.getSpoolCatalog.mockResolvedValue([
    { id: 5, name: 'Cardboard A', weight: 200 },
    { id: 6, name: 'Cardboard B', weight: 200 },
  ]);
  mApi.getSuppliers.mockResolvedValue([{ id: 8, name: 'Shop' }]);
};

function renderForm(props: Partial<React.ComponentProps<typeof SpoolFormModal>> = {}) {
  return render(
    <SpoolFormModal isOpen onClose={vi.fn()} mode="create" currencySymbol="$" {...props} />,
  );
}

// Lets the lists answer and the remembered records land on the form.
async function settle() {
  await waitFor(() => expect(mApi.getLocations).toHaveBeenCalled());
  await new Promise((r) => setTimeout(r, 80));
}

function submit(label = 'Add Spool') {
  const buttons = screen.getAllByRole('button', { name: label });
  fireEvent.click(buttons[buttons.length - 1]);
}

const spoolRow = {
  id: 1, material: 'ABS', subtype: 'X', brand: 'Other', color_name: 'Blue', rgba: '0000FFFF',
  extra_colors: null, effect_type: null, label_weight: 1000, core_weight: 250, core_weight_catalog_id: null,
  weight_used: 0, slicer_filament: 'GFB00', slicer_filament_name: 'Generic ABS', note: null,
  tag_uid: null, tray_uuid: null, k_profiles: [], cost_per_kg: null, suppliers: [],
} as unknown as InventorySpool;

describe('SpoolFormModal last used values (#3619)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    store.clear();
    vi.mocked(localStorage.getItem).mockImplementation((k: string) => store.get(k) ?? null);
    vi.mocked(localStorage.setItem).mockImplementation((k: string, v: string) => { store.set(k, v); });
    lists();
    mApi.resolveProfileDefault.mockResolvedValue(null);
    mApi.getSpoolmanInventoryFilaments.mockResolvedValue([]);
  });

  it('applies the remembered fields and leaves the per-spool ones alone', async () => {
    store.set(KEY, JSON.stringify(remembered));
    renderForm();
    expect(await screen.findByText(/Started with your last spool \(Polymaker PLA Basic\)/)).toBeInTheDocument();
    await waitFor(() => expect(mApi.getSuppliers).toHaveBeenCalled());
    // Let the lists land before saving.
    await new Promise((r) => setTimeout(r, 50));
    submit();
    await waitFor(() => expect(mApi.createSpool).toHaveBeenCalledTimes(1));
    const sent = mApi.createSpool.mock.calls[0][0];
    expect(sent).toMatchObject({
      brand: 'Polymaker',
      material: 'PLA',
      subtype: 'Basic',
      slicer_filament: 'GFL99',
      slicer_filament_name: 'Generic PLA',
      label_weight: 750,
      core_weight: 200,
      core_weight_catalog_id: 6,
      location_id: 3,
      cost_per_kg: 19.5,
      category: 'Stock',
      rgba: '808080FF',
      color_name: null,
      note: null,
      weight_used: 0,
    });
    await waitFor(() => expect(mApi.setSpoolSuppliers).toHaveBeenCalledWith(99, [expect.objectContaining({ supplier_id: 8, supplier_article_number: null })]));
  });

  it('drops a location, catalogue entry and supplier that no longer exist', async () => {
    store.set(KEY, JSON.stringify({ ...remembered, location_id: 99, core_weight_catalog_id: 77, supplier_ids: [55] }));
    renderForm();
    await screen.findByText(/Started with your last spool/);
    await waitFor(() => expect(mApi.getSuppliers).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 50));
    submit();
    await waitFor(() => expect(mApi.createSpool).toHaveBeenCalled());
    const sent = mApi.createSpool.mock.calls[0][0];
    expect(sent.location_id).toBeNull();
    expect(sent.core_weight_catalog_id).toBeNull();
    expect(mApi.setSpoolSuppliers).not.toHaveBeenCalled();
  });

  it('starts empty on corrupt or missing storage', async () => {
    store.set(KEY, '{not json');
    renderForm();
    await screen.findByRole('heading', { name: 'Add Spool' });
    expect(screen.queryByText(/Started with your last spool/)).not.toBeInTheDocument();
  });

  it('starts empty when storage cannot be read', async () => {
    store.set(KEY, JSON.stringify(remembered));
    vi.mocked(localStorage.getItem).mockImplementation((k: string) => {
      if (k === KEY) throw new Error('blocked');
      return store.get(k) ?? null;
    });
    vi.mocked(localStorage.setItem).mockImplementation((k: string, v: string) => {
      if (k === KEY) throw new Error('blocked');
      store.set(k, v);
    });
    renderForm();
    await screen.findByRole('heading', { name: 'Add Spool' });
    expect(screen.queryByText(/Started with your last spool/)).not.toBeInTheDocument();
  });

  it('ignores fields of the wrong type', async () => {
    store.set(KEY, JSON.stringify({ v: 1, brand: 5, material: 'PETG', label_weight: 'x', location_id: -2 }));
    renderForm();
    expect(await screen.findByText(/Started with your last spool \(PETG\)/)).toBeInTheDocument();
  });

  it('ignores a record of another or no version', async () => {
    // Otherwise complete records: only the version keeps them off the form.
    for (const v of [2, '1', undefined]) {
      store.set(KEY, JSON.stringify({ ...remembered, v }));
      const { unmount } = renderForm();
      await screen.findByRole('heading', { name: 'Add Spool' });
      await settle();
      expect(screen.queryByText(/Started with your last spool/)).not.toBeInTheDocument();
      expect(screen.queryByDisplayValue('Generic PLA')).not.toBeInTheDocument();
      unmount();
    }
  });

  it('saves after a create and never on cancel', async () => {
    const onClose = vi.fn();
    const seeded = JSON.stringify({ v: 1, brand: 'Polymaker', material: 'PLA', subtype: 'Basic', slicer_filament: 'GFL99' });
    store.set(KEY, seeded);
    const { unmount } = renderForm({ onClose });
    await screen.findByText(/Started with your last spool/);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(store.get(KEY)).toBe(seeded);
    submit();
    await waitFor(() => expect(mApi.createSpool).toHaveBeenCalled());
    await waitFor(() => expect(JSON.parse(store.get(KEY)!).label_weight).toBe(1000));
    const saved = JSON.parse(store.get(KEY)!);
    expect(saved).toMatchObject({ v: 1, brand: 'Polymaker', material: 'PLA', subtype: 'Basic', slicer_filament: 'GFL99', supplier_ids: [] });
    expect(saved).not.toHaveProperty('rgba');
    expect(saved).not.toHaveProperty('note');
    unmount();
  });

  it('saves after a quick add', async () => {
    store.set(KEY, JSON.stringify({ v: 1, material: 'PETG' }));
    renderForm();
    await screen.findByText(/Started with your last spool/);
    // Different material typed would need the combobox; the remembered one is enough for a quick add.
    fireEvent.click(document.querySelector('button.rounded-full')!);
    submit();
    await waitFor(() => expect(mApi.createSpool).toHaveBeenCalled());
    await waitFor(() => expect(JSON.parse(store.get(KEY)!).label_weight).toBe(1000));
    expect(JSON.parse(store.get(KEY)!).material).toBe('PETG');
  });

  it('saves after a bulk create', async () => {
    store.set(KEY, JSON.stringify({ v: 1, material: 'PETG' }));
    renderForm();
    await screen.findByText(/Started with your last spool/);
    fireEvent.click(document.querySelector('button.rounded-full')!);
    await waitFor(() => expect(document.querySelector('input[max="100"]')).not.toBeNull());
    fireEvent.change(document.querySelector('input[max="100"]')!, { target: { value: '2' } });
    submit();
    await waitFor(() => expect(mApi.bulkCreateSpools).toHaveBeenCalled());
    await waitFor(() => expect(JSON.parse(store.get(KEY)!).label_weight).toBe(1000));
  });

  it('neither applies nor saves in edit and copy mode', async () => {
    store.set(KEY, JSON.stringify(remembered));
    const before = store.get(KEY);
    const edit = renderForm({ mode: 'edit', spool: spoolRow });
    await screen.findByRole('heading', { name: /Edit Spool/ });
    expect(screen.queryByText(/Started with your last spool/)).not.toBeInTheDocument();
    submit('Save');
    await waitFor(() => expect(mApi.updateSpool).toHaveBeenCalled());
    expect(store.get(KEY)).toBe(before);
    edit.unmount();

    const copy = renderForm({ mode: 'copy', spool: spoolRow });
    await screen.findByRole('heading', { name: 'Copy Spool' });
    expect(screen.queryByText(/Started with your last spool/)).not.toBeInTheDocument();
    submit('Copy Spool');
    await waitFor(() => expect(mApi.createSpool).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 50));
    expect(mApi.createSpool.mock.calls[0][0].brand).toBe('Other');
    expect(store.get(KEY)).toBe(before);
    copy.unmount();
  });

  it('Clear empties the form and hides the hint', async () => {
    store.set(KEY, JSON.stringify(remembered));
    renderForm();
    await screen.findByText(/Started with your last spool/);
    await new Promise((r) => setTimeout(r, 50));
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }));
    expect(screen.queryByText(/Started with your last spool/)).not.toBeInTheDocument();
    submit();
    // Validation stops an empty form; nothing is created.
    await new Promise((r) => setTimeout(r, 50));
    expect(mApi.createSpool).not.toHaveBeenCalled();
    expect(mApi.setSpoolSuppliers).not.toHaveBeenCalled();
  });

  it('keeps Spoolman and built-in memory apart and never carries the filament link', async () => {
    store.set(KEY, JSON.stringify({ v: 1, brand: 'Builtin', material: 'PLA' }));
    store.set(SPOOLMAN_KEY, JSON.stringify({ v: 1, brand: 'Remote', material: 'PETG', spoolman_filament_id: 4 }));
    mApi.getSpoolmanInventoryFilaments.mockResolvedValue([{ id: 4, name: 'PETG Remote', material: 'PETG', vendor: { name: 'Remote' }, color_hex: 'FF0000' }]);
    renderForm({ spoolmanMode: true });
    expect(await screen.findByText(/Started with your last spool \(Remote PETG\)/)).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 50));
    submit();
    await waitFor(() => expect(mApi.createSpoolmanInventorySpool).toHaveBeenCalled());
    // The filament carries the colour in Spoolman: it is never remembered or applied.
    const sent = mApi.createSpoolmanInventorySpool.mock.calls[0][0];
    expect(sent.brand).toBe('Remote');
    expect(sent.spoolman_filament_id ?? null).toBeNull();
    await waitFor(() => expect(JSON.parse(store.get(SPOOLMAN_KEY)!).brand).toBe('Remote'));
    expect(JSON.parse(store.get(SPOOLMAN_KEY)!)).not.toHaveProperty('spoolman_filament_id');
    expect(JSON.parse(store.get(KEY)!).brand).toBe('Builtin');
  });

  it('saves remembered suppliers in Spoolman mode, which has no inheritance', async () => {
    store.set(SPOOLMAN_KEY, JSON.stringify({ v: 1, brand: 'Remote', material: 'PETG', supplier_ids: [8] }));
    renderForm({ spoolmanMode: true });
    await screen.findByText(/Started with your last spool/);
    await settle();
    submit();
    await waitFor(() => expect(mApi.createSpoolmanInventorySpool).toHaveBeenCalled());
    await waitFor(() => expect(mApi.setSpoolmanSpoolSuppliers).toHaveBeenCalledWith(77, [expect.objectContaining({ supplier_id: 8 })]));
  });

  it('leaves the suppliers the backend inherited alone', async () => {
    store.set(KEY, JSON.stringify(remembered));
    mApi.createSpool.mockResolvedValue({ id: 99, suppliers: [{ id: 1, supplier_id: 2, supplier_name: 'Inherited' }] });
    renderForm();
    await screen.findByText(/Started with your last spool/);
    await settle();
    submit();
    await waitFor(() => expect(mApi.createSpool).toHaveBeenCalled());
    await waitFor(() => expect(JSON.parse(store.get(KEY)!).label_weight).toBe(750));
    await new Promise((r) => setTimeout(r, 50));
    expect(mApi.setSpoolSuppliers).not.toHaveBeenCalled();
    mApi.createSpool.mockResolvedValue({ id: 99 });
  });

  it('leaves the suppliers the backend inherited alone on a bulk create', async () => {
    store.set(KEY, JSON.stringify({ ...remembered, location_id: undefined }));
    mApi.bulkCreateSpools.mockResolvedValue([
      { id: 100, suppliers: [{ id: 1, supplier_id: 2, supplier_name: 'Inherited' }] },
      { id: 101, suppliers: [{ id: 2, supplier_id: 2, supplier_name: 'Inherited' }] },
    ]);
    renderForm();
    await screen.findByText(/Started with your last spool/);
    await settle();
    fireEvent.click(document.querySelector('button.rounded-full')!);
    await waitFor(() => expect(document.querySelector('input[max="100"]')).not.toBeNull());
    fireEvent.change(document.querySelector('input[max="100"]')!, { target: { value: '2' } });
    submit();
    await waitFor(() => expect(mApi.bulkCreateSpools).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 100));
    expect(mApi.setSpoolSuppliers).not.toHaveBeenCalled();
    mApi.bulkCreateSpools.mockResolvedValue([{ id: 100 }, { id: 101 }]);
  });

  it('saves remembered suppliers on a bulk create when nothing was inherited', async () => {
    store.set(KEY, JSON.stringify({ ...remembered, location_id: undefined }));
    renderForm();
    await settle();
    fireEvent.click(document.querySelector('button.rounded-full')!);
    await waitFor(() => expect(document.querySelector('input[max="100"]')).not.toBeNull());
    fireEvent.change(document.querySelector('input[max="100"]')!, { target: { value: '2' } });
    submit();
    await waitFor(() => expect(mApi.setSpoolSuppliers).toHaveBeenCalledTimes(2));
  });

  it('keeps the memory when submitted before the lists have loaded', async () => {
    store.set(KEY, JSON.stringify(remembered));
    // None of the lists answers before the submit.
    mApi.getLocations.mockReturnValue(new Promise(() => {}));
    mApi.getSpoolCatalog.mockReturnValue(new Promise(() => {}));
    mApi.getSuppliers.mockReturnValue(new Promise(() => {}));
    renderForm();
    await screen.findByText(/Started with your last spool/);
    submit();
    await waitFor(() => expect(mApi.createSpool).toHaveBeenCalled());
    const sent = mApi.createSpool.mock.calls[0][0];
    expect(sent.location_id).toBeNull();
    await waitFor(() => expect(JSON.parse(store.get(KEY)!).label_weight).toBe(750));
    const saved = JSON.parse(store.get(KEY)!);
    expect(saved.location_id).toBe(3);
    expect(saved.core_weight_catalog_id).toBe(6);
    expect(saved.supplier_ids).toEqual([8]);
  });

  it('a late list does not overwrite what the user changed meanwhile', async () => {
    store.set(KEY, JSON.stringify(remembered));
    let resolveLocations: (v: unknown) => void = () => {};
    let resolveCatalog: (v: unknown) => void = () => {};
    mApi.getLocations.mockReturnValue(new Promise((r) => { resolveLocations = r; }));
    mApi.getSpoolCatalog.mockReturnValue(new Promise((r) => { resolveCatalog = r; }));
    renderForm();
    await screen.findByText(/Started with your last spool/);
    fireEvent.click(screen.getByText('Color & Cost'));
    // The user picks another tare before the catalogue arrives.
    fireEvent.change(document.querySelector('input[type="number"][max="2000"]')!, { target: { value: '250' } });
    await act(async () => {
      resolveLocations([{ id: 3, name: 'Shelf' }, { id: 4, name: 'Drawer' }]);
      resolveCatalog([{ id: 6, name: 'Cardboard B', weight: 200 }]);
    });
    // The user then chooses a location of their own; the remembered one stays out.
    fireEvent.change(await screen.findByLabelText('Storage Location'), { target: { value: '4' } });
    submit();
    await waitFor(() => expect(mApi.createSpool).toHaveBeenCalled());
    const sent = mApi.createSpool.mock.calls[0][0];
    expect(sent.core_weight).toBe(250);
    expect(sent.core_weight_catalog_id).toBeNull();
    expect(sent.location_id).toBe(4);
  });

  it('a late location list still fills a location nobody chose', async () => {
    store.set(KEY, JSON.stringify(remembered));
    let resolveLocations: (v: unknown) => void = () => {};
    mApi.getLocations.mockReturnValue(new Promise((r) => { resolveLocations = r; }));
    renderForm();
    await screen.findByText(/Started with your last spool/);
    await new Promise((r) => setTimeout(r, 50));
    await act(async () => { resolveLocations([{ id: 3, name: 'Shelf' }]); });
    await settle();
    await new Promise((r) => setTimeout(r, 50));
    submit();
    await waitFor(() => expect(mApi.createSpool).toHaveBeenCalled());
    expect(mApi.createSpool.mock.calls[0][0].location_id).toBe(3);
  });

  it('shows the hint with a generic label when no product name was remembered', async () => {
    store.set(KEY, JSON.stringify({ v: 1, label_weight: 750, category: 'Stock' }));
    renderForm();
    expect(await screen.findByText('Started with your last spool')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Clear' })).toBeInTheDocument();
  });

  it('drops the hint and the pending memory when the modal closes', async () => {
    store.set(KEY, JSON.stringify(remembered));
    mApi.getLocations.mockReturnValue(new Promise(() => {}));
    const { rerender } = renderForm();
    await screen.findByText(/Started with your last spool/);
    rerender(<SpoolFormModal isOpen={false} onClose={vi.fn()} mode="create" currencySymbol="$" />);
    expect(screen.queryByText(/Started with your last spool/)).not.toBeInTheDocument();
    // Edit mode never reads the memory, even when it follows a create.
    rerender(<SpoolFormModal isOpen onClose={vi.fn()} mode="edit" spool={spoolRow} currencySymbol="$" />);
    await screen.findByRole('heading', { name: /Edit Spool/ });
    expect(screen.queryByText(/Started with your last spool/)).not.toBeInTheDocument();
  });

  it('keeps a remembered preset against the profile default', async () => {
    store.set(KEY, JSON.stringify({ v: 1, brand: 'Polymaker', material: 'PLA', subtype: 'Basic', slicer_filament: 'GFL99', slicer_filament_name: 'Generic PLA' }));
    mApi.resolveProfileDefault.mockResolvedValue({
      slicer_filament: 'PFUS01', slicer_filament_name: 'Default PLA', presets: [], k_profiles: [], default_ids: [1],
    });
    renderForm();
    await waitFor(() => expect(mApi.resolveProfileDefault).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 50));
    submit();
    await waitFor(() => expect(mApi.createSpool).toHaveBeenCalled());
    expect(mApi.createSpool.mock.calls[0][0].slicer_filament).toBe('GFL99');
  });

  it('fills an empty preset from the profile default', async () => {
    store.set(KEY, JSON.stringify({ v: 1, brand: 'Polymaker', material: 'PLA', subtype: 'Basic' }));
    mApi.resolveProfileDefault.mockResolvedValue({
      slicer_filament: 'PFUS01', slicer_filament_name: 'Default PLA', presets: [], k_profiles: [], default_ids: [1],
    });
    renderForm();
    await waitFor(() => expect(mApi.resolveProfileDefault).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 50));
    submit();
    await waitFor(() => expect(mApi.createSpool).toHaveBeenCalled());
    expect(mApi.createSpool.mock.calls[0][0].slicer_filament).toBe('PFUS01');
  });

  it('keeps the remembered empty spool entry when several share the weight', async () => {
    store.set(KEY, JSON.stringify({ v: 1, brand: 'Polymaker', material: 'PLA', subtype: 'Basic', slicer_filament: 'GFL99', core_weight: 200, core_weight_catalog_id: 6 }));
    renderForm();
    await screen.findByText(/Started with your last spool/);
    await waitFor(() => expect(mApi.getSpoolCatalog).toHaveBeenCalled());
    // Open the tab that renders the weight picker.
    fireEvent.click(screen.getByText('Color & Cost'));
    expect(await screen.findByDisplayValue('Cardboard B')).toBeInTheDocument();
    submit();
    await waitFor(() => expect(mApi.createSpool).toHaveBeenCalled());
    expect(mApi.createSpool.mock.calls[0][0].core_weight_catalog_id).toBe(6);
  });

  it('remembers whether the preset came from a profile default', async () => {
    mApi.resolveProfileDefault.mockResolvedValue({
      slicer_filament: 'PFUS01', slicer_filament_name: 'Default PLA', presets: [], k_profiles: [], default_ids: [1],
    });
    store.set(KEY, JSON.stringify({ v: 1, brand: 'Polymaker', material: 'PLA', subtype: 'Basic' }));
    renderForm();
    await waitFor(() => expect(mApi.resolveProfileDefault).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 50));
    submit();
    await waitFor(() => expect(mApi.createSpool).toHaveBeenCalled());
    await waitFor(() => expect(JSON.parse(store.get(KEY)!).slicer_filament).toBe('PFUS01'));
    expect(JSON.parse(store.get(KEY)!).preset_from_default).toBe(true);
  });

  it('a remembered default-filled preset gives way to the next default', async () => {
    store.set(KEY, JSON.stringify({ v: 1, brand: 'Polymaker', material: 'PLA', subtype: 'Basic', slicer_filament: 'PFUS01', slicer_filament_name: 'Default PLA', preset_from_default: true }));
    mApi.resolveProfileDefault.mockResolvedValue({
      slicer_filament: 'PFUS02', slicer_filament_name: 'Newer PLA', presets: [], k_profiles: [], default_ids: [1],
    });
    renderForm();
    await waitFor(() => expect(mApi.resolveProfileDefault).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 50));
    submit();
    await waitFor(() => expect(mApi.createSpool).toHaveBeenCalled());
    expect(mApi.createSpool.mock.calls[0][0].slicer_filament).toBe('PFUS02');
  });

  it('an untouched remembered preset gives way once the material changes', async () => {
    store.set(KEY, JSON.stringify({ v: 1, brand: 'Polymaker', material: 'PLA', subtype: 'Basic', slicer_filament: 'GFL99', slicer_filament_name: 'Generic PLA' }));
    mApi.resolveProfileDefault.mockImplementation(async (key: { material: string }) => (
      key.material === 'PETG'
        ? { slicer_filament: 'PFUS09', slicer_filament_name: 'Default PETG', presets: [], k_profiles: [], default_ids: [1] }
        : null
    ));
    renderForm();
    await screen.findByText(/Started with your last spool/);
    fireEvent.focus(screen.getByPlaceholderText('Select material...'));
    fireEvent.click(await screen.findByRole('button', { name: 'PETG' }));
    await waitFor(() => expect(mApi.resolveProfileDefault).toHaveBeenCalledWith(expect.objectContaining({ material: 'PETG' })));
    // Past the lookup's debounce and the answer.
    await new Promise((r) => setTimeout(r, 500));
    submit();
    await waitFor(() => expect(mApi.createSpool).toHaveBeenCalled());
    expect(mApi.createSpool.mock.calls[0][0].slicer_filament).toBe('PFUS09');
  });

  describe('a remembered preset that came from a profile default', () => {
    const remember = () => store.set(KEY, JSON.stringify({ v: 1, brand: 'Polymaker', material: 'PLA', subtype: 'Basic', slicer_filament: 'PFUS01', slicer_filament_name: 'Default PLA', preset_from_default: true }));

    it('survives the first open when the lookup fails', async () => {
      remember();
      mApi.resolveProfileDefault.mockRejectedValue(new Error('offline'));
      const err = vi.spyOn(console, 'error').mockImplementation(() => {});
      renderForm();
      await waitFor(() => expect(mApi.resolveProfileDefault).toHaveBeenCalled());
      await new Promise((r) => setTimeout(r, 50));
      submit();
      await waitFor(() => expect(mApi.createSpool).toHaveBeenCalled());
      expect(mApi.createSpool.mock.calls[0][0].slicer_filament).toBe('PFUS01');
      err.mockRestore();
    });

    it('is on the form when submitted immediately', async () => {
      remember();
      mApi.resolveProfileDefault.mockReturnValue(new Promise(() => {}));
      renderForm();
      await screen.findByText(/Started with your last spool/);
      submit();
      await waitFor(() => expect(mApi.createSpool).toHaveBeenCalled());
      expect(mApi.createSpool.mock.calls[0][0].slicer_filament).toBe('PFUS01');
    });

    it('never shows an empty preset input while opening', async () => {
      remember();
      mApi.resolveProfileDefault.mockResolvedValue({
        slicer_filament: 'PFUS01', slicer_filament_name: 'Default PLA', presets: [], k_profiles: [], default_ids: [1],
      });
      const { container } = renderForm();
      const values: string[] = [];
      const sample = () => {
        const el = container.ownerDocument.querySelector<HTMLInputElement>('input[placeholder="Search filament presets..."]');
        // Before the remembered values land the field is empty; only a later empty is a flicker.
        if (el && (el.value || values.length)) values.push(el.value);
      };
      const obs = new MutationObserver(sample);
      obs.observe(container.ownerDocument.body, { subtree: true, childList: true, attributes: true });
      await screen.findByText(/Started with your last spool/);
      await new Promise((r) => setTimeout(r, 500));
      obs.disconnect();
      expect(values).not.toContain('');
      expect(screen.getByDisplayValue('Default PLA')).toBeInTheDocument();
    });
  });
});
