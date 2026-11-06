/**
 * The "Pause Coming Up" channel toggle (#2984): in the add/edit modal, on the
 * provider card, and saved under its own field.
 */

import { describe, it, expect, afterEach, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
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
    name: 'My ntfy',
    provider_type: 'ntfy',
    enabled: true,
    config: { server: 'https://ntfy.sh', topic: 'bambuddy' },
    on_print_start: false,
    on_print_complete: false,
    on_print_failed: false,
    on_print_stopped: false,
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
    on_plate_not_empty: false,
    on_bed_cooled: false,
    on_first_layer_complete: false,
    on_print_pause_upcoming: false,
    on_queue_job_added: false,
    on_queue_job_assigned: false,
    on_queue_job_started: false,
    on_queue_job_waiting: false,
    on_queue_job_skipped: false,
    on_queue_job_failed: false,
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
    created_at: '2026-10-24T00:00:00Z',
    updated_at: '2026-10-24T00:00:00Z',
    ...overrides,
  };
}

function modalToggle() {
  return screen
    .getAllByRole('switch')
    .find((s) => s.closest('div')?.textContent?.match(/pause coming up/i))!;
}

describe('AddNotificationModal - Pause Coming Up', () => {
  it('pre-fills the toggle from the provider', async () => {
    render(<AddNotificationModal provider={buildProvider({ on_print_pause_upcoming: true })} onClose={() => undefined} />);
    await screen.findByDisplayValue('My ntfy');
    expect(modalToggle()).toHaveAttribute('aria-checked', 'true');
  });

  it('saves on_print_pause_upcoming', async () => {
    let captured: Record<string, unknown> | null = null;
    server.use(
      http.patch('*/api/v1/notifications/1', async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: 1 });
      }),
    );
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<AddNotificationModal provider={buildProvider()} onClose={onClose} />);
    await screen.findByDisplayValue('My ntfy');

    expect(modalToggle()).toHaveAttribute('aria-checked', 'false');
    await user.click(modalToggle());
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());

    expect(captured).toMatchObject({ on_print_pause_upcoming: true });
  });

  it('lists the event in the ntfy priority section once enabled', async () => {
    render(<AddNotificationModal provider={buildProvider({ on_print_pause_upcoming: true })} onClose={() => undefined} />);
    const sectionRoot = (await screen.findByText(/ntfy priority/i)).closest('div')!;
    expect(within(sectionRoot).getByText(/pause coming up/i)).toBeInTheDocument();
  });
});

describe('NotificationProviderCard - Pause Coming Up', () => {
  it('shows the badge only when enabled', async () => {
    const { unmount } = render(
      <NotificationProviderCard provider={buildProvider({ on_print_pause_upcoming: true })} onEdit={vi.fn()} />,
    );
    expect(await screen.findByText('Pause')).toBeInTheDocument();
    unmount();

    render(<NotificationProviderCard provider={buildProvider()} onEdit={vi.fn()} />);
    await screen.findByText('My ntfy');
    expect(screen.queryByText('Pause')).not.toBeInTheDocument();
  });

  it('PATCHes on_print_pause_upcoming when toggled', async () => {
    let captured: Record<string, unknown> | null = null;
    server.use(
      http.patch('*/api/v1/notifications/1', async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(buildProvider({ on_print_pause_upcoming: true }));
      }),
    );
    const user = userEvent.setup();
    render(<NotificationProviderCard provider={buildProvider()} onEdit={vi.fn()} />);

    await user.click(await screen.findByText(/event settings/i));
    const row = (await screen.findByText('Pause Coming Up')).closest('div.flex')!;
    await user.click(within(row as HTMLElement).getByRole('switch'));

    await waitFor(() => expect(captured).toEqual({ on_print_pause_upcoming: true }));
  });
});
