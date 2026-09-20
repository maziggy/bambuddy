/**
 * Which way the Z arrows move the printer (#1334).
 *
 * `POST /printers/{id}/bed-jog` takes a signed nozzle-bed gap that means one
 * physical thing on every model, so the card is what decides which gap change
 * an arrow stands for. On an X1 the plate rides the Z axis and "up" walks it
 * toward the nozzle; on an A1 or A2L the plate is fixed in Z and "up" lifts
 * the toolhead off it. Same arrow, opposite sign on the wire.
 *
 * The original report was an A1 Mini owner clicking "up" and watching the
 * nozzle dive into the plate; the follow-up was an A1 owner asking the API
 * for 5 mm of clearance and getting the same dive. These tests pin both ends.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const printer = (model: string) => [
  {
    id: 1,
    name: `Test ${model}`,
    ip_address: '192.168.1.100',
    serial_number: '00M09A350100001',
    access_code: '12345678',
    model,
    enabled: true,
    nozzle_diameter: 0.4,
    nozzle_type: 'hardened_steel',
    auto_archive: true,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  },
];

const idleStatus = {
  connected: true,
  state: 'IDLE',
  progress: 0,
  layer_num: 0,
  total_layers: 0,
  temperatures: { nozzle: 25, bed: 25, chamber: 25 },
  remaining_time: 0,
  filename: null,
  wifi_signal: -50,
  vt_tray: [],
};

/** Opens the movement popover and clicks one Z arrow; returns the distance sent. */
async function clickZArrow(model: string, arrowLabel: string): Promise<number> {
  const sent: number[] = [];
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json(printer(model))),
    http.get('/api/v1/queue/', () => HttpResponse.json([])),
    http.get('/api/v1/printers/:id/status', () => HttpResponse.json(idleStatus)),
    http.post('/api/v1/printers/:id/bed-jog', ({ request }) => {
      sent.push(Number(new URL(request.url).searchParams.get('distance')));
      return HttpResponse.json({ success: true, message: 'ok' });
    })
  );

  const user = userEvent.setup();
  render(<PrintersPage />);

  await user.click(await screen.findByTitle('Jog Controls'));
  await user.click(await screen.findByLabelText(arrowLabel));
  await waitFor(() => expect(sent).toHaveLength(1));
  return sent[0];
}

describe('PrintersPage Z jog direction (#1334)', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([])),
      http.get('/api/v1/queue/', () => HttpResponse.json([]))
    );
  });

  describe('bed-on-Z printers, where the plate is what moves', () => {
    it('sends a gap decrease when the plate is asked to go up', async () => {
      expect(await clickZArrow('X1C', 'Move plate up')).toBeLessThan(0);
    });

    it('sends a gap increase when the plate is asked to go down', async () => {
      expect(await clickZArrow('X1C', 'Move plate down')).toBeGreaterThan(0);
    });
  });

  describe('bed-slingers, where the plate stays put and the toolhead moves', () => {
    it.each(['A1', 'A1 Mini', 'A2L'])(
      'opens the gap on %s when the toolhead is asked to go up',
      async model => {
        // The #1334 crash, from the UI side: this click used to send the
        // nozzle down. Nothing about "up" may ever close the gap here.
        expect(await clickZArrow(model, 'Move toolhead up')).toBeGreaterThan(0);
      }
    );

    it.each(['A1', 'A1 Mini', 'A2L'])(
      'closes the gap on %s when the toolhead is asked to go down',
      async model => {
        expect(await clickZArrow(model, 'Move toolhead down')).toBeLessThan(0);
      }
    );

    it('does not offer the plate wording on a printer whose plate cannot move in Z', async () => {
      server.use(
        http.get('/api/v1/printers/', () => HttpResponse.json(printer('A1 Mini'))),
        http.get('/api/v1/printers/:id/status', () => HttpResponse.json(idleStatus))
      );
      const user = userEvent.setup();
      render(<PrintersPage />);

      await user.click(await screen.findByTitle('Jog Controls'));
      await screen.findByLabelText('Move toolhead up');
      expect(screen.queryByLabelText('Move plate up')).toBeNull();
    });
  });
});
