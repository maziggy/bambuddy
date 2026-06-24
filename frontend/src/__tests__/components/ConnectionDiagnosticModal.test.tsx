/**
 * Tests for the connection diagnostic modal.
 *
 * Covers the user-facing contract: the modal runs the diagnostic on mount,
 * renders each check's localized title and fix text keyed on id + status,
 * picks the right API for printer vs pre-add mode, and re-runs on retry.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nextProvider } from 'react-i18next';
import i18n from '../../i18n';
import { ConnectionDiagnosticModal } from '../../components/ConnectionDiagnostic';
import { api, type PrinterDiagnosticResult } from '../../api/client';

const PROBLEM_RESULT: PrinterDiagnosticResult = {
  printer_id: 1,
  ip_address: '192.168.1.50',
  overall: 'problems',
  checks: [
    { id: 'port_mqtt', status: 'pass', params: {} },
    { id: 'port_ftps', status: 'pass', params: {} },
    { id: 'port_rtsps', status: 'warn', params: {} },
    { id: 'network_mode', status: 'pass', params: { mode: 'host' } },
    { id: 'subnet', status: 'pass', params: {} },
    { id: 'mqtt_auth', status: 'pass', params: {} },
    { id: 'developer_mode', status: 'fail', params: {} },
  ],
};

function renderModal(props: Parameters<typeof ConnectionDiagnosticModal>[0]) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <ConnectionDiagnosticModal {...props} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe('ConnectionDiagnosticModal', () => {
  it('runs the diagnostic on mount and renders check titles + the overall banner', async () => {
    const spy = vi.spyOn(api, 'diagnosePrinter').mockResolvedValue(PROBLEM_RESULT);

    renderModal({ printerId: 1, printerName: 'Test P1S', onClose: vi.fn() });

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy).toHaveBeenCalledWith(1);

    // Each check's localized title renders.
    expect(await screen.findByText(/Control port \(MQTT 8883\)/i)).toBeInTheDocument();
    expect(screen.getByText(/LAN Developer Mode/i)).toBeInTheDocument();

    // The failing developer_mode check shows its fix text.
    expect(screen.getByText(/Developer Mode is OFF/i)).toBeInTheDocument();

    // Overall banner reflects "problems".
    expect(
      screen.getByText(/Found problems that explain why the printer/i),
    ).toBeInTheDocument();

    spy.mockRestore();
  });

  it('uses the pre-add API when given a connection instead of a printerId', async () => {
    const spy = vi.spyOn(api, 'diagnoseConnection').mockResolvedValue({
      ...PROBLEM_RESULT,
      printer_id: null,
    });

    renderModal({
      connection: { ip_address: '192.168.1.99', serial_number: '01P', access_code: 'abc' },
      onClose: vi.fn(),
    });

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy).toHaveBeenCalledWith({
      ip_address: '192.168.1.99',
      serial_number: '01P',
      access_code: 'abc',
    });

    spy.mockRestore();
  });

  it('re-runs the diagnostic when the user clicks Run again', async () => {
    const spy = vi.spyOn(api, 'diagnosePrinter').mockResolvedValue(PROBLEM_RESULT);

    renderModal({ printerId: 1, printerName: 'Test P1S', onClose: vi.fn() });
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByText(/Run again/i));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));

    spy.mockRestore();
  });

  it('renders model-specific camera port diagnostics', async () => {
    const spy = vi.spyOn(api, 'diagnosePrinter').mockResolvedValue({
      ...PROBLEM_RESULT,
      overall: 'warnings',
      checks: [
        { id: 'port_mqtt', status: 'pass', params: {} },
        { id: 'port_ftps', status: 'pass', params: {} },
        {
          id: 'port_rtsps',
          status: 'warn',
          params: { protocol: 'Chamber Image', port: 6000 },
        },
      ],
    });

    renderModal({ printerId: 1, printerName: 'Test A1 Mini', onClose: vi.fn() });

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/Camera port \(Chamber Image 6000\)/i)).toBeInTheDocument();
    expect(screen.getByText(/Port 6000 is unreachable/i)).toBeInTheDocument();

    spy.mockRestore();
  });
});
