/**
 * AMS slot / unit action wiring on the printer card.
 *
 * - "Re-read RFID" posts the refresh for the hovered slot's (amsId, slotId),
 *   including an AMS-HT (unit id 128+); the external holder offers no re-read.
 * - "Unassign" routes by inventory mode: the built-in inventory deletes the
 *   (printer, ams, tray) assignment, Spoolman mode deletes the Spoolman slot
 *   assignment by spool id. Neither may hit the other's endpoint.
 * - AMS Filament Backup toggle, stop drying, and the AMS friendly-name editor
 *   (save / clear, keyed by the unit's serial).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse, type JsonBodyType } from 'msw';
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

const PRINTER_ID = 5;

const mockPrinter = {
  id: PRINTER_ID,
  name: 'AMS X1C',
  ip_address: '192.0.2.105',
  serial_number: '00M09A350100005',
  access_code: '12345678',
  model: 'X1C',
  enabled: true,
  nozzle_diameter: 0.4,
  nozzle_type: 'hardened_steel',
  auto_archive: true,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

// No tray_uuid / tag_uid: a third-party spool, so Unassign is offered.
const baseTray = {
  tray_color: 'FF0000FF',
  tray_type: 'PLA',
  tray_sub_brands: 'PLA Basic',
  tray_id_name: '',
  tray_info_idx: 'GFL99',
  remain: 80,
  k: 0.02,
  cali_idx: null,
  tag_uid: null,
  tray_uuid: null,
  nozzle_temp_min: 190,
  nozzle_temp_max: 230,
  drying_temp: null,
  drying_time: null,
  state: 11,
};

const amsUnit = (extra: Record<string, unknown> = {}) => ({
  id: 0,
  humidity: 30,
  temp: 25,
  is_ams_ht: false,
  serial_number: 'AMS0SERIAL',
  sw_ver: '1.0.0',
  dry_time: 0,
  dry_status: 0,
  dry_sub_status: 0,
  dry_sf_reason: [],
  module_type: 'ams',
  tray: [0, 1, 2, 3].map((id) => ({ id, ...baseTray })),
  ...extra,
});

const htUnit = {
  id: 128,
  humidity: 20,
  temp: 30,
  is_ams_ht: true,
  serial_number: 'HT128SERIAL',
  sw_ver: '1.0.0',
  dry_time: 0,
  dry_status: 0,
  dry_sub_status: 0,
  dry_sf_reason: [],
  module_type: 'n3s',
  tray: [{ id: 0, ...baseTray, tray_type: 'PETG' }],
};

const statusWith = (ams: unknown[], extra: Record<string, unknown> = {}) => ({
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
  vt_tray: [],
  ams,
  ...extra,
});

const inventorySpool = {
  id: 77,
  material: 'PLA',
  subtype: 'Basic',
  brand: 'Generic',
  color_name: 'Red',
  rgba: 'FF0000FF',
  label_weight: 1000,
  weight_used: 200,
};

type Sent = { method: string; path: string; search: string; body: unknown };

interface SetupOptions {
  status: JsonBodyType;
  spoolman?: boolean;
  amsLabels?: Record<number, string>;
}

function setup({ status, spoolman = false, amsLabels = {} }: SetupOptions) {
  const sent: Sent[] = [];
  const record =
    (response: JsonBodyType = { success: true, message: 'ok', status: 'ok' }) =>
    async ({ request }: { request: Request }) => {
      const url = new URL(request.url);
      const text = await request.text();
      sent.push({ method: request.method, path: url.pathname, search: url.search, body: text ? JSON.parse(text) : null });
      return HttpResponse.json(response);
    };
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json([mockPrinter])),
    http.get('/api/v1/queue/', () => HttpResponse.json([])),
    http.get('/api/v1/printers/:id/status', () => HttpResponse.json(status)),
    http.get('/api/v1/printers/:id/ams-labels', () => HttpResponse.json(amsLabels)),
    http.get('/api/v1/spoolman/status', () =>
      HttpResponse.json({ enabled: spoolman, connected: spoolman, url: spoolman ? 'http://spoolman' : null })
    ),
    // Built-in inventory: slot 1 of AMS 0 holds spool 77.
    http.get('/api/v1/inventory/assignments', () =>
      HttpResponse.json(
        spoolman
          ? []
          : [
              {
                id: 1,
                spool_id: 77,
                printer_id: PRINTER_ID,
                printer_name: 'AMS X1C',
                ams_id: 0,
                tray_id: 1,
                fingerprint_color: null,
                fingerprint_type: null,
                spool: inventorySpool,
                configured: true,
                created_at: '2024-01-01T00:00:00Z',
              },
            ]
      )
    ),
    // Spoolman: slot 1 of AMS 0 holds Spoolman spool 55.
    http.get('/api/v1/spoolman/inventory/slot-assignments/all', () =>
      HttpResponse.json(
        spoolman
          ? [{ printer_id: PRINTER_ID, printer_name: 'AMS X1C', ams_id: 0, tray_id: 1, spoolman_spool_id: 55, ams_label: null }]
          : []
      )
    ),
    http.get('/api/v1/spoolman/inventory/spools', () =>
      HttpResponse.json(spoolman ? [{ ...inventorySpool, id: 55 }] : [])
    ),
    http.post('/api/v1/printers/:id/ams/:amsId/slot/:slotId/refresh', record()),
    http.delete('/api/v1/inventory/assignments/:pid/:aid/:tid', record({ status: 'ok' })),
    http.delete('/api/v1/spoolman/inventory/slot-assignments/:id', record({ ...inventorySpool, id: 55 })),
    http.post('/api/v1/printers/:id/ams-backup', record({ success: true, ams_filament_backup: true })),
    http.post('/api/v1/printers/:id/drying/stop', record({ status: 'ok', ams_id: 0 })),
    http.put('/api/v1/printers/:id/ams-labels/:amsId', record({ ams_id: 0, label: 'x' })),
    http.delete('/api/v1/printers/:id/ams-labels/:amsId', record({ success: true })),
  );
  render(<PrintersPage />);
  return sent;
}

async function slots() {
  await waitFor(() => expect(screen.getAllByTestId('filament-slot').length).toBeGreaterThan(0));
  return screen.getAllByTestId('filament-slot');
}

/** Hover a slot and wait for its portaled card to show the given action. */
async function hover(slot: Element, action: string) {
  fireEvent.mouseEnter(slot);
  return screen.findByRole('button', { name: action });
}

const settle = () => new Promise((r) => setTimeout(r, 50));

describe('AMS slot: Re-read RFID', () => {
  beforeEach(() => {
    permissions.denied = new Set();
  });

  it('posts refresh for the hovered regular-AMS slot (ams 0, slot 2)', async () => {
    const user = userEvent.setup();
    const sent = setup({ status: statusWith([amsUnit()]) });

    await user.click(await hover((await slots())[2], 'Re-read RFID'));
    await waitFor(() =>
      expect(sent).toEqual([
        { method: 'POST', path: `/api/v1/printers/${PRINTER_ID}/ams/0/slot/2/refresh`, search: '', body: null },
      ])
    );
  });

  it('posts refresh for an AMS-HT with its unit id 128', async () => {
    const user = userEvent.setup();
    const sent = setup({ status: statusWith([htUnit]) });

    await user.click(await hover((await slots())[0], 'Re-read RFID'));
    await waitFor(() =>
      expect(sent).toEqual([
        { method: 'POST', path: `/api/v1/printers/${PRINTER_ID}/ams/128/slot/0/refresh`, search: '', body: null },
      ])
    );
  });

  it('the external spool holder offers no RFID re-read', async () => {
    setup({
      status: statusWith([], { vt_tray: [{ id: 254, ...baseTray, tray_color: 'FFFFFFFF' }] }),
    });
    const [ext] = await slots();
    // Load is the external slot's first action; wait for it, then check.
    await hover(ext, 'Load');
    expect(screen.queryByRole('button', { name: 'Re-read RFID' })).toBeNull();
  });

  it('without printers:ams_rfid the re-read is disabled and sends nothing', async () => {
    permissions.denied = new Set(['printers:ams_rfid']);
    const user = userEvent.setup();
    const sent = setup({ status: statusWith([amsUnit()]) });

    const button = await hover((await slots())[0], 'Re-read RFID');
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('title', 'You do not have permission to re-read AMS RFID');
    await user.click(button);
    await settle();
    expect(sent).toEqual([]);
  });

  it('is disabled while the printer is RUNNING', async () => {
    const sent = setup({ status: statusWith([amsUnit()], { state: 'RUNNING' }) });
    const button = await hover((await slots())[0], 'Re-read RFID');
    expect(button).toBeDisabled();
    await settle();
    expect(sent).toEqual([]);
  });
});

describe('AMS slot: Unassign routes by inventory mode', () => {
  beforeEach(() => {
    permissions.denied = new Set();
  });

  it('built-in inventory: DELETE /inventory/assignments/{printer}/{ams}/{tray}', async () => {
    const user = userEvent.setup();
    const sent = setup({ status: statusWith([amsUnit()]) });

    await user.click(await hover((await slots())[1], 'Unassign'));
    await waitFor(() =>
      expect(sent).toEqual([
        { method: 'DELETE', path: `/api/v1/inventory/assignments/${PRINTER_ID}/0/1`, search: '', body: null },
      ])
    );
  });

  it('Spoolman mode: DELETE /spoolman/inventory/slot-assignments/{spoolmanSpoolId}', async () => {
    const user = userEvent.setup();
    const sent = setup({ status: statusWith([amsUnit()]), spoolman: true });

    await user.click(await hover((await slots())[1], 'Unassign'));
    await waitFor(() =>
      expect(sent).toEqual([
        { method: 'DELETE', path: '/api/v1/spoolman/inventory/slot-assignments/55', search: '', body: null },
      ])
    );
  });

  it('an unassigned slot offers no Unassign', async () => {
    setup({ status: statusWith([amsUnit()]) });
    await hover((await slots())[2], 'Re-read RFID');
    expect(screen.queryByRole('button', { name: 'Unassign' })).toBeNull();
  });
});

describe('AMS unit: backup, drying, friendly name', () => {
  beforeEach(() => {
    permissions.denied = new Set();
  });

  it('AMS Filament Backup toggle POSTs ams-backup?enabled=true when off', async () => {
    const user = userEvent.setup();
    const sent = setup({ status: statusWith([amsUnit()], { ams_filament_backup: false }) });

    await user.click(await screen.findByTitle('AMS Filament Backup is OFF. Click to enable.'));
    await user.click(await screen.findByRole('switch'));
    await waitFor(() =>
      expect(sent).toEqual([
        { method: 'POST', path: `/api/v1/printers/${PRINTER_ID}/ams-backup`, search: '?enabled=true', body: null },
      ])
    );
  });

  it('AMS Filament Backup toggle POSTs enabled=false when on', async () => {
    const user = userEvent.setup();
    const sent = setup({ status: statusWith([amsUnit()], { ams_filament_backup: true }) });

    await user.click(await screen.findByTitle('AMS Filament Backup is ON. Click to disable.'));
    await user.click(await screen.findByRole('switch'));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0].search).toBe('?enabled=false');
  });

  it('AMS Filament Backup switch is disabled without printers:control', async () => {
    permissions.denied = new Set(['printers:control']);
    const user = userEvent.setup();
    const sent = setup({ status: statusWith([amsUnit()], { ams_filament_backup: false }) });

    await user.click(await screen.findByTitle('AMS Filament Backup is OFF. Click to enable.'));
    const toggle = await screen.findByRole('switch');
    expect(toggle).toBeDisabled();
    await user.click(toggle);
    await settle();
    expect(sent).toEqual([]);
  });

  it('Stop Drying on a drying AMS 2 Pro POSTs drying/stop with its ams id', async () => {
    const user = userEvent.setup();
    const sent = setup({
      status: statusWith([amsUnit({ id: 1, module_type: 'n3f', dry_time: 120, serial_number: 'N3F1' })], {
        supports_drying: true,
      }),
    });

    const [stop] = await screen.findAllByTitle('Stop Drying');
    await user.click(stop);
    await waitFor(() =>
      expect(sent).toEqual([
        { method: 'POST', path: `/api/v1/printers/${PRINTER_ID}/drying/stop`, search: '?ams_id=1', body: null },
      ])
    );
  });

  async function openNameCard() {
    // The unit label ("AMS-A" style, or the saved friendly name) is the hover trigger.
    const label = await screen.findByText(/^(AMS[- ]?A|Left box)$/);
    fireEvent.mouseEnter(label.parentElement!);
    return (await screen.findByText('AMS0SERIAL')).closest('div.w-52') as HTMLElement;
  }

  it('saving a friendly name PUTs the trimmed label with the unit serial', async () => {
    const user = userEvent.setup();
    const sent = setup({ status: statusWith([amsUnit()]) });

    const card = await openNameCard();
    await user.type(within(card).getByPlaceholderText('e.g. AMS Friendly Name'), '  Left box  ');
    await user.click(within(card).getByRole('button', { name: 'Save' }));

    await waitFor(() =>
      expect(sent).toEqual([
        {
          method: 'PUT',
          path: `/api/v1/printers/${PRINTER_ID}/ams-labels/0`,
          search: '',
          body: { label: 'Left box', ams_serial: 'AMS0SERIAL' },
        },
      ])
    );
  });

  it('Clear DELETEs the label for that unit serial', async () => {
    const user = userEvent.setup();
    const sent = setup({ status: statusWith([amsUnit()]), amsLabels: { 0: 'Left box' } });

    const card = await openNameCard();
    await user.click(within(card).getByRole('button', { name: 'Clear' }));

    await waitFor(() =>
      expect(sent).toEqual([
        {
          method: 'DELETE',
          path: `/api/v1/printers/${PRINTER_ID}/ams-labels/0`,
          search: '?ams_serial=AMS0SERIAL',
          body: null,
        },
      ])
    );
  });

  it('saving an emptied name DELETEs instead of saving a blank label', async () => {
    const user = userEvent.setup();
    const sent = setup({ status: statusWith([amsUnit()]), amsLabels: { 0: 'Left box' } });

    const card = await openNameCard();
    const input = within(card).getByPlaceholderText('e.g. AMS Friendly Name');
    await user.clear(input);
    await user.type(input, '   ');
    await user.click(within(card).getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({ method: 'DELETE', search: '?ams_serial=AMS0SERIAL' });
  });

  it('without printers:update the name field is read-only and has no Save', async () => {
    permissions.denied = new Set(['printers:update']);
    const sent = setup({ status: statusWith([amsUnit()]) });

    const card = await openNameCard();
    expect(within(card).getByRole('textbox')).toBeDisabled();
    expect(within(card).queryByRole('button', { name: 'Save' })).toBeNull();
    await settle();
    expect(sent).toEqual([]);
  });
});

describe('AMS slot: Configure dialog', () => {
  beforeEach(() => {
    permissions.denied = new Set();
  });

  // The dialog refuses a slot reset while a print is loaded; it gets the card's state.
  it.each([
    ['RUNNING', true],
    ['IDLE', false],
  ])('while the printer is %s, Reset Slot disabled: %s', async (state, disabled) => {
    const user = userEvent.setup();
    setup({ status: statusWith([amsUnit()], { state }) });

    await user.click(await hover((await slots())[2], 'Configure'));

    const reset = await screen.findByRole('button', { name: /Reset Slot/ });
    if (disabled) expect(reset).toBeDisabled();
    else expect(reset).toBeEnabled();
  });
});
