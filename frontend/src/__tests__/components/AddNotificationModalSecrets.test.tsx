/**
 * Saved provider secrets come back as a placeholder. The edit dialog keeps it
 * (sent back unchanged it keeps the secret), says so, lets typing replace it,
 * and tests an edited provider by its id so the backend fills in the secret.
 */

import { describe, it, expect, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { AddNotificationModal } from '../../components/AddNotificationModal';
import type { NotificationProvider } from '../../api/client';

const PLACEHOLDER = '********';

afterEach(() => {
  server.resetHandlers();
});

function provider(overrides: Partial<NotificationProvider>): NotificationProvider {
  return {
    id: 7,
    name: 'Saved',
    provider_type: 'telegram',
    enabled: true,
    config: { bot_token: PLACEHOLDER, chat_id: '42' },
    on_print_start: false,
    on_print_complete: true,
    on_print_failed: true,
    on_print_stopped: true,
    on_print_progress: false,
    on_print_missing_spool_assignment: false,
    on_printer_offline: false,
    on_printer_error: false,
    on_ai_failure_detection: false,
    on_filament_low: false,
    on_maintenance_due: false,
    on_ams_humidity_high: false,
    on_ams_temperature_high: false,
    on_ams_ht_humidity_high: false,
    on_ams_ht_temperature_high: false,
    on_plate_not_empty: true,
    on_plate_clear_required: false,
    on_bed_cooled: false,
    on_first_layer_complete: false,
    on_queue_job_added: false,
    on_queue_job_assigned: false,
    on_queue_job_started: false,
    on_queue_job_waiting: true,
    on_queue_job_skipped: true,
    on_queue_job_failed: true,
    on_queue_completed: false,
    on_stock_reorder_alert: false,
    on_stock_break_alert: false,
    quiet_hours_enabled: false,
    quiet_hours_start: null,
    quiet_hours_end: null,
    daily_digest_enabled: false,
    daily_digest_time: null,
    printer_id: null,
    last_success: null,
    last_error: null,
    last_error_at: null,
    created_at: '2026-10-13T00:00:00Z',
    updated_at: '2026-10-13T00:00:00Z',
    ...overrides,
  };
}

describe('AddNotificationModal saved secrets', () => {
  it('tests an edited provider by its id, with the placeholder as it came', async () => {
    let body: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/v1/notifications/test-config', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true, message: 'ok' });
      }),
    );
    const user = userEvent.setup();
    render(<AddNotificationModal provider={provider({})} onClose={() => undefined} />);

    await user.click(await screen.findByRole('button', { name: /test configuration/i }));

    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toMatchObject({ provider_id: 7, config: { bot_token: PLACEHOLDER, chat_id: '42' } });
  });

  it('tests a new provider without an id', async () => {
    let body: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/v1/notifications/test-config', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true, message: 'ok' });
      }),
    );
    const user = userEvent.setup();
    render(<AddNotificationModal onClose={() => undefined} />);

    // A new provider starts as email: fill its required fields.
    await user.type(await screen.findByLabelText(/smtp server/i), 'smtp.example.com');
    await user.type(screen.getByLabelText(/from email/i), 'a@example.com');
    await user.type(screen.getByLabelText(/to email/i), 'b@example.com');
    await user.click(screen.getByRole('button', { name: /test configuration/i }));

    await waitFor(() => expect(body).not.toBeNull());
    expect(body).not.toHaveProperty('provider_id');
  });

  it('says a saved secret is kept, and not for a value being entered', async () => {
    const user = userEvent.setup();
    render(<AddNotificationModal provider={provider({})} onClose={() => undefined} />);

    expect(await screen.findByText(/leave it as is to keep it/i)).toBeInTheDocument();

    const token = screen.getByLabelText(/bot token/i);
    await user.clear(token);
    await user.type(token, 'new-token');

    expect(screen.queryByText(/leave it as is to keep it/i)).not.toBeInTheDocument();
  });

  it('replaces the placeholder with what is typed instead of adding to it', async () => {
    const user = userEvent.setup();
    render(<AddNotificationModal provider={provider({})} onClose={() => undefined} />);

    const token = await screen.findByLabelText(/bot token/i);
    await user.click(token);
    await user.keyboard('fresh');

    expect(token).toHaveValue('fresh');
  });

  it('sends an empty saved value back empty, not as the text null', async () => {
    let body: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/v1/notifications/test-config', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true, message: 'ok' });
      }),
    );
    const user = userEvent.setup();
    render(
      <AddNotificationModal
        provider={provider({ provider_type: 'ntfy', config: { server: null, topic: 't', auth_token: PLACEHOLDER } })}
        onClose={() => undefined}
      />,
    );

    await user.click(await screen.findByRole('button', { name: /test configuration/i }));

    await waitFor(() => expect(body).not.toBeNull());
    expect((body as unknown as { config: Record<string, unknown> }).config.server).toBe('');
  });

  it.each([
    ['discord', { webhook_url: PLACEHOLDER }, /webhook url/i],
    ['webhook', { webhook_url: PLACEHOLDER, auth_header: '' }, /webhook url/i],
    ['pushover', { user_key: PLACEHOLDER, app_token: PLACEHOLDER }, /user key/i],
    ['pushover', { user_key: PLACEHOLDER, app_token: PLACEHOLDER }, /app token/i],
    ['callmebot', { phone: '+1', apikey: PLACEHOLDER }, /api key/i],
    ['bark', { device_key: PLACEHOLDER }, /device key/i],
  ] as const)('hides the %s secret as a password field', async (type, config, label) => {
    render(
      <AddNotificationModal
        provider={provider({ provider_type: type, config: { ...config } })}
        onClose={() => undefined}
      />,
    );

    expect(await screen.findByLabelText(label)).toHaveAttribute('type', 'password');
  });
});
