/**
 * Review-response coverage for BedCheckAiSettings (PR #2894):
 *
 * 1. The Status card fetches GET /bedcheck-ai/health on mount and shows each
 *    monitored printer's last-outcome badge (review point 1, settings side).
 * 2. The debounced auto-save surfaces a failed save (e.g. a 422 from the URL
 *    guard on a half-typed value) via the house toast, instead of failing
 *    silently.
 * 3. The Monitored-printers card's per-printer controls are gated on
 *    printers:update, matching the printer-card backend selector's gate.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { BedCheckAiSettings } from '../../components/BedCheckAiSettings';
import { setAuthToken } from '../../api/client';

const baseSettings = {
  bedcheck_backend: 'ai',
  bedcheck_ai_base_url: 'http://192.168.1.20:11434/v1',
  bedcheck_ai_model: 'qwen2.5vl:7b',
  bedcheck_ai_api_key: '',
};

const monitoredPrinter = {
  id: 1,
  name: 'X1C',
  serial_number: '01P00A000000001',
  ip_address: '192.168.1.100',
  model: 'X1C',
  is_active: true,
  auto_archive: true,
  plate_detection_enabled: true,
  bedcheck_backend_override: null,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

function mockBase(overrides?: { health?: Record<string, unknown>; printers?: unknown[] }) {
  server.use(
    http.get('/api/v1/settings/', () => HttpResponse.json(baseSettings)),
    http.get('/api/v1/printers/', () => HttpResponse.json(overrides?.printers ?? [monitoredPrinter])),
    http.get('/api/v1/bedcheck-ai/health', () =>
      HttpResponse.json({ printers: overrides?.health ?? {} }),
    ),
  );
}

function mockUserWith(permissions: string[]) {
  setAuthToken('test-token', 'session');
  server.use(
    http.get('*/api/v1/auth/status', () =>
      HttpResponse.json({ auth_enabled: true, requires_setup: false }),
    ),
    http.get('*/api/v1/auth/me', () =>
      HttpResponse.json({ id: 1, username: 'operator', is_admin: false, permissions }),
    ),
  );
}

afterEach(() => {
  server.resetHandlers();
  setAuthToken(null);
});

describe('BedCheckAiSettings — Status card health badges', () => {
  it('fetches /bedcheck-ai/health on mount and renders each outcome', async () => {
    mockBase({
      health: {
        '1': { outcome: 'ok', reason: null, at: new Date().toISOString(), request_mode: 'json_schema' },
      },
    });
    render(<BedCheckAiSettings />);

    expect(await screen.findByText(/Last check ok/i)).toBeInTheDocument();
  });

  it('shows the unavailable reason and "No checks yet" for an unmonitored-since-start printer', async () => {
    const secondPrinter = { ...monitoredPrinter, id: 2, name: 'A1 Mini' };
    mockBase({
      printers: [monitoredPrinter, secondPrinter],
      health: {
        '1': {
          outcome: 'unavailable',
          reason: 'connection refused',
          at: new Date().toISOString(),
          request_mode: 'json_object',
        },
        // printer 2 has no entry at all — no AI check since app start.
      },
    });
    render(<BedCheckAiSettings />);

    expect(await screen.findByText(/Unavailable since/i)).toBeInTheDocument();
    expect(screen.getByText(/connection refused/i)).toBeInTheDocument();
    expect(await screen.findByText('No checks yet')).toBeInTheDocument();
  });

  it('renders no health line for an OpenCV-monitored printer', async () => {
    // Health is an AI-backend concept. An override of 'opencv' means this
    // printer never runs an AI check, so a "No checks yet" line under it
    // would be a permanent non-resolving state that reads as a fault.
    const opencvPrinter = { ...monitoredPrinter, id: 2, name: 'A1 Mini', bedcheck_backend_override: 'opencv' };
    mockBase({ printers: [opencvPrinter], health: {} });
    render(<BedCheckAiSettings />);

    // The name appears in both the Monitored-printers card and the Status card.
    expect((await screen.findAllByText('A1 Mini')).length).toBeGreaterThan(0);
    expect(screen.queryByText('No checks yet')).not.toBeInTheDocument();
  });

  it('still renders the health line for a printer overridden to AI while the global backend is OpenCV', async () => {
    // The gate is on the EFFECTIVE backend, not the global one: a per-printer
    // 'ai' override must keep its badge even when the global default is
    // OpenCV, and a global-only 'opencv' install must not show badges at all.
    const aiOverride = { ...monitoredPrinter, id: 3, name: 'P1S', bedcheck_backend_override: 'ai' };
    server.use(
      http.get('/api/v1/settings/', () => HttpResponse.json({ ...baseSettings, bedcheck_backend: 'opencv' })),
      http.get('/api/v1/printers/', () => HttpResponse.json([monitoredPrinter, aiOverride])),
      http.get('/api/v1/bedcheck-ai/health', () =>
        HttpResponse.json({
          printers: {
            '3': { outcome: 'ok', reason: null, at: new Date().toISOString(), request_mode: 'json_schema' },
          },
        }),
      ),
    );
    render(<BedCheckAiSettings />);

    // Printer 3 (explicit 'ai' override) shows its badge...
    expect(await screen.findByText(/Last check ok/i)).toBeInTheDocument();
    // ...while printer 1, inheriting the global 'opencv', shows nothing.
    expect(screen.queryByText('No checks yet')).not.toBeInTheDocument();
  });
});

describe('BedCheckAiSettings — auto-save error surfacing', () => {
  it('shows a toast when the debounced auto-save fails (e.g. a 422 from the URL guard)', async () => {
    mockBase();
    server.use(
      http.put('/api/v1/settings/', () =>
        HttpResponse.json(
          { detail: [{ msg: 'Value error, base_url must be a valid http(s) URL' }] },
          { status: 422 },
        ),
      ),
    );
    render(<BedCheckAiSettings />);

    const modelInput = await screen.findByDisplayValue('qwen2.5vl:7b');
    await userEvent.type(modelInput, '-edited');

    expect(await screen.findByText(/base_url must be a valid http\(s\) URL/i, {}, { timeout: 3000 })).toBeInTheDocument();
  });
});

describe('BedCheckAiSettings — test connection request mode', () => {
  it('renders the discovered request mode on a successful test', async () => {
    mockBase();
    server.use(
      http.post('/api/v1/bedcheck-ai/test-connection', () =>
        HttpResponse.json({
          ok: true,
          error: null,
          latency_ms: 42,
          verdict: { is_empty: true, confidence: 0.9, reason: 'clear plate' },
          request_mode: 'json_schema',
        }),
      ),
    );
    render(<BedCheckAiSettings />);

    const testButton = await screen.findByRole('button', { name: /test connection/i });
    await userEvent.click(testButton);

    expect(await screen.findByText(/Backend reachable/i)).toBeInTheDocument();
    expect(await screen.findByText('Request mode: json_schema')).toBeInTheDocument();
  });

  it('appends the degraded-mode hint when the backend fell back to json_object', async () => {
    mockBase();
    server.use(
      http.post('/api/v1/bedcheck-ai/test-connection', () =>
        HttpResponse.json({
          ok: true,
          error: null,
          latency_ms: 42,
          verdict: { is_empty: true, confidence: 0.9, reason: 'clear plate' },
          request_mode: 'json_object',
        }),
      ),
    );
    render(<BedCheckAiSettings />);

    const testButton = await screen.findByRole('button', { name: /test connection/i });
    await userEvent.click(testButton);

    expect(await screen.findByText(/Request mode: json_object/i)).toBeInTheDocument();
    expect(screen.getByText(/Reduced JSON mode/i)).toBeInTheDocument();
  });

  it('does not render a request mode line on a failed test (field absent)', async () => {
    mockBase();
    server.use(
      http.post('/api/v1/bedcheck-ai/test-connection', () =>
        HttpResponse.json({
          ok: false,
          error: 'connection refused',
          latency_ms: null,
          verdict: null,
          // request_mode intentionally omitted -- matches the real failure
          // branches in bedcheck_ai.test_connection(), which return before
          // a mode is known.
        }),
      ),
    );
    render(<BedCheckAiSettings />);

    const testButton = await screen.findByRole('button', { name: /test connection/i });
    await userEvent.click(testButton);

    expect(await screen.findByText(/connection refused/i)).toBeInTheDocument();
    expect(screen.queryByText(/Request mode:/i)).not.toBeInTheDocument();
  });
});

describe('BedCheckAiSettings — Monitored-printers permission gate', () => {
  it('disables the per-printer controls without printers:update', async () => {
    mockBase();
    mockUserWith(['settings:read']);
    render(<BedCheckAiSettings />);

    const checkbox = await screen.findByRole('checkbox');
    expect(checkbox).toBeDisabled();
    const select = screen.getAllByRole('combobox').find((el) => (el as HTMLSelectElement).value === '');
    expect(select).toBeDisabled();
  });

  it('enables the per-printer controls with printers:update', async () => {
    mockBase();
    mockUserWith(['settings:read', 'printers:update']);
    render(<BedCheckAiSettings />);

    const checkbox = await screen.findByRole('checkbox');
    await waitFor(() => expect(checkbox).not.toBeDisabled());
    const select = screen.getAllByRole('combobox').find((el) => (el as HTMLSelectElement).value === '');
    expect(select).not.toBeDisabled();
  });
});
