/**
 * Feedback for the AMS drying start/stop buttons (#2533).
 *
 * The reporter's P1S accepted `ams_filament_drying` with result=success and
 * then never dried. Two gaps: the Start button gave no confirmation at all,
 * and Bambuddy treated the MQTT ack as proof the cycle had begun. These tests
 * cover the toast and the post-ack watcher that catches a printer which takes
 * the command and drops it.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const mockShowToast = vi.fn();
vi.mock('../../contexts/ToastContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/ToastContext')>();
  return { ...actual, useToast: () => ({ showToast: mockShowToast }) };
});

const mockPrinter = {
  id: 1,
  name: 'P1S',
  ip_address: '192.168.1.100',
  serial_number: '01P00A000000001',
  access_code: '12345678',
  model: 'P1S',
  enabled: true,
  nozzle_diameter: 0.4,
  nozzle_type: 'stainless_steel',
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
  state: 3,
};

/** AMS 2 Pro (n3f) on an idle printer — the reporter's hardware. */
function makeStatus(dry: { dry_time: number; dry_status: number }) {
  return {
    connected: true,
    state: 'IDLE',
    progress: 0,
    layer_num: 0,
    total_layers: 0,
    temperatures: { nozzle: 25, bed: 25, chamber: 25 },
    remaining_time: 0,
    filename: null,
    wifi_signal: -29,
    speed_level: 2,
    supports_drying: true,
    vt_tray: [],
    ams: [
      {
        id: 0,
        humidity: 30,
        temp: 33,
        is_ams_ht: false,
        serial_number: 'AMS00',
        sw_ver: '03.00.21.29',
        dry_sub_status: 0,
        dry_sf_reason: [],
        module_type: 'n3f',
        ...dry,
        tray: [
          { id: 0, ...baseTray },
          { id: 1, ...baseTray },
          { id: 2, ...baseTray },
          { id: 3, ...baseTray },
        ],
      },
    ],
  };
}

const IDLE = makeStatus({ dry_time: 0, dry_status: 0 });
const DRYING = makeStatus({ dry_time: 720, dry_status: 2 });

/**
 * Open the drying popover and press Start. The card renders the AMS in two
 * layouts, so the flame icon appears more than once — either opens the same
 * popover.
 */
async function startDrying(user: ReturnType<typeof userEvent.setup>) {
  await waitFor(() => {
    expect(screen.getAllByTitle('Start Drying').length).toBeGreaterThan(0);
  });
  await user.click(screen.getAllByTitle('Start Drying')[0]);
  await user.click(await screen.findByTestId('drying-start-confirm'));
}

describe('PrintersPage - AMS drying feedback (#2533)', () => {
  beforeEach(() => {
    mockShowToast.mockClear();
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([mockPrinter])),
      http.get('/api/v1/queue/', () => HttpResponse.json([])),
      http.post('/api/v1/printers/:id/drying/start', () =>
        HttpResponse.json({ status: 'drying_started', ams_id: 0, temp: 45, duration: 12 }),
      ),
      http.post('/api/v1/printers/:id/drying/stop', () =>
        HttpResponse.json({ status: 'drying_stopped', ams_id: 0 }),
      ),
    );
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('confirms the start command with a toast', async () => {
    const user = userEvent.setup();
    server.use(http.get('/api/v1/printers/:id/status', () => HttpResponse.json(IDLE)));

    render(<PrintersPage />);
    await startDrying(user);

    await waitFor(() => {
      expect(mockShowToast).toHaveBeenCalledWith('Drying started', 'success');
    });
  });

  it('confirms the stop command with a toast', async () => {
    const user = userEvent.setup();
    server.use(http.get('/api/v1/printers/:id/status', () => HttpResponse.json(DRYING)));

    render(<PrintersPage />);

    // While a cycle is live the same button becomes Stop Drying.
    await waitFor(() => {
      expect(screen.getAllByTitle('Stop Drying').length).toBeGreaterThan(0);
    });
    await user.click(screen.getAllByTitle('Stop Drying')[0]);

    await waitFor(() => {
      expect(mockShowToast).toHaveBeenCalledWith('Drying stopped', 'success');
    });
  });

  it('warns when the printer acks the command but the AMS never starts drying', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    // Status keeps reporting dry_status 0 — the reporter's exact symptom.
    server.use(http.get('/api/v1/printers/:id/status', () => HttpResponse.json(IDLE)));

    render(<PrintersPage />);
    await startDrying(user);

    await waitFor(() => {
      expect(mockShowToast).toHaveBeenCalledWith('Drying started', 'success');
    });

    await vi.advanceTimersByTimeAsync(31_000);

    expect(mockShowToast).toHaveBeenCalledWith(
      'The printer accepted the command but the AMS never started drying. Check that the AMS power adapter is connected and that the printer is idle.',
      'error',
    );
  });

  it('stays quiet when the AMS does enter a drying cycle', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

    // Idle until the command lands, then the AMS reports a live cycle — which is
    // what the mutation's cache invalidation refetches.
    let started = false;
    server.use(
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json(started ? DRYING : IDLE)),
      http.post('/api/v1/printers/:id/drying/start', () => {
        started = true;
        return HttpResponse.json({ status: 'drying_started', ams_id: 0, temp: 45, duration: 12 });
      }),
    );

    render(<PrintersPage />);
    await startDrying(user);

    await waitFor(() => {
      expect(mockShowToast).toHaveBeenCalledWith('Drying started', 'success');
    });

    await vi.advanceTimersByTimeAsync(31_000);

    expect(mockShowToast).not.toHaveBeenCalledWith(expect.stringContaining('never started drying'), 'error');
  });
});
