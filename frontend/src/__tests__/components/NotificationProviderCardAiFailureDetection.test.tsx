/**
 * Tests for the AI Failure Detection toggle on NotificationProviderCard (#1794).
 *
 * Before #1794, Obico failure detection rode the multiplexed
 * on_printer_error toggle so users couldn't subscribe to one without the
 * other. These tests pin the standalone toggle:
 *  - Summary badge renders when enabled.
 *  - The toggle row appears in the expanded settings panel.
 *  - Flipping the toggle PATCHes the correct field.
 */

import { describe, it, expect, afterEach, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { NotificationProviderCard } from '../../components/NotificationProviderCard';
import type { NotificationProvider } from '../../api/client';

afterEach(() => {
  server.resetHandlers();
  vi.restoreAllMocks();
});

function buildProvider(overrides: Partial<NotificationProvider> = {}): NotificationProvider {
  return {
    id: 1,
    name: 'Test Provider',
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
    created_at: '2026-06-22T00:00:00Z',
    updated_at: '2026-06-22T00:00:00Z',
    ...overrides,
  };
}

describe('NotificationProviderCard — AI Failure Detection badge', () => {
  it('renders the badge when on_ai_failure_detection is true', async () => {
    render(
      <NotificationProviderCard
        provider={buildProvider({ on_ai_failure_detection: true })}
        onEdit={vi.fn()}
      />,
    );
    expect(await screen.findByText('AI Failure Detection')).toBeInTheDocument();
  });

  it('omits the badge when on_ai_failure_detection is false', async () => {
    render(<NotificationProviderCard provider={buildProvider()} onEdit={vi.fn()} />);
    await screen.findByText('Test Provider');
    expect(screen.queryByText('AI Failure Detection')).not.toBeInTheDocument();
  });
});

describe('NotificationProviderCard — AI Failure Detection toggle', () => {
  it('renders the toggle in the expanded settings panel', async () => {
    const user = userEvent.setup();
    render(<NotificationProviderCard provider={buildProvider()} onEdit={vi.fn()} />);

    await user.click(await screen.findByText(/event settings/i));

    expect(await screen.findByText('AI Failure Detection')).toBeInTheDocument();
  });

  it('PATCHes on_ai_failure_detection (NOT on_printer_error) when toggled on — #1794 regression guard', async () => {
    let captured: Record<string, unknown> | null = null;
    server.use(
      http.patch('*/api/v1/notifications/1', async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(buildProvider({ on_ai_failure_detection: true }));
      }),
    );

    const user = userEvent.setup();
    render(<NotificationProviderCard provider={buildProvider()} onEdit={vi.fn()} />);

    await user.click(await screen.findByText(/event settings/i));

    // The toggle label "AI Failure Detection" is unique to this row.
    const label = await screen.findByText('AI Failure Detection');
    const row = label.closest('div.flex')!;
    const toggle = within(row).getByRole('switch');
    await user.click(toggle);

    await waitFor(() => expect(captured).not.toBeNull());
    expect(captured).toMatchObject({ on_ai_failure_detection: true });
    // Critical: must NOT also flip the legacy multiplexed field.
    expect(captured).not.toHaveProperty('on_printer_error');
  });
});
