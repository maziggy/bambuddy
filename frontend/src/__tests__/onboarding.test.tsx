import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from './utils';
import userEvent from '@testing-library/user-event';
import { OnboardingFlow } from '../components/onboarding/OnboardingFlow';
import * as OnboardingContextModule from '../contexts/OnboardingContext';

const setStatusMock = vi.fn().mockResolvedValue(undefined);

function mockOnboarding(overrides: Partial<ReturnType<typeof OnboardingContextModule.useOnboarding>>) {
  vi.spyOn(OnboardingContextModule, 'useOnboarding').mockReturnValue({
    status: null,
    snoozedUntil: null,
    isLoaded: true,
    loadFailed: false,
    setStatus: setStatusMock,
    ...overrides,
  });
}

beforeEach(() => {
  setStatusMock.mockClear();
});

describe('OnboardingFlow eligibility', () => {
  it('does not render anything while the provider is still loading', () => {
    mockOnboarding({ isLoaded: false });
    render(<OnboardingFlow />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('does not render when the initial GET errored — distinguishing "new user" from "API down" is unsafe', () => {
    mockOnboarding({ loadFailed: true });
    render(<OnboardingFlow />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('renders the welcome modal when status is null (new user)', () => {
    mockOnboarding({ status: null });
    render(<OnboardingFlow />);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('stays hidden when status is dismissed', () => {
    mockOnboarding({ status: 'dismissed' });
    render(<OnboardingFlow />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('stays hidden when status is completed_tour', () => {
    mockOnboarding({ status: 'completed_tour' });
    render(<OnboardingFlow />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('stays hidden when status is dismissed_at_migration', () => {
    mockOnboarding({ status: 'dismissed_at_migration' });
    render(<OnboardingFlow />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('stays hidden when the snooze window has not yet elapsed', () => {
    const future = new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString();
    mockOnboarding({ status: 'snoozed', snoozedUntil: future });
    render(<OnboardingFlow />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('renders the welcome modal again once the snooze window has elapsed', () => {
    const past = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
    mockOnboarding({ status: 'snoozed', snoozedUntil: past });
    render(<OnboardingFlow />);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('falls back to hidden when snoozedUntil is malformed', () => {
    mockOnboarding({ status: 'snoozed', snoozedUntil: 'not-a-date' });
    render(<OnboardingFlow />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('Welcome modal interactions', () => {
  it('persists "dismissed" when the user clicks "I\'m experienced"', async () => {
    mockOnboarding({ status: null });
    render(<OnboardingFlow />);
    const user = userEvent.setup();
    // The "experienced" button has the second-largest weight in the modal
    // (first is "Start tour", which advances rather than persists).
    const buttons = screen.getAllByRole('button');
    const experiencedButton = buttons[1];
    await user.click(experiencedButton);
    expect(setStatusMock).toHaveBeenCalledWith('dismissed');
  });

  it('persists "snoozed" with a future ISO timestamp when the user clicks "Remind me later"', async () => {
    mockOnboarding({ status: null });
    render(<OnboardingFlow />);
    const user = userEvent.setup();
    const buttons = screen.getAllByRole('button');
    const snoozeButton = buttons[2];
    await user.click(snoozeButton);
    expect(setStatusMock).toHaveBeenCalledTimes(1);
    const [status, snoozedUntil] = setStatusMock.mock.calls[0];
    expect(status).toBe('snoozed');
    expect(typeof snoozedUntil).toBe('string');
    const snoozeMs = new Date(snoozedUntil as string).getTime();
    const sevenDaysMs = 7 * 24 * 60 * 60 * 1000;
    // Allow a small skew window — the test runs concurrently with the click.
    expect(snoozeMs).toBeGreaterThan(Date.now() + sevenDaysMs - 5000);
    expect(snoozeMs).toBeLessThan(Date.now() + sevenDaysMs + 5000);
  });

  it('advances to the About modal when the user clicks "Start tour"', async () => {
    mockOnboarding({ status: null });
    render(<OnboardingFlow />);
    const user = userEvent.setup();
    const buttons = screen.getAllByRole('button');
    // First button is "Start tour"
    await user.click(buttons[0]);
    // setStatus should NOT be called yet — advance happens via local phase state
    expect(setStatusMock).not.toHaveBeenCalled();
    // The dialog is still mounted (now the About modal); its labelled-by id changes.
    await waitFor(() => {
      const dialog = screen.getByRole('dialog');
      expect(dialog.getAttribute('aria-labelledby')).toBe('onboarding-about-title');
    });
  });
});

describe('About modal interactions', () => {
  it('launches the tour engine (tour_in_progress:<first-step>) when the user clicks Done from the About modal', async () => {
    mockOnboarding({ status: null });
    render(<OnboardingFlow />);
    const user = userEvent.setup();
    // Click Start tour to get to the About modal
    const welcomeButtons = screen.getAllByRole('button');
    await user.click(welcomeButtons[0]);
    await waitFor(() => {
      const dialog = screen.getByRole('dialog');
      expect(dialog.getAttribute('aria-labelledby')).toBe('onboarding-about-title');
    });
    // About modal has two buttons: Skip (left), Done (right)
    const aboutButtons = screen.getAllByRole('button');
    await user.click(aboutButtons[aboutButtons.length - 1]);
    // Done should set status to the first tour step, NOT completed_tour. The
    // engine takes over once OnboardingFlow sees the tour_in_progress prefix.
    expect(setStatusMock).toHaveBeenCalledTimes(1);
    const [status] = setStatusMock.mock.calls[0];
    expect(status).toMatch(/^tour_in_progress:/);
  });

  it('persists "dismissed" when the user clicks Skip from the About modal', async () => {
    mockOnboarding({ status: null });
    render(<OnboardingFlow />);
    const user = userEvent.setup();
    const welcomeButtons = screen.getAllByRole('button');
    await user.click(welcomeButtons[0]);
    await waitFor(() => {
      const dialog = screen.getByRole('dialog');
      expect(dialog.getAttribute('aria-labelledby')).toBe('onboarding-about-title');
    });
    const aboutButtons = screen.getAllByRole('button');
    // Skip is the first of the two action buttons
    await user.click(aboutButtons[0]);
    expect(setStatusMock).toHaveBeenCalledWith('dismissed');
  });
});
