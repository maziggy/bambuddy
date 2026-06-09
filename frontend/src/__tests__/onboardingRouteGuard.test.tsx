import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import React from 'react';
import { OnboardingFlow } from '../components/onboarding/OnboardingFlow';
import * as OnboardingContextModule from '../contexts/OnboardingContext';
import * as AuthContextModule from '../contexts/AuthContext';
import type { Permission, UserResponse } from '../api/client';

// We bypass the shared test util here because it provides its own
// BrowserRouter and react-router rejects nested routers. The OnboardingFlow
// uses only useLocation + the mocked useAuth + useOnboarding, so MemoryRouter
// alone is the minimal context it needs.
function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <OnboardingFlow />
    </MemoryRouter>,
  );
}

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

function mockAuth(opts: { authEnabled: boolean; requiresSetup?: boolean; user?: UserResponse | null }) {
  vi.spyOn(AuthContextModule, 'useAuth').mockReturnValue({
    user: opts.user ?? null,
    authEnabled: opts.authEnabled,
    requiresSetup: opts.requiresSetup ?? false,
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
});

describe('OnboardingFlow route guard', () => {
  it('renders the welcome modal on the main app route when the user has never seen the tour', () => {
    mockOnboardingStatus(null);
    mockAuth({ authEnabled: false });
    renderAt('/');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('suppresses the welcome modal during the fresh-install /setup flow', () => {
    mockOnboardingStatus(null);
    mockAuth({ authEnabled: false, requiresSetup: true });
    renderAt('/setup');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('suppresses the welcome modal even when requiresSetup is false if the user is on /setup', () => {
    mockOnboardingStatus(null);
    mockAuth({ authEnabled: false, requiresSetup: false });
    renderAt('/setup');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('suppresses the welcome modal on /login', () => {
    mockOnboardingStatus(null);
    mockAuth({ authEnabled: true });
    renderAt('/login');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('suppresses the welcome modal on the SpoolBuddy kiosk surface', () => {
    mockOnboardingStatus(null);
    mockAuth({ authEnabled: false });
    renderAt('/spoolbuddy');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('suppresses the welcome modal on nested SpoolBuddy routes', () => {
    mockOnboardingStatus(null);
    mockAuth({ authEnabled: false });
    renderAt('/spoolbuddy/write-tag');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('suppresses the welcome modal on the standalone /camera/:id route', () => {
    mockOnboardingStatus(null);
    mockAuth({ authEnabled: false });
    renderAt('/camera/3');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('suppresses the welcome modal on the OBS overlay route', () => {
    mockOnboardingStatus(null);
    mockAuth({ authEnabled: false });
    renderAt('/overlay/3');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('suppresses the welcome modal while requiresSetup is true even on a main app route', () => {
    // Edge case: requiresSetup somehow flips back to true while the user is
    // on /. We should still not pop the modal — the only thing that matters
    // is that setup is unfinished.
    mockOnboardingStatus(null);
    mockAuth({ authEnabled: false, requiresSetup: true });
    renderAt('/');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
