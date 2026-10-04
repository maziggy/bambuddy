/**
 * Printer Locations page (#2962).
 *
 * Locations, with their icon and colour, come from the server: a location with
 * no printers used to live only in one browser's localStorage, invisible to
 * other users and devices. Every change is one request, so a failure cannot
 * leave a location split across printers, and English counts never render as
 * "5 printer".
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { render } from '../utils';
import i18n from '../../i18n';

const permissions = { granted: ['printers:read', 'printers:update'] as string[] };

const mockUseAuth = {
  user: { id: 1, username: 'operator', permissions: [] as string[] },
  authEnabled: true,
  requiresSetup: false,
  loading: false,
  isAdmin: false,
  login: vi.fn(),
  loginWithToken: vi.fn(),
  logout: vi.fn(),
  refreshUser: vi.fn(),
  refreshAuth: vi.fn(),
  hasPermission: vi.fn((permission: string) => permissions.granted.includes(permission)),
  hasAnyPermission: vi.fn(() => true),
  hasAllPermissions: vi.fn(() => true),
  canModify: vi.fn(() => true),
};

vi.mock('../../contexts/AuthContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/AuthContext')>();
  return { ...actual, useAuth: () => mockUseAuth };
});

import { PrinterLocationsPage } from '../../pages/PrinterLocationsPage';

const printer = (id: number, name: string, location: string | null) => ({
  id,
  name,
  location,
  model: 'X1C',
  serial_number: `S${id}`,
  ip_address: `10.0.0.${id}`,
  is_active: true,
});

const PRINTERS = [
  printer(1, 'Alpha', 'Workshop'),
  printer(2, 'Bravo', 'Workshop'),
  printer(3, 'Charlie', null),
  printer(4, 'Delta', ''),
];

const LOCATIONS = [
  { id: 1, name: 'Future rack', icon: 'home', color: '#3b82f6', printer_count: 0 },
  { id: null, name: 'Workshop', icon: null, color: null, printer_count: 5 },
];

let requests: { method: string; path: string; body: unknown }[];

beforeEach(() => {
  permissions.granted = ['printers:read', 'printers:update'];
  requests = [];
  const record = async (request: Request) => {
    requests.push({ method: request.method, path: new URL(request.url).pathname, body: await request.json() });
  };
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json(PRINTERS)),
    http.get('/api/v1/printers/:id/status', () => HttpResponse.json({ connected: true, state: 'IDLE' })),
    http.get('/api/v1/printer-locations/', () => HttpResponse.json(LOCATIONS)),
    http.post('/api/v1/printer-locations/', async ({ request }) => {
      await record(request);
      return HttpResponse.json({ id: 9, name: 'Basement', icon: null, color: null, printer_count: 0 }, { status: 201 });
    }),
    http.post('/api/v1/printer-locations/assign', async ({ request }) => {
      await record(request);
      return HttpResponse.json({ moved: 2 });
    }),
    http.post('/api/v1/printer-locations/delete', async ({ request }) => {
      await record(request);
      return HttpResponse.json({ deleted: 2, printers_ungrouped: 5 });
    }),
  );
});

describe('PrinterLocationsPage', () => {
  it('shows server locations, including an empty one, with readable English counts', async () => {
    render(<PrinterLocationsPage />);

    expect(await screen.findByText('Future rack')).toBeInTheDocument();
    expect(screen.getByText('Workshop')).toBeInTheDocument();
    expect(screen.getByText('Printers: 0')).toBeInTheDocument();
    expect(screen.getByText('Printers: 5')).toBeInTheDocument();
    // NULL and "" are both "no location".
    expect(screen.getByText('Printers without a location: 2')).toBeInTheDocument();
    expect(screen.getByText('Charlie')).toBeInTheDocument();
    expect(screen.getByText('Delta')).toBeInTheDocument();
  });

  it('creates a location on the server, with a name capped at the column width', async () => {
    const user = userEvent.setup();
    render(<PrinterLocationsPage />);

    await user.click(await screen.findByRole('button', { name: /new location/i }));
    const input = screen.getByRole('textbox', { name: /location name/i });
    expect(input).toHaveAttribute('maxLength', '100');
    await user.type(input, 'Basement');
    await user.click(screen.getByRole('button', { name: '#ef4444' }));
    const dialog = screen.getByRole('heading', { name: 'Create Location' }).closest('div')!;
    await user.click(within(dialog).getByRole('button', { name: /new location/i }));

    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toEqual({
      method: 'POST',
      path: '/api/v1/printer-locations/',
      body: { name: 'Basement', icon: null, color: '#ef4444' },
    });
  });

  it('moves selected printers in one request', async () => {
    const user = userEvent.setup();
    render(<PrinterLocationsPage />);

    await user.click(await screen.findByRole('button', { name: /select all/i }));
    expect(screen.getByText('Selected: 2')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /^move$/i }));
    const dialog = screen.getByRole('heading', { name: 'Move printers (2)' }).closest('div')!;
    await user.selectOptions(within(dialog).getByRole('combobox'), 'Future rack');
    await user.click(within(dialog).getByRole('button', { name: /^move$/i }));

    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toEqual({
      method: 'POST',
      path: '/api/v1/printer-locations/assign',
      body: { printer_ids: [3, 4], location: 'Future rack' },
    });
    expect(await screen.findByText('Printers moved: 2')).toBeInTheDocument();
  });

  it('deletes selected locations in one request and names what it affects', async () => {
    const user = userEvent.setup();
    render(<PrinterLocationsPage />);

    await user.click(await screen.findByRole('button', { name: /^select$/i }));
    await user.click(screen.getByText('Future rack'));
    await user.click(screen.getByText('Workshop'));
    await user.click(screen.getByRole('button', { name: /delete selected/i }));

    expect(screen.getByText('Delete locations (2)')).toBeInTheDocument();
    expect(
      screen.getByText('Delete Future rack, Workshop? Printers left without a location: 5.'),
    ).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /^delete$/i }));

    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toEqual({
      method: 'POST',
      path: '/api/v1/printer-locations/delete',
      body: { names: ['Future rack', 'Workshop'] },
    });
  });

  it('is read-only without printers:update', async () => {
    permissions.granted = ['printers:read'];
    render(<PrinterLocationsPage />);

    expect(await screen.findByText('Workshop')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /new location/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^select$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /edit/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /select all/i })).not.toBeInTheDocument();
  });

  it('links back to the printers page', async () => {
    render(<PrinterLocationsPage />);

    const back = await screen.findByRole('link', { name: /back to printers/i });
    expect(back).toHaveAttribute('href', '/');
  });
});

describe('printer locations strings', () => {
  it('keeps printers.dropToQueue, which the printer card still uses', () => {
    expect(i18n.t('printers.dropToQueue', { lng: 'en' })).toBe('Drop to queue');
    expect(i18n.t('printers.dropToQueue', { lng: 'ru' })).not.toBe('printers.dropToQueue');
  });
});
