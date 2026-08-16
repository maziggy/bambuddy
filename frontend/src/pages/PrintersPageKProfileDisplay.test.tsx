/**
 * Tests for #2532: K-profile value shown directly on the AMS slot card,
 * not only inside the hover popup.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
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
];

const mockPrinterStatus = {
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

describe('PrintersPage - K-profile always-visible display (#2532)', () => {
  beforeEach(() => {
    localStorage.removeItem('printerCardSize');

    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json(mockPrinters)),
      http.get('/api/v1/settings/', () => HttpResponse.json({
        auto_archive: true,
        save_thumbnails: true,
        capture_finish_photo: true,
        default_filament_cost: 25.0,
        currency: 'USD',
        ams_humidity_good: 40,
        ams_humidity_fair: 60,
        ams_temp_good: 30,
        ams_temp_fair: 35,
        require_plate_clear: true,
      })),
      http.get('/api/v1/settings/ui-preferences', () => HttpResponse.json({
        ams_humidity_good: 40,
        ams_humidity_fair: 60,
        ams_temp_good: 30,
        ams_temp_fair: 35,
        require_plate_clear: true,
      })),
      http.get('/api/v1/queue/', () => HttpResponse.json([])),
      http.get('/api/v1/inventory/assignments', () => HttpResponse.json([])),
      http.get('/api/v1/spoolman/settings', () => HttpResponse.json({
        spoolman_enabled: 'false', spoolman_url: '',
      })),
    );
  });

  it('shows the K-value on a loaded slot without hovering', async () => {
    server.use(
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json({
        ...mockPrinterStatus,
        ams: [{
          id: 0,
          tray: [{
            id: 0,
            tray_type: 'PETG',
            tray_color: 'FF0000FF',
            tray_sub_brands: 'Bambu PETG HF',
            k: 0.024,
          }],
        }],
      })),
    );

    render(<PrintersPage />);

    // No hover/mouseEnter simulated here on purpose — the whole point of
    // #2532 is that this must be readable without one.
    await waitFor(() => {
      expect(screen.getByText('K 0.024')).toBeInTheDocument();
    });
  });

  it('does not show a K-value on an empty slot', async () => {
    server.use(
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json({
        ...mockPrinterStatus,
        ams: [{
          id: 0,
          tray: [{ id: 0, tray_type: null, state: 9 }],
        }],
      })),
    );

    render(<PrintersPage />);

    await waitFor(() => {
      // Slot itself renders (Empty label visible) ...
      expect(screen.getByText('Empty')).toBeInTheDocument();
    });
    // ... but formatKValue() defaults to 0.020 when there's no tray data,
    // so this specifically guards against that default leaking onto an
    // empty slot as a misleading "K 0.020".
    expect(screen.queryByText(/^K 0\.020$/)).not.toBeInTheDocument();
  });
});
