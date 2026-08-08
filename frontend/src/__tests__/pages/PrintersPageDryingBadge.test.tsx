/**
 * The active-cycle badge on the AMS card (#2759).
 *
 * Bambu never echoes back which filament or temperature a drying cycle is
 * running, so the backend hands us two independent fields: `dry_filament`,
 * which it can also infer from a uniformly loaded unit, and `dry_target_temp`,
 * which it only knows from the target it cached when sending the command. The
 * temperature can therefore go missing while the filament survives, and the
 * badge has to render that pairing rather than dropping both.
 *
 * The reporter's second AMS held only PLA and was drying at the 45°C they
 * picked; with no cached target the badge previously showed the RFID
 * recommendation and read "PLA @ 55°C".
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const mockPrinter = {
  id: 1,
  name: 'X1C',
  ip_address: '192.168.1.100',
  serial_number: '01P00A000000001',
  access_code: '12345678',
  model: 'X1C',
  enabled: true,
  nozzle_diameter: 0.4,
  nozzle_type: 'stainless_steel',
  location: 'Workshop',
  auto_archive: true,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const baseTray = {
  tray_color: 'FF0000FF',
  tray_type: 'PLA',
  tray_sub_brands: 'PLA Basic',
  tray_id_name: 'A00-R0',
  tray_info_idx: 'GFA00',
  remain: 80,
  k: 0.02,
  cali_idx: null,
  tag_uid: null,
  tray_uuid: null,
  nozzle_temp_min: 190,
  nozzle_temp_max: 230,
  drying_temp: 55,
  drying_time: 8,
  state: 3,
};

/** An AMS 2 Pro twelve hours into a cycle, with the badge fields under test. */
function makeStatus(target: { dry_filament: string | null; dry_target_temp: number | null }) {
  return {
    connected: true,
    state: 'IDLE',
    progress: 0,
    layer_num: 0,
    total_layers: 0,
    temperatures: { nozzle: 25, bed: 25, chamber: 25 },
    remaining_time: 0,
    filename: null,
    wifi_signal: -29,
    speed_level: 2,
    supports_drying: true,
    drying_screen_only: false,
    vt_tray: [],
    ams: [
      {
        id: 0,
        humidity: 30,
        temp: 33,
        is_ams_ht: false,
        serial_number: 'AMS00',
        sw_ver: '03.00.21.29',
        dry_time: 719,
        dry_status: 2,
        dry_sub_status: 0,
        dry_sf_reason: [],
        module_type: 'n3f',
        ...target,
        tray: [0, 1, 2, 3].map((id) => ({ id, ...baseTray })),
      },
    ],
  };
}

function renderWith(target: { dry_filament: string | null; dry_target_temp: number | null }) {
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json([mockPrinter])),
    http.get('/api/v1/printers/:id/status', () => HttpResponse.json(makeStatus(target))),
    http.get('/api/v1/queue/', () => HttpResponse.json([])),
  );
  render(<PrintersPage />);
}

describe('PrintersPage — AMS drying badge (#2759)', () => {
  beforeEach(() => {
    server.use(http.get('/api/v1/queue/', () => HttpResponse.json([])));
  });

  it('names the filament and the temperature when the cycle target is known', async () => {
    renderWith({ dry_filament: 'PLA', dry_target_temp: 45 });

    await waitFor(() => {
      expect(screen.getAllByText('PLA @ 45°C').length).toBeGreaterThan(0);
    });
  });

  it('still names the filament when only the temperature is unknown', async () => {
    renderWith({ dry_filament: 'PLA', dry_target_temp: null });

    // The filament survives on its own — dropping it too would leave the badge
    // showing a bare countdown for a cycle we can still describe.
    await waitFor(() => {
      expect(screen.getAllByText('PLA').length).toBeGreaterThan(0);
    });
    // And it must not fall back to the trays' RFID recommendation (55°C here),
    // which is what the user's chosen 45°C was being overwritten with. Scoped
    // to the badge's own "<filament> @ <temp>°C" shape — the card carries
    // unrelated nozzle and bed readings in °C.
    expect(screen.queryByText(/@ \d+°C/)).toBeNull();
  });

  it('shows the countdown alone when the unit gives no evidence at all', async () => {
    renderWith({ dry_filament: null, dry_target_temp: null });

    await waitFor(() => {
      expect(screen.getAllByText(/11h 59m/).length).toBeGreaterThan(0);
    });
    expect(screen.queryByText(/@ \d+°C/)).toBeNull();
  });
});
