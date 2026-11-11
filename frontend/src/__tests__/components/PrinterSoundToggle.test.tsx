/**
 * Printer prompt sounds toggle on the printer card (#3483): offered only when the
 * printer reports the option, shows the state, and sends the opposite on click.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const printer = {
  id: 1,
  name: 'X1 Carbon',
  ip_address: '192.168.1.100',
  serial_number: '00M09A350100001',
  access_code: '12345678',
  model: 'X1C',
  enabled: true,
  nozzle_diameter: 0.4,
  nozzle_type: 'hardened_steel',
  location: null,
  auto_archive: true,
  is_active: true,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const baseStatus = {
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

let sent: string | null = null;

function serve(status: Record<string, unknown>) {
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json([printer])),
    http.get('/api/v1/printers/:id/status', () => HttpResponse.json({ ...baseStatus, ...status })),
    http.get('/api/v1/queue/', () => HttpResponse.json([])),
    http.post('/api/v1/printers/:id/sound', ({ request }) => {
      sent = new URL(request.url).searchParams.get('enabled');
      return HttpResponse.json({ success: true, sound_enabled: sent === 'true' });
    }),
  );
}

describe('Printer sounds toggle', () => {
  beforeEach(() => {
    sent = null;
  });

  it('is not offered when the printer does not report the option', async () => {
    serve({ sound_enabled: null });
    render(<PrintersPage />);
    await waitFor(() => expect(screen.getByText('X1 Carbon')).toBeInTheDocument());
    await screen.findByTitle(/chamber light/i);

    expect(screen.queryByTitle(/printer sounds/i)).not.toBeInTheDocument();
  });

  it('turns the sounds on when they are off', async () => {
    serve({ sound_enabled: false });
    render(<PrintersPage />);

    const button = await screen.findByTitle('Turn on printer sounds');
    expect(button).toHaveAttribute('aria-pressed', 'false');
    await userEvent.click(button);

    await waitFor(() => expect(sent).toBe('true'));
  });

  it('turns the sounds off when they are on', async () => {
    serve({ sound_enabled: true });
    render(<PrintersPage />);

    const button = await screen.findByTitle('Turn off printer sounds');
    expect(button).toHaveAttribute('aria-pressed', 'true');
    await userEvent.click(button);

    await waitFor(() => expect(sent).toBe('false'));
  });
});
