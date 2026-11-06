/**
 * The AMS hover card names a colour picked on the printer's screen (#3279).
 *
 * Without RFID the printer reports a palette hex a step off Bambu Studio's and
 * leaves tray_sub_brands empty. The card passes the slot's preset id, so the
 * colour is looked up in that preset's range: Translucent Orange, not the PLA
 * Basic Pumpkin Orange that is nearer across all ranges.
 */

import { describe, it, expect, afterEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { setColorCatalog, __resetColorCatalogForTests } from '../../utils/colors';

const mockPrinter = {
  id: 1,
  name: 'X1 Carbon',
  ip_address: '192.168.1.100',
  serial_number: '00M09A350100001',
  access_code: '12345678',
  model: 'X1C',
  enabled: true,
  nozzle_diameter: 0.4,
  nozzle_type: 'hardened_steel',
  location: 'Workshop',
  auto_archive: true,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

/** PETG Translucent orange picked on the printer's screen: no RFID, so no
 *  sub-brand and no colour code, only the preset and a palette hex. */
const screenPicked = {
  tray_color: 'FF9017FF',
  tray_type: 'PETG',
  tray_sub_brands: '',
  tray_id_name: '',
  tray_info_idx: 'GFG01',
  remain: 80,
  k: 0.02,
  cali_idx: null,
  tag_uid: null,
  tray_uuid: null,
  nozzle_temp_min: 190,
  nozzle_temp_max: 230,
  drying_temp: null,
  drying_time: null,
  state: 11,
};

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
  vt_tray: [],
  ams: [
    {
      id: 0,
      humidity: 30,
      temp: 25,
      is_ams_ht: false,
      serial_number: 'AMS00',
      sw_ver: '1.0.0',
      dry_time: 0,
      dry_status: 0,
      dry_sub_status: 0,
      dry_sf_reason: [],
      module_type: 'ams',
      tray: [{ id: 0, ...screenPicked }],
    },
  ],
};

/** Hover-card visibility flips after an 80ms timeout — wait it out. */
async function hoverFirstSlot() {
  await waitFor(() => {
    expect(screen.getAllByTestId('filament-slot').length).toBeGreaterThan(0);
  });
  fireEvent.mouseEnter(screen.getAllByTestId('filament-slot')[0]);
}

describe('PrintersPage — colour picked on the printer screen (#3279)', () => {
  afterEach(() => {
    __resetColorCatalogForTests();
  });

  it("names it from the slot preset's range", async () => {
    setColorCatalog(
      {},
      {},
      [
        { hex: 'ff9016', name: 'Pumpkin Orange', material: 'PLA Basic' },
        { hex: 'ff911a', name: 'Translucent Orange', material: 'PETG Translucent' },
      ],
      { GFG01: 'PETG Translucent' },
    );
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([mockPrinter])),
      http.get('/api/v1/queue/', () => HttpResponse.json([])),
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json(status)),
      http.get('/api/v1/printers/:id/slot-presets', () => HttpResponse.json({})),
    );

    render(<PrintersPage />);
    await hoverFirstSlot();

    await waitFor(() => {
      expect(screen.getByText('Translucent Orange')).toBeInTheDocument();
    });
    expect(screen.queryByText('Pumpkin Orange')).not.toBeInTheDocument();
  });
});
