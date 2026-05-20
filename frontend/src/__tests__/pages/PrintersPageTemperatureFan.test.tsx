/**
 * Tests for live temperature and fan controls on the PrintersPage.
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
    nozzle_diameter: 0.4,
    nozzle_type: 'hardened_steel',
    location: 'Workshop',
    auto_archive: true,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  },
];

const mockConnectedStatus = {
  connected: true,
  state: 'IDLE',
  progress: 0,
  layer_num: 0,
  total_layers: 0,
  temperatures: {
    nozzle: 25,
    bed: 25,
    chamber: 25,
    bed_target: 0,
    nozzle_target: 0,
    chamber_target: 0,
  },
  remaining_time: 0,
  filename: null,
  wifi_signal: -50,
  vt_tray: [],
  speed_level: 2,
  cooling_fan_speed: 0,
  big_fan1_speed: 0,
  big_fan2_speed: 0,
};

describe('PrintersPage - Temperature and Fan Controls', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json(mockPrinters)),
      http.get('/api/v1/queue/', () => HttpResponse.json([])),
      http.get('/api/v1/printers/1/control-limits', () =>
        HttpResponse.json({
          success: true,
          bed_min: 0,
          bed_max: 120,
          nozzle_min: 0,
          nozzle_max: 300,
          chamber_min: 0,
          chamber_max: 65,
          fans: [1, 2, 3],
          dual_nozzle: false,
        })
      )
    );
  });

  it('opens bed temperature popover and calls API on apply', async () => {
    const user = userEvent.setup();
    let bedTarget: number | null = null;

    server.use(
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json(mockConnectedStatus)),
      http.post('/api/v1/printers/:id/bed-temperature', ({ request }) => {
        const url = new URL(request.url);
        bedTarget = Number(url.searchParams.get('target'));
        return HttpResponse.json({ success: true, message: 'Bed temperature set to 60°C' });
      })
    );

    render(<PrintersPage />);

    await waitFor(() => {
      expect(screen.getAllByTitle('Set target temperature').length).toBeGreaterThan(0);
    });

    const bedButtons = screen.getAllByTitle('Set target temperature');
    const bedButton = bedButtons.find((el) => el.textContent?.includes('Bed')) ?? bedButtons[bedButtons.length - 1];
    await user.click(bedButton);

    await waitFor(() => {
      expect(screen.getByText('Apply')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Apply'));

    await waitFor(() => {
      expect(bedTarget).not.toBeNull();
    });
  });

  it('opens fan speed popover and calls API on apply', async () => {
    const user = userEvent.setup();
    let fanSpeed: number | null = null;

    server.use(
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json(mockConnectedStatus)),
      http.post('/api/v1/printers/:id/fan-speed', ({ request }) => {
        const url = new URL(request.url);
        fanSpeed = Number(url.searchParams.get('speed_percent'));
        return HttpResponse.json({ success: true, message: 'Part cooling fan set to 50%' });
      })
    );

    render(<PrintersPage />);

    await waitFor(() => {
      expect(screen.getAllByTitle('Set fan speed').length).toBeGreaterThan(0);
    });

    const fanButtons = screen.getAllByTitle('Set fan speed');
    await user.click(fanButtons[0]);

    await waitFor(() => {
      expect(screen.getByText('Speed')).toBeInTheDocument();
    });

    await user.click(screen.getAllByText('Apply')[0]);

    await waitFor(() => {
      expect(fanSpeed).not.toBeNull();
    });
  });
});
