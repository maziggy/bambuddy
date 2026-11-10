import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';

/**
 * The names to suggest for a spool's owner (#1301): everyone already named as
 * an owner on a spool, plus the registered users.
 *
 * Only suggestions. An owner is free text, because the person a spool belongs
 * to is often no user (a customer, a visitor), so a name that is in neither
 * list is still accepted. The user list is the slim one, which a user who may
 * not read the full listing can still fetch; where even that is refused, or
 * there is no user management, the suggestions are just the owners in use.
 */
export function useOwnerSuggestions(
  spools: ReadonlyArray<{ owner?: string | null }> | undefined,
  enabled = true,
): string[] {
  const { data: users } = useQuery({
    queryKey: ['users', 'slim'],
    queryFn: () => api.getUsersSlim(),
    enabled,
    retry: false,
    staleTime: 5 * 60 * 1000,
  });

  return useMemo(() => {
    const names = new Set<string>();
    for (const spool of spools ?? []) {
      const owner = spool.owner?.trim();
      if (owner) names.add(owner);
    }
    for (const user of users ?? []) {
      const username = user.username?.trim();
      if (username) names.add(username);
    }
    return Array.from(names).sort((a, b) => a.localeCompare(b));
  }, [spools, users]);
}
