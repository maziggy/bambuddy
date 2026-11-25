/**
 * Printer card -> firmware modal wiring (inline FirmwareUpdateModal in PrintersPage).
 *
 * Pins: the version the user picks is the version that is prepared and
 * uploaded to the printer's SD card, the upload status poll is shown (progress
 * and error), the Upload button only exists when the backend's prepare check
 * says it can proceed, and firmware:update gates both the prepare check and
 * the upload.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, within, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { QueryClient } from '@tanstack/react-query';

const permissions = { granted: [] as string[] };

const mockUseAuth = {
  user: { id: 1, username: 'operator', permissions: [] as string[] },
  authEnabled: true,
  loading: false,
  isAdmin: false,
  login: vi.fn(),
  loginWithToken: vi.fn(),
  logout: vi.fn(),
  refreshAuth: vi.fn(),
  hasPermission: vi.fn((permission: string) => permissions.granted.includes(permission)),
  hasAnyPermission: vi.fn(() => true),
  canModify: vi.fn(() => true),
};

vi.mock('../../contexts/AuthContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/AuthContext')>();
  return { ...actual, useAuth: () => mockUseAuth };
});

import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';

const PRINTER_ID = 5;

const mockPrinter = {
  id: PRINTER_ID,
  name: 'Garage X1C',
  ip_address: '192.0.2.100',
  serial_number: '01P00A000000005',
  access_code: '12345678',
  model: 'X1C',
  enabled: true,
  nozzle_diameter: 0.4,
  nozzle_type: 'stainless_steel',
  location: null,
  auto_archive: true,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const firmwareInfo = {
  printer_id: PRINTER_ID,
  printer_name: 'Garage X1C',
  model: 'X1C',
  current_version: '01.07.00.00',
  latest_version: '01.08.00.00',
  update_available: true,
  download_url: null,
  release_notes: 'Latest notes',
  available_versions: [
    { version: '01.08.00.00', file_available: true, download_url: null, release_notes: 'v8', release_time: null },
    { version: '01.07.00.00', file_available: true, download_url: null, release_notes: 'v7', release_time: null },
    { version: '01.06.00.00', file_available: true, download_url: null, release_notes: 'v6', release_time: null },
    { version: '01.05.00.00', file_available: false, download_url: null, release_notes: null, release_time: null },
  ],
};

const prepareOk = {
  can_proceed: true,
  sd_card_present: true,
  sd_card_free_space: 1e9,
  firmware_size: 1e8,
  space_sufficient: true,
  update_available: true,
  current_version: '01.07.00.00',
  latest_version: '01.08.00.00',
  target_version: null,
  firmware_filename: 'fw.bin',
  errors: [] as string[],
};

let prepareVersions: (string | null)[] = [];
let uploadVersions: (string | null)[] = [];
let statusPolls = 0;

function renderPage({
  state = 'IDLE',
  prepare = prepareOk,
  statuses = [] as object[],
}: { state?: string; prepare?: typeof prepareOk; statuses?: object[] } = {}) {
  prepareVersions = [];
  uploadVersions = [];
  statusPolls = 0;
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json([mockPrinter])),
    http.get('/api/v1/printers/:id/status', () =>
      HttpResponse.json({
        connected: true,
        state,
        progress: state === 'RUNNING' ? 40 : 0,
        layer_num: state === 'RUNNING' ? 10 : 0,
        total_layers: 100,
        temperatures: { nozzle: 25, bed: 25, chamber: 25 },
        remaining_time: 0,
        wifi_signal: -40,
        speed_level: 2,
        vt_tray: [],
        ams: [],
        firmware_version: '01.07.00.00',
      }),
    ),
    http.get('/api/v1/queue/', () => HttpResponse.json([])),
    http.get(`/api/v1/firmware/updates/${PRINTER_ID}`, () => HttpResponse.json(firmwareInfo)),
    http.get(`/api/v1/firmware/updates/${PRINTER_ID}/prepare`, ({ request }) => {
      prepareVersions.push(new URL(request.url).searchParams.get('version'));
      return HttpResponse.json(prepare);
    }),
    http.post('/api/v1/firmware/updates/:id/upload', ({ request, params }) => {
      expect(params.id).toBe(String(PRINTER_ID));
      uploadVersions.push(new URL(request.url).searchParams.get('version'));
      return HttpResponse.json({ started: true, message: 'started' });
    }),
    http.get(`/api/v1/firmware/updates/${PRINTER_ID}/upload/status`, () => {
      const s = statuses[Math.min(statusPolls, statuses.length - 1)];
      statusPolls += 1;
      return HttpResponse.json(s);
    }),
  );
  render(<PrintersPage />);
}

async function openModal() {
  const user = userEvent.setup();
  const badge = await screen.findByTitle(/Firmware update available/i);
  await user.click(badge);
  await screen.findByText('Firmware Update');
  return user;
}

// The version list rows are buttons; the "Current:" line shows the same text in a plain span.
const versionRow = (v: string) => {
  const rows = screen
    .getAllByText(v, { selector: 'span.font-mono' })
    .map((el) => el.closest('button'))
    .filter((b): b is HTMLButtonElement => b !== null);
  expect(rows).toHaveLength(1);
  return rows[0];
};

describe('PrintersPage firmware modal wiring', () => {
  beforeEach(() => {
    permissions.granted = ['firmware:read', 'firmware:update', 'printers:control'];
  });

  it('uploads the version the user picked, not the latest', async () => {
    renderPage({
      statuses: [{ status: 'complete', progress: 100, message: 'Done', error: null, firmware_filename: 'fw.bin', firmware_version: '01.06.00.00' }],
    });
    const user = await openModal();

    // Latest is pre-selected and prepared first.
    await waitFor(() => expect(prepareVersions).toContain('01.08.00.00'));

    await user.click(versionRow('01.06.00.00'));
    await waitFor(() => expect(prepareVersions).toContain('01.06.00.00'));

    const upload = await screen.findByRole('button', { name: /Upload Firmware/ });
    await user.click(upload);

    await waitFor(() => expect(uploadVersions).toEqual(['01.06.00.00']));
    expect(await screen.findByText('Firmware uploaded to SD card!', {}, { timeout: 5000 })).toBeInTheDocument();
  }, 10000);

  it('shows polled progress and then the upload error', async () => {
    renderPage({
      statuses: [
        { status: 'uploading', progress: 45, message: 'Uploading to SD card', error: null, firmware_filename: 'fw.bin', firmware_version: '01.08.00.00' },
        { status: 'error', progress: 45, message: 'Upload failed', error: 'FTP connection reset', firmware_filename: 'fw.bin', firmware_version: '01.08.00.00' },
      ],
    });
    const user = await openModal();
    await user.click(await screen.findByRole('button', { name: /Upload Firmware/ }));
    await waitFor(() => expect(uploadVersions).toEqual(['01.08.00.00']));

    expect(await screen.findByText('45%', {}, { timeout: 5000 })).toBeInTheDocument();
    expect(await screen.findByText('FTP connection reset', {}, { timeout: 5000 })).toBeInTheDocument();
    // After an error the poll stops.
    const polls = statusPolls;
    await new Promise((r) => setTimeout(r, 2300));
    expect(statusPolls).toBe(polls);
  }, 15000);

  it('offers no upload when the prepare check refuses, and shows why', async () => {
    renderPage({ prepare: { ...prepareOk, can_proceed: false, sd_card_present: false, errors: ['No SD card inserted'] } });
    await openModal();
    expect(await screen.findByText('No SD card inserted')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Upload Firmware/ })).toBeNull();
    expect(uploadVersions).toEqual([]);
  });

  it('without firmware:update, versions are locked, nothing is prepared and there is no upload', async () => {
    permissions.granted = ['firmware:read', 'printers:control'];
    renderPage();
    await openModal();
    const dialog = screen.getByText('Firmware Update').closest('div.fixed') as HTMLElement;
    expect(versionRow('01.06.00.00')).toBeDisabled();
    expect(within(dialog).queryByRole('button', { name: /Upload Firmware/ })).toBeNull();
    await new Promise((r) => setTimeout(r, 50));
    expect(prepareVersions).toEqual([]);
    expect(uploadVersions).toEqual([]);
  });

  it('does not offer the installed version or one without a file', async () => {
    renderPage();
    await openModal();
    expect(versionRow('01.07.00.00')).toBeDisabled();
    expect(versionRow('01.05.00.00')).toBeDisabled();
    expect(versionRow('01.06.00.00')).toBeEnabled();
  });

  // Firmware goes onto the SD card the printer prints from: only while idle,
  // even when the prepare check (made earlier) still says it can proceed.
  it.each(['RUNNING', 'PAUSE', 'PREPARE', 'SLICING'])('offers no upload while the printer is %s', async (state) => {
    renderPage({ state });
    await openModal();
    await waitFor(() => expect(prepareVersions.length).toBeGreaterThan(0));
    expect(await screen.findByText(/can only be uploaded while it is idle/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Upload Firmware/ })).toBeNull();
    expect(uploadVersions).toEqual([]);
  });

  it('asks the prepare check again once a busy printer turns idle, and then offers the upload', async () => {
    // The page's client: the status reaches the card through its cache, as from the WebSocket.
    const spy = vi.spyOn(QueryClient.prototype, 'mount');
    const busy = { ...prepareOk, can_proceed: false, errors: ['The printer is busy; firmware can only be uploaded while it is idle'] };
    renderPage({ state: 'RUNNING', prepare: busy });
    await openModal();
    expect(await screen.findByText(/firmware can only be uploaded while it is idle/)).toBeInTheDocument();
    const asked = prepareVersions.length;

    server.use(http.get(`/api/v1/firmware/updates/${PRINTER_ID}/prepare`, ({ request }) => {
      prepareVersions.push(new URL(request.url).searchParams.get('version'));
      return HttpResponse.json(prepareOk);
    }));
    const client = spy.mock.contexts[spy.mock.contexts.length - 1] as QueryClient;
    act(() => {
      client.setQueryData(['printerStatus', PRINTER_ID], (old: object | undefined) => ({ ...old, state: 'FINISH' }));
    });

    expect(await screen.findByRole('button', { name: /Upload Firmware/ })).toBeInTheDocument();
    expect(prepareVersions.length).toBeGreaterThan(asked);
    spy.mockRestore();
  });

  it.each(['FINISH', 'FAILED'])('offers the upload once the printer is %s', async (state) => {
    renderPage({ state });
    await openModal();
    expect(await screen.findByRole('button', { name: /Upload Firmware/ })).toBeInTheDocument();
  });
});
