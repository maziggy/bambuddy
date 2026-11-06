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

describe('AddNotificationModal — Gotify (#2743)', () => {
  const gotifyProvider = () =>
    buildProvider({
      name: 'My Gotify',
      provider_type: 'gotify',
      config: { server: 'https://gotify.example.com', app_token: 'Atoken' },
    });

  it('shows the server and token fields, with the token masked', async () => {
    render(<AddNotificationModal provider={gotifyProvider()} onClose={() => undefined} />);

    expect(await screen.findByDisplayValue('https://gotify.example.com')).toBeInTheDocument();
    const token = screen.getByDisplayValue('Atoken');
    expect(token).toHaveAttribute('type', 'password');
  });

  it('renders its own priority section and saves event_priorities', async () => {
    let captured: unknown = null;
    server.use(
      http.patch('*/api/v1/notifications/1', async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ id: 1 });
      }),
    );

    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<AddNotificationModal provider={gotifyProvider()} onClose={onClose} />);

    const sectionHeader = await screen.findByText(/gotify priority/i);
    expect(screen.queryByText(/ntfy priority/i)).not.toBeInTheDocument();
    const sectionRoot = sectionHeader.closest('div')!;
    const failedRow = within(sectionRoot).getByText('Failed').closest('div')!;
    await user.selectOptions(within(failedRow).getByRole('combobox'), '4');

    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const payload = captured as { config: Record<string, unknown> };
    expect(payload.config).toMatchObject({
      server: 'https://gotify.example.com',
      app_token: 'Atoken',
      event_priorities: { on_print_failed: 4 },
    });
  });
});

describe('AddNotificationModal — provider type list', () => {
  it('lists the providers alphabetically by their label', async () => {
    render(<AddNotificationModal onClose={() => undefined} />);

    const select = (await screen.findByDisplayValue('Email')) as HTMLSelectElement;
    const labels = Array.from(select.options).map((o) => o.textContent);

    expect(labels).toEqual([
      'Bark',
      'CallMeBot/WhatsApp',
      'Discord',
      'Email',
      'Gotify',
      'Home Assistant',
      'Notify!',
      'ntfy',
      'Pushover',
      'Telegram',
      'Webhook',
    ]);
  });
});

describe('AddNotificationModal — plate clear required (#2525)', () => {
  it('renders the toggle off by default', async () => {
    render(<AddNotificationModal provider={buildProvider()} onClose={() => undefined} />);

    await screen.findByDisplayValue('My ntfy');

    const toggle = screen
      .getAllByRole('switch')
      .find((s) => s.closest('div')?.textContent?.match(/plate clear required/i));
    expect(toggle).toBeDefined();
    expect(toggle).toHaveAttribute('aria-checked', 'false');
  });

  it('pre-fills the toggle from the existing provider value', async () => {
    render(
      <AddNotificationModal
        provider={buildProvider({ on_plate_clear_required: true })}
        onClose={() => undefined}
      />,
    );

    await screen.findByDisplayValue('My ntfy');

    const toggle = screen
      .getAllByRole('switch')
      .find((s) => s.closest('div')?.textContent?.match(/plate clear required/i))!;
    expect(toggle).toHaveAttribute('aria-checked', 'true');
  });

  it('persists on_plate_clear_required on save', async () => {
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

    await screen.findByDisplayValue('My ntfy');

    const toggle = screen
      .getAllByRole('switch')
      .find((s) => s.closest('div')?.textContent?.match(/plate clear required/i))!;
    await user.click(toggle);

    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());

    const payload = captured as Record<string, unknown>;
    expect(payload.on_plate_clear_required).toBe(true);
  });

  it('lists the event in the ntfy priority section once enabled', async () => {
    render(
      <AddNotificationModal
        provider={buildProvider({ on_plate_clear_required: true })}
        onClose={() => undefined}
      />,
    );

    const sectionHeader = await screen.findByText(/ntfy priority/i);
    const sectionRoot = sectionHeader.closest('div')!;
    expect(within(sectionRoot).getByText(/plate clear required/i)).toBeInTheDocument();
  });
});

describe('AddNotificationModal — stock alert toggles', () => {
  it('renders Inventory Alerts section with both stock alert toggles', async () => {
    render(<AddNotificationModal provider={buildProvider()} onClose={() => undefined} />);

    const section = await screen.findByText(/inventory alerts/i);
    const sectionRoot = section.closest('div')!;

    expect(section).toBeInTheDocument();
    expect(sectionRoot.textContent).toMatch(/reorder alert/i);
    expect(sectionRoot.textContent).toMatch(/stock break alert/i);
  });

  it('pre-fills toggles from existing provider values', async () => {
    render(
      <AddNotificationModal
        provider={buildProvider({ on_stock_reorder_alert: true, on_stock_break_alert: false })}
        onClose={() => undefined}
      />,
    );

    await screen.findByText(/inventory alerts/i);

    // Reorder alert switch should be ON, break alert switch OFF
    const switches = screen.getAllByRole('switch');
    const reorderSwitch = switches.find((s) => {
      const row = s.closest('div');
      return row?.textContent?.match(/reorder alert/i);
    });
    const breakSwitch = switches.find((s) => {
      const row = s.closest('div');
      return row?.textContent?.match(/stock break alert/i);
    });

    expect(reorderSwitch).toHaveAttribute('aria-checked', 'true');
    expect(breakSwitch).toHaveAttribute('aria-checked', 'false');
  });

  it('persists on_stock_reorder_alert on save', async () => {
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

    await screen.findByText(/inventory alerts/i);

    // Enable the reorder alert toggle
    const switches = screen.getAllByRole('switch');
    const reorderSwitch = switches.find((s) => {
      const row = s.closest('div');
      return row?.textContent?.match(/reorder alert/i);
    })!;
    await user.click(reorderSwitch);

    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());

    const payload = captured as Record<string, unknown>;
    expect(payload.on_stock_reorder_alert).toBe(true);
  });

  it('persists on_stock_break_alert on save', async () => {
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

    await screen.findByText(/inventory alerts/i);

    const switches = screen.getAllByRole('switch');
    const breakSwitch = switches.find((s) => {
      const row = s.closest('div');
      return row?.textContent?.match(/stock break alert/i);
    })!;
    await user.click(breakSwitch);

    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());

    const payload = captured as Record<string, unknown>;
    expect(payload.on_stock_break_alert).toBe(true);
  });

  it('stock alert events appear in ntfy priority section when enabled', async () => {
    const user = userEvent.setup();
    render(
      <AddNotificationModal
        provider={buildProvider({ on_stock_reorder_alert: true, on_stock_break_alert: true })}
        onClose={() => undefined}
      />,
    );

    const priorityHeader = await screen.findByText(/ntfy priority/i);
    const priorityRoot = priorityHeader.closest('div')!;

    // Both stock alert events should appear in the priority list since they are enabled
    expect(within(priorityRoot).getByText('Reorder Alert')).toBeInTheDocument();
    expect(within(priorityRoot).getByText('Stock Break Alert')).toBeInTheDocument();
    void user; // referenced to avoid unused-var lint warning
  });
});

describe('AddNotificationModal — AI Failure Detection toggle (#1794)', () => {
  it('renders the toggle in the Printer Status section', async () => {
    render(<AddNotificationModal provider={buildProvider()} onClose={() => undefined} />);

    expect(await screen.findByText('AI Failure Detection')).toBeInTheDocument();
  });

  it('persists on_ai_failure_detection on save (and does NOT touch on_printer_error)', async () => {
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

    const label = await screen.findByText('AI Failure Detection');
    const row = label.closest('div.flex')!;
    const toggle = within(row).getByRole('switch');
    await user.click(toggle);

    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());

    expect(captured).not.toBeNull();
    expect(captured!.on_ai_failure_detection).toBe(true);
    // Critical regression guard: don't accidentally flip the legacy multiplexed field.
    expect(captured!.on_printer_error).toBe(false);
  });

  it('AI Failure Detection appears in ntfy priority section when enabled', async () => {
    render(
      <AddNotificationModal
        provider={buildProvider({ on_ai_failure_detection: true })}
        onClose={() => undefined}
      />,
    );

    const priorityHeader = await screen.findByText(/ntfy priority/i);
    const priorityRoot = priorityHeader.closest('div')!;

    expect(within(priorityRoot).getByText('AI Failure Detection')).toBeInTheDocument();
  });
});

describe('AddNotificationModal — Storage Location Sensor Alert toggle (#2824)', () => {
  it('renders its own toggle, separate from the printer Sensor Alert toggle', async () => {
    render(<AddNotificationModal provider={buildProvider()} onClose={() => undefined} />);

    expect(await screen.findByText('Printer Sensor Alert')).toBeInTheDocument();
    expect(await screen.findByText('Storage Location Sensor Alert')).toBeInTheDocument();
  });

  it('persists on_location_ha_sensor_alert on save (and does NOT touch on_ha_sensor_alert)', async () => {
    // The regression this guards: the two events used to share one column,
    // so a provider scoped to a single printer's sensor alerts would also
    // start receiving storage-location alerts the moment either toggle went on.
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

    const label = await screen.findByText('Storage Location Sensor Alert');
    const row = label.closest('div.flex')!;
    const toggle = within(row).getByRole('switch');
    await user.click(toggle);

    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());

    expect(captured).not.toBeNull();
    expect(captured!.on_location_ha_sensor_alert).toBe(true);
    expect(captured!.on_ha_sensor_alert).toBe(false);
  });

  it('appears in ntfy priority section when enabled', async () => {
    render(
      <AddNotificationModal
        provider={buildProvider({ provider_type: 'ntfy', on_location_ha_sensor_alert: true })}
        onClose={() => undefined}
      />,
    );

    const priorityHeader = await screen.findByText(/ntfy priority/i);
    const priorityRoot = priorityHeader.closest('div')!;

    expect(within(priorityRoot).getByText('Storage Location Sensor Alert')).toBeInTheDocument();
  });
});

describe('AddNotificationModal — Home Assistant custom data (#1441)', () => {
  const haProvider = () =>
    buildProvider({
      provider_type: 'homeassistant',
      config: { service: 'notify.mobile_app_myphone' },
    });

  it('renders the Data (JSON) textarea for the homeassistant provider', async () => {
    render(<AddNotificationModal provider={haProvider()} onClose={() => undefined} />);

    await screen.findByDisplayValue('My ntfy');
    expect(screen.getByText(/data \(json, optional\)/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/"priority": "high"/)).toBeInTheDocument();
  });

  it('rejects malformed JSON in the Data field on save', async () => {
    const patchSpy = vi.fn();
    server.use(
      http.patch('*/api/v1/notifications/1', () => {
        patchSpy();
        return HttpResponse.json({ id: 1 });
      }),
    );

    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<AddNotificationModal provider={haProvider()} onClose={onClose} />);

    const textarea = await screen.findByPlaceholderText(/"priority": "high"/);
    await user.type(textarea, '{{priority: high}');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    expect(await screen.findByText(/must be a valid JSON object/i)).toBeInTheDocument();
    expect(patchSpy).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('round-trips valid Data JSON into config on save', async () => {
    let captured: { config: Record<string, unknown> } | null = null;
    server.use(
      http.patch('*/api/v1/notifications/1', async ({ request }) => {
        captured = (await request.json()) as { config: Record<string, unknown> };
        return HttpResponse.json({ id: 1 });
      }),
    );

    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<AddNotificationModal provider={haProvider()} onClose={onClose} />);

    const textarea = await screen.findByPlaceholderText(/"priority": "high"/);
    await user.type(textarea, '{{"ttl": 0}');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(captured).not.toBeNull();
    expect(captured!.config).toMatchObject({
      service: 'notify.mobile_app_myphone',
      data: '{"ttl": 0}',
    });
  });
});

describe('AddNotificationModal — Bark provider (#1495)', () => {
  it('offers Bark in the provider select and renders its config fields', async () => {
    render(
      <AddNotificationModal
        provider={buildProvider({ provider_type: 'bark', config: { device_key: 'abc123' } })}
        onClose={() => undefined}
      />,
    );

    await screen.findByDisplayValue('My ntfy');
    expect(screen.getByRole('option', { name: 'Bark' })).toBeInTheDocument();
    expect(screen.getByText(/device key/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText('https://api.day.app')).toBeInTheDocument();
    expect(screen.getByText(/interruption level/i)).toBeInTheDocument();
  });

  it('round-trips Bark options into config on save', async () => {
    let captured: { config: Record<string, unknown> } | null = null;
    server.use(
      http.patch('*/api/v1/notifications/1', async ({ request }) => {
        captured = (await request.json()) as { config: Record<string, unknown> };
        return HttpResponse.json({ id: 1 });
      }),
    );

    const onClose = vi.fn();
    const user = userEvent.setup();
    render(
      <AddNotificationModal
        provider={buildProvider({ provider_type: 'bark', config: { device_key: 'abc123' } })}
        onClose={onClose}
      />,
    );

    const groupInput = await screen.findByPlaceholderText('Bambuddy');
    await user.type(groupInput, 'Printers');
    const levelRow = screen.getByText(/interruption level/i).closest('div')!;
    await user.selectOptions(within(levelRow).getByRole('combobox'), 'critical');

    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(captured).not.toBeNull();
    expect(captured!.config).toMatchObject({
      device_key: 'abc123',
      group: 'Printers',
      level: 'critical',
    });
  });
});

describe('AddNotificationModal — Telegram forum topic (#1518)', () => {
  const telegramProvider = (config: Record<string, unknown> = { bot_token: 'x', chat_id: '-100123' }) =>
    buildProvider({ provider_type: 'telegram', config });

  it('offers the Forum Topic ID field as optional for telegram', async () => {
    render(<AddNotificationModal provider={telegramProvider()} onClose={() => undefined} />);

    const label = await screen.findByText(/forum topic id/i);
    // Required fields are marked with a trailing asterisk — this one must not be.
    expect(label.textContent).not.toContain('*');
    expect(screen.getByText(/leave empty for the general topic/i)).toBeInTheDocument();
  });

  it('does not offer the field for other providers', async () => {
    render(<AddNotificationModal provider={buildProvider()} onClose={() => undefined} />);

    await screen.findByDisplayValue('My ntfy');
    expect(screen.queryByText(/forum topic id/i)).not.toBeInTheDocument();
  });

  it('round-trips the topic id into config on save', async () => {
    let captured: { config: Record<string, unknown> } | null = null;
    server.use(
      http.patch('*/api/v1/notifications/1', async ({ request }) => {
        captured = (await request.json()) as { config: Record<string, unknown> };
        return HttpResponse.json({ id: 1 });
      }),
    );

    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<AddNotificationModal provider={telegramProvider()} onClose={onClose} />);

    await user.type(await screen.findByPlaceholderText('123'), '25');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(captured).not.toBeNull();
    expect(captured!.config).toMatchObject({ chat_id: '-100123', message_thread_id: '25' });
  });

  it('keeps the config free of the key when the field is left empty', async () => {
    let captured: { config: Record<string, unknown> } | null = null;
    server.use(
      http.patch('*/api/v1/notifications/1', async ({ request }) => {
        captured = (await request.json()) as { config: Record<string, unknown> };
        return HttpResponse.json({ id: 1 });
      }),
    );

    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<AddNotificationModal provider={telegramProvider()} onClose={onClose} />);

    await screen.findByPlaceholderText('123');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(captured!.config).not.toHaveProperty('message_thread_id');
  });

  it('blocks save on a non-numeric topic id', async () => {
    // Reaches the form via a config written by the API rather than the picker —
    // the number input itself already filters most junk out.
    let patched = false;
    server.use(
      http.patch('*/api/v1/notifications/1', async () => {
        patched = true;
        return HttpResponse.json({ id: 1 });
      }),
    );

    const onClose = vi.fn();
    const user = userEvent.setup();
    render(
      <AddNotificationModal
        provider={telegramProvider({ bot_token: 'x', chat_id: '-100123', message_thread_id: 'General' })}
        onClose={onClose}
      />,
    );

    await screen.findByText(/forum topic id/i);
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    expect(await screen.findByText(/forum topic id must be a number/i)).toBeInTheDocument();
    expect(patched).toBe(false);
    expect(onClose).not.toHaveBeenCalled();
  });
});


describe('AddNotificationModal — Notify!', () => {
  const notifyProvider = (config: Record<string, unknown> = {}) => buildProvider({
    name: 'My Notify!',
    provider_type: 'notify',
    config: { device_id: 'IO12345678901234', token: 'notify-secret', ...config },
  });

  const liveActivitiesSwitch = () => within(
    screen.getByRole('group', { name: 'Live Activities (iOS)' }),
  ).getByRole('switch');

  const widgetsSwitch = () => within(
    screen.getByRole('group', { name: 'Lock Screen Widgets (iOS)' }),
  ).getByRole('switch');

  const photoSwitch = () => screen.getAllByRole('switch').find(
    (toggle) => toggle.parentElement?.textContent?.includes('Attach Photo'),
  )!;

  it('offers Notify! with masked credentials and Live Activities disabled by default', async () => {
    const user = userEvent.setup();
    render(<AddNotificationModal onClose={() => undefined} />);
    await user.selectOptions(await screen.findByDisplayValue('Email'), 'notify');

    expect(screen.getByLabelText(/device or group id/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/notify! token/i)).toHaveAttribute('type', 'password');
    expect(liveActivitiesSwitch()).toHaveAttribute('aria-checked', 'false');
    expect(widgetsSwitch()).toHaveAttribute('aria-checked', 'false');
    expect(screen.queryByText('Live Activity appearance')).not.toBeInTheDocument();
    expect(screen.getByText(/Use an iOS device ID; add a separate provider for group pushes/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /test configuration/i })).toBeDisabled();
    await user.type(screen.getByLabelText(/device or group id/i), 'GRP12345678901234');
    expect(screen.getByRole('button', { name: /test configuration/i })).toBeDisabled();
  });

  it('creates a group push provider with Live Activities explicitly disabled', async () => {
    let captured: Record<string, unknown> | null = null;
    server.use(http.post('*/api/v1/notifications/', async ({ request }) => {
      captured = await request.json() as Record<string, unknown>;
      return HttpResponse.json({ id: 2 });
    }));
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<AddNotificationModal onClose={onClose} />);
    await user.selectOptions(await screen.findByDisplayValue('Email'), 'notify');
    await user.type(screen.getByPlaceholderText(/My Notifications/i), 'Notify! group');
    await user.type(screen.getByLabelText(/device or group id/i), 'GRP12345678901234');
    await user.type(screen.getByLabelText(/notify! token/i), 'secret');
    await user.click(screen.getByRole('button', { name: /^add$/i }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(captured).toMatchObject({
      provider_type: 'notify',
      config: { device_id: 'GRP12345678901234', token: 'secret', live_activities: false },
    });
  });

  it('saves tile customization with boolean and array types without enabling push events', async () => {
    let captured: Record<string, unknown> | null = null;
    server.use(http.patch('*/api/v1/notifications/1', async ({ request }) => {
      captured = await request.json() as Record<string, unknown>;
      return HttpResponse.json({ id: 1 });
    }));
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<AddNotificationModal provider={notifyProvider()} onClose={onClose} />);
    await user.click(liveActivitiesSwitch());
    expect(screen.getByText(/Event toggles below control push alerts only/)).toBeInTheDocument();
    expect(screen.getByText(/Quiet hours block new activities; existing activities still update and end/)).toBeInTheDocument();
    await user.click(screen.getByText('Live Activity appearance'));
    await user.selectOptions(screen.getByLabelText('Hide file name on tile'), 'true');
    await user.selectOptions(screen.getByLabelText('Show print stage'), 'true');
    await user.selectOptions(screen.getByLabelText('Progress style'), 'segments');
    await user.selectOptions(screen.getByLabelText('Time Sensitive problem alerts'), 'true');
    await user.click(screen.getByRole('checkbox', { name: 'ETA' }));
    await user.click(screen.getByRole('checkbox', { name: 'Bed' }));
    await user.type(screen.getByLabelText('Dashboard URL (optional)'), 'https://bambuddy.example.com');
    await user.type(screen.getByLabelText('Tint (optional)'), '#123456');
    await user.type(screen.getByLabelText('Symbol (optional)'), 'printer');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(captured).toMatchObject({
      on_print_start: false,
      on_print_progress: false,
      config: {
        live_activities: true,
        live_activity_privacy: true,
        live_activity_stage: true,
        live_activity_style: 'segments',
        live_activity_metrics: ['eta', 'bed'],
        live_activity_button_url: 'https://bambuddy.example.com',
        live_activity_tint: '#123456',
        live_activity_symbol: 'printer',
        time_sensitive: true,
      },
    });
  });

  it('preserves existing customization while disabling Live Activities', async () => {
    let captured: Record<string, unknown> | null = null;
    server.use(http.patch('*/api/v1/notifications/1', async ({ request }) => {
      captured = await request.json() as Record<string, unknown>;
      return HttpResponse.json({ id: 1 });
    }));
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<AddNotificationModal provider={notifyProvider({
      live_activities: true,
      live_activity_privacy: true,
      live_activity_style: 'none',
      live_activity_metrics: ['layers', 'nozzle'],
      time_sensitive: false,
    })} onClose={onClose} />);
    expect(liveActivitiesSwitch()).toHaveAttribute('aria-checked', 'true');
    await user.click(screen.getByText('Live Activity appearance'));
    expect(screen.getByLabelText('Hide file name on tile')).toHaveValue('true');
    expect(screen.getByLabelText('Progress style')).toHaveValue('none');
    expect(screen.getByRole('checkbox', { name: 'Layer' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Nozzle' })).toBeChecked();
    await user.click(liveActivitiesSwitch());
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(captured).toMatchObject({ config: {
      live_activities: false,
      live_activity_privacy: true,
      live_activity_style: 'none',
      live_activity_metrics: ['layers', 'nozzle'],
      time_sensitive: false,
    } });
  });

  it.each(['GRP12345678901234', 'wb12345678901234', 'MC12345678901234'])('keeps %s eligible for pushes with Live Activities disabled', async (deviceId) => {
    let captured: Record<string, unknown> | null = null;
    server.use(http.patch('*/api/v1/notifications/1', async ({ request }) => {
      captured = await request.json() as Record<string, unknown>;
      return HttpResponse.json({ id: 1 });
    }));
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<AddNotificationModal provider={notifyProvider({ device_id: deviceId, live_activities: true, lock_screen_widgets: true })} onClose={onClose} />);
    expect(liveActivitiesSwitch()).toBeDisabled();
    expect(liveActivitiesSwitch()).toHaveAttribute('aria-checked', 'false');
    expect(widgetsSwitch()).toBeDisabled();
    expect(widgetsSwitch()).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByText(/Group, browser and macOS IDs use push notifications only/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /test configuration/i })).toBeEnabled();
    expect(screen.queryByText('Live Activity appearance')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(captured).toMatchObject({ config: {
      device_id: deviceId,
      token: 'notify-secret',
      live_activities: false,
      lock_screen_widgets: false,
    } });
  });

  it.each(['GRP12345678901234', 'WB12345678901234'])('turns off Live Activities when changing an iOS ID to %s', async (deviceId) => {
    let captured: Record<string, unknown> | null = null;
    server.use(http.post('*/api/v1/notifications/test-config', async ({ request }) => {
      captured = await request.json() as Record<string, unknown>;
      return HttpResponse.json({ success: true, message: 'Push delivered' });
    }));
    const user = userEvent.setup();
    render(<AddNotificationModal provider={notifyProvider({ live_activities: true, lock_screen_widgets: true })} onClose={() => undefined} />);
    expect(liveActivitiesSwitch()).toHaveAttribute('aria-checked', 'true');
    expect(widgetsSwitch()).toHaveAttribute('aria-checked', 'true');
    const target = screen.getByLabelText(/device or group id/i);
    await user.clear(target);
    await user.type(target, deviceId);
    expect(liveActivitiesSwitch()).toBeDisabled();
    expect(liveActivitiesSwitch()).toHaveAttribute('aria-checked', 'false');
    expect(widgetsSwitch()).toBeDisabled();
    expect(widgetsSwitch()).toHaveAttribute('aria-checked', 'false');
    expect(photoSwitch()).toBeDisabled();
    expect(photoSwitch()).toHaveAttribute('aria-checked', 'false');
    await user.click(screen.getByRole('button', { name: /test configuration/i }));
    expect(await screen.findByText('Push delivered')).toBeInTheDocument();
    expect(captured).toMatchObject({ attach_photo: false });
    expect(captured).toMatchObject({ config: {
      device_id: deviceId,
      token: 'notify-secret',
      live_activities: false,
      lock_screen_widgets: false,
    } });
    await user.clear(target);
    await user.type(target, 'IO12345678901234');
    expect(liveActivitiesSwitch()).toBeEnabled();
    expect(liveActivitiesSwitch()).toHaveAttribute('aria-checked', 'false');
  });

  it('sends setup tests through the push test endpoint with typed config', async () => {
    let captured: Record<string, unknown> | null = null;
    server.use(http.post('*/api/v1/notifications/test-config', async ({ request }) => {
      captured = await request.json() as Record<string, unknown>;
      return HttpResponse.json({ success: true, message: 'Push delivered' });
    }));
    const user = userEvent.setup();
    render(<AddNotificationModal provider={notifyProvider({ live_activities: true })} onClose={() => undefined} />);
    expect(screen.getByText(/Testing sends a push notification/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /test configuration/i }));
    expect(await screen.findByText('Push delivered')).toBeInTheDocument();
    expect(captured).toMatchObject({
      provider_type: 'notify',
      config: { device_id: 'IO12345678901234', token: 'notify-secret', live_activities: true, live_activity_metrics: [] },
    });
  });

  it('clears Notify customization when changing provider type', async () => {
    const user = userEvent.setup();
    render(<AddNotificationModal onClose={() => undefined} />);
    const providerSelect = await screen.findByDisplayValue('Email');
    await user.selectOptions(providerSelect, 'notify');
    await user.click(liveActivitiesSwitch());
    await user.click(screen.getByText('Live Activity appearance'));
    await user.click(screen.getByRole('checkbox', { name: 'ETA' }));
    await user.selectOptions(providerSelect, 'ntfy');
    await user.selectOptions(providerSelect, 'notify');
    expect(liveActivitiesSwitch()).toHaveAttribute('aria-checked', 'false');
    await user.click(liveActivitiesSwitch());
    await user.click(screen.getByText('Live Activity appearance'));
    expect(screen.getByRole('checkbox', { name: 'ETA' })).not.toBeChecked();
  });

  it('defaults to the timer with optional metrics unchecked', async () => {
    const user = userEvent.setup();
    render(<AddNotificationModal provider={notifyProvider({ live_activities: true })} onClose={() => undefined} />);
    await user.click(screen.getByText('Live Activity appearance'));
    expect(screen.getByText(/The timer is shown by default/)).toBeInTheDocument();
    for (const checkbox of screen.getAllByRole('checkbox')) expect(checkbox).not.toBeChecked();
  });

  it('enables Lock Screen widgets independently of Live Activities and push events', async () => {
    let captured: Record<string, unknown> | null = null;
    server.use(http.patch('*/api/v1/notifications/1', async ({ request }) => {
      captured = await request.json() as Record<string, unknown>;
      return HttpResponse.json({ id: 1 });
    }));
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<AddNotificationModal provider={notifyProvider()} onClose={onClose} />);
    expect(screen.getByText(/iOS refreshes roughly every 15 minutes or longer/)).toBeInTheDocument();
    expect(screen.getByText(/Only active printers outside maintenance mode receive widgets/)).toBeInTheDocument();
    expect(screen.getByText(/Notify! allows 10 widgets per device in total/)).toBeInTheDocument();
    expect(screen.getByDisplayValue('All printers')).toBeEnabled();
    await user.click(widgetsSwitch());
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(captured).toMatchObject({
      on_print_start: false,
      on_print_progress: false,
      printer_id: null,
      config: { token: 'notify-secret', live_activities: false, lock_screen_widgets: true },
    });
  });

  it.each(['GRP12345678901234', 'WB12345678901234'])('saves %s with photo attachments disabled', async (deviceId) => {
    let captured: Record<string, unknown> | null = null;
    server.use(http.patch('*/api/v1/notifications/1', async ({ request }) => {
      captured = await request.json() as Record<string, unknown>;
      return HttpResponse.json({ id: 1 });
    }));
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<AddNotificationModal provider={notifyProvider({ device_id: deviceId })} onClose={onClose} />);
    expect(photoSwitch()).toBeDisabled();
    expect(photoSwitch()).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByText(/Browser and group recipients support text notifications only/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(captured).toMatchObject({ attach_photo: false, config: { device_id: deviceId, token: 'notify-secret' } });
  });

  it.each(['GRP12345678901234', 'WB12345678901234'])('disables photo attachments in the %s provider card', async (deviceId) => {
    const user = userEvent.setup();
    render(<NotificationProviderCard provider={notifyProvider({ device_id: deviceId, live_activities: true, lock_screen_widgets: true })} onEdit={() => undefined} />);
    expect(screen.queryByText('Live Activities (iOS)')).not.toBeInTheDocument();
    expect(screen.queryByText('Lock Screen Widgets (iOS)')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /event settings/i }));
    expect(photoSwitch()).toBeDisabled();
    expect(photoSwitch()).toHaveAttribute('aria-checked', 'false');
  });

  it('shows a separate badge when Lock Screen widgets are enabled', () => {
    render(<NotificationProviderCard provider={notifyProvider({ lock_screen_widgets: true })} onEdit={() => undefined} />);
    expect(screen.getByText('Lock Screen Widgets (iOS)')).toBeInTheDocument();
    expect(screen.queryByText('Live Activities (iOS)')).not.toBeInTheDocument();
  });

  it('shows the Live Activities badge on enabled Notify! provider cards', () => {
    render(<NotificationProviderCard provider={notifyProvider({ live_activities: true })} onEdit={() => undefined} />);
    expect(screen.getByText('Notify!')).toBeInTheDocument();
    expect(screen.getByText('Live Activities (iOS)')).toBeInTheDocument();
  });

  it('does not show the Live Activities badge for push-only providers', () => {
    render(<NotificationProviderCard provider={notifyProvider({ live_activities: false })} onEdit={() => undefined} />);
    expect(screen.queryByText('Live Activities (iOS)')).not.toBeInTheDocument();
  });
});
