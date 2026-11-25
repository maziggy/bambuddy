/**
 * ProfilesPage wiring (Bambu Cloud tab): preset create / edit / delete write
 * to the user's Bambu Cloud account, so pin exactly what each one sends, and
 * the cloud sign-in / token / logout requests.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { ProfilesPage } from '../../pages/ProfilesPage';
import { setAuthToken } from '../../api/client';

type Sent = { method: string; path: string; body: unknown };

const USER_PRESET = {
  setting_id: 'PFUS-wiring-1',
  name: 'My Wiring PLA @BBL X1C',
  type: 'filament',
  version: null,
  user_id: 'u1',
  updated_time: null,
  is_custom: true,
};
const BUILTIN_PRESET = {
  setting_id: 'GFSA00',
  name: 'Bambu PLA Basic @BBL X1C',
  type: 'filament',
  version: null,
  user_id: null,
  updated_time: null,
  is_custom: false,
};

let sent: Sent[];
let authenticated: boolean;

async function record(request: Request) {
  let body: unknown = null;
  try {
    body = await request.json();
  } catch {
    body = null;
  }
  sent.push({ method: request.method, path: new URL(request.url).pathname, body });
}

const writes = () => sent.filter((s) => s.method !== 'GET');

beforeEach(() => {
  sent = [];
  authenticated = true;
  server.use(
    http.get('*/api/v1/cloud/status', () =>
      HttpResponse.json(
        authenticated
          ? { is_authenticated: true, email: 'user@example.com', region: 'global' }
          : { is_authenticated: false, email: null },
      ),
    ),
    http.get('*/api/v1/printers/', () => HttpResponse.json([])),
    http.get('*/api/v1/cloud/settings', () =>
      HttpResponse.json({ filament: [USER_PRESET, BUILTIN_PRESET], printer: [], process: [] }),
    ),
    http.get('*/api/v1/cloud/settings/:id', ({ params }) =>
      HttpResponse.json({
        setting_id: params.id,
        name: params.id === USER_PRESET.setting_id ? USER_PRESET.name : BUILTIN_PRESET.name,
        base_id: 'GFSA00',
        setting: { inherits: BUILTIN_PRESET.name, nozzle_temperature: ['220'] },
      }),
    ),
    http.get('*/api/v1/cloud/fields/:type', () =>
      HttpResponse.json({ version: '1', description: '', fields: [] }),
    ),
    http.post('*/api/v1/cloud/settings', async ({ request }) => {
      await record(request);
      return HttpResponse.json({ setting_id: 'PFUS-new' });
    }),
    http.put('*/api/v1/cloud/settings/:id', async ({ request }) => {
      await record(request);
      return HttpResponse.json({ setting_id: USER_PRESET.setting_id });
    }),
    http.delete('*/api/v1/cloud/settings/:id', async ({ request }) => {
      await record(request);
      return HttpResponse.json({ success: true });
    }),
    http.post('*/api/v1/cloud/login', async ({ request }) => {
      await record(request);
      return HttpResponse.json({
        success: false,
        needs_verification: true,
        verification_type: 'email',
        tfa_key: null,
        message: 'Code sent',
      });
    }),
    http.post('*/api/v1/cloud/verify', async ({ request }) => {
      await record(request);
      return HttpResponse.json({ success: true, needs_verification: false, message: 'ok' });
    }),
    http.post('*/api/v1/cloud/token', async ({ request }) => {
      await record(request);
      return HttpResponse.json({ is_authenticated: true, email: 'user@example.com' });
    }),
    http.post('*/api/v1/cloud/logout', async ({ request }) => {
      await record(request);
      return HttpResponse.json({ success: true });
    }),
  );
});

afterEach(() => {
  setAuthToken(null);
});

async function openUserPreset(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByText(USER_PRESET.name));
  // The detail modal's Edit is enabled once the detail has loaded.
  const edit = await screen.findByRole('button', { name: 'Edit' });
  await waitFor(() => expect(edit).not.toBeDisabled());
  return edit;
}

describe('ProfilesPage wiring: cloud presets', () => {
  it('New Preset -> Create sends createCloudSetting with type, name, base_id and the settings', async () => {
    const user = userEvent.setup();
    render(<ProfilesPage />);

    await user.click(await screen.findByRole('button', { name: 'New Preset' }));
    await screen.findByText('Create New Preset');
    const baseSelect = screen.getByText('Base Preset').parentElement!.querySelector('select')!;
    await user.selectOptions(baseSelect, 'GFSA00');
    await user.type(screen.getByText('Preset Name').parentElement!.querySelector('input')!, 'Wiring PLA Fast');
    const create = screen.getByRole('button', { name: 'Create' });
    await waitFor(() => expect(create).not.toBeDisabled());
    await user.click(create);

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toEqual({
      method: 'POST',
      path: '/api/v1/cloud/settings',
      body: {
        type: 'filament',
        name: 'Wiring PLA Fast',
        base_id: 'GFSA00',
        setting: { inherits: BUILTIN_PRESET.name, filament_settings_id: '"Wiring PLA Fast"' },
      },
    });
  });

  it('Create stays disabled without a base preset, so nothing is sent', async () => {
    const user = userEvent.setup();
    render(<ProfilesPage />);

    await user.click(await screen.findByRole('button', { name: 'New Preset' }));
    await screen.findByText('Create New Preset');
    await user.type(screen.getByText('Preset Name').parentElement!.querySelector('input')!, 'No Base');
    expect(screen.getByRole('button', { name: 'Create' })).toBeDisabled();
    expect(writes()).toEqual([]);
  });

  it('Edit -> Save sends updateCloudSetting(setting_id, { name, setting })', async () => {
    const user = userEvent.setup();
    render(<ProfilesPage />);

    await user.click(await openUserPreset(user));
    const nameInput = await screen.findByDisplayValue(USER_PRESET.name);
    await user.clear(nameInput);
    await user.type(nameInput, 'Renamed Wiring PLA');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toEqual({
      method: 'PUT',
      path: '/api/v1/cloud/settings/PFUS-wiring-1',
      body: {
        name: 'Renamed Wiring PLA',
        setting: { inherits: BUILTIN_PRESET.name, nozzle_temperature: ['220'] },
      },
    });
  });

  it('Delete -> confirm sends deleteCloudSetting for that preset', async () => {
    const user = userEvent.setup();
    render(<ProfilesPage />);

    const edit = await openUserPreset(user);
    await user.click(edit.nextElementSibling as HTMLElement);
    expect(await screen.findByText('Delete this preset?')).toBeInTheDocument();
    expect(writes()).toEqual([]);
    await user.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ method: 'DELETE', path: '/api/v1/cloud/settings/PFUS-wiring-1' });
  });

  it('Delete -> cancel sends nothing', async () => {
    const user = userEvent.setup();
    render(<ProfilesPage />);

    const edit = await openUserPreset(user);
    await user.click(edit.nextElementSibling as HTMLElement);
    await screen.findByText('Delete this preset?');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByText('Delete this preset?')).not.toBeInTheDocument());
    expect(writes()).toEqual([]);
  });

  it('built-in presets offer no edit or delete', async () => {
    const user = userEvent.setup();
    render(<ProfilesPage />);

    await user.click(await screen.findByText(BUILTIN_PRESET.name));
    await screen.findByRole('button', { name: 'Close' });
    expect(screen.queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument();
  });
});

describe('ProfilesPage wiring: cloud sign-in', () => {
  it('login then verify send the credentials, the code and the region', async () => {
    authenticated = false;
    const user = userEvent.setup();
    render(<ProfilesPage />);

    await user.type(await screen.findByPlaceholderText('your@email.com'), 'user@example.com');
    await user.type(screen.getByPlaceholderText('••••••••'), 'test-password-1');
    await user.selectOptions(screen.getByRole('combobox'), 'china');
    await user.click(screen.getByRole('button', { name: 'Login' }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toEqual({
      method: 'POST',
      path: '/api/v1/cloud/login',
      body: { email: 'user@example.com', password: 'test-password-1', region: 'china' },
    });

    await user.type(await screen.findByPlaceholderText('000000'), '123456');
    await user.click(screen.getByRole('button', { name: 'Verify' }));

    await waitFor(() => expect(writes()).toHaveLength(2));
    expect(writes()[1]).toEqual({
      method: 'POST',
      path: '/api/v1/cloud/verify',
      body: { email: 'user@example.com', code: '123456', region: 'china' },
    });
  });

  it('the token route sends cloudSetToken with the token and region', async () => {
    authenticated = false;
    const user = userEvent.setup();
    render(<ProfilesPage />);

    await user.click(await screen.findByRole('button', { name: 'Use access token instead' }));
    await user.type(await screen.findByPlaceholderText('eyJ...'), 'test-token-1');
    await user.click(screen.getByRole('button', { name: 'Set Token' }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toEqual({
      method: 'POST',
      path: '/api/v1/cloud/token',
      body: { access_token: 'test-token-1', region: 'global' },
    });
  });

  it('Logout sends cloudLogout', async () => {
    const user = userEvent.setup();
    render(<ProfilesPage />);

    await user.click(await screen.findByRole('button', { name: 'Logout' }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ method: 'POST', path: '/api/v1/cloud/logout' });
  });
});

describe('ProfilesPage wiring: permissions', () => {
  it('without cloud:auth, New Preset, Logout, Edit and Delete are disabled', async () => {
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
          permissions: ['cloud:read'],
          created_at: '2026-01-01T00:00:00Z',
        }),
      ),
    );
    const user = userEvent.setup();
    render(<ProfilesPage />);

    await waitFor(() => expect(screen.getByRole('button', { name: 'New Preset' })).toBeDisabled());
    expect(screen.getByRole('button', { name: 'Logout' })).toBeDisabled();

    await user.click(screen.getByText(USER_PRESET.name));
    const edit = await screen.findByRole('button', { name: 'Edit' });
    expect(edit).toBeDisabled();
    expect(edit.nextElementSibling).toBeDisabled();
    expect(writes()).toEqual([]);
  });
});
