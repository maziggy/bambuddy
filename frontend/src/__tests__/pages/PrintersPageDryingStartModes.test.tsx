/**
 * UX for the scheduled-drying start modes.
 *
 * "After delay" and "At time" reveal an extra control at the bottom of the
 * drying popover. These used to render below the fold of a height-capped,
 * scrollable body with no affordance; the delay options are now inline
 * chips and an above-placed popover grows upward from its anchored bottom
 * edge. The click that dismisses the native date picker must not tear down
 * the popover.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

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
const IDLE = {
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
  drying_screen_only: false,
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
      dry_time: 0,
      dry_status: 0,
      tray: [
        { id: 0, ...baseTray },
        { id: 1, ...baseTray },
        { id: 2, ...baseTray },
        { id: 3, ...baseTray },
      ],
    },
  ],
};

/** Same AMS mid-cycle: 12h remaining on the dryer. */
const DRYING = {
  ...IDLE,
  ams: [{ ...IDLE.ams[0], dry_time: 720, dry_status: 2 }],
};

const PENDING_ROW = {
  id: 1,
  printer_id: 1,
  ams_id: 0,
  temp: 45,
  duration_hours: 12,
  filament: 'PLA',
  rotate_tray: false,
  start_after: '2026-07-25T23:24:00',
  status: 'pending',
  waiting_reason: null,
  error_message: null,
  created_at: '2026-07-25T20:00:00',
  started_at: null,
  completed_at: null,
};

async function openDryingPopover(user: ReturnType<typeof userEvent.setup>) {
  await waitFor(() => {
    expect(screen.getAllByTitle('Start Drying').length).toBeGreaterThan(0);
  });
  await user.click(screen.getAllByTitle('Start Drying')[0]);
  await screen.findByTestId('drying-start-confirm');
}

describe('PrintersPage - drying start modes', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([mockPrinter])),
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json(IDLE)),
      http.get('/api/v1/queue/', () => HttpResponse.json([])),
      http.get('/api/v1/scheduled-dryings', () => HttpResponse.json([])),
    );
  });

  it('reveals the delay chips when After delay is selected, with 2h preselected', async () => {
    const user = userEvent.setup();
    render(<PrintersPage />);
    await openDryingPopover(user);

    await user.click(screen.getByRole('button', { name: 'After delay' }));

    for (const label of ['30m', '1h', '2h', '4h', '8h', '12h', '24h']) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument();
    }
    expect(screen.getByRole('button', { name: '2h' })).toHaveAttribute('aria-pressed', 'true');

    await user.click(screen.getByRole('button', { name: '4h' }));
    expect(screen.getByRole('button', { name: '4h' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: '2h' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('reveals the datetime input for At time and keeps Schedule disabled until a time is set', async () => {
    const user = userEvent.setup();
    render(<PrintersPage />);
    await openDryingPopover(user);

    await user.click(screen.getByRole('button', { name: 'At time' }));

    const input = await screen.findByTestId('drying-start-at');
    expect(screen.getByTestId('drying-start-confirm')).toBeDisabled();

    fireEvent.change(input, { target: { value: '2099-01-15T18:00' } });
    expect(screen.getByTestId('drying-start-confirm')).toBeEnabled();
  });

  it('drops the scheduled banner promptly once a drying cycle starts', async () => {
    // The banner polls every 30s; without a nudge from the live AMS status
    // it shows a dispatched schedule as still pending for up to that long.
    let calls = 0;
    server.use(
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json(DRYING)),
      http.get('/api/v1/scheduled-dryings', () => {
        calls += 1;
        return HttpResponse.json(calls === 1 ? [PENDING_ROW] : []);
      }),
    );
    render(<PrintersPage />);

    await waitFor(() => expect(calls).toBeGreaterThanOrEqual(2));
    await waitFor(() => {
      expect(screen.queryByText(/Drying scheduled for/)).not.toBeInTheDocument();
    });
  });

  it('labels a transient cannot-dry reason accurately, not as a power problem', async () => {
    const blocked = { ...IDLE, ams: [{ ...IDLE.ams[0], dry_sf_reason: [2] }] };
    server.use(http.get('/api/v1/printers/:id/status', () => HttpResponse.json(blocked)));
    render(<PrintersPage />);
    await waitFor(() => {
      expect(screen.getAllByTitle("AMS can't start drying right now").length).toBeGreaterThan(0);
    });
  });

  it('keeps the power tooltip for the power-supply reason codes', async () => {
    const blocked = { ...IDLE, ams: [{ ...IDLE.ams[0], dry_sf_reason: [8] }] };
    server.use(http.get('/api/v1/printers/:id/status', () => HttpResponse.json(blocked)));
    render(<PrintersPage />);
    await waitFor(() => {
      expect(screen.getAllByTitle('Connect AMS power adapter to enable drying').length).toBeGreaterThan(0);
    });
  });

  it('resets the start mode when the popover is reopened', async () => {
    // Leaving the mode set carried a stale "At time" timestamp into the next
    // open, by then in the past, and the POST rejected it.
    const user = userEvent.setup();
    render(<PrintersPage />);
    await openDryingPopover(user);

    await user.click(screen.getByRole('button', { name: 'At time' }));
    const input = await screen.findByTestId('drying-start-at');
    fireEvent.change(input, { target: { value: '2026-07-25T23:24' } });
    expect(screen.getByTestId('drying-start-at')).toHaveValue('2026-07-25T23:24');

    // Close, then reopen on the same flame button.
    await user.click(screen.getByTestId('drying-popover-backdrop'));
    await waitFor(() => {
      expect(screen.queryByTestId('drying-start-confirm')).not.toBeInTheDocument();
    });
    await openDryingPopover(user);

    // Back on "Now": neither the delay chips nor the datetime input show.
    expect(screen.queryByTestId('drying-start-at')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'At time' }));
    expect(screen.getByTestId('drying-start-at')).toHaveValue('');
  });

  it('fetches the scheduled list once for the fleet rather than per printer', async () => {
    const requests: string[] = [];
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([mockPrinter, { ...mockPrinter, id: 2, name: 'P1S-2' }])),
      http.get('/api/v1/scheduled-dryings', ({ request }) => {
        requests.push(new URL(request.url).search);
        return HttpResponse.json([]);
      }),
    );
    render(<PrintersPage />);
    await waitFor(() => expect(requests.length).toBeGreaterThan(0));
    // No printer_id filter: one shared query, filtered client-side.
    expect(requests.every(search => search === '')).toBe(true);
  });

  it('says why a due run has not started when the AMS needs the power adapter', async () => {
    server.use(
      http.get('/api/v1/scheduled-dryings', () =>
        HttpResponse.json([{ ...PENDING_ROW, start_after: null, waiting_reason: 'ams_power_required' }])
      ),
    );
    render(<PrintersPage />);
    expect(await screen.findByText('Connect AMS power adapter to enable drying')).toBeInTheDocument();
  });

  it('shows a run that failed at dispatch, with a dismiss that clears it', async () => {
    // Only dispatch can fail (firmware too old on a printer that was offline
    // at schedule time). Without this the run vanishes and only the backend
    // log says why.
    const failedRow = {
      ...PENDING_ROW,
      status: 'failed',
      error_message: 'Drying not supported for this printer model or firmware version',
      completed_at: '2026-07-25T23:25:00',
    };
    let listed = [failedRow];
    let deleted: number | null = null;
    server.use(
      http.get('/api/v1/scheduled-dryings', () => HttpResponse.json(listed)),
      http.delete('/api/v1/scheduled-dryings/:id', ({ params }) => {
        deleted = Number(params.id);
        listed = [];
        return HttpResponse.json({ status: 'dismissed', id: deleted });
      }),
    );
    const user = userEvent.setup();
    render(<PrintersPage />);

    expect(
      await screen.findByText(/Scheduled drying failed: Drying not supported for this printer model/)
    ).toBeInTheDocument();

    await user.click(screen.getByTitle('Dismiss'));
    await waitFor(() => expect(deleted).toBe(failedRow.id));
    await waitFor(() => {
      expect(screen.queryByText(/Scheduled drying failed/)).not.toBeInTheDocument();
    });
  });

  it.each([
    ['ams_retract_filament', 'Retract the filament at the AMS outlet to start drying'],
    ['ams_not_found', 'Waiting for the AMS to be detected'],
    ['printer_offline', 'Waiting for the printer to come online'],
    ['printer_busy', 'Waiting for the printer to be free'],
    ['already_drying', 'Waiting for the current drying cycle to finish'],
    ['interrupted', 'Interrupted, will restart when the printer is free'],
  ])('renders text for the %s waiting reason', async (reason, text) => {
    // An unmapped reason left the card showing a bare "Drying scheduled for"
    // with no hint why it had not started.
    server.use(
      http.get('/api/v1/scheduled-dryings', () =>
        HttpResponse.json([{ ...PENDING_ROW, start_after: null, waiting_reason: reason }])
      ),
    );
    render(<PrintersPage />);
    expect(await screen.findByText(text)).toBeInTheDocument();
  });

  it('renders the banner inside the card body, not below it', async () => {
    // Between </CardContent> and </Card> it went full-bleed and its corners
    // collided with the card's rounded bottom edge.
    server.use(
      http.get('/api/v1/scheduled-dryings', () => HttpResponse.json([PENDING_ROW])),
    );
    render(<PrintersPage />);
    const banner = await screen.findByTestId('scheduled-drying-pending');
    // Its wrapper's parent is CardContent (which carries the padding), not the
    // bare Card root it used to hang off.
    const parent = banner.parentElement?.parentElement;
    expect(parent?.className).toMatch(/\bp-\d/);
  });

  it('does not close the popover on the outside click that dismisses the native date picker', async () => {
    const user = userEvent.setup();
    render(<PrintersPage />);
    await openDryingPopover(user);

    await user.click(screen.getByRole('button', { name: 'At time' }));
    const input = await screen.findByTestId('drying-start-at');

    // With the native picker open the input keeps focus; the click that
    // dismisses the picker lands on the backdrop.
    input.focus();
    await user.click(screen.getByTestId('drying-popover-backdrop'));
    expect(screen.getByTestId('drying-start-confirm')).toBeInTheDocument();

    // A second outside click (input no longer focused) closes as before.
    await user.click(screen.getByTestId('drying-popover-backdrop'));
    expect(screen.queryByTestId('drying-start-confirm')).not.toBeInTheDocument();
  });

  // The flame button's tooltip is the only place an immediate drying attempt
  // explains itself, and it has to name the same code the scheduled path would
  // record — otherwise the same blocked AMS reads two different ways depending
  // on which button you pressed.
  it.each([
    [[1], 'Connect AMS power adapter to enable drying'],
    [[8], 'Connect AMS power adapter to enable drying'],
    [[3], 'Retract the filament at the AMS outlet to start drying'],
    [[2], "AMS can't start drying right now"],
    // Both set: the power problem outranks the retract, as it does server-side.
    [[3, 1], 'Connect AMS power adapter to enable drying'],
    // Transient alongside an actionable one: name the one the user can fix.
    [[2, 3], 'Retract the filament at the AMS outlet to start drying'],
  ])('explains dry_sf_reason %j on the drying button', async (reasons, expected) => {
    server.use(
      http.get('/api/v1/printers/:id/status', () =>
        HttpResponse.json({ ...IDLE, ams: [{ ...IDLE.ams[0], dry_sf_reason: reasons }] })
      ),
    );
    render(<PrintersPage />);

    await waitFor(() => {
      expect(screen.getAllByTitle(expected).length).toBeGreaterThan(0);
    });
  });
});
