/**
 * Reprint the last print straight from the printer card.
 *
 * The card already knows the printer's last archive (`last-per-printer`); the
 * Reprint button only decides whether re-sending it is possible: the machine
 * has to be free, the archive has to still carry its file (an internal-history
 * print has nothing to send), and the user needs queue:create plus the right
 * to reprint that archive. What is pinned here is exactly that gating — the
 * click itself opens the ordinary PrintModal, which the Archives page tests
 * already cover.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { PrintersPage } from '../../pages/PrintersPage';
import { setAuthToken } from '../../api/client';

const printer = {
  id: 1,
  name: 'H2S-1',
  ip_address: '192.168.20.10',
  model: 'H2S',
  serial_number: '00M09A350100001',
  access_code: '12345678',
  enabled: true,
  is_active: true,
  nozzle_diameter: 0.4,
  nozzle_type: 'hardened_steel',
  location: 'Workshop',
  auto_archive: true,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const idleStatus = {
  connected: true,
  state: 'FINISH',
  awaiting_plate_clear: false,
  progress: 100,
  layer_num: 0,
  total_layers: 0,
  temperatures: { nozzle: 25, bed: 25, chamber: 25 },
  remaining_time: 0,
  filename: 'bracket.gcode.3mf',
  wifi_signal: -50,
  vt_tray: [],
};

const uiPreferences = {
  ams_humidity_good: 40,
  ams_humidity_fair: 60,
  ams_temp_good: 30,
  ams_temp_fair: 35,
  require_plate_clear: false,
};

/** The printer's last print, still holding the 3MF a reprint needs. */
const lastPrint = {
  id: 42,
  printer_id: 1,
  filename: 'bracket.gcode.3mf',
  file_path: '/data/archives/bracket.gcode.3mf',
  print_name: 'Bracket',
  plate_id: 1,
  status: 'completed',
  confirm_requested: false,
  user_verdict: null,
  user_verdict_source: null,
  photos: null,
  created_by_id: 7,
};

const reprintButton = () => screen.queryByTitle('Reprint this print');

describe('PrintersPage — reprint from the card', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([printer])),
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json(idleStatus)),
      http.get('/api/v1/settings/ui-preferences', () => HttpResponse.json(uiPreferences)),
      http.get('/api/v1/settings/', () => HttpResponse.json({ ...uiPreferences, auto_archive: true })),
      http.get('/api/v1/archives/last-per-printer', () => HttpResponse.json([lastPrint])),
    );
  });

  it('offers Reprint when the machine is free and the print still has its file', async () => {
    render(<PrintersPage />);
    expect(await screen.findByTitle('Reprint this print')).toBeInTheDocument();
  });

  it('hides it while the printer is running', async () => {
    server.use(
      http.get('/api/v1/printers/:id/status', () =>
        HttpResponse.json({ ...idleStatus, state: 'RUNNING', progress: 40 }),
      ),
    );
    render(<PrintersPage />);

    await waitFor(() => expect(reprintButton()).toBeNull());
  });

  it('hides it when the last print has no file to re-send (internal history)', async () => {
    server.use(
      http.get('/api/v1/archives/last-per-printer', () =>
        HttpResponse.json([{ ...lastPrint, file_path: '' }]),
      ),
    );
    render(<PrintersPage />);

    await waitFor(() => expect(reprintButton()).toBeNull());
  });

  describe('with auth enabled', () => {
    let meServed: boolean;
    let archiveServed: boolean;
    const settled = async () => {
      await waitFor(() => expect(meServed && archiveServed).toBe(true));
    };

    const signInWith = (permissions: string[]) => {
      meServed = false;
      archiveServed = false;
      setAuthToken('card-reprint-token', 'session');
      server.use(
        http.get('*/api/v1/auth/status', () =>
          HttpResponse.json({ auth_enabled: true, requires_setup: false }),
        ),
        http.get('/api/v1/auth/me', () => {
          meServed = true;
          return HttpResponse.json({
            id: 7,
            username: 'operator',
            is_active: true,
            is_admin: false,
            groups: [],
            permissions,
            created_at: '2026-08-18T00:00:00Z',
          });
        }),
        http.get('/api/v1/archives/last-per-printer', () => {
          archiveServed = true;
          return HttpResponse.json([lastPrint]);
        }),
      );
    };

    afterEach(() => {
      setAuthToken(null);
    });

    it('hides it without queue:create — Reprint enqueues a job', async () => {
      signInWith(['printers:read', 'archives:read_all', 'archives:reprint_own']);
      render(<PrintersPage />);

      await settled();
      await waitFor(() => expect(reprintButton()).toBeNull());
    });

    it('hides it without the right to reprint the archive', async () => {
      signInWith(['printers:read', 'archives:read_all', 'queue:create']);
      render(<PrintersPage />);

      await settled();
      await waitFor(() => expect(reprintButton()).toBeNull());
    });

    it('offers it to someone who may both enqueue and reprint their own', async () => {
      signInWith([
        'printers:read',
        'archives:read_all',
        'queue:create',
        'archives:reprint_own',
      ]);
      render(<PrintersPage />);

      await settled();
      expect(await screen.findByTitle('Reprint this print')).toBeInTheDocument();
    });
  });
});
