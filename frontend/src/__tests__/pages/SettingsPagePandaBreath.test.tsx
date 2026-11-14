/**
 * The BIQU Panda Breath switch under MQTT (#1466): it saves with the other
 * MQTT settings, and once on, lists each heater with the printer it is bound to.
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
  ha_enabled: false,
  ha_url: '',
  ha_token: '',
  check_updates: false,
  check_printer_firmware: false,
};

describe('SettingsPage: Panda Breath', () => {
  let saves: Record<string, unknown>[];

  function serve(panda_breath_enabled: boolean) {
    const settings = { ...baseSettings, panda_breath_enabled };
    server.use(
      http.get('/api/v1/settings/', () => HttpResponse.json(settings)),
      http.put('/api/v1/settings/', async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        saves.push(body);
        return HttpResponse.json({ ...settings, ...body });
      }),
      http.get('/api/v1/settings/panda-breath/status', () =>
        HttpResponse.json({
          enabled: true,
          connected: true,
          devices: [
            { device_id: 'AABBCC', online: true, bound: true, printer_id: 3, printer_name: 'Workshop X1C' },
            { device_id: 'DDEEFF', online: false, bound: false, printer_id: null, printer_name: null },
          ],
        })
      ),
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

  it('switching it on saves panda_breath_enabled', async () => {
    serve(false);
    const user = await openNetwork();

    await user.click(screen.getByRole('checkbox', { name: 'BIQU Panda Breath' }));

    await waitFor(() => expect(saves.length).toBeGreaterThan(0), { timeout: 3000 });
    expect(saves.at(-1)).toMatchObject({ panda_breath_enabled: true });
  });

  it('lists each heater with the printer it is bound to', async () => {
    serve(true);
    await openNetwork();

    expect(await screen.findByText('AABBCC')).toBeInTheDocument();
    expect(screen.getByText('bound to Workshop X1C')).toBeInTheDocument();
    expect(screen.getByText('not bound to a printer')).toBeInTheDocument();
  });

  it('switched off, it lists nothing', async () => {
    serve(false);
    await openNetwork();

    expect(screen.getByRole('checkbox', { name: 'BIQU Panda Breath' })).not.toBeChecked();
    expect(screen.queryByText('AABBCC')).not.toBeInTheDocument();
  });

  it('switching it off hides the list', async () => {
    serve(true);
    const user = await openNetwork();
    await screen.findByText('AABBCC');

    await user.click(screen.getByRole('checkbox', { name: 'BIQU Panda Breath' }));

    await waitFor(() => expect(screen.queryByText('AABBCC')).not.toBeInTheDocument(), { timeout: 3000 });
  });
});
