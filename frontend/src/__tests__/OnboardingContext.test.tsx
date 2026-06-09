import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import React from 'react';
import { OnboardingProvider, useOnboarding } from '../contexts/OnboardingContext';
import * as AuthContextModule from '../contexts/AuthContext';
import * as ApiClient from '../api/client';
import type { Permission, UserResponse } from '../api/client';

function wrap({ children }: { children: React.ReactNode }) {
  return <OnboardingProvider>{children}</OnboardingProvider>;
}

function mockAuth(opts: { authEnabled: boolean; user?: UserResponse | null; loading?: boolean }) {
  const user: UserResponse | null = opts.user ?? null;
  vi.spyOn(AuthContextModule, 'useAuth').mockReturnValue({
    user,
    authEnabled: opts.authEnabled,
    requiresSetup: false,
    loading: opts.loading ?? false,
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

const fakeUser: UserResponse = {
  id: 1,
  username: 'tester',
  email: null,
  role: 'admin',
  is_active: true,
  is_admin: true,
  auth_source: 'local',
  groups: [],
  permissions: [],
  created_at: '2024-01-01T00:00:00Z',
};

beforeEach(() => {
  vi.restoreAllMocks();
  // localStorage is mocked in setup.ts — reset call counts per test.
  vi.mocked(window.localStorage.getItem).mockReset();
  vi.mocked(window.localStorage.setItem).mockReset();
  vi.mocked(window.localStorage.removeItem).mockReset();
});

describe('OnboardingProvider — auth on, user logged in', () => {
  it('GETs the user state and exposes status / snoozedUntil from the response', async () => {
    mockAuth({ authEnabled: true, user: fakeUser });
    vi.spyOn(ApiClient.api, 'getOnboarding').mockResolvedValue({
      status: 'completed_tour',
      snoozed_until: null,
    });

    const { result } = renderHook(() => useOnboarding(), { wrapper: wrap });

    await waitFor(() => {
      expect(result.current.isLoaded).toBe(true);
    });
    expect(result.current.status).toBe('completed_tour');
    expect(result.current.snoozedUntil).toBeNull();
    expect(result.current.loadFailed).toBe(false);
  });

  it('sets loadFailed when the GET errors so the welcome modal stays hidden', async () => {
    mockAuth({ authEnabled: true, user: fakeUser });
    vi.spyOn(ApiClient.api, 'getOnboarding').mockRejectedValue(new Error('5xx'));

    const { result } = renderHook(() => useOnboarding(), { wrapper: wrap });

    await waitFor(() => {
      expect(result.current.isLoaded).toBe(true);
    });
    expect(result.current.loadFailed).toBe(true);
    expect(result.current.status).toBeNull();
  });

  it('marks loadFailed=true when auth is on but there is no active user — "me" cannot be queried', async () => {
    mockAuth({ authEnabled: true, user: null });
    const spy = vi.spyOn(ApiClient.api, 'getOnboarding');

    const { result } = renderHook(() => useOnboarding(), { wrapper: wrap });

    await waitFor(() => {
      expect(result.current.isLoaded).toBe(true);
    });
    expect(result.current.loadFailed).toBe(true);
    expect(spy).not.toHaveBeenCalled();
  });

  it('PATCHes the backend when setStatus is called and updates local state from the response', async () => {
    mockAuth({ authEnabled: true, user: fakeUser });
    vi.spyOn(ApiClient.api, 'getOnboarding').mockResolvedValue({
      status: null,
      snoozed_until: null,
    });
    const patchSpy = vi.spyOn(ApiClient.api, 'updateOnboarding').mockResolvedValue({
      status: 'dismissed',
      snoozed_until: null,
    });

    const { result } = renderHook(() => useOnboarding(), { wrapper: wrap });
    await waitFor(() => expect(result.current.isLoaded).toBe(true));

    await act(async () => {
      await result.current.setStatus('dismissed');
    });

    expect(patchSpy).toHaveBeenCalledWith({ status: 'dismissed' });
    expect(result.current.status).toBe('dismissed');
  });

  it('keeps the UI responsive when the PATCH errors — falls through to local state so the modal still closes', async () => {
    mockAuth({ authEnabled: true, user: fakeUser });
    vi.spyOn(ApiClient.api, 'getOnboarding').mockResolvedValue({
      status: null,
      snoozed_until: null,
    });
    vi.spyOn(ApiClient.api, 'updateOnboarding').mockRejectedValue(new Error('5xx'));

    const { result } = renderHook(() => useOnboarding(), { wrapper: wrap });
    await waitFor(() => expect(result.current.isLoaded).toBe(true));

    await act(async () => {
      await result.current.setStatus('dismissed');
    });
    expect(result.current.status).toBe('dismissed');
  });

  it('includes snoozed_until in the PATCH body when status is snoozed', async () => {
    mockAuth({ authEnabled: true, user: fakeUser });
    vi.spyOn(ApiClient.api, 'getOnboarding').mockResolvedValue({
      status: null,
      snoozed_until: null,
    });
    const patchSpy = vi.spyOn(ApiClient.api, 'updateOnboarding').mockResolvedValue({
      status: 'snoozed',
      snoozed_until: '2026-06-15T00:00:00Z',
    });

    const { result } = renderHook(() => useOnboarding(), { wrapper: wrap });
    await waitFor(() => expect(result.current.isLoaded).toBe(true));

    await act(async () => {
      await result.current.setStatus('snoozed', '2026-06-15T00:00:00Z');
    });
    expect(patchSpy).toHaveBeenCalledWith({
      status: 'snoozed',
      snoozed_until: '2026-06-15T00:00:00Z',
    });
    expect(result.current.snoozedUntil).toBe('2026-06-15T00:00:00Z');
  });
});

describe('OnboardingProvider — auth off (localStorage path)', () => {
  it('reads status from localStorage on mount', async () => {
    mockAuth({ authEnabled: false });
    vi.mocked(window.localStorage.getItem).mockImplementation((key: string) => {
      if (key === 'bambuddy.onboarding_status') return 'dismissed';
      return null;
    });

    const { result } = renderHook(() => useOnboarding(), { wrapper: wrap });

    await waitFor(() => expect(result.current.isLoaded).toBe(true));
    expect(result.current.status).toBe('dismissed');
    expect(result.current.loadFailed).toBe(false);
  });

  it('writes status to localStorage when setStatus is called', async () => {
    mockAuth({ authEnabled: false });
    vi.mocked(window.localStorage.getItem).mockReturnValue(null);
    const patchSpy = vi.spyOn(ApiClient.api, 'updateOnboarding');

    const { result } = renderHook(() => useOnboarding(), { wrapper: wrap });
    await waitFor(() => expect(result.current.isLoaded).toBe(true));

    await act(async () => {
      await result.current.setStatus('dismissed');
    });

    expect(window.localStorage.setItem).toHaveBeenCalledWith(
      'bambuddy.onboarding_status',
      'dismissed',
    );
    expect(patchSpy).not.toHaveBeenCalled();
    expect(result.current.status).toBe('dismissed');
  });

  it('writes snoozed_until to localStorage when status is snoozed, removes it otherwise', async () => {
    mockAuth({ authEnabled: false });
    vi.mocked(window.localStorage.getItem).mockReturnValue(null);

    const { result } = renderHook(() => useOnboarding(), { wrapper: wrap });
    await waitFor(() => expect(result.current.isLoaded).toBe(true));

    await act(async () => {
      await result.current.setStatus('snoozed', '2026-06-15T00:00:00Z');
    });
    expect(window.localStorage.setItem).toHaveBeenCalledWith(
      'bambuddy.onboarding_snoozed_until',
      '2026-06-15T00:00:00Z',
    );
    expect(result.current.snoozedUntil).toBe('2026-06-15T00:00:00Z');

    await act(async () => {
      await result.current.setStatus('dismissed');
    });
    expect(window.localStorage.removeItem).toHaveBeenCalledWith(
      'bambuddy.onboarding_snoozed_until',
    );
    expect(result.current.snoozedUntil).toBeNull();
  });
});

describe('OnboardingProvider — waiting on auth', () => {
  it('does not fire the GET while authLoading is true', () => {
    mockAuth({ authEnabled: true, user: fakeUser, loading: true });
    const spy = vi.spyOn(ApiClient.api, 'getOnboarding');

    const { result } = renderHook(() => useOnboarding(), { wrapper: wrap });

    expect(spy).not.toHaveBeenCalled();
    expect(result.current.isLoaded).toBe(false);
  });
});
