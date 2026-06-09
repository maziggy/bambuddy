import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from './utils';
import userEvent from '@testing-library/user-event';
import { OnboardingFlow } from '../components/onboarding/OnboardingFlow';
import * as OnboardingContextModule from '../contexts/OnboardingContext';
import * as AuthContextModule from '../contexts/AuthContext';
import * as ApiClient from '../api/client';
import {
  TOUR_STEPS,
  stepIndexFromStatus,
  statusForStep,
} from '../components/onboarding/tourSteps';
import type { Printer, UserResponse, Permission } from '../api/client';

const setStatusMock = vi.fn().mockResolvedValue(undefined);

function mockOnboardingStatus(status: string | null) {
  vi.spyOn(OnboardingContextModule, 'useOnboarding').mockReturnValue({
    status,
    snoozedUntil: null,
    isLoaded: true,
    loadFailed: false,
    setStatus: setStatusMock,
  });
}

function mockAuth(authEnabled: boolean, user: UserResponse | null = null) {
  vi.spyOn(AuthContextModule, 'useAuth').mockReturnValue({
    user,
    authEnabled,
    requiresSetup: false,
    loading: false,
    isAdmin: false,
    login: vi.fn(),
    loginWithToken: vi.fn(),
    logout: vi.fn(),
    refreshUser: vi.fn(),
    refreshAuth: vi.fn(),
    hasPermission: (_: Permission) => false,
    hasAnyPermission: (..._: Permission[]) => false,
    hasAllPermissions: (..._: Permission[]) => false,
    canModify: () => false,
  });
}

beforeEach(() => {
  setStatusMock.mockClear();
  vi.restoreAllMocks();
  // Default to zero printers so steps with `skipIf: printerCount > 0` do
  // not auto-advance and tests that exercise the Back/Skip/Next buttons can
  // actually find them. MSW's default `mockPrinters` returns one printer,
  // which would otherwise skip the add-printer step before the click lands.
  vi.spyOn(ApiClient.api, 'getPrinters').mockResolvedValue([] as Printer[]);
});

describe('tourSteps helpers', () => {
  it('round-trips index → status → index for every step', () => {
    for (let i = 0; i < TOUR_STEPS.length; i++) {
      const status = statusForStep(i);
      expect(status).toBe(`tour_in_progress:${TOUR_STEPS[i].id}`);
      expect(stepIndexFromStatus(status)).toBe(i);
    }
  });

  it('returns -1 for non-tour-progress statuses', () => {
    expect(stepIndexFromStatus(null)).toBe(-1);
    expect(stepIndexFromStatus('dismissed')).toBe(-1);
    expect(stepIndexFromStatus('completed_tour')).toBe(-1);
    expect(stepIndexFromStatus('snoozed')).toBe(-1);
    expect(stepIndexFromStatus('tour_in_progress:unknown-step')).toBe(-1);
  });

  it('throws when building a status for an out-of-range index', () => {
    expect(() => statusForStep(TOUR_STEPS.length)).toThrow();
    expect(() => statusForStep(-1)).toThrow();
  });
});

describe('TourEngine wiring', () => {
  it('renders the engine modal when status is a valid tour step', () => {
    // Pick the outro step — it has no anchor so we skip the 3s anchor-poll
    // timeout and the engine centres the modal immediately.
    mockOnboardingStatus(statusForStep(TOUR_STEPS.length - 1));
    render(<OnboardingFlow />);
    const dialog = screen.getByRole('dialog');
    expect(dialog.getAttribute('aria-labelledby')).toBe('tour-step-title');
  });

  it('falls through to the welcome modal when the tour step id is malformed', () => {
    mockOnboardingStatus('tour_in_progress:not-a-real-step');
    render(<OnboardingFlow />);
    // status starts with tour_in_progress: → OnboardingFlow renders TourEngine.
    // Engine sees stepIndex === -1 and returns null. Welcome modal is also
    // hidden because status !== null. So nothing renders.
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('advances to the next step when the user clicks Next', async () => {
    // Pick the last step that doesn't skip under the default mocked state
    // (authEnabled=false, printerCount=0, no makerworld permission). The
    // notifications step is permission-agnostic and a good target.
    const notificationsIndex = TOUR_STEPS.findIndex((s) => s.id === 'notifications');
    expect(notificationsIndex).toBeGreaterThan(-1);
    mockOnboardingStatus(statusForStep(notificationsIndex));
    render(<OnboardingFlow />);
    const user = userEvent.setup();
    const buttons = screen.getAllByRole('button');
    // Buttons in order: Skip (ghost, leftmost), Back, Next/Done.
    const nextButton = buttons[buttons.length - 1];
    await user.click(nextButton);
    expect(setStatusMock).toHaveBeenCalledWith(statusForStep(notificationsIndex + 1));
  });

  it('marks the tour completed when Next is clicked on the last step', async () => {
    mockOnboardingStatus(statusForStep(TOUR_STEPS.length - 1));
    render(<OnboardingFlow />);
    const user = userEvent.setup();
    const buttons = screen.getAllByRole('button');
    const doneButton = buttons[buttons.length - 1];
    await user.click(doneButton);
    expect(setStatusMock).toHaveBeenCalledWith('completed_tour');
  });

  it('goes one step back when the user clicks Back', async () => {
    // Pick a step that does NOT auto-skip under default mocked state (no
    // useAuth/useQuery mocks → authEnabled=false, printerCount=0). The
    // first non-skipIf step in the sequence is `add-spool`.
    const addSpoolIndex = TOUR_STEPS.findIndex((s) => s.id === 'add-spool');
    mockOnboardingStatus(statusForStep(addSpoolIndex));
    render(<OnboardingFlow />);
    const user = userEvent.setup();
    const buttons = screen.getAllByRole('button');
    const backButton = buttons[buttons.length - 2];
    await user.click(backButton);
    expect(setStatusMock).toHaveBeenCalledWith(statusForStep(addSpoolIndex - 1));
  });

  it('disables Back on the first non-skipping step', () => {
    // auth step would skip if authEnabled, so make sure it doesn't skip
    // under the default mock. The mock useAuth path returns authEnabled=false
    // by default, so the auth step renders.
    mockOnboardingStatus(statusForStep(0));
    render(<OnboardingFlow />);
    const buttons = screen.getAllByRole('button');
    const backButton = buttons[buttons.length - 2];
    expect(backButton).toBeDisabled();
  });

  it('persists dismissed when the user clicks Skip', async () => {
    mockOnboardingStatus(statusForStep(0));
    render(<OnboardingFlow />);
    const user = userEvent.setup();
    const buttons = screen.getAllByRole('button');
    // Skip is the leftmost button — first in document order in the modal.
    const skipButton = buttons[0];
    await user.click(skipButton);
    expect(setStatusMock).toHaveBeenCalledWith('dismissed');
  });
});

describe('TourEngine conditional skipping', () => {
  it('skips the add-printer step when at least one printer is configured', async () => {
    const addPrinterIndex = TOUR_STEPS.findIndex((s) => s.id === 'add-printer');
    mockOnboardingStatus(statusForStep(addPrinterIndex));
    mockAuth(false);
    vi.spyOn(ApiClient.api, 'getPrinters').mockResolvedValue([
      { id: 1, name: 'X1C' } as Printer,
    ]);
    render(<OnboardingFlow />);
    await waitFor(() => {
      expect(setStatusMock).toHaveBeenCalledWith(statusForStep(addPrinterIndex + 1));
    });
  });

  it('marks completed_tour when the last step skips', async () => {
    // Force a fictitious skipIf on the last step by spying on TOUR_STEPS.
    const lastIndex = TOUR_STEPS.length - 1;
    const originalSkipIf = TOUR_STEPS[lastIndex].skipIf;
    TOUR_STEPS[lastIndex].skipIf = () => true;
    try {
      mockOnboardingStatus(statusForStep(lastIndex));
      mockAuth(false);
      vi.spyOn(ApiClient.api, 'getPrinters').mockResolvedValue([] as Printer[]);
      render(<OnboardingFlow />);
      await waitFor(() => {
        expect(setStatusMock).toHaveBeenCalledWith('completed_tour');
      });
    } finally {
      TOUR_STEPS[lastIndex].skipIf = originalSkipIf;
    }
  });
});
