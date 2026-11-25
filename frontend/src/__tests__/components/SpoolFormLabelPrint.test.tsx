import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, screen, waitFor, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { SpoolFormModal } from '../../components/SpoolFormModal';
import { api, setAuthToken } from '../../api/client';
import type { InventorySpool } from '../../api/client';

vi.mock('../../api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/client')>()),
  api: {
    getSettings: vi.fn(),
    getUiSettings: vi.fn(),
    getCurrentUser: vi.fn(),
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
    getThreedfpSpoolLink: vi.fn().mockResolvedValue({ filament_id: null }),
    createSpool: vi.fn().mockResolvedValue({ id: 99 }),
    bulkCreateSpools: vi.fn().mockResolvedValue([{ id: 100, k_profiles: [] }, { id: 101, k_profiles: [] }]),
    saveSpoolKProfiles: vi.fn().mockResolvedValue([]),
    getSpoolFilamentPresets: vi.fn().mockResolvedValue([]),
    saveSpoolFilamentPresets: vi.fn().mockResolvedValue([]),
    resolveProfileDefault: vi.fn().mockResolvedValue(null),
    saveProfileDefault: vi.fn().mockResolvedValue({ id: 1 }),
    printLabelsToPrinter: vi.fn(),
    getLabelPrintJob: vi.fn(),
  },
}));

const mockShowToast = vi.fn();
vi.mock('../../contexts/ToastContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/ToastContext')>();
  return { ...actual, useToast: () => ({ showToast: mockShowToast }) };
});

const mApi = api as unknown as Record<string, ReturnType<typeof vi.fn>>;
const CONFIGURED = { label_printer_type: 'brother_ql', label_printer_host: 'lp.local', label_printer_tape: '50' };

// A create starts from this product, so the form needs no typing.
const product = {
  id: 1, material: 'PLA', subtype: 'Basic', brand: 'Polymaker', color_name: 'Red', rgba: 'FF0000FF',
  extra_colors: null, effect_type: null, label_weight: 1000, core_weight: 250, core_weight_catalog_id: null,
  weight_used: 0, slicer_filament: 'GFL99', slicer_filament_name: 'Generic PLA', nozzle_temp_min: null,
  nozzle_temp_max: null, note: null, added_full: null, last_used: null, encode_time: null, tag_uid: null,
  tray_uuid: null, data_origin: null, tag_type: null, archived_at: null, created_at: '', updated_at: '',
  cost_per_kg: null, last_scale_weight: null, last_weighed_at: null, k_profiles: [], category: null,
  low_stock_threshold_pct: null, storage_location: null, location_id: null,
} as unknown as InventorySpool;

// The print request is accepted as a job; the job poll then reports it done.
function acceptJob(total: number) {
  mApi.printLabelsToPrinter.mockResolvedValue({ job_id: 'j1', total });
  mApi.getLabelPrintJob.mockResolvedValue({ job_id: 'j1', state: 'done', printed: total, total, error: null });
}

const store = new Map<string, string>();

function openCreate() {
  return render(
    <SpoolFormModal isOpen onClose={vi.fn()} spool={product} mode="create" printersWithCalibrations={[]} currencySymbol="$" />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  store.clear();
  vi.mocked(localStorage.getItem).mockImplementation((key) => store.get(key) ?? null);
  vi.mocked(localStorage.setItem).mockImplementation((key, value) => void store.set(key, String(value)));
  mApi.getSettings.mockResolvedValue(CONFIGURED);
  mApi.getAuthStatus.mockResolvedValue({ auth_enabled: false });
  acceptJob(1);
});

describe('"Print label after saving" in the Add spool dialog (#3612)', () => {
  it('is hidden without a configured label printer', async () => {
    mApi.getSettings.mockResolvedValue({ label_printer_type: '', label_printer_host: '' });
    openCreate();
    await screen.findByText('Quick Add (Stock)');
    await waitFor(() => expect(mApi.getSettings).toHaveBeenCalled());
    expect(screen.queryByTestId('auto-print-label')).not.toBeInTheDocument();
  });

  it('is shown with a printer, off by default, and remembered per device', async () => {
    openCreate();
    const box = await screen.findByTestId('auto-print-label');
    expect(box).not.toBeChecked();
    fireEvent.click(box);
    expect(store.get('bambuddy.autoPrintLabel')).toBe('1');
    fireEvent.click(box);
    expect(store.get('bambuddy.autoPrintLabel')).toBe('0');
  });

  it('starts checked when it was left on', async () => {
    store.set('bambuddy.autoPrintLabel', '1');
    openCreate();
    expect(await screen.findByTestId('auto-print-label')).toBeChecked();
  });

  it('prints one roll label for the created spool with the configured tape', async () => {
    store.set('bambuddy.autoPrintLabel', '1');
    openCreate();
    await screen.findByTestId('auto-print-label');
    fireEvent.click(screen.getByRole('button', { name: /^Add Spool$/ }));
    await waitFor(() => expect(mApi.printLabelsToPrinter).toHaveBeenCalledTimes(1));
    const [source, body] = mApi.printLabelsToPrinter.mock.calls[0];
    expect(source).toBe('inventory');
    expect(body).toMatchObject({ spool_ids: [99], template: 'roll', roll: { tape: '50', length_mm: 40 }, monochrome: true });
  });

  it('does not print when the box is off', async () => {
    openCreate();
    await screen.findByTestId('auto-print-label');
    fireEvent.click(screen.getByRole('button', { name: /^Add Spool$/ }));
    await waitFor(() => expect(mApi.createSpool).toHaveBeenCalled());
    expect(mApi.printLabelsToPrinter).not.toHaveBeenCalled();
  });

  it('uses the roll options last used in the labels dialog', async () => {
    store.set('bambuddy.autoPrintLabel', '1');
    store.set(
      'bambuddy-label-print-options',
      JSON.stringify({ template: 'roll', rollTape: '50', rollLength: 70, fields: { roll: ['brand', 'qr'] } }),
    );
    openCreate();
    await screen.findByTestId('auto-print-label');
    fireEvent.click(screen.getByRole('button', { name: /^Add Spool$/ }));
    await waitFor(() => expect(mApi.printLabelsToPrinter).toHaveBeenCalled());
    expect(mApi.printLabelsToPrinter.mock.calls[0][1]).toMatchObject({
      roll: { tape: '50', length_mm: 70 },
      fields: ['brand', 'qr'],
    });
  });

  it('keeps the spool and toasts when the label cannot be printed', async () => {
    store.set('bambuddy.autoPrintLabel', '1');
    mApi.printLabelsToPrinter.mockRejectedValue(new Error('Label printer not reachable at lp.local:9100'));
    openCreate();
    await screen.findByTestId('auto-print-label');
    fireEvent.click(screen.getByRole('button', { name: /^Add Spool$/ }));
    await waitFor(() =>
      expect(mockShowToast).toHaveBeenCalledWith(expect.stringContaining('not reachable'), 'error'),
    );
    expect(mApi.createSpool).toHaveBeenCalledTimes(1);
    expect(mockShowToast).toHaveBeenCalledWith('Spool created', 'success');
  });

  it('prints one label per spool of a quick add with quantity', async () => {
    store.set('bambuddy.autoPrintLabel', '1');
    openCreate();
    await screen.findByTestId('auto-print-label');
    const quickAddToggle = screen
      .getAllByRole('button')
      .find((btn) => btn.className.includes('rounded-full') && btn.closest('div')?.textContent?.includes('Quick Add'));
    fireEvent.click(quickAddToggle!);
    const quantity = (await screen.findByText('Quantity')).parentElement!.querySelector('input') as HTMLInputElement;
    fireEvent.change(quantity, { target: { value: '2' } });
    fireEvent.click(screen.getByRole('button', { name: /^Add Spool$/ }));
    await waitFor(() => expect(mApi.printLabelsToPrinter).toHaveBeenCalledTimes(1));
    expect(mApi.printLabelsToPrinter.mock.calls[0][1]).toMatchObject({ spool_ids: [100, 101], template: 'roll' });
  });

  it('does not wait for the print: the spool toast and close come first, the result toast later', async () => {
    store.set('bambuddy.autoPrintLabel', '1');
    let finish!: (v: unknown) => void;
    mApi.getLabelPrintJob.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    const onClose = vi.fn();
    render(
      <SpoolFormModal isOpen onClose={onClose} spool={product} mode="create" printersWithCalibrations={[]} currencySymbol="$" />,
    );
    await screen.findByTestId('auto-print-label');
    fireEvent.click(screen.getByRole('button', { name: /^Add Spool$/ }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(mockShowToast).toHaveBeenCalledWith('Spool created', 'success');
    expect(mockShowToast).not.toHaveBeenCalledWith(expect.stringContaining('Printed'), 'success');
    finish({ job_id: 'j1', state: 'done', printed: 1, total: 1, error: null });
    await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith('Printed 1 label', 'success'));
  });

  it('toasts the plural count after a quick add with quantity', async () => {
    store.set('bambuddy.autoPrintLabel', '1');
    acceptJob(2);
    openCreate();
    await screen.findByTestId('auto-print-label');
    const quickAddToggle = screen
      .getAllByRole('button')
      .find((btn) => btn.className.includes('rounded-full') && btn.closest('div')?.textContent?.includes('Quick Add'));
    fireEvent.click(quickAddToggle!);
    const quantity = (await screen.findByText('Quantity')).parentElement!.querySelector('input') as HTMLInputElement;
    fireEvent.change(quantity, { target: { value: '2' } });
    fireEvent.click(screen.getByRole('button', { name: /^Add Spool$/ }));
    await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith('Printed 2 labels', 'success'));
  });

  it('splits the labels of a quick add into requests within the 4000 mm tape cap', async () => {
    store.set('bambuddy.autoPrintLabel', '1');
    // 45 labels of 200 mm = 9000 mm: 20 + 20 + 5 labels (the dialog caps a quick add at 100).
    store.set('bambuddy-label-print-options', JSON.stringify({ rollTape: '50', rollLength: 200 }));
    const ids = Array.from({ length: 45 }, (_, i) => ({ id: 1000 + i, k_profiles: [] }));
    mApi.bulkCreateSpools.mockResolvedValue(ids);
    mApi.printLabelsToPrinter.mockImplementation(async (_s: string, body: { spool_ids: number[] }) => ({
      job_id: `j${body.spool_ids.length}`,
      total: body.spool_ids.length,
    }));
    mApi.getLabelPrintJob.mockImplementation(async (id: string) => ({
      job_id: id, state: 'done', printed: Number(id.slice(1)), total: Number(id.slice(1)), error: null,
    }));
    openCreate();
    await screen.findByTestId('auto-print-label');
    const quickAddToggle = screen
      .getAllByRole('button')
      .find((btn) => btn.className.includes('rounded-full') && btn.closest('div')?.textContent?.includes('Quick Add'));
    fireEvent.click(quickAddToggle!);
    const quantity = (await screen.findByText('Quantity')).parentElement!.querySelector('input') as HTMLInputElement;
    fireEvent.change(quantity, { target: { value: '45' } });
    fireEvent.click(screen.getByRole('button', { name: /^Add Spool$/ }));
    await waitFor(() => expect(mApi.printLabelsToPrinter).toHaveBeenCalledTimes(3));
    expect(mApi.printLabelsToPrinter.mock.calls.map((c) => c[1].spool_ids.length)).toEqual([20, 20, 5]);
    await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith('Printed 45 labels', 'success'));
  });

  it('uses the remembered roll length and fields even when the dialog last showed a sheet format', async () => {
    store.set('bambuddy.autoPrintLabel', '1');
    store.set(
      'bambuddy-label-print-options',
      JSON.stringify({ template: 'avery_l7160', rollTape: '', rollLength: 90, fields: { roll: ['material'] } }),
    );
    openCreate();
    await screen.findByTestId('auto-print-label');
    fireEvent.click(screen.getByRole('button', { name: /^Add Spool$/ }));
    await waitFor(() => expect(mApi.printLabelsToPrinter).toHaveBeenCalled());
    const body = mApi.printLabelsToPrinter.mock.calls[0][1];
    expect(body).toMatchObject({ roll: { tape: '50', length_mm: 90 }, fields: ['material'] });
    expect(body).not.toHaveProperty('starting_position');
  });
});

describe('"Print label after saving" needs inventory:update (#3612)', () => {
  function signIn(permissions: string[]) {
    setAuthToken('test-token');
    mApi.getAuthStatus.mockResolvedValue({ auth_enabled: true, requires_setup: false });
    mApi.getCurrentUser.mockResolvedValue({ id: 2, username: 'user', is_admin: false, is_active: true, permissions });
    mApi.getUiSettings.mockResolvedValue({ label_printer_configured: true, label_printer_tape: '50' });
  }
  afterEach(() => setAuthToken(null));

  // The settings are in once the query has answered and React has rendered.
  async function settingsLoaded() {
    await screen.findByText('Quick Add (Stock)');
    await waitFor(() => expect(mApi.getUiSettings).toHaveBeenCalled());
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  it('is hidden without the permission', async () => {
    signIn(['inventory:read', 'inventory:create']);
    openCreate();
    await settingsLoaded();
    expect(screen.queryByTestId('auto-print-label')).not.toBeInTheDocument();
  });

  it('does not print for a choice remembered on this device without the permission', async () => {
    // Left on by someone who may print, on the same browser.
    store.set('bambuddy.autoPrintLabel', '1');
    signIn(['inventory:read', 'inventory:create']);
    openCreate();
    await settingsLoaded();
    expect(screen.queryByTestId('auto-print-label')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^Add Spool$/ }));
    await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith('Spool created', 'success'));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(mApi.printLabelsToPrinter).not.toHaveBeenCalled();
    // The device's choice is left alone for those who may print.
    expect(store.get('bambuddy.autoPrintLabel')).toBe('1');
  });

  it('is shown and prints with the permission', async () => {
    store.set('bambuddy.autoPrintLabel', '1');
    signIn(['inventory:read', 'inventory:create', 'inventory:update']);
    openCreate();
    expect(await screen.findByTestId('auto-print-label')).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: /^Add Spool$/ }));
    await waitFor(() => expect(mApi.printLabelsToPrinter).toHaveBeenCalledTimes(1));
  });
});
