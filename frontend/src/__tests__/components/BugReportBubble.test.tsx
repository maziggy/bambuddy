/**
 * Tests for the BugReportBubble component.
 */

import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '../utils';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { BugReportBubble } from '../../components/BugReportBubble';

function getDescriptionTextarea() {
  return document.querySelector('textarea') as HTMLTextAreaElement;
}

function getSubmitButton() {
  const buttons = screen.getAllByRole('button');
  return buttons.find(
    (b) =>
      b.className.includes('bg-red-500') &&
      !b.className.includes('rounded-full') &&
      b.textContent !== ''
  );
}

function setupLoggingEndpoints() {
  server.use(
    http.post('*/bug-report/start-logging', () => {
      return HttpResponse.json({ started: true, was_debug: false });
    }),
    http.post('*/bug-report/stop-logging', () => {
      return HttpResponse.json({ logs: 'test debug logs' });
    })
  );
}

/** Mocks the printer list and per-printer diagnostic the form scans on open. */
function setupDiagnosticEndpoints(
  printers: { id: number; name: string }[],
  results: Record<number, 'ok' | 'problems'>
) {
  server.use(
    http.get('*/printers/', () =>
      HttpResponse.json(
        printers.map((p) => ({
          id: p.id,
          name: p.name,
          serial_number: '00M09A000000000',
          ip_address: `192.168.1.${20 + p.id}`,
          is_active: true,
          model: 'X1C',
          nozzle_count: 1,
        }))
      )
    ),
    http.get('*/printers/:id/diagnostic', ({ params }) => {
      const overall = results[Number(params.id)] ?? 'ok';
      return HttpResponse.json({
        printer_id: Number(params.id),
        ip_address: `192.168.1.${20 + Number(params.id)}`,
        overall,
        checks: [{ id: 'port_mqtt', status: overall === 'problems' ? 'fail' : 'pass', params: {} }],
      });
    })
  );
}

describe('BugReportBubble', () => {
  it('renders the floating bug button', () => {
    render(<BugReportBubble />);

    const button = screen.getByRole('button');
    expect(button).toBeInTheDocument();
  });

  it('opens panel when bubble is clicked', async () => {
    const user = userEvent.setup();

    render(<BugReportBubble />);
    await user.click(screen.getByRole('button'));

    expect(getDescriptionTextarea()).toBeInTheDocument();
  });

  it('closes panel when X button is clicked', async () => {
    const user = userEvent.setup();

    render(<BugReportBubble />);

    // Open
    await user.click(screen.getByRole('button'));
    expect(getDescriptionTextarea()).toBeInTheDocument();

    // Close via the X button
    const buttons = screen.getAllByRole('button');
    const closeButton = buttons.find((b) => b.querySelector('.lucide-x'));
    if (closeButton) await user.click(closeButton);

    await waitFor(() => {
      expect(document.querySelector('textarea')).not.toBeInTheDocument();
    });
  });

  it('disables submit when description is empty', async () => {
    const user = userEvent.setup();

    render(<BugReportBubble />);
    await user.click(screen.getByRole('button'));

    expect(getSubmitButton()).toBeDisabled();
  });

  it('enables submit when description is provided', async () => {
    const user = userEvent.setup();

    render(<BugReportBubble />);
    await user.click(screen.getByRole('button'));

    await user.type(getDescriptionTextarea(), 'Something is broken');

    expect(getSubmitButton()).not.toBeDisabled();
  });

  it('shows logging state with step indicators after start', async () => {
    const user = userEvent.setup();
    setupLoggingEndpoints();

    render(<BugReportBubble />);
    await user.click(screen.getByRole('button'));

    await user.type(getDescriptionTextarea(), 'Test bug report');

    const submitBtn = getSubmitButton();
    if (submitBtn) await user.click(submitBtn);

    // Should show step indicators and elapsed timer
    await waitFor(() => {
      expect(screen.queryByTestId('bug-report-step-reproduce')).toBeInTheDocument();
    });

    // Should show elapsed timer (00:00 format)
    await waitFor(() => {
      const timer = screen.queryByText(/00:0/);
      expect(timer).toBeInTheDocument();
    });
  });

  it('shows success state after successful submission', async () => {
    const user = userEvent.setup();

    setupLoggingEndpoints();
    server.use(
      http.post('*/bug-report/submit', () => {
        return HttpResponse.json({
          success: true,
          message: 'Bug report submitted successfully!',
          issue_url: 'https://github.com/maziggy/bambuddy/issues/42',
          issue_number: 42,
        });
      })
    );

    render(<BugReportBubble />);
    await user.click(screen.getByRole('button'));

    await user.type(getDescriptionTextarea(), 'Test bug');

    const submitBtn = getSubmitButton();
    if (submitBtn) await user.click(submitBtn);

    // Wait for logging state, then click stop
    await waitFor(() => {
      expect(screen.queryByTestId('bug-report-step-reproduce')).toBeInTheDocument();
    });

    // Find and click the Stop & Submit button
    const stopBtn = screen.getAllByRole('button').find(
      (b) => b.className.includes('bg-red-500') && !b.className.includes('rounded-full')
    );
    if (stopBtn) await user.click(stopBtn);

    await waitFor(
      () => {
        expect(screen.getByText(/#42/)).toBeInTheDocument();
      },
      { timeout: 10000 }
    );
  });

  it('shows error state after failed submission', async () => {
    const user = userEvent.setup();

    setupLoggingEndpoints();
    server.use(
      http.post('*/bug-report/submit', () => {
        return HttpResponse.json({
          success: false,
          message: 'Relay not available',
          issue_url: null,
          issue_number: null,
        });
      })
    );

    render(<BugReportBubble />);
    await user.click(screen.getByRole('button'));

    await user.type(getDescriptionTextarea(), 'Test bug');

    const submitBtn = getSubmitButton();
    if (submitBtn) await user.click(submitBtn);

    // Wait for logging state, then click stop
    await waitFor(() => {
      expect(screen.queryByTestId('bug-report-step-reproduce')).toBeInTheDocument();
    });

    const stopBtn = screen.getAllByRole('button').find(
      (b) => b.className.includes('bg-red-500') && !b.className.includes('rounded-full')
    );
    if (stopBtn) await user.click(stopBtn);

    await waitFor(
      () => {
        expect(screen.getByText(/Relay not available/)).toBeInTheDocument();
      },
      { timeout: 10000 }
    );
  });

  it('has expandable data collection notice', async () => {
    const user = userEvent.setup();

    render(<BugReportBubble />);
    await user.click(screen.getByRole('button'));

    const details = document.querySelector('details');
    expect(details).toBeInTheDocument();
  });

  it('lists affected printers as collapsed rows, not stacked checklists', async () => {
    const user = userEvent.setup();
    setupDiagnosticEndpoints(
      [
        { id: 1, name: 'Printer Alpha' },
        { id: 2, name: 'Printer Beta' },
        { id: 3, name: 'Printer Gamma' },
      ],
      { 1: 'problems', 2: 'problems', 3: 'ok' }
    );

    render(<BugReportBubble />);
    await user.click(screen.getByRole('button'));

    // Summary counts problem printers against all scanned printers.
    expect(
      await screen.findByText('2 of 3 printers have connection issues')
    ).toBeInTheDocument();
    // Affected printers are listed by name; the healthy one is not.
    expect(screen.getByText('Printer Alpha')).toBeInTheDocument();
    expect(screen.getByText('Printer Beta')).toBeInTheDocument();
    expect(screen.queryByText('Printer Gamma')).not.toBeInTheDocument();
    // With more than one problem the per-printer checklists stay collapsed.
    expect(screen.queryByText(/Found problems that explain/)).not.toBeInTheDocument();

    // Expanding a row reveals just that printer's checklist.
    await user.click(screen.getByText('Printer Alpha'));
    expect(await screen.findByText(/Found problems that explain/)).toBeInTheDocument();
  });

  it('auto-expands the checklist when only one printer has problems', async () => {
    const user = userEvent.setup();
    setupDiagnosticEndpoints([{ id: 1, name: 'Solo Printer' }], { 1: 'problems' });

    render(<BugReportBubble />);
    await user.click(screen.getByRole('button'));

    expect(
      await screen.findByText('1 of 1 printers have connection issues')
    ).toBeInTheDocument();
    // Single problem → the checklist is expanded without a click.
    expect(await screen.findByText(/Found problems that explain/)).toBeInTheDocument();
  });

  it('shows the log-health panel when the scan finds known issues', async () => {
    const user = userEvent.setup();
    setupDiagnosticEndpoints([{ id: 1, name: 'Solo Printer' }], { 1: 'ok' });
    server.use(
      http.get('*/system/health', () =>
        HttpResponse.json({
          findings: [
            {
              signature_id: 'ftp-auth-rejected',
              severity: 'error',
              category: 'layer8',
              wiki_anchor: 'wrong-access-code',
              count: 3,
              first_seen: '2026-05-22 09:00:00,000',
              last_seen: '2026-05-22 10:00:00,000',
              sample: 'FTP connection permission error to [IP]',
            },
          ],
          scanned_entries: 500,
          log_available: true,
          summary: { total: 1, layer8: 1, environment: 0, bug: 0 },
        })
      )
    );

    render(<BugReportBubble />);
    await user.click(screen.getByRole('button'));

    expect(await screen.findByText('Known issues found in your logs')).toBeInTheDocument();
    expect(screen.getByText('Printer rejected the access code')).toBeInTheDocument();
  });

  // Step 2 asks the user to reproduce the problem, and the panel sits over the
  // part of the app they have to reach to do it. Closing it used to be the only
  // way through and it threw the run away: the reset-on-open effect put the
  // panel back on step 1 while the server stayed at DEBUG, with nothing left
  // that could stop it (#2847).
  describe('a logging run that outlives the panel (#2847)', () => {
    afterEach(() => {
      vi.mocked(localStorage.getItem).mockReset();
    });

    /** The mock is shared with every other localStorage reader in the tree --
     *  the theme context among them -- so only answer for our own key. */
    const storeSession = (session: Record<string, unknown>) => {
      vi.mocked(localStorage.getItem).mockImplementation((key: string) =>
        key === 'bambuddy-bug-report-session' ? JSON.stringify(session) : null
      );
    };

    /** Types a description and presses Start, landing on step 2. */
    const startRun = async (user: ReturnType<typeof userEvent.setup>, description: string) => {
      await user.click(screen.getByRole('button'));
      await user.type(getDescriptionTextarea(), description);
      const startBtn = getSubmitButton();
      if (startBtn) await user.click(startBtn);
      await waitFor(() => {
        expect(screen.queryByTestId('bug-report-step-reproduce')).toBeInTheDocument();
      });
    };

    const closePanel = async (user: ReturnType<typeof userEvent.setup>) => {
      const closeButton = screen.getAllByRole('button').find((b) => b.querySelector('.lucide-x'));
      if (closeButton) await user.click(closeButton);
      await waitFor(() => {
        expect(screen.queryByTestId('bug-report-step-reproduce')).not.toBeInTheDocument();
      });
    };

    it('survives a close and reopens on the step it left, description intact', async () => {
      const user = userEvent.setup();
      let stopCalls = 0;
      let submitted: { description?: string } | null = null;
      server.use(
        http.post('*/bug-report/start-logging', () => HttpResponse.json({ started: true, was_debug: false })),
        http.post('*/bug-report/stop-logging', () => {
          stopCalls += 1;
          return HttpResponse.json({ logs: 'captured' });
        }),
        http.post('*/bug-report/submit', async ({ request }) => {
          submitted = (await request.json()) as { description?: string };
          return HttpResponse.json({ success: true, message: 'ok', issue_number: 7 });
        }),
      );

      render(<BugReportBubble />);
      await startRun(user, 'Queue page freezes');
      await closePanel(user);

      // Closing is not cancelling: the log level only comes back down when the
      // user presses Stop & Submit.
      expect(stopCalls).toBe(0);

      // The disc carries the run's colour, so a closed panel still says a
      // recording is live and that clicking gets back to it.
      const disc = screen.getByRole('button');
      expect(disc.className).toContain('bg-amber-500');

      await user.click(disc);
      expect(screen.getByTestId('bug-report-step-reproduce')).toBeInTheDocument();

      const stopBtn = screen.getAllByRole('button').find(
        (b) => b.className.includes('bg-red-500') && !b.className.includes('rounded-full')
      );
      if (stopBtn) await user.click(stopBtn);

      await waitFor(() => expect(submitted).not.toBeNull());
      expect(submitted!.description).toBe('Queue page freezes');
      expect(stopCalls).toBe(1);
    });

    it('picks the run back up after a reload while the server is still logging', async () => {
      const user = userEvent.setup();
      const startedAt = Date.now() - 30_000;
      storeSession({ description: 'Printer card goes blank', email: '', wasDebug: false, startedAt });
      server.use(
        http.get('*/support/debug-logging', () =>
          HttpResponse.json({
            enabled: true,
            enabled_at: new Date(startedAt).toISOString(),
            duration_seconds: 30,
          })
        ),
      );

      render(<BugReportBubble />);

      await waitFor(() => {
        expect(screen.getByRole('button').className).toContain('bg-amber-500');
      });
      await user.click(screen.getByRole('button'));
      expect(screen.getByTestId('bug-report-step-reproduce')).toBeInTheDocument();
      // Elapsed comes off the run's start time, so the reload does not reset it.
      expect(screen.getByText('00:30')).toBeInTheDocument();
    });

    it('drops a stored run the server already stopped', async () => {
      storeSession({ description: 'stale', email: '', wasDebug: false, startedAt: Date.now() - 30_000 });
      let stopCalls = 0;
      server.use(
        http.get('*/support/debug-logging', () =>
          HttpResponse.json({ enabled: false, enabled_at: null, duration_seconds: null })
        ),
        http.post('*/bug-report/stop-logging', () => {
          stopCalls += 1;
          return HttpResponse.json({ logs: '' });
        }),
      );

      render(<BugReportBubble />);

      await waitFor(() => expect(localStorage.removeItem).toHaveBeenCalled());
      expect(screen.getByRole('button').className).toContain('bg-red-500');
      // Logging is already off; there is nothing to put back.
      expect(stopCalls).toBe(0);
    });

    it('restores the log level for a run that outlived the cap, without filing it', async () => {
      let stopCalls = 0;
      let submitCalls = 0;
      storeSession({ description: 'from an hour ago', email: '', wasDebug: false, startedAt: Date.now() - 3_600_000 });
      server.use(
        http.get('*/support/debug-logging', () =>
          HttpResponse.json({ enabled: true, enabled_at: new Date(Date.now() - 3_600_000).toISOString(), duration_seconds: 3600 })
        ),
        http.post('*/bug-report/stop-logging', () => {
          stopCalls += 1;
          return HttpResponse.json({ logs: '' });
        }),
        http.post('*/bug-report/submit', () => {
          submitCalls += 1;
          return HttpResponse.json({ success: true, message: 'ok' });
        }),
      );

      render(<BugReportBubble />);

      // The level comes back down, because nothing else was going to do it...
      await waitFor(() => expect(stopCalls).toBe(1));
      // ...but an hour-old description is not a report anyone is still waiting
      // to be filed, and nobody is here to see it happen.
      expect(submitCalls).toBe(0);
      expect(screen.getByRole('button').className).toContain('bg-red-500');
    });
  });
});
