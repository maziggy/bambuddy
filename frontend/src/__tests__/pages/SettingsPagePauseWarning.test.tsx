/**
 * How long before a slicer pause the "Pause Coming Up" notification goes out
 * (#2984): a setting next to the bed-cooled threshold, saved as whole minutes
 * within what the server accepts.
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
  bed_cooled_threshold: 35,
  pause_warning_minutes: 5,
  // Set, so the page has no detected address of its own to save with any edit.
  external_url: window.location.origin,
};

let saved: Array<Record<string, unknown>>;

beforeEach(() => {
  window.history.replaceState({}, '', '/?tab=notifications');
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
    http.get('/api/v1/notifications/', () => HttpResponse.json([])),
    http.get('/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: false, requires_setup: false })),
  );
});

async function field() {
  const input = (await screen.findByLabelText('Pause Heads-Up')) as HTMLInputElement;
  // Let the loaded settings land first; a change before that is overwritten.
  await new Promise(resolve => setTimeout(resolve, 200));
  return input;
}

describe('SettingsPage - pause heads-up minutes (#2984)', () => {
  it('shows the saved value, and saves nothing unchanged', async () => {
    render(<SettingsPage />);
    expect((await field()).value).toBe('5');
    await new Promise(resolve => setTimeout(resolve, 1000));
    expect(saved).toEqual([]);
  });

  it('saves a changed value', async () => {
    render(<SettingsPage />);
    fireEvent.change(await field(), { target: { value: '15' } });
    await waitFor(() => expect(saved.at(-1)?.pause_warning_minutes).toBe(15), { timeout: 3000 });
  });

  it('saves an out-of-range value within 1-120', async () => {
    render(<SettingsPage />);
    fireEvent.change(await field(), { target: { value: '500' } });
    await waitFor(() => expect(saved.at(-1)?.pause_warning_minutes).toBe(120), { timeout: 3000 });
  });

  it('saves an out-of-range value once, and shows what was saved on leaving the field', async () => {
    render(<SettingsPage />);
    const input = await field();
    fireEvent.change(input, { target: { value: '500' } });
    await waitFor(() => expect(saved).toHaveLength(1), { timeout: 3000 });
    // The server holds 120 now; 500 in the field must not read as a change
    // still to save, or the page saves it again and again.
    await new Promise(resolve => setTimeout(resolve, 1500));
    expect(saved).toHaveLength(1);
    fireEvent.blur(input);
    expect(input.value).toBe('120');
  });

  it('an emptied field is the default: nothing to save, and it shows 5 on leaving', async () => {
    render(<SettingsPage />);
    const input = await field();
    fireEvent.change(input, { target: { value: '' } });
    await new Promise(resolve => setTimeout(resolve, 1000));
    expect(saved).toEqual([]);
    fireEvent.blur(input);
    expect(input.value).toBe('5');
  });
});
