/**
 * Loaded filament first + maximum wait (#3587): a toggle and an hours field in
 * the Queue tab. Empty hours is "no limit" and must be saved as null.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { render } from '../utils';
import { SettingsPage } from '../../pages/SettingsPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { setAuthToken } from '../../api/client';

const base: Record<string, unknown> = {
  auto_archive: true,
  currency: 'USD',
  time_format: 'system',
  date_format: 'system',
  check_updates: false,
  check_printer_firmware: false,
  external_url: window.location.origin,
};

let saved: Array<Record<string, unknown>>;

function serve(current: Record<string, unknown>) {
  server.use(
    http.get('/api/v1/settings/', () => HttpResponse.json(current)),
    http.put('/api/v1/settings/', async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      saved.push(body);
      return HttpResponse.json({ ...current, ...body });
    }),
    http.get('/api/v1/printers/', () => HttpResponse.json([])),
    http.get('/api/v1/notifications/', () => HttpResponse.json([])),
    http.get('/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: false, requires_setup: false })),
  );
}

beforeEach(() => {
  window.history.replaceState({}, '', '/?tab=queue');
  vi.mocked(localStorage.getItem).mockReset();
  setAuthToken(null);
  saved = [];
});

async function controls() {
  const toggle = (await screen.findByLabelText('Run jobs whose filament is loaded first')) as HTMLInputElement;
  const hours = (await screen.findByLabelText('Maximum wait (hours)')) as HTMLInputElement;
  // Let the loaded settings land first; a change before that is overwritten.
  await new Promise((resolve) => setTimeout(resolve, 200));
  return { toggle, hours };
}

describe('SettingsPage - loaded filament first (#3587)', () => {
  it('toggle saves queue_loaded_filament_first: true', async () => {
    serve({ ...base, queue_loaded_filament_first: false, queue_max_wait_hours: 48 });
    render(<SettingsPage />);
    const { toggle } = await controls();
    expect(toggle.checked).toBe(false);

    fireEvent.click(toggle);

    await waitFor(() => expect(saved.at(-1)?.queue_loaded_filament_first).toBe(true), { timeout: 3000 });
  });

  it('disables the hours field while the toggle is off and enables it when on', async () => {
    serve({ ...base, queue_loaded_filament_first: false, queue_max_wait_hours: 48 });
    render(<SettingsPage />);
    const { toggle, hours } = await controls();
    expect(hours.disabled).toBe(true);

    fireEvent.click(toggle);

    await waitFor(() => expect(hours.disabled).toBe(false));
  });

  it('saves a typed number', async () => {
    serve({ ...base, queue_loaded_filament_first: true, queue_max_wait_hours: 48 });
    render(<SettingsPage />);
    const { hours } = await controls();

    fireEvent.change(hours, { target: { value: '12' } });

    await waitFor(() => expect(saved.at(-1)?.queue_max_wait_hours).toBe(12), { timeout: 3000 });
  });

  it('saves null when the field is cleared', async () => {
    serve({ ...base, queue_loaded_filament_first: true, queue_max_wait_hours: 48 });
    render(<SettingsPage />);
    const { hours } = await controls();

    fireEvent.change(hours, { target: { value: '' } });

    await waitFor(() => expect(saved.at(-1)?.queue_max_wait_hours).toBeNull(), { timeout: 3000 });
  });

  it('shows an empty field with the placeholder for a stored null, and saves nothing', async () => {
    serve({ ...base, queue_loaded_filament_first: true, queue_max_wait_hours: null });
    render(<SettingsPage />);
    const { hours } = await controls();

    expect(hours.value).toBe('');
    expect(hours.placeholder).toBe('No limit');
    await new Promise((resolve) => setTimeout(resolve, 800));
    expect(saved).toEqual([]);
  });

  it.each(['e', '-', '1e', '-5', '0', '9000', '10000', '24.5', '1e2'])('keeps %s as a draft, warns and sends no PUT', async (value) => {
    serve({ ...base, queue_loaded_filament_first: true, queue_max_wait_hours: 48 });
    render(<SettingsPage />);
    const { hours } = await controls();

    fireEvent.change(hours, { target: { value } });

    await screen.findByText('Enter a whole number of hours from 1 to 8760, or leave it empty for no limit.');
    expect(hours.value).toBe(value);
    await new Promise((resolve) => setTimeout(resolve, 1500));
    expect(saved.filter((b) => 'queue_max_wait_hours' in b)).toEqual([]);
    expect(saved).toEqual([]);

    fireEvent.change(hours, { target: { value: '24' } });
    await waitFor(() => expect(saved.at(-1)?.queue_max_wait_hours).toBe(24), { timeout: 3000 });
    expect(screen.queryByText(/Enter a whole number of hours/)).toBeNull();
  });

  it('shows 48 when the value is missing', async () => {
    serve({ ...base });
    render(<SettingsPage />);
    const { hours } = await controls();

    expect(hours.value).toBe('48');
  });
});
