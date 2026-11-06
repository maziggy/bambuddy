/**
 * HMSErrorModal wiring: the requests behind "Clear Errors" and the per-fault
 * action buttons (Resume / Retry / Ignore ...).
 *
 * HMSErrorModal.test.tsx covers rendering and the clear button's visibility;
 * this pins what is actually sent and to which printer.
 */
import { describe, it, expect, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { render } from '../utils';
import { HMSErrorModal } from '../../components/HMSErrorModal';
import type { HMSError } from '../../api/client';

const PRINTER_ID = 42;

// A 16-char hms[] fault with actions and a job id.
const actionable: HMSError = {
  attr: 0x07000700,
  code: '0x20009',
  module: 7,
  severity: 2,
  full_code: '0700070000020009',
  description: 'AMS filament ran out.',
  actions: ['IGNORE_RESUME', 'RETRY_PROBLEM_SOLVED'],
  job_id: 'job-777',
};

// A print_error-style fault without full_code: the 8-char short code is sent.
const shortOnly: HMSError = {
  attr: 0x0300,
  code: '0x400C',
  module: 3,
  severity: 1,
  description: 'The task was canceled.',
  actions: ['DBL_CHECK_RETRY'],
};

type Captured = { url: string; body: unknown };

function capture() {
  const clears: string[] = [];
  const actions: Captured[] = [];
  server.use(
    http.post('/api/v1/printers/:id/hms/clear', ({ params }) => {
      clears.push(params.id as string);
      return HttpResponse.json({ success: true, message: 'ok' });
    }),
    http.post('/api/v1/printers/:id/hms/execute-action', async ({ request, params }) => {
      actions.push({ url: params.id as string, body: await request.json() });
      return HttpResponse.json({ success: true, message: 'ok' });
    }),
  );
  return { clears, actions };
}

const allow = (granted: boolean) =>
  vi.fn().mockReturnValue(granted) as unknown as (permission: 'printers:control') => boolean;

function renderModal(errors: HMSError[], granted = true) {
  const onClose = vi.fn();
  const hasPermission = allow(granted);
  render(
    <HMSErrorModal
      printerName="Shop H2D"
      printerId={PRINTER_ID}
      errors={errors}
      onClose={onClose}
      hasPermission={hasPermission}
    />,
  );
  return { onClose, hasPermission };
}

describe('HMSErrorModal wiring', () => {
  it('Clear Errors posts hms/clear for this printer only', async () => {
    const user = userEvent.setup();
    const { clears, actions } = capture();
    const { onClose } = renderModal([actionable]);

    await user.click(screen.getByRole('button', { name: 'Clear Errors' }));

    await waitFor(() => expect(clears).toEqual([String(PRINTER_ID)]));
    expect(actions).toEqual([]);
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });

  it('an action button sends the action, the full code and the job id', async () => {
    const user = userEvent.setup();
    const { clears, actions } = capture();
    const { onClose } = renderModal([actionable]);

    await user.click(screen.getByRole('button', { name: 'Ignore this and Resume' }));

    await waitFor(() => expect(actions).toHaveLength(1));
    expect(actions[0]).toEqual({
      url: String(PRINTER_ID),
      body: { action: 'IGNORE_RESUME', print_error: '0700070000020009', job_id: 'job-777' },
    });
    expect(clears).toEqual([]);
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });

  it('falls back to the 8-char short code and a null job id', async () => {
    const user = userEvent.setup();
    const { actions } = capture();
    renderModal([shortOnly]);

    await user.click(screen.getByRole('button', { name: 'Retry' }));

    await waitFor(() => expect(actions).toHaveLength(1));
    expect(actions[0].body).toEqual({ action: 'DBL_CHECK_RETRY', print_error: '0300400C', job_id: null });
  });

  it('sends the action of the fault whose button was clicked', async () => {
    const user = userEvent.setup();
    const { actions } = capture();
    renderModal([actionable]);

    // Two buttons on the same fault: Retry, not Ignore.
    await user.click(screen.getByRole('button', { name: 'Retry (problem solved)' }));

    await waitFor(() => expect(actions).toHaveLength(1));
    expect((actions[0].body as { action: string }).action).toBe('RETRY_PROBLEM_SOLVED');
  });

  it('without printers:control, action and clear buttons are disabled and send nothing', async () => {
    const user = userEvent.setup();
    const { clears, actions } = capture();
    const { hasPermission } = renderModal([actionable], false);

    const ignore = screen.getByRole('button', { name: 'Ignore this and Resume' });
    const clear = screen.getByRole('button', { name: 'Clear Errors' });
    expect(ignore).toBeDisabled();
    expect(clear).toBeDisabled();
    expect(hasPermission).toHaveBeenCalledWith('printers:control');

    await user.click(ignore);
    await user.click(clear);
    // Give any stray request a chance to land.
    await new Promise((r) => setTimeout(r, 20));
    expect(actions).toEqual([]);
    expect(clears).toEqual([]);
  });
});
