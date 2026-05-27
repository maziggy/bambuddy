/**
 * Tests for the VirtualPrinterDiagnosticModal component.
 */

import { describe, it, expect, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { render } from '../utils';
import { VirtualPrinterDiagnosticModal } from '../../components/VirtualPrinterDiagnosticModal';
import type { VPDiagnosticResult } from '../../api/client';

const problemResult: VPDiagnosticResult = {
  vp_id: 3,
  vp_name: 'Garage VP',
  mode: 'immediate',
  overall: 'problems',
  checks: [
    { id: 'enabled', status: 'pass', params: {} },
    { id: 'running', status: 'fail', params: {} },
    { id: 'port_mqtt', status: 'fail', params: { port: 8883 } },
  ],
};

/** Stub the diagnostic endpoint and count how often it is hit. */
function setupDiagnostic(result: VPDiagnosticResult): { calls: () => number } {
  let count = 0;
  server.use(
    http.get('*/virtual-printers/:id/diagnostic', () => {
      count += 1;
      return HttpResponse.json(result);
    }),
  );
  return { calls: () => count };
}

describe('VirtualPrinterDiagnosticModal', () => {
  it('runs the diagnostic on mount and renders the checks', async () => {
    const probe = setupDiagnostic(problemResult);

    render(<VirtualPrinterDiagnosticModal vpId={3} vpName="Garage VP" onClose={() => {}} />);

    expect(await screen.findByText(/Found problems that explain/)).toBeInTheDocument();
    expect(probe.calls()).toBe(1);
    // Per-check titles render; the port param is interpolated into the title.
    expect(screen.getByText('Services running')).toBeInTheDocument();
    expect(screen.getByText('Control service (port 8883)')).toBeInTheDocument();
  });

  it('re-runs the diagnostic when "Run again" is clicked', async () => {
    const probe = setupDiagnostic(problemResult);
    const user = userEvent.setup();

    render(<VirtualPrinterDiagnosticModal vpId={3} vpName="Garage VP" onClose={() => {}} />);

    await screen.findByText(/Found problems that explain/);
    expect(probe.calls()).toBe(1);

    await user.click(screen.getByText('Run again'));
    await waitFor(() => expect(probe.calls()).toBe(2));
  });

  it('calls onClose when the Close button is clicked', async () => {
    setupDiagnostic({ ...problemResult, overall: 'ok' });
    const onClose = vi.fn();
    const user = userEvent.setup();

    render(<VirtualPrinterDiagnosticModal vpId={3} vpName="Garage VP" onClose={onClose} />);

    await screen.findByText(/set up correctly/);
    await user.click(screen.getByText('Close'));
    expect(onClose).toHaveBeenCalled();
  });
});
