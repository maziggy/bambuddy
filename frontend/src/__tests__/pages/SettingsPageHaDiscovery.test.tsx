/**
 * Home Assistant discovery under MQTT (#3724): a switch that saves with the
 * other MQTT settings, and a discovery prefix that may not equal the topic
 * prefix.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { SettingsPage } from '../../pages/SettingsPage';
import { setAuthToken } from '../../api/client';

const baseSettings = {
  auto_archive: true,
  save_thumbnails: true,
  capture_finish_photo: true,
  default_filament_cost: 25.0,
  currency: 'USD',
  time_format: 'system',
  date_format: 'system',
  mqtt_enabled: true,
  mqtt_broker: 'broker.example',
  mqtt_port: 1883,
  mqtt_username: '',
  mqtt_password: '',
  mqtt_topic_prefix: 'bambuddy',
  mqtt_use_tls: false,
  mqtt_ha_discovery_enabled: false,
  mqtt_ha_discovery_prefix: 'homeassistant',
  panda_breath_enabled: false,
  ha_enabled: false,
  ha_url: '',
  ha_token: '',
  check_updates: false,
  check_printer_firmware: false,
};

describe('SettingsPage: Home Assistant discovery', () => {
  let saves: Record<string, unknown>[];

  function serve(overrides: Record<string, unknown> = {}) {
    const settings = { ...baseSettings, ...overrides };
    server.use(
      http.get('/api/v1/settings/', () => HttpResponse.json(settings)),
      http.put('/api/v1/settings/', async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        saves.push(body);
        return HttpResponse.json({ ...settings, ...body });
      }),
    );
  }

  beforeEach(() => {
    window.history.replaceState({}, '', '/');
    vi.mocked(localStorage.getItem).mockReset();
    localStorage.clear();
    setAuthToken(null);
    saves = [];
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([])),
      http.get('/api/v1/smart-plugs/', () => HttpResponse.json([])),
      http.get('/api/v1/notifications/', () => HttpResponse.json([])),
      http.get('/api/v1/api-keys/', () => HttpResponse.json([])),
      http.get('/api/v1/virtual-printer/status', () => HttpResponse.json({ running: false })),
      http.get('/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: false, requires_setup: false })),
      http.get('/api/v1/external-links/', () => HttpResponse.json([])),
    );
  });

  async function openNetwork() {
    const user = userEvent.setup();
    render(<SettingsPage />);
    await screen.findByText('Date Format');
    await user.click(screen.getByText('Network'));
    await screen.findByText('MQTT Publishing');
    // The page suppresses auto-save right after the settings load.
    await new Promise((resolve) => setTimeout(resolve, 200));
    return user;
  }

  it('is off by default and hides the prefix', async () => {
    serve();
    await openNetwork();

    expect(screen.getByRole('checkbox', { name: 'Home Assistant discovery' })).not.toBeChecked();
    expect(screen.queryByLabelText('Discovery prefix')).not.toBeInTheDocument();
  });

  it('switching it on saves mqtt_ha_discovery_enabled and shows the prefix', async () => {
    serve();
    const user = await openNetwork();

    await user.click(screen.getByRole('checkbox', { name: 'Home Assistant discovery' }));

    await waitFor(() => expect(saves.length).toBeGreaterThan(0), { timeout: 3000 });
    expect(saves.at(-1)).toMatchObject({
      mqtt_ha_discovery_enabled: true,
      mqtt_ha_discovery_prefix: 'homeassistant',
    });
    expect(screen.getByLabelText('Discovery prefix')).toHaveValue('homeassistant');
  });

  it('warns when the discovery prefix equals the topic prefix', async () => {
    serve({ mqtt_ha_discovery_enabled: true, mqtt_ha_discovery_prefix: 'bambuddy/' });
    await openNetwork();

    expect(screen.getByRole('alert')).toHaveTextContent('The discovery prefix must differ from the topic prefix.');
  });

  it('warns about a wildcard in the discovery prefix', async () => {
    serve({ mqtt_ha_discovery_enabled: true, mqtt_ha_discovery_prefix: 'home+assistant' });
    await openNetwork();

    expect(screen.getByRole('alert')).toHaveTextContent('The discovery prefix cannot contain + or #.');
  });

  it('does not save while the discovery prefix is refused, and saves once it is fixed', async () => {
    serve({ mqtt_ha_discovery_enabled: true });
    const user = await openNetwork();
    const field = screen.getByLabelText('Discovery prefix');

    await user.clear(field);
    await user.type(field, 'bambuddy');
    await new Promise((resolve) => setTimeout(resolve, 900));
    expect(saves).toEqual([]);
    expect(screen.getByRole('alert')).toBeInTheDocument();

    await user.type(field, '-ha');
    await waitFor(() => expect(saves.length).toBeGreaterThan(0), { timeout: 3000 });
    expect(saves.at(-1)).toMatchObject({ mqtt_ha_discovery_prefix: 'bambuddy-ha' });
  });

  it('shows no warning for different prefixes', async () => {
    serve({ mqtt_ha_discovery_enabled: true });
    await openNetwork();

    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});
