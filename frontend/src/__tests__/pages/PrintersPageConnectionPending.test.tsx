/**
 * The printer card's connection badge before the first status arrives (#3570):
 * neither Connected nor Offline, and no Run diagnostic until the printer is
 * known to be offline.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { delay, http, HttpResponse } from 'msw';
import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';
import { server } from '../mocks/server';

const mockPrinter = {
  id: 1,
  name: 'Workshop X1C',
  ip_address: '192.168.1.10',
  serial_number: '00M00A000000001',
  access_code: '12345678',
  model: 'X1C',
  enabled: true,
  is_active: true,
  nozzle_diameter: 0.4,
  nozzle_type: 'hardened_steel',
  location: 'Workshop',
  auto_archive: true,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const offlineStatus = {
  connected: false,
  state: null,
  progress: 0,
  layer_num: 0,
  total_layers: 0,
  temperatures: {},
  remaining_time: 0,
  filename: null,
  ams: [],
  vt_tray: [],
};

async function badge() {
  return screen.findByTestId('printer-connection-badge');
}

describe('PrintersPage - connection badge before the first status (#3570)', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([mockPrinter])),
      http.get('/api/v1/queue/', () => HttpResponse.json([])),
    );
  });

  it('shows a neutral pending badge and no diagnostic while the status loads', async () => {
    server.use(
      http.get('/api/v1/printers/:id/status', async () => {
        await delay('infinite');
        return HttpResponse.json(offlineStatus);
      }),
    );

    render(<PrintersPage />);

    expect(await badge()).toHaveAttribute('data-connection', 'pending');
    expect(screen.getByTestId('printer-connection-badge')).toHaveTextContent('Checking…');
    expect(screen.queryByText('Offline')).not.toBeInTheDocument();
    expect(screen.queryByTitle('Run diagnostic')).not.toBeInTheDocument();
  });

  it('says the status is unavailable when the request fails, without a diagnostic', async () => {
    server.use(http.get('/api/v1/printers/:id/status', () => HttpResponse.json({ detail: 'boom' }, { status: 500 })));

    render(<PrintersPage />);

    await waitFor(
      async () => {
        expect(await badge()).toHaveAttribute('data-connection', 'unavailable');
      },
      { timeout: 10000 },
    );
    expect(screen.queryByTitle('Run diagnostic')).not.toBeInTheDocument();
  });

  it('shows Offline and offers the diagnostic once the printer reports offline', async () => {
    server.use(http.get('/api/v1/printers/:id/status', () => HttpResponse.json(offlineStatus)));

    render(<PrintersPage />);

    await waitFor(async () => {
      expect(await badge()).toHaveAttribute('data-connection', 'offline');
    });
    expect(screen.getByTestId('printer-connection-badge')).toHaveTextContent('Offline');
    expect(screen.getByTitle('Run diagnostic')).toBeInTheDocument();
  });
});
