/**
 * Groups can be given a location (#1727), so editing a printer's location is
 * an access change: the dialog names the groups it affects, and clearing the
 * location has to actually clear it.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { PrintersPage } from '../../pages/PrintersPage';
import { setAuthToken } from '../../api/client';

const mockPrinter = {
  id: 1,
  name: 'X1 Carbon',
  ip_address: '192.168.1.100',
  serial_number: '00M09A350100001',
  model: 'X1C',
  location: 'Lab A',
  is_active: true,
  auto_archive: true,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const teamGroup = (name: string, locations: string[], restrict = true) => ({
  id: name.length,
  name,
  description: null,
  permissions: [],
  is_system: false,
  restrict_printers: restrict,
  printer_ids: [],
  locations,
  user_count: 1,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
});

let patched: Record<string, unknown> | null;

beforeEach(() => {
  patched = null;
  setAuthToken('test-token', 'session');
  server.use(
    http.get('*/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: true, requires_setup: false })),
    http.get('*/api/v1/auth/me', () =>
      HttpResponse.json({ id: 1, username: 'boss', is_admin: true, groups: [], permissions: ['printers:update', 'printers:read'] })
    ),
    http.get('/api/v1/printers/', () => HttpResponse.json([mockPrinter])),
    http.get('/api/v1/printers/:id/status', () =>
      HttpResponse.json({
        connected: true,
        state: 'IDLE',
        progress: 0,
        layer_num: 0,
        total_layers: 0,
        temperatures: { nozzle: 25, bed: 25, chamber: 25 },
        remaining_time: 0,
        filename: null,
        wifi_signal: -50,
        vt_tray: [],
      })
    ),
    http.get('/api/v1/queue/', () => HttpResponse.json([])),
    http.get('/api/v1/groups/', () =>
      HttpResponse.json([
        teamGroup('Lab A team', ['Lab A']),
        teamGroup('Lab B crew', ['Lab B']),
        teamGroup('Unlimited', ['Lab A'], false),
      ])
    ),
    http.post('/api/v1/printers/diagnostic', () =>
      HttpResponse.json({ printer_id: null, ip_address: '192.168.1.100', overall: 'ok', checks: [] })
    ),
    http.patch('/api/v1/printers/:id', async ({ request }) => {
      patched = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json({ ...mockPrinter, ...patched });
    })
  );
});

afterEach(() => {
  setAuthToken(null);
});

async function openEditModal() {
  render(<PrintersPage />);
  await waitFor(() => expect(screen.getByText('X1 Carbon')).toBeInTheDocument());
  const menuBtn = [...document.querySelectorAll('button')].find((b) => b.querySelector('.lucide-ellipsis-vertical'))!;
  await userEvent.click(menuBtn);
  await userEvent.click(await screen.findByRole('button', { name: /^edit$/i }));
  await screen.findByText('Edit Printer');
}

describe('EditPrinterModal location access', () => {
  it('names the limited groups a move affects', async () => {
    await openEditModal();
    const input = screen.getByDisplayValue('Lab A');
    expect(screen.queryByText(/changes who can use it/)).not.toBeInTheDocument();

    await userEvent.clear(input);
    await userEvent.type(input, 'Lab B');

    expect(
      await screen.findByText('Moving this printer to another location changes who can use it: Lab A team, Lab B crew.')
    ).toBeInTheDocument();
  });

  it('clears the location instead of keeping the old one', async () => {
    await openEditModal();
    await userEvent.clear(screen.getByDisplayValue('Lab A'));
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(patched).not.toBeNull());
    expect(patched).toHaveProperty('location', null);
  });

  it('offers "Who has access" in the card menu', async () => {
    render(<PrintersPage />);
    await waitFor(() => expect(screen.getByText('X1 Carbon')).toBeInTheDocument());
    const menuBtn = [...document.querySelectorAll('button')].find((b) => b.querySelector('.lucide-ellipsis-vertical'))!;
    await userEvent.click(menuBtn);
    await userEvent.click(await screen.findByRole('button', { name: 'Who has access' }));

    await waitFor(() => expect(window.location.search).toBe('?tab=users&sub=printer-access&view=printers&printer=1'));
  });
});
