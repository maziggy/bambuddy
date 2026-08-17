/**
 * The printer card's drop zone is gated on the permissions it actually uses (#2849).
 *
 * Dropping a file uploads it to the library and creates a queue item, so the
 * two permissions that matter are `library:upload` and `queue:create` -- the
 * same pair the card's Print button has always checked. The drop zone instead
 * checked `printers:control`, which the flow never exercises, so a user with
 * control but neither of the other two got the drop accepted, the file
 * uploaded, and then a 403 from the queue with an orphaned library row left
 * behind.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const permissions = { granted: ['library:upload', 'queue:create'] as string[] };

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

import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';

const mockPrinter = {
  id: 1,
  name: 'X1C',
  ip_address: '192.168.1.100',
  serial_number: '01P00A000000001',
  access_code: '12345678',
  model: 'X1C',
  enabled: true,
  nozzle_diameter: 0.4,
  nozzle_type: 'stainless_steel',
  location: 'Workshop',
  auto_archive: true,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const uploads: string[] = [];

function renderPage() {
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json([mockPrinter])),
    http.get('/api/v1/printers/:id/status', () =>
      HttpResponse.json({
        connected: true,
        state: 'RUNNING',
        progress: 40,
        layer_num: 10,
        total_layers: 100,
        temperatures: { nozzle: 220, bed: 60, chamber: 30 },
        remaining_time: 600,
        filename: 'running.gcode',
        wifi_signal: -29,
        speed_level: 2,
        vt_tray: [],
        ams: [],
      })
    ),
    http.get('/api/v1/queue/', () => HttpResponse.json([])),
    http.post('/api/v1/library/files', () => {
      uploads.push('upload');
      return HttpResponse.json({ id: 7, filename: 'part.gcode', metadata: {} });
    }),
  );
  render(<PrintersPage />);
}

async function card(): Promise<HTMLElement> {
  await waitFor(() => expect(document.getElementById('printer-card-1')).not.toBeNull());
  return document.getElementById('printer-card-1') as HTMLElement;
}

const gcode = () => new File(['G28\n'], 'part.gcode', { type: 'text/plain' });

describe('PrintersPage — drop zone permissions (#2849)', () => {
  beforeEach(() => {
    uploads.length = 0;
    permissions.granted = ['library:upload', 'queue:create'];
  });

  it('accepts the drop when the user can upload and queue', async () => {
    renderPage();
    fireEvent.dragEnter(await card(), { dataTransfer: { files: [gcode()] } });

    expect(await screen.findByText('Drop to queue')).toBeInTheDocument();
  });

  it('refuses when the user cannot upload to the library', async () => {
    permissions.granted = ['queue:create'];
    renderPage();
    const el = await card();
    fireEvent.dragEnter(el, { dataTransfer: { files: [gcode()] } });

    expect(await screen.findByText('You do not have permission to upload files')).toBeInTheDocument();
    fireEvent.drop(el, { dataTransfer: { files: [gcode()] } });
    // Nothing is uploaded, so no orphaned library row is left behind.
    await waitFor(() => expect(uploads).toEqual([]));
  });

  it('refuses when the user cannot create queue items', async () => {
    // The case the old printers:control gate got wrong: the upload would have
    // succeeded and the queue POST then 403'd.
    permissions.granted = ['library:upload'];
    renderPage();
    const el = await card();
    fireEvent.dragEnter(el, { dataTransfer: { files: [gcode()] } });

    expect(await screen.findByText('You do not have permission to add to queue')).toBeInTheDocument();
    fireEvent.drop(el, { dataTransfer: { files: [gcode()] } });
    await waitFor(() => expect(uploads).toEqual([]));
  });

  it('is not granted by printers:control alone', async () => {
    permissions.granted = ['printers:control'];
    renderPage();
    fireEvent.dragEnter(await card(), { dataTransfer: { files: [gcode()] } });

    // printers:control grants neither, so the more specific upload message wins.
    expect(await screen.findByText('You do not have permission to upload files')).toBeInTheDocument();
    expect(screen.queryByText('Drop to queue')).toBeNull();
  });
});
