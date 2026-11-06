/**
 * A saved plug's password and REST headers come back as a placeholder. The
 * dialog keeps it, tests a saved plug by its id so the backend fills it in,
 * and asks for it again when the plug's address changes.
 */

import { describe, it, expect, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { AddSmartPlugModal } from '../../components/AddSmartPlugModal';
import type { SmartPlug } from '../../api/client';

const M = '********';

afterEach(() => server.resetHandlers());

function plug(overrides: Partial<SmartPlug>): SmartPlug {
  return {
    id: 5,
    name: 'Plug',
    plug_type: 'tasmota',
    ip_address: '10.0.0.5',
    username: 'admin',
    password: M,
    enabled: true,
    printer_id: null,
    ...overrides,
  } as unknown as SmartPlug;
}

describe('AddSmartPlugModal saved secrets', () => {
  it('tests a saved Tasmota plug by its id', async () => {
    let body: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/v1/smart-plugs/test-connection', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true, state: 'ON', device_name: 'x' });
      }),
    );
    const user = userEvent.setup();
    render(<AddSmartPlugModal plug={plug({})} onClose={() => undefined} />);

    await user.click(await screen.findByRole('button', { name: /^test$/i }));

    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toMatchObject({ ip_address: '10.0.0.5', password: M, plug_id: 5 });
  });

  it('says the saved password is kept, and asks for it again for a new address', async () => {
    const user = userEvent.setup();
    render(<AddSmartPlugModal plug={plug({})} onClose={() => undefined} />);

    expect(await screen.findByText(/leave it as is to keep it/i)).toBeInTheDocument();

    const ip = screen.getByDisplayValue('10.0.0.5');
    await user.clear(ip);
    await user.type(ip, '10.0.0.66');

    expect(screen.getByText(/the address changed: enter it again/i)).toBeInTheDocument();
  });

  it('tests a saved REST plug by its id, and asks for the headers again for a new URL', async () => {
    let body: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/v1/smart-plugs/rest/test-connection', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true, error: null });
      }),
    );
    const user = userEvent.setup();
    const rest = plug({
      plug_type: 'rest',
      ip_address: null,
      password: null,
      rest_on_url: 'http://plug.example/on',
      rest_off_url: 'http://plug.example/off',
      rest_headers: M,
    } as Partial<SmartPlug>);
    render(<AddSmartPlugModal plug={rest} onClose={() => undefined} />);

    await user.click(await screen.findByRole('button', { name: /test connection/i }));
    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toMatchObject({ url: 'http://plug.example/on', headers: M, plug_id: 5 });

    const on = screen.getByDisplayValue('http://plug.example/on');
    await user.clear(on);
    await user.type(on, 'http://elsewhere.example/on');
    expect(screen.getByText(/the address changed: enter it again/i)).toBeInTheDocument();
  });
});
