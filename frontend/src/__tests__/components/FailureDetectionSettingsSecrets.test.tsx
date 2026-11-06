/**
 * The saved Obico token comes back as a placeholder. Kept, it is not sent to a
 * new URL: changing the URL holds the auto-save until the token is entered
 * again, and the field says so.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { FailureDetectionSettings } from '../../components/FailureDetectionSettings';

const M = '********';

const settings = {
  obico_enabled: true,
  obico_ml_url: 'http://obico.example:3333',
  bambuddy_internal_url: '',
  obico_ml_token: M,
  obico_sensitivity: 'medium',
  obico_action: 'notify',
  obico_poll_interval: 10,
  obico_enabled_printers: '',
};

describe('FailureDetectionSettings saved token', () => {
  let saves: Record<string, unknown>[];

  beforeEach(() => {
    saves = [];
    server.use(
      http.get('/api/v1/settings/', () => HttpResponse.json(settings)),
      http.put('/api/v1/settings/', async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        saves.push(body);
        return HttpResponse.json({ ...settings, ...body });
      }),
      http.get('/api/v1/obico/status', () =>
        HttpResponse.json({ is_running: true, last_error: null, per_printer: {}, thresholds: { low: 0.38, high: 0.78 }, history: [], enabled: true, ml_url: '', sensitivity: 'medium', action: 'notify', poll_interval: 10 }),
      ),
      http.get('/api/v1/printers', () => HttpResponse.json([])),
    );
  });

  it('holds the save and asks for the token again when the URL changes', async () => {
    const user = userEvent.setup();
    render(<FailureDetectionSettings />);
    expect(await screen.findByText(/leave it as is to keep it/i)).toBeInTheDocument();

    const url = screen.getByDisplayValue('http://obico.example:3333');
    await user.clear(url);
    await user.type(url, 'http://obico.elsewhere.example:3333');

    expect(await screen.findByText(/the address changed: enter it again/i)).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 1000));
    expect(saves).toEqual([]);

    const token = screen.getByPlaceholderText('Leave empty if the server runs without ML_API_TOKEN');
    await user.click(token);
    await user.keyboard('new-token');

    await waitFor(() => expect(saves.length).toBeGreaterThan(0), { timeout: 3000 });
    expect(saves.at(-1)).toMatchObject({ obico_ml_url: 'http://obico.elsewhere.example:3333', obico_ml_token: 'new-token' });
  });
});
