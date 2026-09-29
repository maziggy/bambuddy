/**
 * Telegram outcome verdict mode (#3046).
 *
 * Coverage:
 * - AddNotificationModal shows the mode select only for Telegram providers.
 * - The select pre-fills from provider.telegram_verdict_mode and the chosen
 *   value is submitted; non-Telegram providers always submit "buttons".
 * - NotificationProviderCard renders the reactions / both badge.
 */

import { describe, it, expect, afterEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { AddNotificationModal } from '../../components/AddNotificationModal';
import { NotificationProviderCard } from '../../components/NotificationProviderCard';
import type { NotificationProvider } from '../../api/client';

afterEach(() => {
  server.resetHandlers();
  vi.restoreAllMocks();
});

function buildProvider(overrides: Partial<NotificationProvider> = {}): NotificationProvider {
  return {
    id: 1,
    name: 'My Telegram',
    provider_type: 'telegram',
    enabled: true,
    config: { bot_token: '123:abc', chat_id: '-100123' },
    on_print_start: false,
    on_print_complete: true,
    on_print_failed: true,
    on_print_stopped: true,
    on_print_progress: false,
    on_print_missing_spool_assignment: false,
    on_billing_charge_failed: true,
    on_printer_offline: false,
    on_printer_error: false,
    on_ai_failure_detection: false,
    on_filament_low: false,
    on_maintenance_due: false,
    on_ams_humidity_high: false,
    on_ams_temperature_high: false,
    on_ams_drying_suspended: true,
    on_ams_ht_humidity_high: false,
    on_ams_ht_temperature_high: false,
    on_plate_not_empty: true,
    on_plate_clear_required: false,
    on_print_confirm_request: true,
    telegram_verdict_mode: 'buttons',
    on_bed_cooled: false,
    on_ha_sensor_alert: false,
    on_location_ha_sensor_alert: false,
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
    created_at: '2026-09-20T00:00:00Z',
    updated_at: '2026-09-20T00:00:00Z',
    ...overrides,
  };
}

describe('AddNotificationModal — Telegram verdict mode (#3046)', () => {
  it('shows the mode select for a Telegram provider, pre-filled from the provider', async () => {
    render(
      <AddNotificationModal provider={buildProvider({ telegram_verdict_mode: 'reactions' })} onClose={() => undefined} />,
    );

    const select = (await screen.findByLabelText('Outcome verdict via')) as HTMLSelectElement;
    expect(select.value).toBe('reactions');
    expect(screen.getByRole('option', { name: 'Inline buttons (link)' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Reaction (👍 / 👎)' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Buttons and reaction' })).toBeInTheDocument();
    expect(screen.getByText(/Reactions need no inbound connectivity/)).toBeInTheDocument();
  });

  it('does not show the mode select for other provider types', async () => {
    render(
      <AddNotificationModal
        provider={buildProvider({ provider_type: 'ntfy', config: { server: 'https://ntfy.sh', topic: 'x' } })}
        onClose={() => undefined}
      />,
    );

    await screen.findByDisplayValue('My Telegram');
    expect(screen.queryByLabelText('Outcome verdict via')).not.toBeInTheDocument();
  });

  it('submits the chosen mode on save', async () => {
    let captured: unknown = null;
    server.use(
      http.patch('*/api/v1/notifications/1', async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ id: 1 });
      }),
    );

    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<AddNotificationModal provider={buildProvider()} onClose={onClose} />);

    const select = await screen.findByLabelText('Outcome verdict via');
    await user.selectOptions(select, 'both');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(captured).toMatchObject({ provider_type: 'telegram', telegram_verdict_mode: 'both' });
  });

  it('always submits "buttons" for a non-Telegram provider', async () => {
    let captured: unknown = null;
    server.use(
      http.patch('*/api/v1/notifications/1', async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ id: 1 });
      }),
    );

    const onClose = vi.fn();
    const user = userEvent.setup();
    render(
      <AddNotificationModal
        provider={buildProvider({
          provider_type: 'ntfy',
          config: { server: 'https://ntfy.sh', topic: 'x' },
          telegram_verdict_mode: 'reactions',
        })}
        onClose={onClose}
      />,
    );

    await screen.findByDisplayValue('My Telegram');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(captured).toMatchObject({ provider_type: 'ntfy', telegram_verdict_mode: 'buttons' });
  });
});

describe('NotificationProviderCard — Telegram verdict badge (#3046)', () => {
  it('shows the Reactions badge in reactions mode', async () => {
    render(<NotificationProviderCard provider={buildProvider({ telegram_verdict_mode: 'reactions' })} onEdit={vi.fn()} />);
    expect(await screen.findByText('Reactions')).toBeInTheDocument();
    expect(screen.queryByText('Buttons + reactions')).not.toBeInTheDocument();
  });

  it('shows the Buttons + reactions badge in both mode', async () => {
    render(<NotificationProviderCard provider={buildProvider({ telegram_verdict_mode: 'both' })} onEdit={vi.fn()} />);
    expect(await screen.findByText('Buttons + reactions')).toBeInTheDocument();
  });

  it('shows no mode badge in buttons mode or when the prompt event is muted', async () => {
    const { unmount } = render(<NotificationProviderCard provider={buildProvider()} onEdit={vi.fn()} />);
    expect(await screen.findByText('Outcome Confirmation')).toBeInTheDocument();
    expect(screen.queryByText('Reactions')).not.toBeInTheDocument();
    unmount();

    render(
      <NotificationProviderCard
        provider={buildProvider({ telegram_verdict_mode: 'reactions', on_print_confirm_request: false })}
        onEdit={vi.fn()}
      />,
    );
    expect(await screen.findByText('My Telegram')).toBeInTheDocument();
    expect(screen.queryByText('Reactions')).not.toBeInTheDocument();
  });
});
