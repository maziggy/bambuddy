/**
 * Keep bed warm follows each printer's plate-clear value (#2860), so it can be
 * switched on while the app's "Require plate-clear confirmation" is off: a
 * printer set to On still waits for its plate.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { render } from '../utils';
import { SettingsPage } from '../../pages/SettingsPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { setAuthToken } from '../../api/client';

const settings: Record<string, unknown> = {
  auto_archive: true,
  currency: 'USD',
  time_format: 'system',
  date_format: 'system',
  check_updates: false,
  check_printer_firmware: false,
  external_url: window.location.origin,
  preheat_enabled: true,
  require_plate_clear: false,
  queue_keep_bed_warm: false,
};

function serve(current: Record<string, unknown>) {
  server.use(
    http.get('/api/v1/settings/', () => HttpResponse.json(current)),
    http.get('/api/v1/printers/', () => HttpResponse.json([])),
    http.get('/api/v1/notifications/', () => HttpResponse.json([])),
    http.get('/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: false, requires_setup: false })),
  );
}

beforeEach(() => {
  window.history.replaceState({}, '', '/?tab=queue');
  vi.mocked(localStorage.getItem).mockReset();
  setAuthToken(null);
});

async function keepWarmToggle(): Promise<HTMLInputElement> {
  const title = await screen.findByText('Keep bed warm between prints');
  // Let the loaded settings land first.
  await new Promise((resolve) => setTimeout(resolve, 200));
  return title.closest('div.flex')!.querySelector('input[type="checkbox"]') as HTMLInputElement;
}

describe('SettingsPage - keep bed warm with per-printer plate-clear (#2860)', () => {
  it('can be switched on with the app setting off', async () => {
    serve(settings);
    render(<SettingsPage />);

    expect((await keepWarmToggle()).disabled).toBe(false);
  });

  it('still needs preheat', async () => {
    serve({ ...settings, preheat_enabled: false });
    render(<SettingsPage />);

    expect((await keepWarmToggle()).disabled).toBe(true);
  });
});
