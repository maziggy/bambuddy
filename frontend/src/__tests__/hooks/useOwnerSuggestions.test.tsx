/**
 * The owner suggestions (#1301): owners already in use plus the registered
 * users. Only suggestions, so a failing user list must leave the owners alone.
 */

import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { type ReactNode } from 'react';
import { server } from '../mocks/server';
import { useOwnerSuggestions } from '../../hooks/useOwnerSuggestions';

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

const USERS = [
  { id: 1, username: 'carol' },
  { id: 2, username: 'Alice' },
];

describe('useOwnerSuggestions', () => {
  it('offers the owners in use and the registered users, sorted and without repeats', async () => {
    server.use(http.get('*/api/v1/users/slim', () => HttpResponse.json(USERS)));
    const spools = [{ owner: 'Bob' }, { owner: 'Alice' }, { owner: ' Bob ' }, { owner: null }, {}];

    const { result } = renderHook(() => useOwnerSuggestions(spools), { wrapper: wrapper() });

    await waitFor(() => expect(result.current).toEqual(['Alice', 'Bob', 'carol']));
  });

  it('offers the registered users when no spool has an owner yet', async () => {
    server.use(http.get('*/api/v1/users/slim', () => HttpResponse.json(USERS)));

    const { result } = renderHook(() => useOwnerSuggestions([]), { wrapper: wrapper() });

    await waitFor(() => expect(result.current).toEqual(['Alice', 'carol']));
  });

  it('keeps the owners in use when the user list is refused', async () => {
    let asked = false;
    server.use(
      http.get('*/api/v1/users/slim', () => {
        asked = true;
        return HttpResponse.json({ detail: 'Forbidden' }, { status: 403 });
      }),
    );

    const { result } = renderHook(() => useOwnerSuggestions([{ owner: 'Bob' }]), { wrapper: wrapper() });

    await waitFor(() => expect(asked).toBe(true));
    expect(result.current).toEqual(['Bob']);
  });

  it('does not ask for the users when switched off', async () => {
    let asked = false;
    server.use(
      http.get('*/api/v1/users/slim', () => {
        asked = true;
        return HttpResponse.json(USERS);
      }),
    );

    const { result } = renderHook(() => useOwnerSuggestions([{ owner: 'Bob' }], false), { wrapper: wrapper() });

    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(asked).toBe(false);
    expect(result.current).toEqual(['Bob']);
  });

  it('knows nothing before the spools arrive', () => {
    server.use(http.get('*/api/v1/users/slim', () => HttpResponse.json([])));

    const { result } = renderHook(() => useOwnerSuggestions(undefined), { wrapper: wrapper() });

    expect(result.current).toEqual([]);
  });
});
