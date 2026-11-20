/**
 * Printer order for any-model jobs (#2915): a select in the Workflow tab, saved
 * like the other settings, defaulting to fewest print hours.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
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

async function select() {
  const el = (await screen.findByLabelText('Pick the printer')) as HTMLSelectElement;
  // Let the loaded settings land first; a change before that is overwritten.
  await new Promise((resolve) => setTimeout(resolve, 200));
  return el;
}

describe('SettingsPage - printer order for any-model jobs (#2915)', () => {
  it('shows fewest print hours when nothing is set, and saves nothing', async () => {
    serve(settings);
    render(<SettingsPage />);

    expect((await select()).value).toBe('fewest_hours');
    await new Promise((resolve) => setTimeout(resolve, 800));
    expect(saved).toEqual([]);
  });

  it('offers the four strategies', async () => {
    serve(settings);
    render(<SettingsPage />);

    const options = Array.from((await select()).options).map((o) => o.value);
    expect(options).toEqual(['fewest_hours', 'most_hours', 'least_recently_used', 'priority']);
  });

  it('shows the saved choice', async () => {
    serve({ ...settings, model_dispatch_order: 'least_recently_used' });
    render(<SettingsPage />);

    expect((await select()).value).toBe('least_recently_used');
  });

  it('saves a changed choice', async () => {
    serve(settings);
    render(<SettingsPage />);

    fireEvent.change(await select(), { target: { value: 'priority' } });

    await waitFor(() => expect(saved.at(-1)?.model_dispatch_order).toBe('priority'), { timeout: 3000 });
  });
});
