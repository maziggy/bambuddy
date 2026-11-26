/**
 * The P3C's AMS 3P on the printer card (#3737): ONE unit (id 33, AMS type 7)
 * with seven slots in one row, a seven-dock nozzle rack, and its per-hotend
 * external inputs 249-255 drawn compactly. Trimmed from the reporter's status:
 * five PrintTools (rack ids 16-20), slots 0-4 loaded.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const mockPrinter = {
  id: 3,
  name: 'Desk P3C',
  ip_address: '192.168.1.14',
  serial_number: '33189A00000001',
  access_code: '12345678',
  model: 'P3C',
  enabled: true,
  nozzle_diameter: 0.4,
  nozzle_type: 'hardened_steel',
  location: 'Desk',
  auto_archive: true,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const COLOURS = ['00AE42FF', '5E43B7FF', 'F4EE2AFF', 'FFFFFFFF', 'BB3D43FF'];

const tray = (id: number) => ({
  id,
  tray_color: COLOURS[id] ?? '00000000',
  tray_type: id < COLOURS.length ? 'PLA' : '',
  tray_sub_brands: id < COLOURS.length ? 'PLA Basic' : '',
  tray_id_name: null,
  tray_info_idx: id < COLOURS.length ? 'GFA00' : '',
  remain: id < COLOURS.length ? 93 : -1,
  k: null,
  cali_idx: null,
  tag_uid: null,
  tray_uuid: null,
  nozzle_temp_min: 190,
  nozzle_temp_max: 230,
  drying_temp: null,
  drying_time: null,
  state: id < COLOURS.length ? 283 : 27,
  exists: true,
});

const ams3p = {
  id: 33,
  ams_type: 7,
  humidity: 2,
  temp: 33,
  is_ams_ht: false,
  serial_number: '',
  sw_ver: '',
  dry_time: 0,
  dry_status: 0,
  dry_sub_status: 0,
  dry_sf_reason: [8],
  module_type: 'n11a',
  dryer_slots: [],
  dryer_humidity: null,
  dryer_temp: null,
  tray: Array.from({ length: 7 }, (_, id) => tray(id)),
};

const emptyInputs = [249, 250, 251, 252, 253, 254, 255].map((id) => ({ id, tray_type: '', tray_color: '00000000', state: 1 }));

const rackNozzle = (id: number) => ({
  id, addr_id: id - 16, nozzle_type: 'HS01', nozzle_diameter: '0.4', wear: 0, stat: 184, max_temp: 300,
  serial_number: '', filament_color: COLOURS[id - 16], filament_id: 'GFA00', filament_type: 'PLA',
});

const p3cStatus = {
  connected: true,
  state: 'IDLE',
  progress: 0,
  layer_num: 0,
  total_layers: 0,
  temperatures: { nozzle: 25, bed: 25, chamber: 25 },
  remaining_time: 0,
  filename: null,
  wifi_signal: -57,
  speed_level: 2,
  tray_now: 255,
  vt_tray: emptyInputs,
  ams: [ams3p],
  nozzle_rack: [16, 17, 18, 19, 20].map(rackNozzle),
};

describe('PrintersPage - P3C AMS 3P', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([mockPrinter])),
      http.get('/api/v1/queue/', () => HttpResponse.json([])),
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json(p3cStatus)),
    );
  });

  it('draws one AMS 3P card with its seven slots in one row', async () => {
    const { container } = render(<PrintersPage />);

    // Five loaded slots behind filament hover cards; 5 and 6 are empty.
    await waitFor(() => {
      expect(screen.getAllByTestId('filament-slot')).toHaveLength(5);
    });
    expect(screen.getByText('AMS 3P')).toBeInTheDocument();
    const grid = Array.from(container.querySelectorAll<HTMLElement>('.grid')).find((el) =>
      el.style.gridTemplateColumns.startsWith('repeat(7,'),
    );
    expect(grid).toBeDefined();
    expect(screen.getByTestId('rack-externals-empty')).toHaveTextContent('7');
  });

  it('shows the door badge: the P3C has a door sensor', async () => {
    render(<PrintersPage />);

    expect(await screen.findByTitle('Closed')).toBeInTheDocument();
  });

  it('draws a seven-dock nozzle rack with the two empty docks', async () => {
    const { container } = render(<PrintersPage />);

    await waitFor(() => {
      expect(container.querySelectorAll('[data-rack-diameter]')).toHaveLength(7);
    });
    const chips = Array.from(container.querySelectorAll('[data-rack-diameter]')).map((chip) => chip.textContent);
    expect(chips).toEqual(['0.4', '0.4', '0.4', '0.4', '0.4', '—', '—']);
  });
});
