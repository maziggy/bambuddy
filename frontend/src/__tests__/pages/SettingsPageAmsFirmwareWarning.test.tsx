/**
 * The option to hide the AMS firmware mismatch warning, for a BMCU (#3269):
 * off by default, in the Filament tab next to the other filament checks, saved
 * as hide_ams_firmware_warning.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { render } from '../utils';
import { SettingsPage } from '../../pages/SettingsPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { setAuthToken } from '../../api/client';

const settings = {
  auto_archive: true,
  currency: 'USD',
  time_format: 'system',
  date_format: 'system',
  check_updates: false,
  check_printer_firmware: false,
  disable_filament_warnings: false,
  prefer_lowest_filament: false,
  hide_ams_firmware_warning: false,
  // Set, so the page has no detected address of its own to save with any edit.
  external_url: window.location.origin,
};

let saved: Array<Record<string, unknown>>;

beforeEach(() => {
  window.history.replaceState({}, '', '/?tab=filament');
  vi.mocked(localStorage.getItem).mockReset();
  setAuthToken(null);
  saved = [];
  server.use(
    http.get('/api/v1/settings/', () => HttpResponse.json(settings)),
    http.put('/api/v1/settings/', async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      saved.push(body);
      return HttpResponse.json({ ...settings, ...body });
    }),
    http.get('/api/v1/printers/', () => HttpResponse.json([])),
    http.get('/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: false, requires_setup: false })),
  );
});

async function toggle() {
  const label = await screen.findByText('Hide the AMS firmware warning');
  // Let the loaded settings land first; a change before that is overwritten.
  await new Promise(resolve => setTimeout(resolve, 200));
  return label.closest('div')!.parentElement!.querySelector('input[type="checkbox"]') as HTMLInputElement;
}

describe('SettingsPage - hide the AMS firmware warning (#3269)', () => {
  it('is off by default and explains what it hides', async () => {
    render(<SettingsPage />);

    expect((await toggle()).checked).toBe(false);
    expect(screen.getByText(/Print errors are never hidden/)).toBeInTheDocument();
  });

  it('saves the choice', async () => {
    render(<SettingsPage />);
    fireEvent.click(await toggle());

    await waitFor(() => expect(saved.some(body => body.hide_ams_firmware_warning === true)).toBe(true), {
      timeout: 4000,
    });
  });
});
