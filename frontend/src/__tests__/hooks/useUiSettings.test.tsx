import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { hasGcodeSnippets, useUiSettings } from '../../hooks/useUiSettings';

const permissions = vi.hoisted(() => ({ granted: [] as string[] }));

vi.mock('../../contexts/AuthContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/AuthContext')>();
  return { ...actual, useAuth: () => ({ hasPermission: (p: string) => permissions.granted.includes(p) }) };
});

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe('useUiSettings', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/settings/', () => HttpResponse.json({ billing_enabled: true, smtp_host: 'mail.example' })),
      http.get('/api/v1/settings/ui-flags', () => HttpResponse.json({ billing_enabled: true, has_gcode_snippets: true })),
      http.get('/api/v1/settings/ui-preferences', () => HttpResponse.json({ time_format: '24h' })),
    );
  });

  it('gives a user who may read the settings all of them', async () => {
    permissions.granted = ['settings:read'];
    const { result } = renderHook(() => useUiSettings(), { wrapper });

    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data).toEqual({ billing_enabled: true, smtp_host: 'mail.example' });
  });

  it('gives any other user what the pages need without a refused request', async () => {
    permissions.granted = [];
    let refused = false;
    server.use(
      http.get('/api/v1/settings/', () => {
        refused = true;
        return new HttpResponse(null, { status: 403 });
      }),
    );
    const { result } = renderHook(() => useUiSettings(), { wrapper });

    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(result.current.data).toEqual({ billing_enabled: true, has_gcode_snippets: true, time_format: '24h' });
    expect(refused).toBe(false);
  });
});

describe('hasGcodeSnippets', () => {
  it.each([
    [{ gcode_snippets: '{"X1C": {}}' }, true],
    [{ has_gcode_snippets: true }, true],
    [{ gcode_snippets: '' }, false],
    [{}, false],
    [undefined, false],
  ])('%j -> %s', (settings, expected) => {
    expect(hasGcodeSnippets(settings)).toBe(expected);
  });
});
