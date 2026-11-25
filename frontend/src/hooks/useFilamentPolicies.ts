import { useAuth } from '../contexts/AuthContext';
import type { FilamentPolicy } from '../api/client';

/** The signed-in user's filament rules (#3522); null when unrestricted or auth is off. */
export function useFilamentPolicies(): FilamentPolicy[] | null {
  const { authEnabled, user } = useAuth();
  if (!authEnabled) return null;
  return user?.filament_policies ?? null;
}
