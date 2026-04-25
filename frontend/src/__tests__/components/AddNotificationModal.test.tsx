/**
 * Frontend tests for the AddNotificationModal — focused on the per-event
 * ntfy Priority section (#990).
 *
 * Coverage:
 * - Priority section renders only for ntfy provider type.
 * - Section lists ONLY events the user has enabled, not the whole catalogue.
 * - Save round-trips event_priorities into config.
 * - Editing an existing ntfy provider pre-fills priorities from config.
 * - Switching off a toggle drops the matching row from the priority section.
 * - For non-ntfy providers, event_priorities never appears in the saved config.
 */

import { describe, it, expect, afterEach, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { AddNotificationModal } from '../../components/AddNotificationModal';
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
    on_print_complete: true,
    on_print_failed: true,
    on_print_stopped: true,
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
    on_plate_not_empty: true,
    on_bed_cooled: false,
    on_first_layer_complete: false,
    on_queue_job_added: false,
    on_queue_job_assigned: false,
    on_queue_job_started: false,
    on_queue_job_waiting: true,
    on_queue_job_skipped: true,
    on_queue_job_failed: true,
    on_queue_completed: false,
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

describe('AddNotificationModal — ntfy Priority (#990)', () => {
  it('renders the ntfy Priority section listing only enabled events', async () => {
    render(<AddNotificationModal provider={buildProvider()} onClose={() => undefined} />);

    // Section header present, then scope every label query to it — the same
    // labels also appear in the toggle grid above.
    const sectionHeader = await screen.findByText(/ntfy priority/i);
    const sectionRoot = sectionHeader.closest('div')!;

    // Defaults from buildProvider(): complete + failed + stopped enabled;
    // start + progress + offline disabled. The priority list mirrors that.
    expect(within(sectionRoot).getByText('Complete')).toBeInTheDocument();
    expect(within(sectionRoot).getByText('Failed')).toBeInTheDocument();
    expect(within(sectionRoot).getByText('Stopped')).toBeInTheDocument();

    // Disabled events must not appear in the priority block.
    expect(within(sectionRoot).queryByText('Start')).not.toBeInTheDocument();
    expect(within(sectionRoot).queryByText('Progress')).not.toBeInTheDocument();
    expect(within(sectionRoot).queryByText('Offline')).not.toBeInTheDocument();
  });

  it('does not render the Priority section for non-ntfy providers', async () => {
    render(
      <AddNotificationModal
        provider={buildProvider({ provider_type: 'telegram', config: { bot_token: 'x', chat_id: 'y' } })}
        onClose={() => undefined}
      />,
    );

    // Wait for the modal to settle.
    await screen.findByDisplayValue('My ntfy');

    expect(screen.queryByText(/ntfy priority/i)).not.toBeInTheDocument();
  });

  it('persists event_priorities into config on save', async () => {
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

    // Pick "Urgent" (5) for the on_print_failed row.
    const sectionHeader = await screen.findByText(/ntfy priority/i);
    const sectionRoot = sectionHeader.closest('div')!;
    const failedRow = within(sectionRoot).getByText('Failed').closest('div')!;
    const select = within(failedRow).getByRole('combobox');
    await user.selectOptions(select, '5');

    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(captured).not.toBeNull();
    const payload = captured as { config: Record<string, unknown> };
    expect(payload.config).toMatchObject({
      server: 'https://ntfy.sh',
      topic: 'bambuddy',
      event_priorities: { on_print_failed: 5 },
    });
  });

  it('pre-fills priorities from existing provider.config.event_priorities', async () => {
    const provider = buildProvider({
      config: {
        server: 'https://ntfy.sh',
        topic: 'bambuddy',
        event_priorities: { on_print_failed: 5, on_print_complete: 2 },
      },
    });

    render(<AddNotificationModal provider={provider} onClose={() => undefined} />);

    const sectionHeader = await screen.findByText(/ntfy priority/i);
    const sectionRoot = sectionHeader.closest('div')!;

    const failedRow = within(sectionRoot).getByText('Failed').closest('div')!;
    expect((within(failedRow).getByRole('combobox') as HTMLSelectElement).value).toBe('5');

    const completeRow = within(sectionRoot).getByText('Complete').closest('div')!;
    expect((within(completeRow).getByRole('combobox') as HTMLSelectElement).value).toBe('2');

    // Stopped is enabled but has no override → defaults to 3.
    const stoppedRow = within(sectionRoot).getByText('Stopped').closest('div')!;
    expect((within(stoppedRow).getByRole('combobox') as HTMLSelectElement).value).toBe('3');
  });

  it('drops events from the priority section when their toggle is disabled', async () => {
    const user = userEvent.setup();
    render(<AddNotificationModal provider={buildProvider()} onClose={() => undefined} />);

    const sectionHeader = await screen.findByText(/ntfy priority/i);
    const sectionRoot = sectionHeader.closest('div')!;

    // Stopped is initially enabled → row visible.
    expect(within(sectionRoot).getByText('Stopped')).toBeInTheDocument();

    // Find the Stopped toggle in the events grid (a separate area). Its label
    // appears in the priority section AND the toggle grid; we need the toggle
    // one. The toggle is a sibling of the label inside an event-row div.
    const allStoppedNodes = screen.getAllByText('Stopped');
    // The first occurrence is in the Print Events grid; the second is in the
    // Priority section. Click the toggle next to the first one.
    const togglesGridStopped = allStoppedNodes[0];
    const toggleRow = togglesGridStopped.closest('div')!;
    const toggle = within(toggleRow).getByRole('switch');
    await user.click(toggle);

    // Row drops out of the priority section.
    await waitFor(() => {
      const stillSection = screen.getByText(/ntfy priority/i).closest('div')!;
      expect(within(stillSection).queryByText('Stopped')).not.toBeInTheDocument();
    });
  });

  it('omits event_priorities for non-ntfy providers on save', async () => {
    let captured: unknown = null;
    server.use(
      http.post('*/api/v1/notifications/', async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ id: 99 });
      }),
    );

    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<AddNotificationModal onClose={onClose} />);

    // Default new-provider type is email. Fill required fields and save.
    await user.type(screen.getByPlaceholderText(/My Notifications/i), 'Test');
    await user.type(screen.getByPlaceholderText('smtp.gmail.com'), 'smtp.example.com');
    const fromInputs = screen.getAllByPlaceholderText('your@email.com');
    await user.type(fromInputs[fromInputs.length - 1], 'me@example.com');
    await user.type(screen.getByPlaceholderText('recipient@email.com'), 'them@example.com');

    await user.click(screen.getByRole('button', { name: /^add$/i }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const payload = captured as { provider_type: string; config: Record<string, unknown> };
    expect(payload.provider_type).toBe('email');
    expect(payload.config).not.toHaveProperty('event_priorities');
  });
});
