/**
 * Tests for the Failure Detection settings component (#172).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { FailureDetectionSettings } from '../../components/FailureDetectionSettings';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const baseSettings = {
  auto_archive: true,
  save_thumbnails: true,
  capture_finish_photo: true,
  default_filament_cost: 25,
  currency: 'USD',
  energy_cost_per_kwh: 0.15,
  energy_tracking_mode: 'total',
  check_updates: true,
  check_printer_firmware: true,
  include_beta_updates: false,
  obico_enabled: false,
  obico_ml_url: '',
  obico_ml_token: '',
  obico_sensitivity: 'medium',
  obico_action: 'notify',
  obico_poll_interval: 10,
  obico_enabled_printers: '',
};

const baseStatus = {
  is_running: true,
  last_error: null,
  per_printer: {},
  thresholds: { low: 0.38, high: 0.78 },
  history: [],
  enabled: false,
  ml_url: '',
  sensitivity: 'medium',
  action: 'notify',
  poll_interval: 10,
};

describe('FailureDetectionSettings', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    server.use(
      http.get('/api/v1/settings/', () => HttpResponse.json(baseSettings)),
      http.get('/api/v1/obico/status', () => HttpResponse.json(baseStatus)),
      http.get('/api/v1/printers', () => HttpResponse.json([])),
    );
  });

  it('renders headings and fields', async () => {
    render(<FailureDetectionSettings />);
    await waitFor(() => {
      expect(screen.getByText(/AI Failure Detection|Failure Detection/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/Obico ML API URL/i)).toBeInTheDocument();
    expect(screen.getByText(/Sensitivity/i)).toBeInTheDocument();
  });

  it('test button calls the test-connection endpoint and shows success', async () => {
    let called = false;
    server.use(
      http.get('/api/v1/settings/', () =>
        HttpResponse.json({ ...baseSettings, obico_enabled: true, obico_ml_url: 'http://obico:3333' }),
      ),
      http.post('/api/v1/obico/test-connection', async ({ request }) => {
        called = true;
        const body = (await request.json()) as { url: string };
        expect(body.url).toBe('http://obico:3333');
        return HttpResponse.json({ ok: true, status_code: 200, body: 'ok', error: null });
      }),
    );
    render(<FailureDetectionSettings />);
    const testBtn = await screen.findByRole('button', { name: /test/i });
    await userEvent.click(testBtn);
    await waitFor(() => {
      expect(called).toBe(true);
    });
    expect(await screen.findByText(/ML API reachable/i)).toBeInTheDocument();
  });

  describe('ML API token (#2733)', () => {
    const enabledWithToken = {
      ...baseSettings,
      obico_enabled: true,
      obico_ml_url: 'http://obico:3333',
      obico_ml_token: 's3cret',
    };

    it('renders the token as a masked field populated from settings', async () => {
      server.use(http.get('/api/v1/settings/', () => HttpResponse.json(enabledWithToken)));
      render(<FailureDetectionSettings />);

      const input = await screen.findByDisplayValue('s3cret');
      expect(input).toHaveAttribute('type', 'password');
      expect(screen.getByText(/ML API Token/i)).toBeInTheDocument();
    });

    it('sends the token with the test-connection request', async () => {
      let sent: { url: string; token?: string } | null = null;
      server.use(
        http.get('/api/v1/settings/', () => HttpResponse.json(enabledWithToken)),
        http.post('/api/v1/obico/test-connection', async ({ request }) => {
          sent = (await request.json()) as { url: string; token?: string };
          return HttpResponse.json({
            ok: true,
            status_code: 200,
            body: 'ok',
            error: null,
            auth_ok: true,
          });
        }),
      );
      render(<FailureDetectionSettings />);
      await screen.findByDisplayValue('http://obico:3333');
      await userEvent.click(screen.getByRole('button', { name: /test/i }));

      await waitFor(() => expect(sent).not.toBeNull());
      // The value in the box, not the saved one — so a token can be checked
      // before it is committed.
      expect(sent!.token).toBe('s3cret');
    });

    it('reports a rejected token instead of a bare success', async () => {
      server.use(
        http.get('/api/v1/settings/', () => HttpResponse.json(enabledWithToken)),
        http.post('/api/v1/obico/test-connection', () =>
          HttpResponse.json({
            ok: false,
            status_code: 401,
            body: 'ok',
            error: 'The ML API is reachable but rejected the token.',
            auth_ok: false,
          }),
        ),
      );
      render(<FailureDetectionSettings />);
      await screen.findByDisplayValue('http://obico:3333');
      await userEvent.click(screen.getByRole('button', { name: /test/i }));

      expect(await screen.findByText(/rejected the token/i)).toBeInTheDocument();
    });

    it('does not claim the token works when it could not be checked', async () => {
      server.use(
        http.get('/api/v1/settings/', () => HttpResponse.json(enabledWithToken)),
        http.post('/api/v1/obico/test-connection', () =>
          HttpResponse.json({ ok: true, status_code: 200, body: 'ok', error: null, auth_ok: null }),
        ),
      );
      render(<FailureDetectionSettings />);
      await screen.findByDisplayValue('http://obico:3333');
      await userEvent.click(screen.getByRole('button', { name: /test/i }));

      expect(await screen.findByText(/token could not be checked/i)).toBeInTheDocument();
    });

    it('auto-saves the token', async () => {
      let saved: Record<string, unknown> | null = null;
      server.use(
        http.get('/api/v1/settings/', () => HttpResponse.json({ ...enabledWithToken, obico_ml_token: '' })),
        http.put('/api/v1/settings/', async ({ request }) => {
          saved = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json({ ...enabledWithToken, obico_ml_token: 'typed' });
        }),
      );
      render(<FailureDetectionSettings />);
      const input = await screen.findByPlaceholderText(/ML_API_TOKEN/i);
      // Every field stays disabled until the settings query lands.
      await waitFor(() => expect(input).not.toBeDisabled());
      await userEvent.type(input, 'typed');

      await waitFor(() => expect(saved).not.toBeNull(), { timeout: 3000 });
      expect(saved!.obico_ml_token).toBe('typed');
    });
  });

  it('shows failure class history entries with red styling', async () => {
    server.use(
      http.get('/api/v1/obico/status', () =>
        HttpResponse.json({
          ...baseStatus,
          history: [
            {
              printer_id: 1,
              task_name: 'test.3mf',
              timestamp: '2026-04-13T10:00:00Z',
              current_p: 0.9,
              score: 0.85,
              class: 'failure',
              detections: 1,
            },
          ],
        }),
      ),
    );
    render(<FailureDetectionSettings />);
    // Match the history row's score-and-class text, which looks like "failure 0.850"
    expect(await screen.findByText(/failure\s+0\.850/)).toBeInTheDocument();
  });
});
