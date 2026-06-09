import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import { useAuth } from './AuthContext';

interface OnboardingState {
  status: string | null;
  snoozedUntil: string | null;
}

interface OnboardingContextType extends OnboardingState {
  /** True once the initial GET (or localStorage read) has settled. */
  isLoaded: boolean;
  /** True if the initial GET errored. The UI should not pop the welcome
   *  modal in this case — we cannot distinguish "new user" from "backend
   *  unreachable", so showing the welcome would be wrong. */
  loadFailed: boolean;
  /** Persist a new status. PATCHes to backend when auth is on, writes
   *  localStorage when auth is off. Silent on failure — onboarding is
   *  non-critical so we do not block the UI. */
  setStatus: (status: string, snoozedUntil?: string | null) => Promise<void>;
}

const OnboardingContext = createContext<OnboardingContextType | undefined>(undefined);

const LOCALSTORAGE_STATUS = 'bambuddy.onboarding_status';
const LOCALSTORAGE_SNOOZE = 'bambuddy.onboarding_snoozed_until';

export function OnboardingProvider({ children }: { children: React.ReactNode }) {
  const { authEnabled, user, loading: authLoading } = useAuth();
  const [state, setState] = useState<OnboardingState>({ status: null, snoozedUntil: null });
  const [isLoaded, setIsLoaded] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);

  useEffect(() => {
    if (authLoading) return;

    let cancelled = false;

    if (authEnabled) {
      if (!user) {
        // No active session — there is no "me" to query. Mark loaded so the
        // rest of the app does not block, but leave loadFailed so the welcome
        // modal stays hidden until the user logs in.
        setIsLoaded(true);
        setLoadFailed(true);
        return;
      }
      api.getOnboarding()
        .then((data) => {
          if (cancelled) return;
          setState({
            status: data.status ?? null,
            snoozedUntil: data.snoozed_until ?? null,
          });
          setLoadFailed(false);
        })
        .catch(() => {
          if (cancelled) return;
          setLoadFailed(true);
        })
        .finally(() => {
          if (cancelled) return;
          setIsLoaded(true);
        });
    } else {
      const status = localStorage.getItem(LOCALSTORAGE_STATUS);
      const snoozedUntil = localStorage.getItem(LOCALSTORAGE_SNOOZE);
      setState({ status, snoozedUntil });
      setLoadFailed(false);
      setIsLoaded(true);
    }

    return () => {
      cancelled = true;
    };
  }, [authLoading, authEnabled, user]);

  const setStatus = useCallback(
    async (status: string, snoozedUntil?: string | null) => {
      if (authEnabled && user) {
        try {
          const body: { status: string; snoozed_until?: string | null } = { status };
          if (status === 'snoozed') body.snoozed_until = snoozedUntil ?? null;
          const data = await api.updateOnboarding(body);
          setState({
            status: data.status ?? null,
            snoozedUntil: data.snoozed_until ?? null,
          });
        } catch {
          // Persistence failed — keep the UI responsive by updating local state
          // anyway so the modal closes. The next page load will re-GET and the
          // real backend state will surface.
          setState({ status, snoozedUntil: snoozedUntil ?? null });
        }
      } else {
        localStorage.setItem(LOCALSTORAGE_STATUS, status);
        if (status === 'snoozed' && snoozedUntil) {
          localStorage.setItem(LOCALSTORAGE_SNOOZE, snoozedUntil);
        } else {
          localStorage.removeItem(LOCALSTORAGE_SNOOZE);
        }
        setState({ status, snoozedUntil: status === 'snoozed' ? snoozedUntil ?? null : null });
      }
    },
    [authEnabled, user],
  );

  const value = useMemo<OnboardingContextType>(
    () => ({ ...state, isLoaded, loadFailed, setStatus }),
    [state, isLoaded, loadFailed, setStatus],
  );

  return <OnboardingContext.Provider value={value}>{children}</OnboardingContext.Provider>;
}

export function useOnboarding(): OnboardingContextType {
  const ctx = useContext(OnboardingContext);
  if (!ctx) throw new Error('useOnboarding must be used within OnboardingProvider');
  return ctx;
}
