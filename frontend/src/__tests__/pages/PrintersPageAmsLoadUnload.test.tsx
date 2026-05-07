/**
 * Tests for the AMS slot load / unload buttons on PrintersPage (#891).
 *
 * Verifies that the menu in each AMS slot popover exposes Load and Unload,
 * that clicking them POSTs to the right endpoint with the right tray_id, and
 * that the buttons are hidden while the printer is RUNNING.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const mockPrinter = {
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
};

const baseTray = {
  tray_color: 'FF0000FF',
  tray_type: 'PLA',
  tray_sub_brands: 'PLA Basic',
  tray_id_name: 'A00-R0',
  tray_info_idx: 'GFA00',
  remain: 80,
  k: 0.02,
  cali_idx: null,
  tag_uid: null,
  tray_uuid: null,
  nozzle_temp_min: 190,
  nozzle_temp_max: 230,
  drying_temp: null,
  drying_time: null,
  state: 11,
};

const mockIdleStatusWithAms = {
  connected: true,
  state: 'IDLE',
  progress: 0,
  layer_num: 0,
  total_layers: 0,
  temperatures: { nozzle: 25, bed: 25, chamber: 25 },
  remaining_time: 0,
  filename: null,
  wifi_signal: -50,
  speed_level: 2,
  vt_tray: [],
  ams: [
    {
      id: 0,
      humidity: 30,
      temp: 25,
      is_ams_ht: false,
      serial_number: 'AMS00',
      sw_ver: '1.0.0',
      dry_time: 0,
      dry_status: 0,
      dry_sub_status: 0,
      dry_sf_reason: [],
      module_type: 'ams',
      tray: [
        { id: 0, ...baseTray },
        { id: 1, ...baseTray, tray_color: '00FF00FF', tray_type: 'PETG' },
        { id: 2, ...baseTray, tray_color: '0000FFFF', tray_type: 'ABS' },
        { id: 3, ...baseTray, tray_color: 'FFFF00FF', tray_type: 'TPU' },
      ],
    },
  ],
};

const mockRunningStatus = {
  ...mockIdleStatusWithAms,
  state: 'RUNNING',
};

describe('PrintersPage - AMS load/unload (#891)', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([mockPrinter])),
      http.get('/api/v1/queue/', () => HttpResponse.json([])),
    );
  });

  it('Load posts to /ams/load with tray_id derived from amsId*4 + slot', async () => {
    const user = userEvent.setup();
    let captured: { tray_id: string | null } | null = null;

    server.use(
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json(mockIdleStatusWithAms)),
      http.post('/api/v1/printers/:id/ams/load', ({ request }) => {
        const url = new URL(request.url);
        captured = { tray_id: url.searchParams.get('tray_id') };
        return HttpResponse.json({ success: true, message: 'Loading filament from AMS 0 slot 3' });
      }),
    );

    render(<PrintersPage />);

    await waitFor(() => {
      // The slot menu button is hidden until we hover. Pull it directly out of the DOM.
      expect(document.querySelectorAll('[title="Slot options"]').length).toBeGreaterThan(0);
    });

    // Slot 2 (third one, slotIdx=2) → expected tray_id = 0*4 + 2 = 2
    const menuButtons = document.querySelectorAll<HTMLButtonElement>('[title="Slot options"]');
    await user.click(menuButtons[2]);

    await waitFor(() => {
      expect(screen.getByText('Load')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Load'));

    await waitFor(() => {
      expect(captured).not.toBeNull();
      expect(captured!.tray_id).toBe('2');
    });
  });

  it('Unload posts to /ams/unload (no body, no params)', async () => {
    const user = userEvent.setup();
    let unloadCalled = false;

    server.use(
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json(mockIdleStatusWithAms)),
      http.post('/api/v1/printers/:id/ams/unload', () => {
        unloadCalled = true;
        return HttpResponse.json({ success: true, message: 'Unloading filament' });
      }),
    );

    render(<PrintersPage />);

    await waitFor(() => {
      expect(document.querySelectorAll('[title="Slot options"]').length).toBeGreaterThan(0);
    });

    const menuButtons = document.querySelectorAll<HTMLButtonElement>('[title="Slot options"]');
    await user.click(menuButtons[0]);

    await waitFor(() => {
      expect(screen.getByText('Unload')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Unload'));

    await waitFor(() => {
      expect(unloadCalled).toBe(true);
    });
  });

  it('hides the slot menu while the printer is RUNNING', async () => {
    server.use(
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json(mockRunningStatus)),
    );

    render(<PrintersPage />);

    // Wait for the page to render the printer card.
    await waitFor(() => {
      expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
    });

    // No "Slot options" menu trigger should be present at all while running.
    expect(document.querySelectorAll('[title="Slot options"]').length).toBe(0);
  });

  it('external spool slot exposes Load and posts tray_id=254', async () => {
    const user = userEvent.setup();
    let captured: string | null = null;

    server.use(
      http.get('/api/v1/printers/:id/status', () =>
        HttpResponse.json({
          ...mockIdleStatusWithAms,
          ams: [], // external-only
          vt_tray: [{ id: 254, ...baseTray, tray_type: 'PLA', tray_color: 'FFFFFFFF' }],
        }),
      ),
      http.post('/api/v1/printers/:id/ams/load', ({ request }) => {
        captured = new URL(request.url).searchParams.get('tray_id');
        return HttpResponse.json({ success: true, message: 'Loading filament from external spool' });
      }),
    );

    render(<PrintersPage />);

    await waitFor(() => {
      expect(document.querySelectorAll('[title="Slot options"]').length).toBeGreaterThan(0);
    });

    const menuButtons = document.querySelectorAll<HTMLButtonElement>('[title="Slot options"]');
    await user.click(menuButtons[0]);

    await waitFor(() => {
      expect(screen.getByText('Load')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Load'));

    await waitFor(() => {
      expect(captured).toBe('254');
    });
  });
});
