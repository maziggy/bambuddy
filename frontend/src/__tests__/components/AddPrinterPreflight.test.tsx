/**
 * Tests for the Add-Printer setup-time pre-flight.
 *
 * On save, the modal runs the connection diagnostic; if any check fails it
 * warns (rather than blocks) before the printer is added.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import type { UserEvent } from '@testing-library/user-event';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

describe('AddPrinterModal pre-flight', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([])),
      http.get('/api/v1/queue/', () => HttpResponse.json([])),
      http.get('/api/v1/discovery/info', () =>
        HttpResponse.json({ is_docker: false, ssdp_running: false, scan_running: false, subnets: [] }),
      ),
    );
  });

  it('warns instead of saving when a connection check fails', async () => {
    const user = userEvent.setup();
    server.use(
      http.post('/api/v1/printers/diagnostic', () =>
        HttpResponse.json({
          printer_id: null,
          ip_address: '192.168.1.55',
          overall: 'problems',
          checks: [{ id: 'developer_mode', status: 'fail', params: {} }],
        }),
      ),
    );

    render(<PrintersPage />);
    await user.click(await screen.findByText(/add printer/i));

    await user.type(await screen.findByPlaceholderText('My Printer'), 'Test Printer');
    await user.type(screen.getByPlaceholderText('192.168.1.100 or printer.local'), '192.168.1.55');
    await user.type(screen.getByPlaceholderText('01P00A000000000'), '01P00A000000000');
    await user.type(screen.getByPlaceholderText('From printer settings'), '12345678');

    const submit = screen
      .getAllByRole('button', { name: /add printer/i })
      .find((b) => b.getAttribute('type') === 'submit')!;
    await user.click(submit);

    // The failed check surfaces a warning with a "save anyway" escape hatch.
    expect(await screen.findByText(/Some connection checks failed/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /save anyway/i })).toBeInTheDocument();
    expect(screen.getByText(/LAN Developer Mode/i)).toBeInTheDocument();
  });

  it('saves directly when all connection checks pass', async () => {
    const user = userEvent.setup();
    let created = false;
    server.use(
      http.post('/api/v1/printers/diagnostic', () =>
        HttpResponse.json({
          printer_id: null,
          ip_address: '192.168.1.55',
          overall: 'ok',
          checks: [{ id: 'developer_mode', status: 'pass', params: {} }],
        }),
      ),
      http.post('/api/v1/printers/', async () => {
        created = true;
        return HttpResponse.json({ id: 9, name: 'Test Printer' });
      }),
    );

    render(<PrintersPage />);
    await user.click(await screen.findByText(/add printer/i));

    await user.type(await screen.findByPlaceholderText('My Printer'), 'Test Printer');
    await user.type(screen.getByPlaceholderText('192.168.1.100 or printer.local'), '192.168.1.55');
    await user.type(screen.getByPlaceholderText('01P00A000000000'), '01P00A000000000');
    await user.type(screen.getByPlaceholderText('From printer settings'), '12345678');

    const submit = screen
      .getAllByRole('button', { name: /add printer/i })
      .find((b) => b.getAttribute('type') === 'submit')!;
    await user.click(submit);

    await waitFor(() => expect(created).toBe(true));
    expect(screen.queryByText(/Some connection checks failed/i)).not.toBeInTheDocument();
  });
});

// A corrected value resets the failed check, "Check again" runs it again, and
// the access code can be shown (#3624).
describe('a failed connection check after a correction (#3624)', () => {
  const WRONG = '12345678';
  let checked: Record<string, unknown>[];
  let created: Record<string, unknown> | null;

  beforeEach(() => {
    checked = [];
    created = null;
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([])),
      http.get('/api/v1/queue/', () => HttpResponse.json([])),
      http.get('/api/v1/discovery/info', () =>
        HttpResponse.json({ is_docker: false, ssdp_running: false, scan_running: false, subnets: [] }),
      ),
      // Only the mistyped code fails.
      http.post('/api/v1/printers/diagnostic', async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        checked.push(body);
        const fail = body.access_code === WRONG;
        return HttpResponse.json({
          printer_id: null,
          ip_address: body.ip_address,
          overall: fail ? 'problems' : 'ok',
          checks: [{ id: 'developer_mode', status: fail ? 'fail' : 'pass', params: {} }],
        });
      }),
      http.post('/api/v1/printers/', async ({ request }) => {
        created = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: 9, name: 'Test Printer' });
      }),
    );
  });

  async function failedAdd(user: UserEvent) {
    render(<PrintersPage />);
    await user.click(await screen.findByText(/add printer/i));
    await user.type(await screen.findByPlaceholderText('My Printer'), 'Test Printer');
    await user.type(screen.getByPlaceholderText('192.168.1.100 or printer.local'), '192.168.1.55');
    await user.type(screen.getByPlaceholderText('01P00A000000000'), '01P00A000000000');
    await user.type(screen.getByPlaceholderText('From printer settings'), WRONG);
    await user.click(addButton()!);
    await screen.findByText(/Some connection checks failed/i);
  }

  const addButton = () =>
    screen.queryAllByRole('button', { name: /add printer/i }).find((b) => b.getAttribute('type') === 'submit');

  it('clears the warning when the access code is corrected, and checks the new one', async () => {
    const user = userEvent.setup();
    await failedAdd(user);

    const code = screen.getByPlaceholderText('From printer settings');
    await user.clear(code);
    await user.type(code, '87654321');

    expect(screen.queryByText(/Some connection checks failed/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /save anyway/i })).not.toBeInTheDocument();
    await user.click(addButton()!);

    await waitFor(() => expect(created).not.toBeNull());
    expect(checked.at(-1)?.access_code).toBe('87654321');
    expect(created!.access_code).toBe('87654321');
  });

  it.each([
    ['IP address', '192.168.1.100 or printer.local'],
    ['serial number', '01P00A000000000'],
  ])('clears the warning when the %s changes', async (_field, placeholder) => {
    const user = userEvent.setup();
    await failedAdd(user);

    await user.type(screen.getByPlaceholderText(placeholder), '1');

    expect(screen.queryByText(/Some connection checks failed/i)).not.toBeInTheDocument();
    expect(addButton()).toBeDefined();
  });

  it('keeps the warning when a field the check does not read changes', async () => {
    const user = userEvent.setup();
    await failedAdd(user);

    await user.type(screen.getByPlaceholderText('My Printer'), ' 2');

    expect(screen.getByText(/Some connection checks failed/i)).toBeInTheDocument();
  });

  it('runs the check again on "Check again", and saves once it passes', async () => {
    const user = userEvent.setup();
    await failedAdd(user);
    expect(screen.queryByRole('button', { name: /^back$/i })).not.toBeInTheDocument();

    // Still the wrong code: checked again, still warned, nothing saved.
    await user.click(screen.getByRole('button', { name: /check again/i }));
    await waitFor(() => expect(checked).toHaveLength(2));
    expect(await screen.findByText(/Some connection checks failed/i)).toBeInTheDocument();
    expect(created).toBeNull();

    // The printer answers now (here: the mock no longer fails it).
    server.use(
      http.post('/api/v1/printers/diagnostic', () =>
        HttpResponse.json({ printer_id: null, ip_address: '192.168.1.55', overall: 'ok', checks: [] }),
      ),
    );
    await user.click(screen.getByRole('button', { name: /check again/i }));
    await waitFor(() => expect(created).not.toBeNull());
  });

  it('shows and hides the access code', async () => {
    const user = userEvent.setup();
    render(<PrintersPage />);
    await user.click(await screen.findByText(/add printer/i));
    const code = await screen.findByPlaceholderText('From printer settings');
    expect(code).toHaveAttribute('type', 'password');

    await user.click(screen.getByRole('button', { name: 'Show access code' }));
    expect(code).toHaveAttribute('type', 'text');

    await user.click(screen.getByRole('button', { name: 'Hide access code' }));
    expect(code).toHaveAttribute('type', 'password');
  });

  it('does the same in the Edit Printer dialog', async () => {
    const user = userEvent.setup();
    const printer = {
      id: 1, name: 'A1 mini', ip_address: '192.168.1.100', serial_number: '0300AA000000001',
      access_code: '', model: 'A1 Mini', enabled: true, nozzle_diameter: 0.4, nozzle_type: 'hardened_steel',
      location: null, auto_archive: true, is_active: true, wear_cost_per_hour: null, dispatch_priority: null,
      require_plate_clear: null, created_at: '2024-01-01T00:00:00Z', updated_at: '2024-01-01T00:00:00Z',
    };
    let saved: Record<string, unknown> | null = null;
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([printer])),
      http.get('/api/v1/printers/:id/status', () => HttpResponse.json({ connected: false, state: 'IDLE' })),
      http.patch('/api/v1/printers/:id', async ({ request }) => {
        saved = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...printer, ...saved });
      }),
    );
    render(<PrintersPage />);
    await waitFor(() => expect(screen.getByText('A1 mini')).toBeInTheDocument());
    const menuBtn = [...document.querySelectorAll('button')].find((b) => b.querySelector('.lucide-ellipsis-vertical'))!;
    await user.click(menuBtn);
    await user.click(await screen.findByRole('button', { name: /^edit$/i }));
    await screen.findByText('Edit Printer');

    const code = screen.getByLabelText('Access Code');
    await user.type(code, WRONG);
    await user.click(screen.getByRole('button', { name: /save changes/i }));
    await screen.findByText(/Some connection checks failed/i);
    await user.click(screen.getByRole('button', { name: /check again/i }));
    await waitFor(() => expect(checked).toHaveLength(2));
    expect(await screen.findByText(/Some connection checks failed/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Show access code' }));
    expect(code).toHaveAttribute('type', 'text');

    await user.clear(code);
    await user.type(code, '87654321');
    expect(screen.queryByText(/Some connection checks failed/i)).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /save changes/i }));
    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved!.access_code).toBe('87654321');
  });
});
