/**
 * Good / Reject on the printer card (#1898).
 *
 * The card offers the verdict where the operator is standing: the plate-clear
 * gate is up, the print that just finished asked for a verdict, and answering
 * it should not cost a trip to the archive.
 *
 * What is pinned here is which button does what. Good is answered on the spot;
 * Reject wants a reason and possibly a reprint, so it opens the dialog and
 * records nothing by itself. The card's mutation therefore only ever sends
 * "good", and its type says so — a verdict it cannot send must not be able to
 * reach a success message that calls it good.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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

const status = {
  connected: true,
  state: 'FINISH',
  awaiting_plate_clear: true,
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
  require_plate_clear: true,
};

const finishedPrint = {
  id: 42,
  printer_id: 1,
  filename: 'bracket.gcode.3mf',
  print_name: 'Bracket',
  status: 'completed',
  confirm_requested: true,
  user_verdict: null,
  user_verdict_source: null,
  photos: null,
  created_by_id: 7,
};

describe('PrintersPage — verdict on the card', () => {
  let patched: Array<Record<string, unknown>>;

  beforeEach(() => {
    patched = [];
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([printer])),
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json(status)),
      http.get('/api/v1/settings/ui-preferences', () => HttpResponse.json(uiPreferences)),
      http.get('/api/v1/settings/', () => HttpResponse.json({ ...uiPreferences, auto_archive: true })),
      http.get('/api/v1/archives/', () => HttpResponse.json([finishedPrint])),
      http.patch('/api/v1/archives/:id', async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        patched.push(body);
        return HttpResponse.json({ ...finishedPrint, ...body });
      }),
    );
  });

  it('records good from the card and says so', async () => {
    const user = userEvent.setup();
    render(<PrintersPage />);

    await user.click(await screen.findByTitle('Good'));

    await waitFor(() => expect(patched).toHaveLength(1));
    expect(patched[0].user_verdict).toBe('good');
    expect(patched[0].user_verdict_source).toBe('printer_card');
    expect(await screen.findByText('Marked as good part')).toBeInTheDocument();
  });

  it('sends the reject to the dialog and records nothing on its own', async () => {
    const user = userEvent.setup();
    render(<PrintersPage />);

    await user.click(await screen.findByTitle('Reject'));

    expect(await screen.findByTestId('confirm-outcome-dialog')).toBeInTheDocument();
    expect(patched).toHaveLength(0);
    expect(screen.queryByText('Marked as good part')).toBeNull();
  });

  // The PATCH checks archives:update against the archive's owner, so the pair
  // is only offered to whoever it can succeed for. Mark cleared, which has its
  // own permission, stays either way.
  describe('with auth enabled', () => {
    // Before /auth/me has answered, every permission check says yes, so an
    // absence is only worth asserting once both it and the archive are in.
    let meServed: boolean;
    let archiveServed: boolean;
    const settled = async () => {
      await waitFor(() => expect(meServed && archiveServed).toBe(true));
      expect(await screen.findByRole('button', { name: 'Mark plate as cleared' })).toBeInTheDocument();
    };

    const signInWith = (permissions: string[], archive = finishedPrint) => {
      meServed = false;
      archiveServed = false;
      setAuthToken('card-verdict-token', 'session');
      server.use(
        http.get('*/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: true, requires_setup: false })),
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
        http.get('/api/v1/archives/', () => {
          archiveServed = true;
          return HttpResponse.json([archive]);
        }),
      );
    };

    afterEach(() => {
      setAuthToken(null);
    });

    it('offers no verdict to a user who may not update archives', async () => {
      signInWith(['printers:read', 'printers:clear_plate', 'archives:read_all']);
      render(<PrintersPage />);

      await settled();
      await waitFor(() => expect(screen.queryByTitle('Good')).toBeNull());
      expect(screen.queryByTitle('Reject')).toBeNull();
    });

    it('offers no verdict on somebody else\'s print to a user who may update only their own', async () => {
      signInWith(
        ['printers:read', 'printers:clear_plate', 'archives:read_all', 'archives:update_own'],
        { ...finishedPrint, created_by_id: 8 },
      );
      render(<PrintersPage />);

      await settled();
      await waitFor(() => expect(screen.queryByTitle('Good')).toBeNull());
    });

    it('offers it to the owner', async () => {
      signInWith(['printers:read', 'printers:clear_plate', 'archives:read_all', 'archives:update_own']);
      render(<PrintersPage />);

      expect(await screen.findByTitle('Good')).toBeInTheDocument();
      expect(screen.getByTitle('Reject')).toBeInTheDocument();
    });
  });
});
