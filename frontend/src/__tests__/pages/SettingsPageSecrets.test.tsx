/**
 * Saved settings secrets come back as a placeholder. Kept, they are not sent
 * anywhere new: changing the MQTT broker holds the auto-save until the
 * password is entered again, and the field says so.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { SettingsPage } from '../../pages/SettingsPage';
import { setAuthToken } from '../../api/client';

const M = '********';

const settings = {
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
  mqtt_username: 'u',
  mqtt_password: M,
  mqtt_topic_prefix: 'bambuddy',
  mqtt_use_tls: false,
  ha_enabled: false,
  ha_url: '',
  ha_token: '',
  check_updates: false,
  check_printer_firmware: false,
};

describe('SettingsPage saved secrets', () => {
  let saves: Record<string, unknown>[];

  beforeEach(() => {
    window.history.replaceState({}, '', '/');
    vi.mocked(localStorage.getItem).mockReset();
    localStorage.clear();
    setAuthToken(null);
    saves = [];
    server.use(
      http.get('/api/v1/settings/', () => HttpResponse.json(settings)),
      http.put('/api/v1/settings/', async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        saves.push(body);
        return HttpResponse.json({ ...settings, ...body });
      }),
      http.get('/api/v1/printers/', () => HttpResponse.json([])),
      http.get('/api/v1/smart-plugs/', () => HttpResponse.json([])),
      http.get('/api/v1/notifications/', () => HttpResponse.json([])),
      http.get('/api/v1/api-keys/', () => HttpResponse.json([])),
      http.get('/api/v1/mqtt/status', () => HttpResponse.json({ enabled: true })),
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

  it('saves a new Home Assistant URL when the token comes from the environment', async () => {
    server.use(
      http.get('/api/v1/settings/', () =>
        HttpResponse.json({ ...settings, ha_enabled: true, ha_url: 'http://ha.example:8123', ha_token: M, ha_token_from_env: true }),
      ),
    );
    const user = userEvent.setup();
    render(<SettingsPage />);
    await screen.findByText('Date Format');
    await user.click(screen.getByText('Network'));
    await new Promise((resolve) => setTimeout(resolve, 200));

    const url = await screen.findByDisplayValue('http://ha.example:8123');
    await user.clear(url);
    await user.type(url, 'http://ha.new.example:8123');

    await waitFor(() => expect(saves.length).toBeGreaterThan(0), { timeout: 3000 });
    expect(saves.at(-1)).toMatchObject({ ha_url: 'http://ha.new.example:8123' });
  });

  it('says the saved password is kept', async () => {
    await openNetwork();

    expect(await screen.findByText(/leave it as is to keep it/i)).toBeInTheDocument();
  });

  it('holds the save and asks for the password again when the broker changes', async () => {
    const user = await openNetwork();

    const broker = screen.getByPlaceholderText('mqtt.example.com or 192.168.1.100');
    await user.clear(broker);
    await user.type(broker, 'broker.elsewhere.example');

    expect(await screen.findByText(/the address changed: enter it again/i)).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 1200));
    expect(saves).toEqual([]);

    const password = screen
      .getAllByPlaceholderText('Leave empty for anonymous')
      .find((el) => el.getAttribute('type') === 'password')!;
    await user.click(password);
    await user.keyboard('new-pw');

    await waitFor(() => expect(saves.length).toBeGreaterThan(0), { timeout: 3000 });
    expect(saves.at(-1)).toMatchObject({ mqtt_broker: 'broker.elsewhere.example', mqtt_password: 'new-pw' });
  });
});
