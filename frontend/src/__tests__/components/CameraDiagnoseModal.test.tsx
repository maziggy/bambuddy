/**
 * Tests for the camera diagnostic modal (#1395 follow-up).
 *
 * Covers the three observable behaviours that matter for user-facing
 * triage: the modal kicks off the diagnostic on mount, renders per-
 * stage results when the API replies, and maps the summary code to a
 * translated remediation hint. Each test mocks the API client so the
 * suite never actually opens a socket.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nextProvider } from 'react-i18next';
import i18n from '../../i18n';
import { CameraDiagnoseModal } from '../../components/CameraDiagnoseModal';
import { api, type CameraDiagnoseResult } from '../../api/client';

function renderModal() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <CameraDiagnoseModal printerId={1} printerName="Test P2S" onClose={onClose} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return { onClose };
}

describe('CameraDiagnoseModal', () => {
  it('runs the diagnostic on mount and shows per-stage results', async () => {
    const okResult: CameraDiagnoseResult = {
      printer_id: 1,
      protocol: 'rtsp',
      port: 322,
      profile: 'P2S',
      overall_status: 'ok',
      stages: [
        { name: 'tcp_reachable', status: 'ok', duration_ms: 12, code: null },
        { name: 'first_frame', status: 'ok', duration_ms: 1230, code: null },
      ],
      summary_code: 'all_ok',
    };
    const spy = vi.spyOn(api, 'diagnoseCamera').mockResolvedValue(okResult);

    renderModal();

    // Mounted → API called once
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));

    // Stage names render via i18n
    expect(await screen.findByText(/Network reachability/i)).toBeInTheDocument();
    expect(screen.getByText(/Frame capture/i)).toBeInTheDocument();

    // Per-stage duration is shown for support triage
    expect(screen.getByText(/12 ms/i)).toBeInTheDocument();
    expect(screen.getByText(/1230 ms/i)).toBeInTheDocument();

    // Summary remediation message is rendered translated
    expect(screen.getByText(/Camera is working/i)).toBeInTheDocument();

    // Metadata for support triage
    expect(screen.getByText('rtsp')).toBeInTheDocument();
    expect(screen.getByText('322')).toBeInTheDocument();
    expect(screen.getByText('P2S')).toBeInTheDocument();

    spy.mockRestore();
  });

  it('maps a failure summary code to a translated remediation hint', async () => {
    const failedResult: CameraDiagnoseResult = {
      printer_id: 1,
      protocol: 'rtsp',
      port: 322,
      profile: 'P2S',
      overall_status: 'failed',
      stages: [
        { name: 'tcp_reachable', status: 'failed', duration_ms: 3001, code: 'tcp_timeout' },
        { name: 'first_frame', status: 'skipped', duration_ms: 0, code: null },
      ],
      summary_code: 'printer_unreachable',
    };
    const spy = vi.spyOn(api, 'diagnoseCamera').mockResolvedValue(failedResult);

    renderModal();

    // The remediation hint for printer_unreachable mentions IP / network /
    // power — the user-facing fix-it instructions, not the raw summary code.
    expect(await screen.findByText(/IP address/i)).toBeInTheDocument();

    // The machine-readable stage code is also surfaced (small font) for
    // support triage so users can paste it into a ticket.
    expect(screen.getByText('tcp_timeout')).toBeInTheDocument();

    spy.mockRestore();
  });

  it('re-runs the diagnostic when the user clicks Run again', async () => {
    const okResult: CameraDiagnoseResult = {
      printer_id: 1,
      protocol: 'rtsp',
      port: 322,
      profile: 'P2S',
      overall_status: 'ok',
      stages: [{ name: 'tcp_reachable', status: 'ok', duration_ms: 12, code: null }],
      summary_code: 'all_ok',
    };
    const spy = vi.spyOn(api, 'diagnoseCamera').mockResolvedValue(okResult);

    renderModal();

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByText(/Run again/i));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));

    spy.mockRestore();
  });
});
