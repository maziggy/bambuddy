/**
 * Tests for the metrics line on the size-S (compact) printer card (#2674).
 *
 * Size S used to show a name, a connection pip and a progress bar — not enough
 * to answer "which printer finishes first", which is the whole point of a
 * wall-mounted fleet view. It now carries remaining time, ETA and layer
 * progress, reusing the formatters the expanded card already uses.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const mockPrinters = [
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

const baseStatus = {
  connected: true,
  temperatures: { nozzle: 220, bed: 60, chamber: 35 },
  filename: 'test_print.3mf',
  wifi_signal: -50,
  vt_tray: [],
  speed_level: 2,
  hms_errors: [],
};

// 83 minutes remaining → formatDuration(83 * 60) === "1h 23m"
const printingStatus = {
  ...baseStatus,
  state: 'RUNNING',
  progress: 42,
  layer_num: 120,
  total_layers: 267,
  remaining_time: 83,
};

const idleStatus = {
  ...baseStatus,
  state: 'IDLE',
  progress: 0,
  layer_num: 0,
  total_layers: 0,
  remaining_time: 0,
  temperatures: { nozzle: 25, bed: 25, chamber: 25 },
};

function useStatus(status: Record<string, unknown>) {
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json(mockPrinters)),
    http.get('/api/v1/queue/', () => HttpResponse.json([])),
    http.get('/api/v1/printers/:id/status', () => HttpResponse.json(status)),
  );
}

describe('PrintersPage — size S metrics line (#2674)', () => {
  beforeEach(() => {
    // Size S. The card size is read from localStorage on first render — and
    // the test harness replaces localStorage with bare vi.fn() stubs
    // (setup.ts), so setItem is a no-op and getItem has to be taught the
    // value. Without this the page falls back to its size-M default and the
    // compact branch never renders.
    vi.mocked(localStorage.getItem).mockImplementation((key: string) =>
      key === 'printerCardSize' ? '1' : null,
    );
  });

  afterEach(() => {
    vi.mocked(localStorage.getItem).mockReset();
  });

  it('shows remaining time, ETA and layer progress while printing', async () => {
    useStatus(printingStatus);

    render(<PrintersPage />);

    expect(await screen.findByText('1h 23m')).toBeInTheDocument();
    expect(screen.getByText('120/267')).toBeInTheDocument();
    // ETA is rendered as "ETA <clock time>"; the clock time itself depends on
    // when the test runs, so match the prefix.
    expect(screen.getByText(/^ETA\s/)).toBeInTheDocument();
  });

  it('shows no metrics when the printer is idle', async () => {
    useStatus(idleStatus);

    render(<PrintersPage />);

    // The progress placeholder confirms we are looking at the compact card.
    expect(await screen.findByText('---%')).toBeInTheDocument();
    expect(screen.queryByText(/^ETA\s/)).not.toBeInTheDocument();
    expect(screen.queryByText('0/0')).not.toBeInTheDocument();
  });

  it('omits the ETA but keeps layers when the printer reports no remaining time', async () => {
    useStatus({ ...printingStatus, remaining_time: 0 });

    render(<PrintersPage />);

    expect(await screen.findByText('120/267')).toBeInTheDocument();
    expect(screen.queryByText(/^ETA\s/)).not.toBeInTheDocument();
  });

  it('omits layers when the printer reports no total layer count', async () => {
    useStatus({ ...printingStatus, layer_num: 0, total_layers: 0 });

    render(<PrintersPage />);

    expect(await screen.findByText('1h 23m')).toBeInTheDocument();
    expect(screen.queryByText('0/0')).not.toBeInTheDocument();
  });
});
