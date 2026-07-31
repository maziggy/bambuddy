/**
 * Tests for the AI failure detection badge on printer cards (#1546).
 *
 * The badge reflects /obico/printer-status: always shown for printers in the
 * monitored set while detection is enabled — gray "Idle" outside a monitored
 * print, class-colored (safe/warning/failure) during one.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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
    is_active: true,
    nozzle_diameter: 0.4,
    nozzle_type: 'hardened_steel',
    location: 'Workshop',
    auto_archive: true,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  },
  {
    id: 2,
    name: 'P1S Backup',
    ip_address: '192.168.1.101',
    serial_number: '00W00A123456789',
    access_code: '87654321',
    model: 'P1S',
    enabled: true,
    is_active: true,
    nozzle_diameter: 0.4,
    nozzle_type: 'stainless_steel',
    location: null,
    auto_archive: true,
    created_at: '2024-01-02T00:00:00Z',
    updated_at: '2024-01-02T00:00:00Z',
  },
];

const mockPrinterStatus = {
  connected: true,
  state: 'RUNNING',
  awaiting_plate_clear: false,
  progress: 42,
  layer_num: 10,
  total_layers: 100,
  temperatures: { nozzle: 220, bed: 55, chamber: 30 },
  remaining_time: 3600,
  filename: 'benchy.3mf',
  wifi_signal: -50,
  vt_tray: [],
};

describe('PrintersPage AI detection badge (#1546)', () => {
  beforeEach(() => {
    localStorage.removeItem('printerCardSize');

    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json(mockPrinters)),
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json(mockPrinterStatus)),
      http.get('/api/v1/settings/ui-preferences', () =>
        HttpResponse.json({
          ams_humidity_good: 40,
          ams_humidity_fair: 60,
          ams_temp_good: 30,
          ams_temp_fair: 35,
        })
      ),
      http.get('/api/v1/queue/', () => HttpResponse.json([]))
    );
  });

  it('shows the live class for a monitored print and Idle for other monitored printers', async () => {
    server.use(
      http.get('/api/v1/obico/printer-status', () =>
        HttpResponse.json({
          enabled: true,
          monitored_printers: null,
          per_printer: { '1': { class: 'warning', frame_count: 12, score: 0.31 } },
          last_error: null,
        })
      )
    );

    render(<PrintersPage />);

    const badge = await screen.findByText('Warning');
    expect(badge.closest('button')).toHaveAttribute(
      'title',
      'AI Failure Detection: Warning (score 0.310) - click for details'
    );
    // Printer 2 is monitored (null = all) but has no active print — gray Idle badge
    const idleBadge = await screen.findByText('Idle');
    expect(idleBadge.closest('button')).toHaveAttribute(
      'title',
      'AI Failure Detection enabled - monitoring starts with the next print - click for details'
    );
    expect(screen.getAllByText('Warning')).toHaveLength(1);
  });

  it('shows no badge for printers outside the monitored subset', async () => {
    server.use(
      http.get('/api/v1/obico/printer-status', () =>
        HttpResponse.json({
          enabled: true,
          monitored_printers: [2],
          per_printer: {},
          last_error: null,
        })
      )
    );

    render(<PrintersPage />);

    // Printer 2 gets the Idle badge; printer 1 (not monitored) gets none
    expect(await screen.findAllByText('Idle')).toHaveLength(1);
  });

  it('clicking the badge opens a modal with live status and the last error', async () => {
    server.use(
      http.get('/api/v1/obico/printer-status', () =>
        HttpResponse.json({
          enabled: true,
          monitored_printers: null,
          per_printer: { '1': { class: 'failure', frame_count: 30, score: 0.92 } },
          last_error: 'ML API call failed for printer 1: connection refused',
        })
      )
    );

    render(<PrintersPage />);
    const user = userEvent.setup();

    await user.click(await screen.findByText('Failure'));

    expect(await screen.findByText('AI Failure Detection - X1 Carbon')).toBeInTheDocument();
    expect(screen.getByText('0.920')).toBeInTheDocument();
    expect(screen.getByText('30')).toBeInTheDocument();
    expect(screen.getByText('Last error')).toBeInTheDocument();
    expect(screen.getByText('ML API call failed for printer 1: connection refused')).toBeInTheDocument();
  });

  it('shows no badge when detection is disabled, even with stale per_printer state', async () => {
    server.use(
      http.get('/api/v1/obico/printer-status', () =>
        HttpResponse.json({
          enabled: false,
          monitored_printers: null,
          per_printer: { '1': { class: 'failure', frame_count: 30, score: 0.92 } },
          last_error: null,
        })
      )
    );

    render(<PrintersPage />);

    await waitFor(() => {
      expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
    });
    expect(screen.queryByText('Failure')).not.toBeInTheDocument();
    expect(screen.queryByText('Idle')).not.toBeInTheDocument();
  });
});
