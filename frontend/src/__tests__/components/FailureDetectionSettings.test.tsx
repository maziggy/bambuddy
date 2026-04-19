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
