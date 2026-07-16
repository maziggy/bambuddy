/**
 * Feedback for the AMS drying start/stop buttons (#2533).
 *
 * The reporter's P1S accepted `ams_filament_drying` with result=success and
 * then never dried. Two gaps: the Start button gave no confirmation at all,
 * and Bambuddy treated the MQTT ack as proof the cycle had begun. These tests
 * cover the toast and the post-ack watcher that catches a printer which takes
 * the command and drops it.
 *
 * The reporter's own printer is now handled further up: Bambu's P1 manual says
 * P1-series AMS drying is screen-only, so the card no longer offers to command
 * it (last describe block). The watcher stays for any other firmware that acks
 * and declines — on the models we *can* command, the ack is still all we have,
 * because the `dry_sf_reason` refusal array only exists on some of them.
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

/** AMS 2 Pro (n3f) on an idle printer that accepts remote drying commands. */
function makeStatus(
  dry: { dry_time: number; dry_status: number },
  caps: { supports_drying?: boolean; drying_screen_only?: boolean } = {},
) {
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
    supports_drying: caps.supports_drying ?? true,
    drying_screen_only: caps.drying_screen_only ?? false,
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

/** A P1: the AMS dries, but only from the printer's own screen. */
const SCREEN_ONLY = makeStatus(
  { dry_time: 0, dry_status: 0 },
  { supports_drying: false, drying_screen_only: true },
);
const SCREEN_ONLY_DRYING = makeStatus(
  { dry_time: 720, dry_status: 2 },
  { supports_drying: false, drying_screen_only: true },
);

const SCREEN_ONLY_TITLE =
  "AMS drying on this printer can only be controlled from the printer's own screen (Bambu limitation)";

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

  it('acknowledges the start command with a toast that claims only that it was sent', async () => {
    const user = userEvent.setup();
    server.use(http.get('/api/v1/printers/:id/status', () => HttpResponse.json(IDLE)));

    render(<PrintersPage />);
    await startDrying(user);

    await waitFor(() => {
      expect(mockShowToast).toHaveBeenCalledWith('Drying command sent', 'success');
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
      expect(mockShowToast).toHaveBeenCalledWith('Drying command sent', 'success');
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
      expect(mockShowToast).toHaveBeenCalledWith('Drying command sent', 'success');
    });

    await vi.advanceTimersByTimeAsync(31_000);

    expect(mockShowToast).not.toHaveBeenCalledWith(expect.stringContaining('never started drying'), 'error');
  });
});

describe('PrintersPage - screen-only AMS drying (#2533)', () => {
  beforeEach(() => {
    mockShowToast.mockClear();
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([mockPrinter])),
      http.get('/api/v1/queue/', () => HttpResponse.json([])),
    );
  });

  it('keeps the drying control visible but disabled, and says why', async () => {
    const user = userEvent.setup();
    server.use(http.get('/api/v1/printers/:id/status', () => HttpResponse.json(SCREEN_ONLY)));

    render(<PrintersPage />);

    // Present, so the user learns the AMS *can* dry and where to do it — silently
    // dropping the button would just look like the feature vanished.
    const buttons = await screen.findAllByTitle(SCREEN_ONLY_TITLE);
    expect(buttons[0]).toBeDisabled();

    await user.click(buttons[0]);
    expect(screen.queryByTestId('drying-start-confirm')).not.toBeInTheDocument();
  });

  it('shows a cycle started at the printer, without offering to stop it', async () => {
    server.use(http.get('/api/v1/printers/:id/status', () => HttpResponse.json(SCREEN_ONLY_DRYING)));

    render(<PrintersPage />);

    // The countdown is pure observation and still works.
    expect((await screen.findAllByText(/12h 0m/)).length).toBeGreaterThan(0);
    // Stop is a command, and a P1 ignores it exactly as it ignores start.
    expect(screen.queryByTitle('Stop Drying')).not.toBeInTheDocument();
  });
});
