/**
 * Tests for the BugReportBubble component.
 */

import { describe, it, expect } from 'vitest';
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
      const reproduceText = screen.queryByText(/reproduce|Reproduce|reproduzieren|reproduire|riproduci|再現|reproduza|重现/i);
      expect(reproduceText).toBeInTheDocument();
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
      expect(screen.queryByText(/reproduce|Reproduce|reproduzieren|reproduire|riproduci|再現|reproduza|重现/i)).toBeInTheDocument();
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
      expect(screen.queryByText(/reproduce|Reproduce|reproduzieren|reproduire|riproduci|再現|reproduza|重现/i)).toBeInTheDocument();
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
});
