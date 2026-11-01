import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { PrintersPage } from '../../pages/PrintersPage';
import { __resetColorCatalogForTests, setColorCatalog } from '../../utils/colors';

const printer = {
  id: 1,
  name: 'Filament appearance X1C',
  ip_address: '192.0.2.10',
  serial_number: '01P00A000000001',
  access_code: '12345678',
  model: 'X1C',
  enabled: true,
  nozzle_count: 1,
  nozzle_diameter: 0.4,
  nozzle_type: 'stainless_steel',
  auto_archive: true,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const baseTray = {
  tray_color: '387120FF',
  tray_type: 'PLA',
  tray_sub_brands: '',
  tray_info_idx: 'GFL99',
  tray_id_name: '',
  remain: 0,
  k: 0,
  cali_idx: null,
  tag_uid: null,
  tray_uuid: null,
  nozzle_temp_min: 190,
  nozzle_temp_max: 230,
  drying_temp: null,
  drying_time: null,
  state: 3,
};

const tray = (id: number, extra: Partial<typeof baseTray> = {}) => ({ id, ...baseTray, ...extra });
const ams = (id: number, trays: ReturnType<typeof tray>[]) => ({
  id,
  humidity: 30,
  temp: 25,
  is_ams_ht: id >= 128,
  serial_number: `AMS${id}`,
  sw_ver: '1.0.0',
  dry_time: 0,
  dry_status: 0,
  dry_sub_status: 0,
  dry_sf_reason: [],
  module_type: id >= 128 ? 'n3s' : 'ams',
  tray: trays,
});

const status = {
  connected: true,
  state: 'IDLE',
  progress: 0,
  layer_num: 0,
  total_layers: 0,
  temperatures: { nozzle: 25, bed: 25, chamber: 25 },
  remaining_time: 0,
  filename: null,
  wifi_signal: -50,
  speed_level: 2,
  ams: [
    ams(0, [
      tray(0),
      tray(1, { tray_color: 'FF8000FF', remain: 15 }),
      tray(2, { remain: -1 }),
      tray(3, { tray_type: '', state: 9, remain: 0 }),
    ]),
    ams(128, [tray(0, { tray_type: 'PETG', remain: 8 })]),
  ],
  vt_tray: [tray(254, { remain: 15 })],
};

let store: Record<string, string>;

function setup({
  printerStatus = status,
  dualNozzle = false,
  inventoryUsed,
}: {
  printerStatus?: typeof status & { expected_tray?: number; ams_mapping?: number[] };
  dualNozzle?: boolean;
  inventoryUsed?: number;
} = {}) {
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json([{ ...printer, nozzle_count: dualNozzle ? 2 : 1 }])),
    http.get('/api/v1/printers/:id/status', () => HttpResponse.json(printerStatus)),
    http.get('/api/v1/printers/:id/slot-presets', () => HttpResponse.json({})),
    http.get('/api/v1/queue/', () => HttpResponse.json([])),
    http.get('/api/v1/spoolman/status', () => HttpResponse.json({ enabled: false, connected: false, url: null })),
    http.get('/api/v1/inventory/assignments', () => HttpResponse.json(inventoryUsed === undefined ? [] : [{
      id: 1,
      spool_id: 77,
      printer_id: printer.id,
      printer_name: printer.name,
      ams_id: 0,
      tray_id: 0,
      fingerprint_color: null,
      fingerprint_type: null,
      configured: true,
      created_at: '2024-01-01T00:00:00Z',
      spool: {
        id: 77,
        material: 'PLA',
        subtype: null,
        brand: 'SUNLU',
        color_name: 'Olive Green',
        color_name_is_synthesized: false,
        rgba: '387120FF',
        label_weight: 1000,
        weight_used: inventoryUsed,
      },
    }])),
  );
  return render(<PrintersPage />);
}

async function slot(label: string): Promise<HTMLElement> {
  return waitFor(() => {
    const card = document.querySelector<HTMLElement>(`[data-filament-label="${label}"]`);
    expect(card).not.toBeNull();
    return card!;
  });
}

describe('PrintersPage filament appearance', () => {
  beforeEach(() => {
    store = { printerCardSize: '3' };
    vi.mocked(localStorage.getItem).mockImplementation((key: string) => store[key] ?? null);
    vi.mocked(localStorage.setItem).mockImplementation((key: string, value: string) => {
      store[key] = String(value);
    });
    setColorCatalog({ ff8000: 'Sunny Orange' });
  });

  afterEach(() => {
    __resetColorCatalogForTests();
    vi.mocked(localStorage.getItem).mockReset();
    vi.mocked(localStorage.setItem).mockReset();
  });

  it('retains classic circles by default and outlines known low amounts including zero', async () => {
    setup();
    const first = await slot('A1');
    expect(first).toHaveAttribute('data-filament-card', 'classic');
    expect(first.querySelector('.rounded-full.mx-auto')).not.toBeNull();
    expect(first).toHaveClass('border-red-500');
    expect(await slot('A2')).toHaveClass('border-red-500');
    expect(await slot('HT-A')).toHaveClass('border-red-500');
    expect(await slot('Ext')).toHaveClass('border-red-500');
    expect(await slot('A3')).not.toHaveClass('border-red-500');
    expect(await slot('A4')).not.toHaveClass('border-red-500');
  });

  it('renders detailed AMS, HT and external slots with catalog and assigned spool metadata', async () => {
    store['filament-card-style'] = 'detailed';
    setup({ inventoryUsed: 900 });
    const first = await slot('A1');
    expect(first).toHaveAttribute('data-filament-card', 'detailed');
    expect(within(first).getByText('A1')).toBeInTheDocument();
    await waitFor(() => expect(within(first).getByText('SUNLU')).toBeInTheDocument());
    expect(within(first).getByText('Olive Green')).toBeInTheDocument();
    expect(within(first).getByText('10%')).toBeInTheDocument();
    expect(first.querySelector('.rounded-full.mx-auto')).toBeNull();
    expect(within(await slot('A2')).getByText('Sunny Orange')).toBeInTheDocument();
    expect(within(await slot('A3')).getByText('—')).toBeInTheDocument();
    expect(within(await slot('HT-A')).getByText('HT-A')).toBeInTheDocument();
    expect(within(await slot('HT-A')).getByText('PETG')).toBeInTheDocument();
    expect(within(await slot('Ext')).getByText('Ext')).toBeInTheDocument();
    expect(within(await slot('Ext')).getByText('15%')).toBeInTheDocument();
  });

  it('identifies both detailed external holders on a dual-nozzle printer', async () => {
    store['filament-card-style'] = 'detailed';
    setup({ dualNozzle: true, printerStatus: {
      ...status,
      vt_tray: [tray(254, { remain: 20 }), tray(255, { remain: 60 })],
    } });
    expect(within(await slot('Ext-L')).getByText('Ext-L')).toBeInTheDocument();
    expect(within(await slot('Ext-R')).getByText('Ext-R')).toBeInTheDocument();
    expect(within(await slot('Ext-L')).getByText('20%')).toBeInTheDocument();
    expect(within(await slot('Ext-R')).getByText('60%')).toBeInTheDocument();
  });

  it('can disable low-filament borders for all holder types', async () => {
    store['filament-card-style'] = 'detailed';
    store['low-filament-border-enabled'] = 'false';
    setup();
    for (const label of ['A1', 'A2', 'HT-A', 'Ext']) {
      expect(await slot(label)).not.toHaveClass('border-red-500');
    }
  });

  it.each([
    { label: 'A1', trayId: 0, borderEnabled: true },
    { label: 'A1', trayId: 0, borderEnabled: false },
    { label: 'HT-A', trayId: 128, borderEnabled: true },
    { label: 'HT-A', trayId: 128, borderEnabled: false },
  ])('keeps detailed $label guidance badges in the header and its expected ring with low border $borderEnabled', async ({ label, trayId, borderEnabled }) => {
    store['filament-card-style'] = 'detailed';
    store['low-filament-border-enabled'] = String(borderEnabled);
    setup({ printerStatus: {
      ...status,
      state: 'PAUSE',
      expected_tray: trayId,
      ams_mapping: [trayId],
    } });

    const card = await slot(label);
    const expectedBadge = within(card).getByLabelText('Expected filament slot 1');
    const printBadge = within(card).getByLabelText('Active print slot 1');
    const header = within(card).getByText(label).parentElement!;
    expect(header).toHaveClass('flex');
    expect(header).toContainElement(expectedBadge);
    expect(header).toContainElement(printBadge);
    expect(expectedBadge.parentElement).toBe(printBadge.parentElement);
    expect(expectedBadge).not.toHaveClass('absolute');
    expect(printBadge).not.toHaveClass('absolute');
    expect(printBadge).toHaveTextContent('P1');
    expect(card).toHaveClass('ring-2', 'ring-amber-400');
    expect(card.classList.contains('border-red-500')).toBe(borderEnabled);
  });

  it('uses the configured low-filament threshold', async () => {
    store['filament-card-style'] = 'detailed';
    store['low-filament-threshold'] = '30';
    setup({ printerStatus: {
      ...status,
      ams: [ams(0, [tray(0, { remain: 30 }), tray(1, { remain: 31 })])],
    } });
    expect(await slot('A1')).toHaveClass('border-red-500');
    expect(await slot('A2')).not.toHaveClass('border-red-500');
  });

  it.each([
    { inventoryUsed: 900, percentage: '10%', low: true },
    { inventoryUsed: 1000, percentage: '80%', low: false },
  ])('uses resolved inventory fill and preserves the stale-zero fallback ($percentage)', async ({ inventoryUsed, percentage, low }) => {
    store['filament-card-style'] = 'detailed';
    setup({ inventoryUsed, printerStatus: {
      ...status,
      ams: [ams(0, [tray(0, { remain: 80 })])],
    } });
    const first = await slot('A1');
    await waitFor(() => expect(within(first).getByText('SUNLU')).toBeInTheDocument());
    expect(within(first).getByText(percentage)).toBeInTheDocument();
    expect(first.classList.contains('border-red-500')).toBe(low);
  });
});
