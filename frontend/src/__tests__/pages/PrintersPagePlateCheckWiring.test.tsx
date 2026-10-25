/**
 * Build-plate check wiring on the printer card.
 *
 * Opening the check turns the chamber light on when it is off (the camera
 * needs it), then asks the backend for a check with the debug image. Closing
 * the result (X, Close or Escape) must turn the light back off -- but only if
 * the check was what turned it on. A failed check must restore the light too.
 * From the result modal: calibrate, delete a reference and save the ROI all
 * name THIS printer.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, within, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const permissions = { denied: new Set<string>() };

const mockUseAuth = {
  user: { id: 1, username: 'operator', permissions: [] as string[] },
  authEnabled: true,
  loading: false,
  isAdmin: false,
  login: vi.fn(),
  loginWithToken: vi.fn(),
  logout: vi.fn(),
  refreshAuth: vi.fn(),
  hasPermission: vi.fn((permission: string) => !permissions.denied.has(permission)),
  hasAnyPermission: vi.fn(() => true),
  canModify: vi.fn(() => true),
};

vi.mock('../../contexts/AuthContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/AuthContext')>();
  return { ...actual, useAuth: () => mockUseAuth };
});

import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';

const PRINTER_ID = 9;
// Opening with the light off waits 2.5 s for the camera before checking.
const LIGHT_WAIT = { timeout: 6000 };

const mockPrinter = {
  id: PRINTER_ID,
  name: 'Plate X1C',
  ip_address: '192.0.2.109',
  serial_number: '00M09A350100009',
  access_code: '12345678',
  model: 'X1C',
  enabled: true,
  plate_detection_enabled: true,
  nozzle_diameter: 0.4,
  nozzle_type: 'hardened_steel',
  auto_archive: true,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const statusWithLight = (on: boolean) => ({
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
  chamber_light: on,
  vt_tray: [],
  ams: [],
});

const emptyResult = {
  is_empty: true,
  confidence: 0.93,
  difference_percent: 1.2,
  message: 'Plate is empty',
  has_debug_image: false,
  needs_calibration: false,
  failure_reason: null,
  reference_count: 2,
  max_references: 5,
  roi: { x: 0.1, y: 0.2, w: 0.6, h: 0.5 },
};

type Sent = { method: string; path: string; params: Record<string, string>; body: unknown };

interface SetupOptions {
  lightOn?: boolean;
  checkResult?: Record<string, unknown>;
  checkStatus?: number;
}

function setup({ lightOn = false, checkResult = emptyResult, checkStatus = 200 }: SetupOptions = {}) {
  const sent: Sent[] = [];
  const log = async (request: Request) => {
    const url = new URL(request.url);
    const text = request.method === 'GET' ? '' : await request.text();
    sent.push({
      method: request.method,
      path: url.pathname.replace(`/api/v1/printers/${PRINTER_ID}`, ''),
      params: Object.fromEntries(url.searchParams.entries()),
      body: text ? JSON.parse(text) : null,
    });
  };
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json([mockPrinter])),
    http.get('/api/v1/queue/', () => HttpResponse.json([])),
    http.get('/api/v1/printers/:id/status', () => HttpResponse.json(statusWithLight(lightOn))),
    http.post('/api/v1/printers/:id/chamber-light', async ({ request }) => {
      await log(request);
      return HttpResponse.json({ success: true, message: 'ok' });
    }),
    http.get('/api/v1/printers/:id/camera/check-plate', async ({ request }) => {
      await log(request);
      if (checkStatus !== 200) {
        return HttpResponse.json({ detail: 'Camera unreachable' }, { status: checkStatus });
      }
      return HttpResponse.json(checkResult);
    }),
    http.get('/api/v1/printers/:id/camera/plate-detection/references', () =>
      HttpResponse.json({
        references: [
          { index: 0, label: 'Textured', timestamp: '2024-01-01T00:00:00Z', has_image: true, thumbnail_url: '' },
          { index: 3, label: 'Smooth', timestamp: '2024-01-02T00:00:00Z', has_image: true, thumbnail_url: '' },
        ],
        max_references: 5,
      })
    ),
    http.get('/api/v1/printers/:id/camera/plate-detection/references/:index/thumbnail', () =>
      new HttpResponse(null, { status: 204 })
    ),
    http.post('/api/v1/printers/:id/camera/plate-detection/calibrate', async ({ request }) => {
      await log(request);
      return HttpResponse.json({ success: true, message: 'Calibrated', index: 2 });
    }),
    http.delete('/api/v1/printers/:id/camera/plate-detection/references/:index', async ({ request }) => {
      await log(request);
      return HttpResponse.json({ success: true, message: 'Deleted' });
    }),
    http.patch('/api/v1/printers/:id', async ({ request }) => {
      await log(request);
      return HttpResponse.json(mockPrinter);
    }),
  );
  render(<PrintersPage />);
  return sent;
}

const light = (on: boolean) => ({ method: 'POST', path: '/chamber-light', params: { on: String(on) }, body: null });
const check = { method: 'GET', path: '/camera/check-plate', params: { include_debug_image: 'true' }, body: null };

async function openCheck(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByTitle('Manage plate detection calibration'));
}

function modal() {
  return screen.getByText('Build Plate Check').closest('div.max-w-lg') as HTMLElement;
}

describe('PrintersPage plate check: chamber light handling', () => {
  beforeEach(() => {
    permissions.denied = new Set();
  });

  it('light off: turns it on, then checks with the debug image; X turns it back off', async () => {
    const user = userEvent.setup();
    const sent = setup({ lightOn: false });

    await openCheck(user);
    await screen.findByText('Build Plate Check', {}, LIGHT_WAIT);
    expect(sent).toEqual([light(true), check]);

    // The header X is the first button in the modal.
    await user.click(within(modal()).getAllByRole('button')[0]);
    await waitFor(() => expect(screen.queryByText('Build Plate Check')).toBeNull());
    await waitFor(() => expect(sent).toEqual([light(true), check, light(false)]));
  });

  it('light off: Escape closes the result and turns the light back off', async () => {
    const user = userEvent.setup();
    const sent = setup({ lightOn: false });

    await openCheck(user);
    await screen.findByText('Build Plate Check', {}, LIGHT_WAIT);
    fireEvent.keyDown(window, { key: 'Escape' });

    await waitFor(() => expect(screen.queryByText('Build Plate Check')).toBeNull());
    await waitFor(() => expect(sent).toEqual([light(true), check, light(false)]));
  });

  it('Escape pressed the moment the result shows still closes it', async () => {
    const user = userEvent.setup();
    const sent = setup({ lightOn: false });
    // Pressed from the DOM change itself: before React's passive effects of
    // that render could run, as on a busy machine.
    const pressed = { done: false };
    const observer = new MutationObserver(() => {
      if (!pressed.done && screen.queryByText('Build Plate Check')) {
        pressed.done = true;
        fireEvent.keyDown(window, { key: 'Escape' });
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });

    try {
      await openCheck(user);
      await waitFor(() => expect(pressed.done).toBe(true), LIGHT_WAIT);
      await waitFor(() => expect(screen.queryByText('Build Plate Check')).toBeNull());
      await waitFor(() => expect(sent).toEqual([light(true), check, light(false)]));
    } finally {
      observer.disconnect();
    }
  });

  it('light already on: no chamber-light request on open or on close', async () => {
    const user = userEvent.setup();
    const sent = setup({ lightOn: true });

    await openCheck(user);
    await screen.findByText('Build Plate Check');
    await user.click(within(modal()).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByText('Build Plate Check')).toBeNull());
    await new Promise((r) => setTimeout(r, 50));
    expect(sent).toEqual([check]);
  });

  it('check failure (500): error toast, no modal, and the light it turned on goes back off', async () => {
    const user = userEvent.setup();
    const sent = setup({ lightOn: false, checkStatus: 500 });

    await openCheck(user);
    expect(await screen.findByText('Camera unreachable', {}, LIGHT_WAIT)).toBeInTheDocument();
    await waitFor(() => expect(sent).toEqual([light(true), check, light(false)]));
    expect(screen.queryByText('Build Plate Check')).toBeNull();
  });

  it('check failure with the light already on sends no chamber-light request', async () => {
    const user = userEvent.setup();
    const sent = setup({ lightOn: true, checkStatus: 500 });

    await openCheck(user);
    expect(await screen.findByText('Camera unreachable')).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 50));
    expect(sent).toEqual([check]);
  });

  it('without printers:update the check button is disabled and sends nothing', async () => {
    permissions.denied = new Set(['printers:update']);
    const user = userEvent.setup();
    const sent = setup({ lightOn: true });

    const buttons = (await screen.findAllByTitle('You do not have permission to update printers')).filter(
      (el) => el.tagName === 'BUTTON'
    );
    expect(buttons.length).toBeGreaterThanOrEqual(2);
    for (const b of buttons) {
      expect(b).toBeDisabled();
      await user.click(b);
    }
    await new Promise((r) => setTimeout(r, 50));
    expect(sent).toEqual([]);
  });
});

describe('PrintersPage plate check: result rendering', () => {
  beforeEach(() => {
    permissions.denied = new Set();
  });

  it('empty plate shows the empty verdict', async () => {
    const user = userEvent.setup();
    setup({ lightOn: true });
    await openCheck(user);
    expect(await screen.findByText('Plate appears empty')).toBeInTheDocument();
    expect(screen.queryByText('Objects detected on plate')).toBeNull();
  });

  it('occupied plate shows objects detected', async () => {
    const user = userEvent.setup();
    setup({ lightOn: true, checkResult: { ...emptyResult, is_empty: false } });
    await openCheck(user);
    expect(await screen.findByText('Objects detected on plate')).toBeInTheDocument();
    expect(screen.queryByText('Plate appears empty')).toBeNull();
  });

  it('failure_reason shows the could-not-run text instead of a verdict', async () => {
    const user = userEvent.setup();
    setup({ lightOn: true, checkResult: { ...emptyResult, failure_reason: 'no_camera_frame' } });
    await openCheck(user);
    expect(await screen.findByText('Plate check could not run')).toBeInTheDocument();
    expect(screen.getByText('no camera image')).toBeInTheDocument();
    expect(screen.queryByText('Plate appears empty')).toBeNull();
  });

  it('needs_calibration shows the calibration prompt and no ROI editor', async () => {
    const user = userEvent.setup();
    setup({ lightOn: true, checkResult: { ...emptyResult, needs_calibration: true } });
    await openCheck(user);
    expect(await screen.findByText('Calibration Required')).toBeInTheDocument();
    expect(screen.queryByText('Detection Area (ROI)')).toBeNull();
    expect(screen.queryByText('Plate appears empty')).toBeNull();
  });
});

describe('PrintersPage plate check: calibrate, references, ROI', () => {
  beforeEach(() => {
    permissions.denied = new Set();
  });

  it('Calibrate Empty Plate -> POST calibrate for this printer, then re-checks', async () => {
    const user = userEvent.setup();
    const sent = setup({ lightOn: true, checkResult: { ...emptyResult, needs_calibration: true } });
    await openCheck(user);
    await screen.findByText('Calibration Required');

    await user.click(screen.getByRole('button', { name: 'Calibrate Empty Plate' }));
    // The card never passes a label, so the request carries none.
    await waitFor(() =>
      expect(sent).toEqual([
        check,
        { method: 'POST', path: '/camera/plate-detection/calibrate', params: {}, body: null },
        check,
      ])
    );
  });

  it('Add Reference on a calibrated printer also POSTs calibrate', async () => {
    const user = userEvent.setup();
    const sent = setup({ lightOn: true });
    await openCheck(user);
    await user.click(await screen.findByRole('button', { name: /^Add Reference/ }));
    await waitFor(() =>
      expect(sent.filter((s) => s.path === '/camera/plate-detection/calibrate')).toHaveLength(1)
    );
  });

  it('deleting a reference DELETEs that reference index for this printer, then re-checks', async () => {
    const user = userEvent.setup();
    const sent = setup({ lightOn: true });
    await openCheck(user);

    // Second saved reference has index 3 (indices are not positions).
    const deletes = await screen.findAllByTitle('Delete reference');
    expect(deletes).toHaveLength(2);
    await user.click(deletes[1]);

    await waitFor(() =>
      expect(sent).toEqual([
        check,
        { method: 'DELETE', path: '/camera/plate-detection/references/3', params: {}, body: null },
        check,
      ])
    );
  });

  it('Save ROI -> PATCH /printers/{id} {plate_detection_roi}', async () => {
    const user = userEvent.setup();
    const sent = setup({ lightOn: true });
    await openCheck(user);
    await screen.findByText('Detection Area (ROI)');

    await user.click(within(modal()).getByRole('button', { name: 'Edit' }));
    const sliders = within(modal()).getAllByRole('slider');
    fireEvent.change(sliders[0], { target: { value: '0.25' } });
    await user.click(within(modal()).getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(sent).toContainEqual({
        method: 'PATCH',
        path: '',
        params: {},
        body: { plate_detection_roi: { x: 0.25, y: 0.2, w: 0.6, h: 0.5 } },
      })
    );
    // and then re-checks with the new area
    await waitFor(() => expect(sent.at(-1)).toEqual(check));
  });

  it('cancelling the ROI edit sends nothing', async () => {
    const user = userEvent.setup();
    const sent = setup({ lightOn: true });
    await openCheck(user);
    await screen.findByText('Detection Area (ROI)');

    await user.click(within(modal()).getByRole('button', { name: 'Edit' }));
    await user.click(within(modal()).getByRole('button', { name: 'Cancel' }));
    await new Promise((r) => setTimeout(r, 50));
    expect(sent).toEqual([check]);
  });
});
