/**
 * Tile size on the Cam Wall (#2735).
 *
 * The S/M/L/XL control used to grey out as soon as the page switched to the
 * Cam Wall, so a two-printer setup was stuck with small tiles side by side. It
 * now sizes the tiles there, and keeps that choice apart from the card size:
 * S on the cards also means the compact card layout.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const printer = {
  id: 1,
  name: 'A1-002',
  serial_number: '03919C111111111',
  model: 'A1',
  ip_address: '192.168.1.100',
  access_code: '12345678',
  enabled: true,
  is_active: true,
  nozzle_diameter: 0.4,
  nozzle_type: 'hardened_steel',
  auto_archive: true,
  camera_rotation: 0,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const status = {
  connected: true,
  state: 'IDLE',
  awaiting_plate_clear: false,
  progress: 0,
  layer_num: 0,
  total_layers: 0,
  temperatures: { nozzle: 25, bed: 25, chamber: 25 },
  remaining_time: 0,
  filename: null,
  wifi_signal: -50,
  vt_tray: [],
};

// The shared setup's localStorage stores nothing; this one holds what it is given.
const store = new Map<string, string>();

beforeEach(() => {
  store.clear();
  vi.mocked(localStorage.getItem).mockImplementation((key: string) => store.get(key) ?? null);
  vi.mocked(localStorage.setItem).mockImplementation((key: string, value: string) => {
    store.set(key, String(value));
  });
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json([printer])),
    http.get('/api/v1/printers/:id/status', () => HttpResponse.json(status)),
    http.get('/api/v1/settings/ui-preferences', () => HttpResponse.json({})),
    http.get('/api/v1/queue/', () => HttpResponse.json([])),
  );
});

afterEach(() => {
  store.clear();
  vi.mocked(localStorage.getItem).mockReset();
  vi.mocked(localStorage.setItem).mockReset();
});

const grid = () => screen.getByTestId('camwall-grid');

describe('Cam Wall tile size', () => {
  it('sizes the tiles on the Cam Wall', async () => {
    store.set('printerPageView', 'camwall');
    render(<PrintersPage />);
    await waitFor(() => expect(grid()).toBeInTheDocument());
    expect(grid().className).toContain('xl:grid-cols-4');

    await userEvent.setup().click(screen.getAllByTitle('Extra large tiles')[0]);

    expect(grid().className).toContain('grid-cols-1');
    expect(grid().className).not.toContain('sm:grid-cols-2');
    expect(store.get('camWallTileSize')).toBe('4');
  });

  it('leaves the card size alone', async () => {
    store.set('printerPageView', 'camwall');
    store.set('printerCardSize', '2');
    render(<PrintersPage />);
    await waitFor(() => expect(grid()).toBeInTheDocument());

    await userEvent.setup().click(screen.getAllByTitle('Small tiles')[0]);

    expect(store.get('camWallTileSize')).toBe('1');
    expect(store.get('printerCardSize')).toBe('2');
  });

  it('opens with the size picked last time', async () => {
    store.set('printerPageView', 'camwall');
    store.set('camWallTileSize', '3');
    render(<PrintersPage />);
    await waitFor(() => expect(grid()).toBeInTheDocument());

    expect(grid().className).toContain('lg:grid-cols-2');
    expect(screen.getAllByTitle('Large tiles')[0].className).toContain('bg-bambu-green');
  });

  it('still sizes the cards in Cards mode', async () => {
    render(<PrintersPage />);
    await screen.findByText('A1-002');

    expect(screen.getAllByTitle('Large cards')[0]).toBeInTheDocument();
    expect(screen.queryByTitle('Large tiles')).not.toBeInTheDocument();
  });
});
