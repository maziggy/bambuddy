/**
 * The Skip button must stay reachable when the object list is merely unknown.
 *
 * `printable_objects_count` is derived from an in-memory list that a Bambuddy
 * restart wipes, so mid-print it can drop to 0 while the print carries on. The
 * card read 0 as "nothing to skip" and disabled the button — and the button is
 * what opens the modal whose fetch rebuilds the list, so the print never got it
 * back. A running print always has at least one object, so 0 means unknown;
 * exactly 1 is the real nothing-to-skip case.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { PrintersPage } from '../../pages/PrintersPage';

const printer = {
  id: 1,
  name: 'H2C-1',
  ip_address: '192.168.1.100',
  serial_number: '00M09A350100001',
  access_code: '12345678',
  model: 'H2C',
  enabled: true,
  is_active: true,
  nozzle_diameter: 0.4,
  location: 'Workshop',
  auto_archive: true,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const printing = {
  connected: true,
  state: 'RUNNING',
  // The card treats a print as active only when it can name it, so the fixture
  // has to carry one for the Skip button to be in its printing state at all.
  current_print: 'HULA_H2D_air_pad.gcode.3mf',
  subtask_name: 'HULA_H2D_air_pad',
  gcode_file: '/data/Metadata/plate_1.gcode',
  progress: 42,
  layer_num: 30,
  total_layers: 184,
  awaiting_plate_clear: false,
  temperatures: { nozzle: 220, bed: 60, chamber: 30 },
  remaining_time: 3600,
  wifi_signal: -50,
  vt_tray: [],
};

function serveStatus(status: Record<string, unknown>) {
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json([printer])),
    http.get('/api/v1/printers/:id/status', () => HttpResponse.json(status)),
    http.get('/api/v1/printers/:id/print/objects', () =>
      HttpResponse.json({ objects: [], total: 0, skipped_count: 0, is_printing: true, bbox_all: null }),
    ),
  );
}

// The whole live-status block waits on the status query, so find rather than get.
const skipButton = () => screen.findByRole('button', { name: /skip objects/i });

describe('skip objects button gate', () => {
  beforeEach(() => {
    localStorage.removeItem('printerCardSize');
  });

  it('stays enabled while printing when the object list is not loaded', async () => {
    serveStatus({ ...printing, printable_objects_count: 0 });

    render(<PrintersPage />);

    expect(await skipButton()).toBeEnabled();
  });

  it('is disabled for a single-object print, where there is nothing to skip', async () => {
    serveStatus({ ...printing, printable_objects_count: 1 });

    render(<PrintersPage />);

    const button = await skipButton();
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('title', 'Skip objects (requires 2+ objects)');
  });

  it('is enabled for the ordinary multi-object print', async () => {
    serveStatus({ ...printing, printable_objects_count: 8 });

    render(<PrintersPage />);

    expect(await skipButton()).toBeEnabled();
  });

  it('is disabled when the printer is not printing', async () => {
    serveStatus({ ...printing, state: 'IDLE', printable_objects_count: 0 });

    render(<PrintersPage />);

    const button = await skipButton();
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('title', 'Skip objects (only while printing)');
  });
});
