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

  it('shows collecting state with countdown after submit', async () => {
    const user = userEvent.setup();

    // Delay the API response so we can see collecting state
    server.use(
      http.post('*/bug-report/submit', async () => {
        await new Promise((resolve) => setTimeout(resolve, 60000));
        return HttpResponse.json({ success: true, message: 'ok', issue_url: null, issue_number: null });
      })
    );

    render(<BugReportBubble />);
    await user.click(screen.getByRole('button'));

    await user.type(getDescriptionTextarea(), 'Test bug report');

    const submitBtn = getSubmitButton();
    if (submitBtn) await user.click(submitBtn);

    // Should show collecting state
    await waitFor(() => {
      const collectingText = screen.queryByText(/collecting|Collecting|収集|Sammeln|Collecte|Raccolta|Coletando|收集/i);
      expect(collectingText).toBeInTheDocument();
    });
  });

  it('shows success state after successful submission', async () => {
    const user = userEvent.setup();

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

    await waitFor(
      () => {
        expect(screen.getByText(/#42/)).toBeInTheDocument();
      },
      { timeout: 35000 }
    );
  });

  it('shows error state after failed submission', async () => {
    const user = userEvent.setup();

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

    await waitFor(
      () => {
        expect(screen.getByText(/Relay not available/)).toBeInTheDocument();
      },
      { timeout: 35000 }
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
