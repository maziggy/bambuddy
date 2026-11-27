/**
 * Printers page at phone width (#3663).
 *
 * The "Next available" time must never wrap mid-value ("5h" above "40m"),
 * and Pause/Stop size to their label so they stay on the icons' row where it
 * fits; when they do wrap they line up on the left under the icons, centred
 * on the row rather than top-aligned. jsdom has no layout, so the classes
 * that produce this are what is pinned here.
 */
import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';

const mockPrinter = {
  id: 7,
  name: 'Workshop P2S',
  ip_address: '192.0.2.100',
  serial_number: '00M09A350100007',
  access_code: '12345678',
  model: 'P2S',
  enabled: true,
  nozzle_diameter: 0.4,
  nozzle_type: 'hardened_steel',
  auto_archive: true,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

function setup() {
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json([mockPrinter])),
    http.get('/api/v1/queue/', () => HttpResponse.json([])),
    http.get('/api/v1/printers/:id/status', () =>
      HttpResponse.json({
        connected: true,
        state: 'RUNNING',
        progress: 47,
        layer_num: 150,
        total_layers: 494,
        temperatures: { nozzle: 220, bed: 40, chamber: 26 },
        remaining_time: 306,
        filename: 'part.gcode',
        wifi_signal: -42,
        speed_level: 2,
        vt_tray: [],
        ams: [],
      })
    )
  );
  render(<PrintersPage />);
}

const classesOf = (el: Element | null) => (el?.className ?? '').split(/\s+/);

describe('PrintersPage at phone width (#3663)', () => {
  it('keeps the next-available percentage and time on one line', async () => {
    setup();
    const time = await screen.findByText('(5h 6m)');
    const percent = time.previousElementSibling;
    const bar = percent?.previousElementSibling ?? null;

    for (const span of [time, percent]) {
      expect(classesOf(span)).toEqual(expect.arrayContaining(['shrink-0', 'whitespace-nowrap']));
    }
    // The bar takes what is left instead of the full width.
    expect(classesOf(bar)).toEqual(expect.arrayContaining(['flex-1', 'min-w-0']));
    expect(classesOf(bar)).not.toContain('w-full');
  });

  it('sizes Pause and Stop to their label and lines them up with the icons', async () => {
    setup();
    const pause = await screen.findByRole('button', { name: 'Pause' });
    const stop = await screen.findByRole('button', { name: 'Stop' });

    for (const button of [pause, stop]) {
      expect(classesOf(button)).toContain('whitespace-nowrap');
      expect(classesOf(button)).not.toContain('w-20');
    }
    const group = pause.parentElement;
    // No ml-auto: wrapped onto their own line they start on the left.
    expect(classesOf(group)).not.toContain('ml-auto');
    const row = group?.parentElement ?? null;
    expect(classesOf(row)).toEqual(expect.arrayContaining(['flex-wrap', 'items-center', 'justify-between']));
    expect(classesOf(row)).not.toContain('items-start');
  });
});
