/**
 * KProfilesView wiring: what goes to the printer when a K-profile is added
 * (single extruder / both extruders), edited, deleted (single and bulk) or
 * imported, and which controls the kprofiles:* permissions disable.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { KProfilesView } from '../../components/KProfilesView';
import { setAuthToken } from '../../api/client';

type Sent = { method: string; path: string; body: unknown };

const PROFILES = [
  { slot_id: 3, extruder_id: 0, nozzle_id: 'HS00-0.4', nozzle_diameter: '0.4', filament_id: 'GFA00', name: 'S Bambu PLA Basic', k_value: '0.020000', n_coef: '0', ams_id: 0, tray_id: 0, setting_id: 'PFUS-row-3' },
  { slot_id: 5, extruder_id: 0, nozzle_id: 'HH00-0.4', nozzle_diameter: '0.4', filament_id: 'GFG99', name: 'HF Generic PETG', k_value: '0.035000', n_coef: '0', ams_id: 0, tray_id: 0, setting_id: null },
  { slot_id: 7, extruder_id: 0, nozzle_id: 'HS00-0.4', nozzle_diameter: '0.4', filament_id: 'GFB99', name: 'S Zeta ABS', k_value: '0.040000', n_coef: '0', ams_id: 0, tray_id: 0, setting_id: null },
];

let sent: Sent[];
let nozzleCount = 1;
let detailFetched: string[];

async function record(request: Request) {
  let body: unknown = null;
  try {
    body = await request.json();
  } catch {
    body = null;
  }
  sent.push({ method: request.method, path: new URL(request.url).pathname, body });
}

const writes = () => sent.filter((s) => s.method !== 'GET' && !s.path.endsWith('/notes'));

beforeEach(() => {
  sent = [];
  detailFetched = [];
  nozzleCount = 1;
  localStorage.removeItem('bambusy_kprofiles_nozzle');
  server.use(
    http.get('*/api/v1/printers/', () =>
      HttpResponse.json([
        { id: 1, name: 'Wiring X1C', model: 'X1C', is_active: true, nozzle_count: nozzleCount, supports_nozzle_flow_type: true },
      ]),
    ),
    http.get('*/api/v1/printers/:id/kprofiles/', () => HttpResponse.json({ profiles: PROFILES, nozzle_diameter: '0.4' })),
    http.get('*/api/v1/printers/:id/kprofiles/notes', () => HttpResponse.json({ notes: {} })),
    http.get('*/api/v1/cloud/builtin-filaments', () =>
      HttpResponse.json([{ filament_id: 'GFA00', name: 'Bambu PLA Basic' }]),
    ),
    http.get('*/api/v1/cloud/filament-id-map', () => HttpResponse.json({})),
    http.get('*/api/v1/local-presets/', () => HttpResponse.json({ filament: [], printer: [], process: [] })),
    http.get('*/api/v1/orca-cloud/profiles', () => HttpResponse.json({ filament: [], printer: [], process: [] })),
    http.get('*/api/v1/cloud/settings', () =>
      HttpResponse.json({
        filament: [
          { setting_id: 'PFUS-test-1', name: 'My Custom PLA @BBL X1C', type: 'filament', version: null, user_id: 'u1', updated_time: null, is_custom: true },
          { setting_id: 'PFUS-test-2', name: 'Mystery PLA @BBL X1C', type: 'filament', version: null, user_id: 'u1', updated_time: null, is_custom: true },
        ],
        printer: [],
        process: [],
      }),
    ),
    http.get('*/api/v1/cloud/settings/:id', ({ params }) => {
      detailFetched.push(String(params.id));
      return HttpResponse.json(params.id === 'PFUS-test-1' ? { filament_id: 'P0000001' } : {});
    }),
    http.post('*/api/v1/printers/:id/kprofiles/', async ({ request }) => {
      await record(request);
      return HttpResponse.json({ success: true, confirmed: true, message: 'ok' });
    }),
    http.post('*/api/v1/printers/:id/kprofiles/batch', async ({ request }) => {
      await record(request);
      return HttpResponse.json({ success: true, confirmed: true, message: 'ok' });
    }),
    http.delete('*/api/v1/printers/:id/kprofiles/', async ({ request }) => {
      await record(request);
      return HttpResponse.json({ success: true, confirmed: true, message: 'ok' });
    }),
    http.post('*/api/v1/printers/:id/kprofiles/notes', async ({ request }) => {
      await record(request);
      return HttpResponse.json({ success: true, message: 'ok' });
    }),
  );
});

afterEach(() => {
  setAuthToken(null);
  vi.restoreAllMocks();
});

async function renderLoaded() {
  const user = userEvent.setup();
  render(<KProfilesView />);
  await screen.findByText('S Bambu PLA Basic');
  return user;
}

/** The edit modal's delete (trash) button sits right before its Cancel. */
function modalTrash(): HTMLElement {
  return screen.getByRole('button', { name: 'Cancel' }).previousElementSibling as HTMLElement;
}

function kInput(): HTMLInputElement {
  return screen.getByText('K-Value').parentElement!.querySelector('input') as HTMLInputElement;
}

describe('KProfilesView wiring: add', () => {
  it('a Bambu Cloud user preset is resolved via its detail and sent as one profile', async () => {
    const user = await renderLoaded();
    await user.click(screen.getByRole('button', { name: 'Add Profile' }));
    await screen.findByText('Add K-Profile');

    await user.click(await screen.findByRole('button', { name: 'My Custom PLA' }));
    const k = kInput();
    await user.clear(k);
    await user.type(k, '0.0251');
    await user.tab();
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(detailFetched).toEqual(['PFUS-test-1']);
    expect(writes()[0]).toEqual({
      method: 'POST',
      path: '/api/v1/printers/1/kprofiles/',
      body: {
        name: 'S My Custom PLA',
        k_value: '0.025000',
        filament_id: 'P0000001',
        nozzle_id: 'HS00-0.4',
        nozzle_diameter: '0.4',
        extruder_id: 0,
        slot_id: 0,
      },
    });
  });

  it('a preset with no resolvable filament id shows an error and sends nothing', async () => {
    const user = await renderLoaded();
    await user.click(screen.getByRole('button', { name: 'Add Profile' }));
    await user.click(await screen.findByRole('button', { name: 'Mystery PLA' }));
    await user.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByText(/No Bambu filament ID for Mystery PLA/)).toBeInTheDocument();
    expect(writes()).toEqual([]);
  });

  it('on a dual-nozzle printer both extruders go out in one batch request', async () => {
    nozzleCount = 2;
    const user = await renderLoaded();
    await user.click(screen.getByRole('button', { name: 'Add Profile' }));
    await user.click(await screen.findByRole('button', { name: 'Bambu PLA Basic' }));
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    const req = writes()[0];
    expect(req.method).toBe('POST');
    expect(req.path).toBe('/api/v1/printers/1/kprofiles/batch');
    const body = req.body as Array<Record<string, unknown>>;
    expect(body).toHaveLength(2);
    expect(body.map((p) => p.extruder_id).sort()).toEqual([0, 1]);
    for (const p of body) {
      expect(p).toMatchObject({
        name: 'S Bambu PLA Basic',
        k_value: '0.020000',
        filament_id: 'GFA00',
        nozzle_id: 'HS00-0.4',
        nozzle_diameter: '0.4',
        slot_id: 0,
      });
    }
  });

  it('on a dual-nozzle printer with one extruder unticked, a single profile for the other is sent', async () => {
    nozzleCount = 2;
    const user = await renderLoaded();
    await user.click(screen.getByRole('button', { name: 'Add Profile' }));
    await user.click(await screen.findByRole('button', { name: 'Bambu PLA Basic' }));
    await user.click(screen.getByRole('checkbox', { name: 'Left' }));
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0].path).toBe('/api/v1/printers/1/kprofiles/');
    expect(writes()[0].body).toMatchObject({ extruder_id: 0, filament_id: 'GFA00', slot_id: 0 });
  });
});

describe('KProfilesView wiring: edit and delete one profile', () => {
  it('edit keeps the row\'s slot_id, setting_id, extruder and nozzle', async () => {
    const user = await renderLoaded();
    await user.click(screen.getByText('S Bambu PLA Basic'));
    await screen.findByText('Edit K-Profile');

    const k = kInput();
    await user.clear(k);
    await user.type(k, '0.031');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toEqual({
      method: 'POST',
      path: '/api/v1/printers/1/kprofiles/',
      body: {
        name: 'S Bambu PLA Basic',
        k_value: '0.031000',
        filament_id: 'GFA00',
        nozzle_id: 'HS00-0.4',
        nozzle_diameter: '0.4',
        extruder_id: 0,
        setting_id: 'PFUS-row-3',
        slot_id: 3,
      },
    });
  });

  async function openDeleteConfirm(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByText('S Bambu PLA Basic'));
    await screen.findByText('Edit K-Profile');
    const cancel = screen.getByRole('button', { name: 'Cancel' });
    await user.click(cancel.previousElementSibling as HTMLElement);
    await screen.findByText('Delete Profile');
  }

  it('delete sends the identity of exactly that row', async () => {
    const user = await renderLoaded();
    await openDeleteConfirm(user);
    await user.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toEqual({
      method: 'DELETE',
      path: '/api/v1/printers/1/kprofiles/',
      body: {
        slot_id: 3,
        extruder_id: 0,
        nozzle_id: 'HS00-0.4',
        nozzle_diameter: '0.4',
        filament_id: 'GFA00',
        setting_id: 'PFUS-row-3',
      },
    });
  });

  it('cancelling the delete confirm sends nothing', async () => {
    const user = await renderLoaded();
    await openDeleteConfirm(user);
    await user.click(screen.getAllByRole('button', { name: 'Cancel' }).at(-1)!);

    await waitFor(() => expect(screen.queryByText('Delete Profile')).not.toBeInTheDocument());
    expect(writes()).toEqual([]);
  });
});

describe('KProfilesView wiring: bulk delete', () => {
  async function selectTwo(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole('button', { name: 'Select' }));
    await user.click(screen.getByText('S Bambu PLA Basic'));
    await user.click(screen.getByText('S Zeta ABS'));
    await user.click(screen.getByRole('button', { name: /Delete \(2\)/ }));
    await screen.findByText('Delete Profiles');
  }

  it('deletes only the selected rows, each with its own identity', async () => {
    const user = await renderLoaded();
    await selectTwo(user);
    const dialog = screen.getByText('Delete Profiles').closest('.fixed') as HTMLElement;
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(writes()).toHaveLength(2), { timeout: 3000 });
    const bodies = writes().map((w) => {
      expect(w.method).toBe('DELETE');
      expect(w.path).toBe('/api/v1/printers/1/kprofiles/');
      return w.body as Record<string, unknown>;
    });
    expect(bodies.map((b) => b.slot_id).sort()).toEqual([3, 7]);
    expect(bodies.find((b) => b.slot_id === 7)).toEqual({
      slot_id: 7,
      extruder_id: 0,
      nozzle_id: 'HS00-0.4',
      nozzle_diameter: '0.4',
      filament_id: 'GFB99',
      setting_id: null,
    });
  });

  it('cancelling the bulk confirm sends nothing', async () => {
    const user = await renderLoaded();
    await selectTwo(user);
    const dialog = screen.getByText('Delete Profiles').closest('.fixed') as HTMLElement;
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByText('Delete Profiles')).not.toBeInTheDocument());
    expect(writes()).toEqual([]);
  });
});

describe('KProfilesView wiring: import', () => {
  it('sends one new profile per complete row and skips rows missing name/k_value/filament_id', async () => {
    let fileInput: HTMLInputElement | null = null;
    const realCreate = document.createElement.bind(document);
    vi.spyOn(document, 'createElement').mockImplementation(((tag: string, options?: ElementCreationOptions) => {
      const el = realCreate(tag, options);
      if (tag === 'input') {
        fileInput = el as HTMLInputElement;
        vi.spyOn(el, 'click').mockImplementation(() => undefined);
      }
      return el;
    }) as typeof document.createElement);

    const user = await renderLoaded();
    await user.click(screen.getByRole('button', { name: 'Import' }));
    expect(fileInput).not.toBeNull();

    const data = {
      version: 1,
      profiles: [
        { name: 'Imported A', k_value: '0.02', filament_id: 'GFA00', nozzle_id: 'HH00-0.4', nozzle_diameter: '0.4', extruder_id: 0 },
        { name: 'No filament', k_value: '0.03' },
        { name: 'Imported B', k_value: '0.045', filament_id: 'GFB99' },
      ],
    };
    // jsdom's File has no text(); the handler only calls that.
    const file = { name: 'kprofiles.json', text: async () => JSON.stringify(data) };
    const input = fileInput as unknown as HTMLInputElement;
    Object.defineProperty(input, 'files', { value: [file] });
    await (input.onchange as unknown as (e: { target: HTMLInputElement }) => Promise<void>)({ target: input });

    await waitFor(() => expect(writes()).toHaveLength(2), { timeout: 3000 });
    expect(writes().map((w) => w.body)).toEqual([
      { name: 'Imported A', k_value: '0.020000', filament_id: 'GFA00', nozzle_id: 'HH00-0.4', nozzle_diameter: '0.4', extruder_id: 0, slot_id: 0 },
      { name: 'Imported B', k_value: '0.045000', filament_id: 'GFB99', nozzle_id: 'HS00-0.4', nozzle_diameter: '0.4', extruder_id: 0, slot_id: 0 },
    ]);
  });
});

describe('KProfilesView wiring: permissions', () => {
  function signInWith(permissions: string[]) {
    setAuthToken('test-token-1', 'session');
    server.use(
      http.get('*/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: true, requires_setup: false })),
      http.get('*/api/v1/auth/me', () =>
        HttpResponse.json({
          id: 9,
          username: 'viewer',
          is_active: true,
          is_admin: false,
          groups: [],
          permissions,
          created_at: '2026-01-01T00:00:00Z',
        }),
      ),
    );
  }

  it('read-only: add, import, select, save and delete are disabled', async () => {
    signInWith(['kprofiles:read']);
    const user = await renderLoaded();

    await waitFor(() => expect(screen.getByRole('button', { name: 'Add Profile' })).toBeDisabled());
    expect(screen.getByRole('button', { name: 'Import' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Select' })).toBeDisabled();

    await user.click(screen.getByText('S Bambu PLA Basic'));
    await screen.findByText('Edit K-Profile');
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    expect(modalTrash()).toBeDisabled();
    expect(writes()).toEqual([]);
  });

  it('kprofiles:update enables Save on an edit but not delete or add', async () => {
    signInWith(['kprofiles:read', 'kprofiles:update']);
    const user = await renderLoaded();

    await waitFor(() => expect(screen.getByRole('button', { name: 'Add Profile' })).toBeDisabled());
    await user.click(screen.getByText('S Bambu PLA Basic'));
    await screen.findByText('Edit K-Profile');
    expect(screen.getByRole('button', { name: 'Save' })).not.toBeDisabled();
    expect(modalTrash()).toBeDisabled();
  });
});
