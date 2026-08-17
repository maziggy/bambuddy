/**
 * The AMS card side badge on a Filament Track Switch machine.
 *
 * Without a switch, each AMS is wired to one nozzle and the card badges it L or
 * R. With a switch fitted, the AMS is bound to one of the switch's two *inlets*
 * instead and reaches BOTH nozzles through it — so every unit reports extruder
 * 0xE and `ams_extruder_map` comes back empty.
 *
 * The card used to fall through to the AMS unit id in that case, which quietly
 * labelled AMS 0 "R" and AMS 1 "L" from nothing but their unit numbers, gave a
 * third unit no badge at all, and was wrong for every one of them. It now shows
 * the inlet the printer's own "Manual AMS Setup" screen assigned — lettered L
 * for In-A and R for In-B, with the tooltip naming the inlet outright so the
 * letter is not mistaken for a claim about which nozzle the AMS feeds.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const mockPrinter = {
  id: 1,
  name: 'H2C',
  ip_address: '192.168.1.100',
  serial_number: '31B8BP610600650',
  access_code: '12345678',
  model: 'H2C',
  enabled: true,
  nozzle_count: 2,
  nozzle_diameter: 0.4,
  nozzle_type: 'hardened_steel',
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

function amsUnit(id: number) {
  return {
    id,
    humidity: 30,
    temp: 33,
    is_ams_ht: false,
    serial_number: `AMS0${id}`,
    sw_ver: '03.00.21.29',
    module_type: 'n3f',
    tray: [0, 1, 2, 3].map((t) => ({ id: t, ...baseTray })),
  };
}

/** Three AMS units on a dual-nozzle printer, with the FTS fields under test. */
function makeStatus(over: Record<string, unknown>) {
  return {
    connected: true,
    state: 'IDLE',
    progress: 0,
    layer_num: 0,
    total_layers: 0,
    // Two nozzle readings are what marks the card as dual-nozzle, which is the
    // precondition for any L/R badge appearing at all.
    temperatures: { nozzle: 25, nozzle_2: 25, bed: 25, chamber: 25 },
    remaining_time: 0,
    filename: null,
    wifi_signal: -29,
    speed_level: 2,
    vt_tray: [],
    ams: [amsUnit(0), amsUnit(1), amsUnit(2)],
    ams_extruder_map: {},
    fila_switch: null,
    ams_switch_inlet: {},
    ...over,
  };
}

function renderWith(over: Record<string, unknown>) {
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json([mockPrinter])),
    http.get('/api/v1/printers/:id/status', () => HttpResponse.json(makeStatus(over))),
    http.get('/api/v1/queue/', () => HttpResponse.json([])),
  );
  render(<PrintersPage />);
}

const FTS_INSTALLED = { installed: true, in_slots: [-1, -1], out_extruders: [1, 0], stat: 0, info: 0 };

describe('PrintersPage — AMS card side badge with a Filament Track Switch', () => {
  beforeEach(() => {
    server.use(http.get('/api/v1/queue/', () => HttpResponse.json([])));
  });

  it('badges each AMS with the switch inlet it is plumbed into', async () => {
    renderWith({
      fila_switch: FTS_INSTALLED,
      ams_switch_inlet: { '0': 'A', '1': 'B', '2': 'B' },
    });

    await waitFor(() => {
      expect(screen.getAllByTitle(/Filament Track Switch IN-A/).length).toBe(1);
    });
    expect(screen.getAllByTitle(/Filament Track Switch IN-B/).length).toBe(2);
  });

  it('letters In-A as L and In-B as R, and names the inlet in the tooltip', async () => {
    renderWith({
      fila_switch: FTS_INSTALLED,
      ams_switch_inlet: { '0': 'A', '1': 'B', '2': 'B' },
    });

    // The letter is familiar; the tooltip carries what it actually means, since
    // an AMS behind the switch reaches both nozzles and "L" is the inlet's
    // position rather than the nozzle it feeds.
    const inA = await screen.findByTitle(/IN-A/);
    expect(inA.textContent).toBe('L');
    expect(inA.title).toMatch(/\(L\)/);
    expect(inA.title).toMatch(/both nozzles/i);
    for (const inB of screen.getAllByTitle(/IN-B/)) {
      expect(inB.textContent).toBe('R');
      expect(inB.title).toMatch(/\(R\)/);
    }
  });

  it('does not reuse the plain nozzle tooltip for an inlet badge', async () => {
    // The two badges share a letter but never a tooltip: a bare "Left" would be
    // the very claim the inlet badge exists to avoid making.
    renderWith({
      fila_switch: FTS_INSTALLED,
      ams_switch_inlet: { '0': 'A', '1': 'B', '2': 'B' },
    });

    await waitFor(() => {
      expect(screen.getAllByTitle(/IN-A/).length).toBeGreaterThan(0);
    });
    expect(screen.queryByTitle('Left')).toBeNull();
    expect(screen.queryByTitle('Right')).toBeNull();
  });

  it('shows nothing rather than guessing when the switch is not set up yet', async () => {
    // A switch fitted but not yet assigned on the printer screen reports no
    // binding. This is the case the old unit-id fallback got wrong.
    renderWith({ fila_switch: FTS_INSTALLED, ams_switch_inlet: {} });

    // Wait for the AMS cards themselves, so the absence assertions below cannot
    // pass trivially during the loading window.
    await waitFor(() => {
      expect(screen.getAllByText('AMS-C').length).toBeGreaterThan(0);
    });
    expect(screen.queryByTitle('Left')).toBeNull();
    expect(screen.queryByTitle('Right')).toBeNull();
    expect(screen.queryByTitle(/IN-[AB]/)).toBeNull();
  });

  it('still shows L/R on a dual-nozzle printer without a switch', async () => {
    // Regression guard: the inlet work must not take the ordinary H2D badge
    // down with it.
    renderWith({ fila_switch: null, ams_extruder_map: { '0': 1, '1': 0, '2': 0 } });

    await waitFor(() => {
      expect(screen.getAllByTitle('Left').length).toBe(1);
    });
    expect(screen.getAllByTitle('Right').length).toBe(2);
    expect(screen.queryByTitle(/IN-[AB]/)).toBeNull();
  });

  it('prefers a real extruder id over the unit-id guess even with a switch fitted', async () => {
    // An AMS reporting a genuine extruder id is bound to that nozzle directly,
    // switch or no switch — BambuStudio treats a non-0xE id as authoritative.
    renderWith({
      fila_switch: FTS_INSTALLED,
      ams_extruder_map: { '2': 1 },
      ams_switch_inlet: { '0': 'A', '1': 'B' },
    });

    await waitFor(() => {
      expect(screen.getAllByTitle(/IN-A/).length).toBe(1);
    });
    expect(screen.getAllByTitle('Left').length).toBe(1);
    expect(screen.queryByTitle('Right')).toBeNull();
  });
});
