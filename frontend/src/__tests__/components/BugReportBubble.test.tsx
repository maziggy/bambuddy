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
});
