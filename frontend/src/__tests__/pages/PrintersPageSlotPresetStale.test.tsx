/**
 * A swapped spool must not leave the previous spool's preset name on the card.
 *
 * `slot_preset_mappings` is fetched over REST and remembers what the slot was
 * last configured with; the tray's own `tray_info_idx` arrives on the
 * WebSocket. The display chain puts the stored row first -- that is what keeps
 * a hand-picked preset name on a slot -- so between the swap and the row being
 * refetched the card named the spool that had just been removed. Everything
 * else on the card rides the status push and was already correct, which is why
 * it read as one wrong line rather than a stale card.
 *
 * Verbatim from the report: a Bambu ABS Orange came out of A1, a PLA Matte
 * Dark Blue went in, and the card still said "Bambu ABS".
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

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

/** The PLA Matte Dark Blue now in the slot, as the printer reports it. */
const darkBlue = {
  tray_color: '042F56FF',
  tray_type: 'PLA',
  tray_sub_brands: 'PLA Matte',
  tray_id_name: 'A01-B3',
  tray_info_idx: 'GFA01',
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
      tray: [{ id: 0, ...darkBlue }],
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

function serve(slotPresets: Record<number, unknown>) {
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json([mockPrinter])),
    http.get('/api/v1/queue/', () => HttpResponse.json([])),
    http.get('/api/v1/printers/:id/status', () => HttpResponse.json(status)),
    http.get('/api/v1/printers/:id/slot-presets', () => HttpResponse.json(slotPresets)),
    http.post('/api/v1/cloud/filament-info', () =>
      HttpResponse.json({
        GFA01: { name: 'Bambu PLA Matte', k: null },
        GFB00: { name: 'Bambu ABS', k: null },
      }),
    ),
  );
}

describe('PrintersPage — a stale slot preset must not name the slot', () => {
  beforeEach(() => {
    server.use(http.get('/api/v1/printers/', () => HttpResponse.json([mockPrinter])));
  });

  it('ignores the row left by the spool that was removed', async () => {
    serve({
      0: { printer_id: 1, ams_id: 0, tray_id: 0, preset_id: 'GFSB00', preset_name: 'Bambu ABS', preset_source: 'cloud' },
    });

    render(<PrintersPage />);
    await hoverFirstSlot();

    // The live filament id names the slot instead.
    await waitFor(() => {
      expect(screen.getByText('Bambu PLA Matte')).toBeInTheDocument();
    });
    expect(screen.queryByText('Bambu ABS')).not.toBeInTheDocument();
  });

  it('still shows a hand-picked name while it describes what is in the slot', async () => {
    // The whole reason the stored row outranks the catalog: this custom name
    // must survive, and it is stored against the same official preset id the
    // tray reports.
    serve({
      0: {
        printer_id: 1,
        ams_id: 0,
        tray_id: 0,
        preset_id: 'GFSA01',
        preset_name: '# Bambu PLA Matte @BBL X1C 0.4 nozzle (Custom)',
        preset_source: 'cloud',
      },
    });

    render(<PrintersPage />);
    await hoverFirstSlot();

    await waitFor(() => {
      expect(screen.getByText('# Bambu PLA Matte @BBL X1C 0.4 nozzle (Custom)')).toBeInTheDocument();
    });
  });
});
