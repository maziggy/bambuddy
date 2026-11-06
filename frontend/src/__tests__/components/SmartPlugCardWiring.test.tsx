/**
 * SmartPlugCard wiring: each control sends the request for THIS plug, and
 * the destructive ones (power off, delete) send nothing when cancelled.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { SmartPlugCard } from '../../components/SmartPlugCard';
import type { SmartPlug } from '../../api/client';

type Sent = { method: string; path: string; body: unknown };

function makePlug(overrides: Partial<SmartPlug> = {}): SmartPlug {
  return {
    id: 42,
    name: 'Wiring Plug',
    plug_type: 'tasmota',
    ip_address: '192.0.2.10',
    ha_entity_id: null,
    printer_id: null,
    controls_printer_power: true,
    enabled: true,
    auto_on: false,
    auto_off: false,
    auto_off_persistent: false,
    off_delay_mode: 'time',
    off_delay_minutes: 5,
    off_temp_threshold: 70,
    username: null,
    password: null,
    power_alert_enabled: false,
    schedule_enabled: false,
    schedule_on_time: null,
    schedule_off_time: null,
    last_state: 'ON',
    show_in_switchbar: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  } as unknown as SmartPlug;
}

let sent: Sent[];

beforeEach(() => {
  sent = [];
  const record = async (request: Request) => {
    const url = new URL(request.url);
    let body: unknown = null;
    try {
      body = await request.json();
    } catch {
      body = null;
    }
    sent.push({ method: request.method, path: url.pathname, body });
  };
  server.use(
    http.get('*/api/v1/smart-plugs/:id/status', () =>
      HttpResponse.json({ state: 'ON', reachable: true, device_name: null, energy: null }),
    ),
    http.get('*/api/v1/printers/', () => HttpResponse.json([])),
    http.post('*/api/v1/smart-plugs/:id/control', async ({ request }) => {
      await record(request);
      return HttpResponse.json({ success: true, action: 'off' });
    }),
    http.patch('*/api/v1/smart-plugs/:id', async ({ request }) => {
      await record(request);
      return HttpResponse.json(makePlug());
    }),
    http.delete('*/api/v1/smart-plugs/:id', async ({ request }) => {
      await record(request);
      return new HttpResponse(null, { status: 204 });
    }),
  );
});

const writes = () => sent.filter((s) => s.method !== 'GET');

async function renderReady() {
  const user = userEvent.setup();
  render(<SmartPlugCard plug={makePlug()} onEdit={() => undefined} />);
  // The power buttons are enabled only once the status says the plug is reachable.
  const off = await screen.findByRole('button', { name: 'Off' });
  await waitFor(() => expect(off).not.toBeDisabled());
  return { user, off };
}

function toggleFor(label: string): HTMLInputElement {
  const row = screen.getByText(label).parentElement!.parentElement!;
  return row.querySelector('input[type="checkbox"]') as HTMLInputElement;
}

describe('SmartPlugCard wiring', () => {
  it('Power Off: confirming sends control "off" for this plug', async () => {
    const { user, off } = await renderReady();
    await user.click(off);

    const dialog = await screen.findByText('Turn Off Smart Plug');
    expect(dialog).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Turn Off' }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toEqual({ method: 'POST', path: '/api/v1/smart-plugs/42/control', body: { action: 'off' } });
  });

  it('Power Off: cancelling sends nothing', async () => {
    const { user, off } = await renderReady();
    await user.click(off);
    await screen.findByText('Turn Off Smart Plug');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByText('Turn Off Smart Plug')).not.toBeInTheDocument());
    expect(writes()).toEqual([]);
  });

  it('Power On: confirming sends control "on"', async () => {
    const { user } = await renderReady();
    await user.click(screen.getByRole('button', { name: 'On' }));
    await user.click(await screen.findByRole('button', { name: 'Turn On' }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toEqual({ method: 'POST', path: '/api/v1/smart-plugs/42/control', body: { action: 'on' } });
  });

  it('Delete: confirming sends DELETE for this plug', async () => {
    const { user } = await renderReady();
    await user.click(screen.getByText('Automation Settings'));
    const edit = await screen.findByRole('button', { name: 'Edit' });
    await user.click(edit.nextElementSibling as HTMLElement);

    await screen.findByText('Delete Smart Plug');
    await user.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ method: 'DELETE', path: '/api/v1/smart-plugs/42' });
  });

  it('Delete: cancelling sends nothing', async () => {
    const { user } = await renderReady();
    await user.click(screen.getByText('Automation Settings'));
    const edit = await screen.findByRole('button', { name: 'Edit' });
    await user.click(edit.nextElementSibling as HTMLElement);
    await screen.findByText('Delete Smart Plug');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByText('Delete Smart Plug')).not.toBeInTheDocument());
    expect(writes()).toEqual([]);
  });

  it.each([
    ['Auto On', { auto_on: true }],
    ['Auto Off', { auto_off: true }],
    ['Enabled', { enabled: false }],
  ])('toggle "%s" PATCHes only that field', async (label, expected) => {
    const { user } = await renderReady();
    await user.click(screen.getByText('Automation Settings'));
    await screen.findByText(label);
    await user.click(toggleFor(label));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ method: 'PATCH', path: '/api/v1/smart-plugs/42' });
    expect(writes()[0].body).toEqual(expected);
  });
});
