/**
 * Plate Changer Empty notification event on NotificationProviderCard (#3575).
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
    on_printer_offline: false,
    on_printer_error: false,
    on_filament_low: false,
    on_maintenance_due: false,
    on_plate_clear_required: false,
    on_plate_changer_empty: false,
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
  } as NotificationProvider;
}

describe('NotificationProviderCard — Plate Changer Empty', () => {
  it('shows the badge when the event is enabled', async () => {
    render(<NotificationProviderCard provider={buildProvider({ on_plate_changer_empty: true })} onEdit={vi.fn()} />);
    expect(await screen.findByText('Plate Changer')).toBeInTheDocument();
  });

  it('shows no badge when the event is disabled', async () => {
    render(<NotificationProviderCard provider={buildProvider()} onEdit={vi.fn()} />);
    await screen.findByText('Test Provider');
    expect(screen.queryByText('Plate Changer')).not.toBeInTheDocument();
  });

  it('renders a toggle reflecting the provider state', async () => {
    const user = userEvent.setup();
    render(<NotificationProviderCard provider={buildProvider({ on_plate_changer_empty: true })} onEdit={vi.fn()} />);

    await user.click(await screen.findByText(/event settings/i));

    const label = await screen.findByText('Plate Changer Empty');
    const row = label.closest('div')!.parentElement!;
    expect(within(row).getByRole('switch')).toHaveAttribute('aria-checked', 'true');
  });

  it('toggling sends on_plate_changer_empty in the PATCH payload', async () => {
    let captured: unknown = null;
    server.use(
      http.patch('*/api/v1/notifications/1', async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json(buildProvider({ on_plate_changer_empty: true }));
      }),
    );

    const user = userEvent.setup();
    render(<NotificationProviderCard provider={buildProvider()} onEdit={vi.fn()} />);
    await user.click(await screen.findByText(/event settings/i));

    const label = await screen.findByText('Plate Changer Empty');
    const row = label.closest('div')!.parentElement!;
    await user.click(within(row).getByRole('switch'));

    await waitFor(() => expect(captured).not.toBeNull());
    expect(captured).toMatchObject({ on_plate_changer_empty: true });
  });
});
