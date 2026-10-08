/**
 * The Printers page reads every card's data with one request per kind of data.
 *
 * Each card used to ask for its own status, slot presets, AMS labels, plugs,
 * sensor readings and queue, so a farm's Printers page sent hundreds of
 * requests on load and the server worked through them one behind the other.
 * The cards still each run their own query; the API client batches them.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';

const printer = (id: number) => ({
  id,
  name: `Printer ${id}`,
  ip_address: `192.168.1.${100 + id}`,
  serial_number: `01P00A00000000${id}`,
  access_code: '12345678',
  model: 'X1C',
  enabled: true,
  is_active: true,
  nozzle_diameter: 0.4,
  nozzle_type: 'stainless_steel',
  location: null,
  auto_archive: true,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
});

const status = (id: number) => ({
  id,
  name: `Printer ${id}`,
  connected: true,
  state: 'IDLE',
  progress: 0,
  layer_num: 0,
  total_layers: 0,
  temperatures: { nozzle: 25, bed: 25, chamber: 25 },
  remaining_time: 0,
  wifi_signal: -40,
  speed_level: 2,
  vt_tray: [],
  ams: [],
});

describe('PrintersPage — batched per-printer reads', () => {
  let paths: string[];

  beforeEach(() => {
    paths = [];
    server.events.removeAllListeners('request:start');
    server.events.on('request:start', ({ request }) => {
      // The default bulk mocks answer by asking the single routes; not the app's
      if (request.headers.get('x-test-fan-out')) return;
      const url = new URL(request.url);
      paths.push(url.pathname + url.search);
    });
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([printer(1), printer(2), printer(3)])),
      http.get('/api/v1/bulk/printer-statuses', () => HttpResponse.json([status(1), status(2), status(3)])),
    );
  });

  it('asks for every printer\'s status in one request', async () => {
    render(<PrintersPage />);

    await waitFor(() =>
      expect(paths.filter((p) => p.startsWith('/api/v1/bulk/printer-statuses'))).toHaveLength(1),
    );
    expect(paths).toContain('/api/v1/bulk/printer-statuses?ids=1,2,3');
    expect(paths.some((p) => /^\/api\/v1\/printers\/\d+\/status/.test(p))).toBe(false);
  });

  it('batches the other per-card reads as well', async () => {
    render(<PrintersPage />);

    await waitFor(() => {
      for (const route of ['slot-presets', 'ams-labels', 'card-plugs', 'ha-sensor-readings', 'printer-queues']) {
        expect(paths.some((p) => p.startsWith(`/api/v1/bulk/${route}?ids=1,2,3`))).toBe(true);
      }
    });
    const perPrinter = paths.filter((p) =>
      /^\/api\/v1\/(printers\/\d+\/(slot-presets|ams-labels)|smart-plugs\/by-printer\/\d+|ha-sensors\/by-printer\/\d+)/.test(p),
    );
    expect(perPrinter).toEqual([]);
  });
});
