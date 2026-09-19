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

  it('shows the unsupported-model explanation for the external_storage skip reason (#2524)', async () => {
    const spy = vi.spyOn(api, 'diagnosePrinter').mockResolvedValue({
      ...PROBLEM_RESULT,
      overall: 'warnings',
      checks: [
        { id: 'external_storage', status: 'skip', params: { reason: 'unsupported_model' } },
      ],
    });

    renderModal({ printerId: 1, printerName: 'Test P1S', onClose: vi.fn() });

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    // The reason-specific variant renders, NOT the generic "needs a live
    // MQTT connection" skip text.
    expect(await screen.findByText(/no way to turn the option on/i)).toBeInTheDocument();
    expect(screen.queryByText(/needs a live MQTT connection/i)).not.toBeInTheDocument();

    spy.mockRestore();
  });

  it('names a refused access code when the printer said so (#2698)', async () => {
    const spy = vi.spyOn(api, 'diagnosePrinter').mockResolvedValue({
      ...PROBLEM_RESULT,
      checks: [{ id: 'mqtt_auth', status: 'fail', params: { reason: 'auth_rejected' } }],
    });

    renderModal({ printerId: 1, printerName: 'Test A1', onClose: vi.fn() });

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    // States the printer refused us, instead of the hedged "most likely wrong"
    // text used when all we know is that there's no session.
    expect(await screen.findByText(/refused Bambuddy's credentials/i)).toBeInTheDocument();
    expect(screen.queryByText(/most likely wrong/i)).not.toBeInTheDocument();

    spy.mockRestore();
  });

  it('hedges on the mqtt_auth failure when the printer gave no reason (#2698)', async () => {
    const spy = vi.spyOn(api, 'diagnosePrinter').mockResolvedValue({
      ...PROBLEM_RESULT,
      checks: [{ id: 'mqtt_auth', status: 'fail', params: {} }],
    });

    renderModal({ printerId: 1, printerName: 'Test A1', onClose: vi.fn() });

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/most likely wrong/i)).toBeInTheDocument();
    expect(screen.queryByText(/refused Bambuddy's credentials/i)).not.toBeInTheDocument();

    spy.mockRestore();
  });

  it('names the container engine in the network-mode check (#3092)', async () => {
    const spy = vi.spyOn(api, 'diagnosePrinter').mockResolvedValue({
      ...PROBLEM_RESULT,
      overall: 'ok',
      checks: [{ id: 'network_mode', status: 'pass', params: { mode: 'host', runtime: 'Podman' } }],
    });

    renderModal({ printerId: 1, printerName: 'Test P1S', onClose: vi.fn() });

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    // The title is no longer Docker-specific, and the engine the user
    // actually runs is named back to them.
    expect(await screen.findByText(/Container network mode/i)).toBeInTheDocument();
    expect(screen.getByText(/Running in Podman with host networking/i)).toBeInTheDocument();
    expect(screen.queryByText(/Docker network mode/i)).not.toBeInTheDocument();

    spy.mockRestore();
  });

  it('localizes an unnamed container engine instead of interpolating a raw word (#3092)', async () => {
    const spy = vi.spyOn(api, 'diagnosePrinter').mockResolvedValue({
      ...PROBLEM_RESULT,
      overall: 'ok',
      checks: [{ id: 'network_mode', status: 'pass', params: { mode: 'host', runtime: 'container' } }],
    });

    renderModal({ printerId: 1, printerName: 'Test P1S', onClose: vi.fn() });

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    // "a container", not the bare backend sentinel — the same slot carries a
    // localized noun phrase in every other locale.
    expect(await screen.findByText(/Running in a container with host networking/i)).toBeInTheDocument();

    spy.mockRestore();
  });

  it('says so when a container network mode cannot be read, instead of guessing (#3092)', async () => {
    const spy = vi.spyOn(api, 'diagnosePrinter').mockResolvedValue({
      ...PROBLEM_RESULT,
      overall: 'warnings',
      checks: [{ id: 'network_mode', status: 'skip', params: { reason: 'unknown', runtime: 'Podman' } }],
    });

    renderModal({ printerId: 1, printerName: 'Test P1S', onClose: vi.fn() });

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/network mode could not be determined/i)).toBeInTheDocument();
    // Must not claim bare metal, which is what sent the reporter looking
    // for the problem somewhere else.
    expect(screen.queryByText(/not running in a container/i)).not.toBeInTheDocument();

    spy.mockRestore();
  });

  it('does not offer host networking to a system container (#3092)', async () => {
    const spy = vi.spyOn(api, 'diagnosePrinter').mockResolvedValue({
      ...PROBLEM_RESULT,
      overall: 'ok',
      checks: [{ id: 'network_mode', status: 'skip', params: { reason: 'system_container', runtime: 'LXC' } }],
    });

    renderModal({ printerId: 1, printerName: 'Test P1S', onClose: vi.fn() });

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/LXC system container/i)).toBeInTheDocument();
    expect(screen.queryByText(/recreate the container/i)).not.toBeInTheDocument();

    spy.mockRestore();
  });

  it('names the interpreter when macOS has no signature to grant against (#3114)', async () => {
    const spy = vi.spyOn(api, 'diagnosePrinter').mockResolvedValue({
      ...PROBLEM_RESULT,
      overall: 'problems',
      checks: [
        {
          id: 'macos_local_network',
          status: 'warn',
          params: {
            reason: 'unsigned',
            executable: '/usr/local/Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14/bin/python3.14',
          },
        },
      ],
    });

    renderModal({ printerId: 1, printerName: 'Test P1S', onClose: vi.fn() });

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/macOS Local Network permission/i)).toBeInTheDocument();
    expect(screen.getByText(/has no code signature/i)).toBeInTheDocument();
    // The path is what tells the user which of several Pythons is meant.
    expect(screen.getByText(/Versions\/3\.14\/bin\/python3\.14/)).toBeInTheDocument();

    spy.mockRestore();
  });

  it('points a signed-but-blocked macOS install at System Settings (#3114)', async () => {
    const spy = vi.spyOn(api, 'diagnosePrinter').mockResolvedValue({
      ...PROBLEM_RESULT,
      overall: 'problems',
      checks: [{ id: 'macos_local_network', status: 'warn', params: { reason: 'permission' } }],
    });

    renderModal({ printerId: 1, printerName: 'Test P1S', onClose: vi.fn() });

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/Privacy & Security > Local Network/i)).toBeInTheDocument();
    // The signing repair must not be offered to a machine that is already
    // signed: re-signing it would revoke the grant it still has.
    expect(screen.queryByText(/update_macos\.sh/i)).not.toBeInTheDocument();

    spy.mockRestore();
  });

  it('falls back to the generic skip text when no reason is present', async () => {
    const spy = vi.spyOn(api, 'diagnosePrinter').mockResolvedValue({
      ...PROBLEM_RESULT,
      overall: 'warnings',
      checks: [{ id: 'external_storage', status: 'skip', params: {} }],
    });

    renderModal({ printerId: 1, printerName: 'Test X1C', onClose: vi.fn() });

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/needs a live MQTT connection/i)).toBeInTheDocument();

    spy.mockRestore();
  });
});
