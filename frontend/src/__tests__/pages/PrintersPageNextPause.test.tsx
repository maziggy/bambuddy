/**
 * The next pause the slicer put into the print, on the printer card (#2984).
 */

import { describe, it, expect } from 'vitest';
import { screen, within } from '@testing-library/react';
import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const printers = [
  {
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
    is_active: true,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  },
];

const printing = {
  connected: true,
  temperatures: { nozzle: 220, bed: 60, chamber: 35 },
  filename: 'wifi_qr.3mf',
  current_print: 'wifi_qr',
  wifi_signal: -50,
  vt_tray: [],
  speed_level: 2,
  hms_errors: [],
  state: 'RUNNING',
  progress: 30,
  layer_num: 9,
  total_layers: 23,
  remaining_time: 188,
};

function useStatus(status: Record<string, unknown>) {
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json(printers)),
    http.get('/api/v1/queue/', () => HttpResponse.json([])),
    http.get('/api/v1/printers/:id/status', () => HttpResponse.json(status)),
  );
}

describe('PrintersPage - next slicer pause (#2984)', () => {
  it('shows the time to the next pause and when it comes', async () => {
    useStatus({ ...printing, next_pause: { layer: 12, minutes: 20 } });

    render(<PrintersPage />);

    const pause = await screen.findByTestId('next-pause');
    expect(pause).toHaveTextContent(/^20m to pause · \S/);
    expect(pause).toHaveAttribute('title', 'Next pause added in the slicer, at layer 12');
  });

  it('a pause under a minute away reads as a minute, not zero', async () => {
    useStatus({ ...printing, next_pause: { layer: 12, minutes: 0 } });

    render(<PrintersPage />);

    expect(await screen.findByTestId('next-pause')).toHaveTextContent(/^1m to pause/);
  });

  it('shows the layer while the printer reports no time left yet', async () => {
    useStatus({ ...printing, remaining_time: 0, next_pause: { layer: 12, minutes: null } });

    render(<PrintersPage />);

    expect(await screen.findByTestId('next-pause')).toHaveTextContent('Pause at layer 12');
  });

  it('shows nothing for a print without pauses', async () => {
    useStatus({ ...printing, next_pause: null });

    render(<PrintersPage />);

    const layers = await screen.findByText('9/23');
    expect(within(layers.parentElement!.parentElement!).queryByTestId('next-pause')).not.toBeInTheDocument();
    expect(screen.queryByTestId('next-pause')).not.toBeInTheDocument();
  });
});
