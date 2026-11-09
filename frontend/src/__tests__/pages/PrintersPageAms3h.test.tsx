/**
 * The H3C's AMS 3H on the printer card: ONE unit (id 32, AMS type 6) with
 * nine slots, numbered as Bambu Studio does (global trays 28-36), and the
 * H3C's per-hotend external inputs 247-255 drawn compactly.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const mockPrinter = {
  id: 2,
  name: 'Workshop H3C',
  ip_address: '192.168.1.13',
  serial_number: '32X00000000001',
  access_code: '12345678',
  model: 'H3C',
  enabled: true,
  nozzle_diameter: 0.4,
  nozzle_type: 'hardened_steel',
  location: 'Workshop',
  auto_archive: true,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const tray = (id: number, color: string) => ({
  id,
  tray_color: color,
  tray_type: 'PLA',
  tray_sub_brands: '',
  tray_id_name: null,
  tray_info_idx: 'GFL99',
  remain: -1,
  k: null,
  cali_idx: null,
  tag_uid: null,
  tray_uuid: null,
  nozzle_temp_min: 190,
  nozzle_temp_max: 230,
  drying_temp: null,
  drying_time: null,
  state: 27,
  exists: true,
});

const ams3h = {
  id: 32,
  ams_type: 6,
  humidity: 33,
  temp: 27.6,
  is_ams_ht: false,
  serial_number: 'SN3H',
  sw_ver: '02.00.32.82',
  dry_time: 0,
  dry_status: 0,
  dry_sub_status: 0,
  dry_sf_reason: [],
  module_type: 'n10a',
  dryer_slots: [5, 7],
  dryer_humidity: 25,
  dryer_temp: 28.1,
  tray: [
    tray(0, 'DE4343FF'), tray(1, 'FFFFFFFF'), tray(2, '757575FF'), tray(3, '000000FF'), tray(4, '0178BFFF'),
    tray(5, 'AE96D4FF'), tray(6, 'F7D959FF'), { id: 7, state: 160, exists: false }, tray(8, '61C680FF'),
  ],
};

const emptyInputs = [247, 248, 249, 250, 251, 252, 253, 254, 255].map((id) => ({ id, tray_type: '', tray_color: '00000000', state: 1 }));

const h3cStatus = {
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
  tray_now: 255,
  vt_tray: emptyInputs,
  ams: [ams3h],
};

describe('PrintersPage - H3C AMS 3H', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([mockPrinter])),
      http.get('/api/v1/queue/', () => HttpResponse.json([])),
    );
  });

  it('draws one card with the nine slots and its dryer, and no tile per empty input', async () => {
    server.use(http.get('/api/v1/printers/:id/status', () => HttpResponse.json(h3cStatus)));

    render(<PrintersPage />);

    // Eight loaded slots (slot 8 is empty) behind filament hover cards.
    await waitFor(() => {
      expect(screen.getAllByTestId('filament-slot')).toHaveLength(8);
    });
    expect(screen.getByText('AMS 3H')).toBeInTheDocument();
    expect(screen.getByTestId('ams3h-dryer')).toBeInTheDocument();
    expect(screen.getByTestId('rack-externals-empty')).toHaveTextContent('9');
  });

  it('loads an AMS 3H slot by its global tray id 28 + slot', async () => {
    const user = userEvent.setup();
    let captured: string | null = null;
    server.use(
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json(h3cStatus)),
      http.post('/api/v1/printers/:id/ams/load', ({ request }) => {
        captured = new URL(request.url).searchParams.get('tray_id');
        return HttpResponse.json({ success: true, message: 'Loading filament from AMS 3H slot 3' });
      }),
    );

    render(<PrintersPage />);

    await waitFor(() => {
      expect(screen.getAllByTestId('filament-slot')).toHaveLength(8);
    });
    fireEvent.mouseEnter(screen.getAllByTestId('filament-slot')[2]);
    await waitFor(() => {
      expect(screen.getByText('Load')).toBeInTheDocument();
    });
    await user.click(screen.getByText('Load'));

    await waitFor(() => {
      expect(captured).toBe('30');
    });
  });

  it('shows a loaded external input as Ext-n without load or unload', async () => {
    const inputs = emptyInputs.map((vt) => (vt.id === 250 ? { ...tray(250, 'FF0000FF'), tray_type: 'PETG' } : vt));
    server.use(http.get('/api/v1/printers/:id/status', () => HttpResponse.json({ ...h3cStatus, vt_tray: inputs })));

    render(<PrintersPage />);

    await waitFor(() => {
      expect(screen.getAllByTestId('filament-slot')).toHaveLength(9);
    });
    expect(screen.queryByTestId('rack-externals-empty')).not.toBeInTheDocument();
    // Input 250 feeds hotend 5 (PrintTool 6).
    const slots = screen.getAllByTestId('filament-slot');
    expect(slots[8]).toHaveTextContent('6');
    expect(slots[8]).toHaveTextContent('PETG');
  });

  it('names an external input Ext-n in its Configure dialog', async () => {
    const inputs = emptyInputs.map((vt) => (vt.id === 250 ? { ...tray(250, 'FF0000FF'), tray_type: 'PETG' } : vt));
    server.use(http.get('/api/v1/printers/:id/status', () => HttpResponse.json({ ...h3cStatus, vt_tray: inputs })));
    const user = userEvent.setup();

    render(<PrintersPage />);

    await waitFor(() => {
      expect(screen.getAllByTestId('filament-slot')).toHaveLength(9);
    });
    fireEvent.mouseEnter(screen.getAllByTestId('filament-slot')[8]);
    await user.click(await screen.findByRole('button', { name: 'Configure' }));

    // Input 250 is stored as (255, 5); the generic label read "External Slot 6".
    await waitFor(() => {
      expect(screen.getAllByText('Ext-6').length).toBeGreaterThan(0);
    });
    expect(screen.queryByText(/External Slot/)).not.toBeInTheDocument();
  });

  it('keeps a tile for an empty external input with a spool assigned', async () => {
    server.use(
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json(h3cStatus)),
      http.get('/api/v1/inventory/assignments', () => HttpResponse.json([{
        id: 1, spool_id: 42, printer_id: 2, ams_id: 255, tray_id: 5, printer_name: 'Workshop H3C', ams_label: null,
        spool: { id: 42, material: 'PLA', brand: 'Generic', color_name: 'Red', label_weight: 1000, weight_used: 0, rgba: 'FF0000FF' },
      }])),
    );

    render(<PrintersPage />);

    // AMS 3H slot 8 and the assigned input 250 (stored as (255, 5)).
    await waitFor(() => {
      expect(screen.getAllByTestId('empty-slot')).toHaveLength(2);
    });
    expect(screen.queryByTestId('rack-externals-empty')).not.toBeInTheDocument();
  });
});
