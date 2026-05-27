/**
 * Tests for stock alert toggles added to NotificationProviderCard.
 *
 * Coverage:
 * - Reorder Alert and Stock Break Alert badges render in the summary strip when enabled.
 * - Badges are absent when both flags are false.
 * - Inventory Alerts section renders in the expanded settings panel.
 * - Toggling a stock alert fires an update mutation with the correct field.
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
    created_at: '2026-04-25T00:00:00Z',
    updated_at: '2026-04-25T00:00:00Z',
    ...overrides,
  };
}

describe('NotificationProviderCard — stock alert badges', () => {
  it('shows Reorder Alert badge when on_stock_reorder_alert is true', async () => {
    render(<NotificationProviderCard provider={buildProvider({ on_stock_reorder_alert: true })} onEdit={vi.fn()} />);
    expect(await screen.findByText('Reorder Alert')).toBeInTheDocument();
  });

  it('shows Stock Break Alert badge when on_stock_break_alert is true', async () => {
    render(<NotificationProviderCard provider={buildProvider({ on_stock_break_alert: true })} onEdit={vi.fn()} />);
    expect(await screen.findByText('Stock Break Alert')).toBeInTheDocument();
  });

  it('shows both badges when both flags are true', async () => {
    render(
      <NotificationProviderCard
        provider={buildProvider({ on_stock_reorder_alert: true, on_stock_break_alert: true })}
        onEdit={vi.fn()}
      />,
    );
    expect(await screen.findByText('Reorder Alert')).toBeInTheDocument();
    expect(screen.getByText('Stock Break Alert')).toBeInTheDocument();
  });

  it('shows no stock alert badges when both flags are false', async () => {
    render(<NotificationProviderCard provider={buildProvider()} onEdit={vi.fn()} />);
    // Wait for the card to mount
    await screen.findByText('Test Provider');
    expect(screen.queryByText('Reorder Alert')).not.toBeInTheDocument();
    expect(screen.queryByText('Stock Break Alert')).not.toBeInTheDocument();
  });
});

describe('NotificationProviderCard — Inventory Alerts expanded section', () => {
  it('renders the Inventory Alerts section header when settings are expanded', async () => {
    const user = userEvent.setup();
    render(<NotificationProviderCard provider={buildProvider()} onEdit={vi.fn()} />);

    const settingsBtn = await screen.findByText(/event settings/i);
    await user.click(settingsBtn);

    expect(await screen.findByText('Inventory Alerts')).toBeInTheDocument();
  });

  it('renders both stock alert toggles in the expanded section', async () => {
    const user = userEvent.setup();
    render(<NotificationProviderCard provider={buildProvider()} onEdit={vi.fn()} />);

    await user.click(await screen.findByText(/event settings/i));

    const section = (await screen.findByText('Inventory Alerts')).closest('div')!;
    expect(within(section).getByText('Reorder Alert')).toBeInTheDocument();
    expect(within(section).getByText('Stock Break Alert')).toBeInTheDocument();
  });

  it('stock alert toggles reflect the provider state', async () => {
    const user = userEvent.setup();
    render(
      <NotificationProviderCard
        provider={buildProvider({ on_stock_reorder_alert: true, on_stock_break_alert: false })}
        onEdit={vi.fn()}
      />,
    );

    await user.click(await screen.findByText(/event settings/i));

    const section = (await screen.findByText('Inventory Alerts')).closest('div')!;
    const switches = within(section).getAllByRole('switch');
    // First switch = reorder alert (true), second = break alert (false)
    expect(switches[0]).toHaveAttribute('aria-checked', 'true');
    expect(switches[1]).toHaveAttribute('aria-checked', 'false');
  });

  it('toggling Reorder Alert sends correct PATCH payload', async () => {
    let captured: unknown = null;
    server.use(
      http.patch('*/api/v1/notifications/1', async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json(buildProvider({ on_stock_reorder_alert: true }));
      }),
    );

    const user = userEvent.setup();
    render(<NotificationProviderCard provider={buildProvider()} onEdit={vi.fn()} />);

    await user.click(await screen.findByText(/event settings/i));

    const section = (await screen.findByText('Inventory Alerts')).closest('div')!;
    const [reorderSwitch] = within(section).getAllByRole('switch');
    await user.click(reorderSwitch);

    await waitFor(() => expect(captured).not.toBeNull());
    expect(captured).toMatchObject({ on_stock_reorder_alert: true });
  });

  it('toggling Stock Break Alert sends correct PATCH payload', async () => {
    let captured: unknown = null;
    server.use(
      http.patch('*/api/v1/notifications/1', async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json(buildProvider({ on_stock_break_alert: true }));
      }),
    );

    const user = userEvent.setup();
    render(<NotificationProviderCard provider={buildProvider()} onEdit={vi.fn()} />);

    await user.click(await screen.findByText(/event settings/i));

    const section = (await screen.findByText('Inventory Alerts')).closest('div')!;
    const switches = within(section).getAllByRole('switch');
    await user.click(switches[1]);

    await waitFor(() => expect(captured).not.toBeNull());
    expect(captured).toMatchObject({ on_stock_break_alert: true });
  });
});
