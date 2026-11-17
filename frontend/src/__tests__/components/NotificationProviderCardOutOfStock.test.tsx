/**
 * Tests for the "Filament Out of Stock" badge and toggle on NotificationProviderCard (#3590).
 */

import { describe, it, expect, afterEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
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
    on_plate_not_empty: false,
    on_plate_clear_required: false,
    on_filament_out_of_stock: false,
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

describe('NotificationProviderCard — filament out of stock (#3590)', () => {
  it('shows the Out of stock badge when enabled', async () => {
    render(<NotificationProviderCard provider={buildProvider({ on_filament_out_of_stock: true })} onEdit={vi.fn()} />);
    expect(await screen.findByText('Out of stock')).toBeInTheDocument();
  });

  it('hides the badge when disabled', async () => {
    render(<NotificationProviderCard provider={buildProvider()} onEdit={vi.fn()} />);
    await screen.findByText('Test Provider');
    expect(screen.queryByText('Out of stock')).not.toBeInTheDocument();
  });

  it('toggling sends on_filament_out_of_stock', async () => {
    let captured: Record<string, unknown> | null = null;
    server.use(
      http.patch('*/api/v1/notifications/1', async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(buildProvider({ on_filament_out_of_stock: true }));
      }),
    );
    const user = userEvent.setup();
    render(<NotificationProviderCard provider={buildProvider()} onEdit={vi.fn()} />);
    await screen.findByText('Test Provider');
    // Expand the settings panel
    const expand = screen.queryByText('Test Provider')?.closest('div');
    if (!screen.queryByText('Filament Out of Stock')) {
      const btn = screen.getAllByRole('button').find((b) => /settings|expand/i.test(b.textContent ?? '') || b.querySelector('svg.lucide-chevron-down'));
      if (btn) await user.click(btn);
    }
    void expand;
    const label = await screen.findByText('Filament Out of Stock');
    const toggle = label.closest('div')!.parentElement!.querySelector('[role="switch"]') as HTMLElement;
    expect(toggle).toHaveAttribute('aria-checked', 'false');
    await user.click(toggle);
    await waitFor(() => expect(captured).toEqual({ on_filament_out_of_stock: true }));
  });
});
