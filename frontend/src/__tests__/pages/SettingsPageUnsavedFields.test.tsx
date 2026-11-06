/**
 * Two settings the page showed and let the user change but never saved:
 * "Max copies per run" (a 100-copy pipeline run stayed refused at 50) and the
 * Docker compose directory used in the update instructions.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { SettingsPage } from '../../pages/SettingsPage';
import { setAuthToken } from '../../api/client';

const settings = {
  auto_archive: true,
  save_thumbnails: true,
  capture_finish_photo: true,
  default_filament_cost: 25.0,
  currency: 'USD',
  time_format: 'system',
  date_format: 'system',
  check_updates: true,
  check_printer_firmware: false,
  pipeline_max_copies: 50,
  docker_compose_dir: '',
};

describe('SettingsPage fields that were never saved', () => {
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
      http.get('/api/v1/updates/check', () =>
        HttpResponse.json({
          update_available: true,
          current_version: '1.0.0',
          latest_version: '9.9.9',
          is_docker: true,
          compose_dir_detected: '',
        }),
      ),
      http.get('/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: false, requires_setup: false })),
    );
  });

  async function open(tab?: string) {
    const user = userEvent.setup();
    render(<SettingsPage />);
    await screen.findByText('Date Format');
    if (tab) await user.click(screen.getByText(tab));
    // The page suppresses auto-save right after the settings load.
    await new Promise((resolve) => setTimeout(resolve, 200));
    return user;
  }

  it('saves the maximum copies per pipeline run', async () => {
    await open('Workflow');

    const input = await screen.findByLabelText('Max copies per run');
    fireEvent.change(input, { target: { value: '200' } });

    await waitFor(() => expect(saves.at(-1)).toMatchObject({ pipeline_max_copies: 200 }), { timeout: 3000 });
  });

  it('saves the Docker compose directory', async () => {
    const user = await open();

    const field = await screen.findByPlaceholderText('/opt/bambuddy');
    await user.type(field, '/srv/bambuddy');

    await waitFor(() => expect(saves.at(-1)).toMatchObject({ docker_compose_dir: '/srv/bambuddy' }), {
      timeout: 3000,
    });
  });
});

describe('SettingsPage printer temperature history retention', () => {
  let saves: Record<string, unknown>[];

  beforeEach(() => {
    window.history.replaceState({}, '', '/');
    vi.mocked(localStorage.getItem).mockReset();
    localStorage.clear();
    setAuthToken(null);
    saves = [];
    const withRetention = { ...settings, ams_history_retention_days: 30, printer_sensor_history_retention_days: 30 };
    server.use(
      http.get('/api/v1/settings/', () => HttpResponse.json(withRetention)),
      http.put('/api/v1/settings/', async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        saves.push(body);
        return HttpResponse.json({ ...withRetention, ...body });
      }),
      http.get('/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: false, requires_setup: false })),
    );
  });

  it('can be set next to the AMS history and is saved', async () => {
    const user = userEvent.setup();
    render(<SettingsPage />);
    await screen.findByText('Date Format');
    await user.click(screen.getAllByText('Filament')[0]);
    await new Promise((resolve) => setTimeout(resolve, 200));

    const input = await screen.findByLabelText('Keep printer temperature history (nozzle, bed, chamber) for');
    fireEvent.change(input, { target: { value: '90' } });

    await waitFor(() => expect(saves.at(-1)).toMatchObject({ printer_sensor_history_retention_days: 90 }), {
      timeout: 3000,
    });
  });
});

/**
 * settings-2 / settings-3: "Save thumbnails" and "Notification Language" were
 * saved but nothing read them -- thumbnails were always stored, notifications
 * always used their template text. The controls are gone; the API keeps the
 * fields for scripts that send them.
 */
describe('SettingsPage controls that did nothing', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/');
    vi.mocked(localStorage.getItem).mockReset();
    localStorage.clear();
    setAuthToken(null);
    server.use(
      http.get('/api/v1/settings/', () => HttpResponse.json(settings)),
      http.get('/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: false, requires_setup: false })),
    );
  });

  it('has no "Save thumbnails" toggle', async () => {
    render(<SettingsPage />);
    expect(await screen.findByText('Capture finish photo')).toBeInTheDocument();
    expect(screen.queryByText('Save thumbnails')).not.toBeInTheDocument();
  });

  it('has no notification language picker', async () => {
    const user = userEvent.setup();
    render(<SettingsPage />);
    await screen.findByText('Date Format');
    await user.click(screen.getAllByText('Notifications')[0]);
    expect(await screen.findByRole('button', { name: /^Add$/ }, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.queryByText('Notification Language')).not.toBeInTheDocument();
  });
});
