/**
 * Tests for ColorCatalogProvider — the provider that fetches the backend color
 * catalog once per session and pushes it into the module-level store used by
 * getColorName / resolveSpoolColorName. See #857.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { ColorCatalogProvider } from '../../contexts/ColorCatalogContext';
import { AuthProvider } from '../../contexts/AuthContext';
import { ThemeProvider } from '../../contexts/ThemeContext';
import { ToastProvider } from '../../contexts/ToastContext';
import { getColorName, __resetColorCatalogForTests } from '../../utils/colors';

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
    },
  });

  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <ThemeProvider>
            <ToastProvider>
              <AuthProvider>
                <ColorCatalogProvider>{children}</ColorCatalogProvider>
              </AuthProvider>
            </ToastProvider>
          </ThemeProvider>
        </BrowserRouter>
      </QueryClientProvider>
    );
  };
}

describe('ColorCatalogProvider', () => {
  beforeEach(() => {
    __resetColorCatalogForTests();
    // Default auth handler: auth disabled so the provider's query is allowed
    // to fire immediately without requiring a login.
    server.use(
      http.get('/api/v1/auth/status', () =>
        HttpResponse.json({ auth_enabled: false, requires_setup: false })
      )
    );
  });

  it('populates the runtime catalog from /inventory/colors/map', async () => {
    server.use(
      http.get('/api/v1/inventory/colors/map', () =>
        HttpResponse.json({
          colors: {
            f5b6cd: 'Cherry Pink',
            de4343: 'Scarlet Red',
            '8344b0': 'Purple',
          },
        })
      )
    );

    const Wrapper = createWrapper();
    render(
      <Wrapper>
        <div data-testid="child">ok</div>
      </Wrapper>
    );

    await waitFor(() => {
      // Regression for #857: before the fix, F5B6CD resolved to 'Scarlet Red'
      // via the suffix fallback. After the fix, it resolves from the catalog.
      expect(getColorName('f5b6cd')).toBe('Cherry Pink');
    });
    expect(getColorName('de4343')).toBe('Scarlet Red');
    expect(getColorName('8344b0')).toBe('Purple');
  });

  it('still renders children even when the catalog fetch fails', async () => {
    server.use(
      http.get('/api/v1/inventory/colors/map', () =>
        HttpResponse.json({ detail: 'kaboom' }, { status: 500 })
      )
    );

    const Wrapper = createWrapper();
    const { getByTestId } = render(
      <Wrapper>
        <div data-testid="child">ok</div>
      </Wrapper>
    );

    // The provider must not block rendering on a failed fetch — if it did,
    // the whole app would white-screen whenever the backend /colors/map route
    // 500'd. The catalog is load-bearing for cosmetics, not correctness.
    expect(getByTestId('child').textContent).toBe('ok');
  });

  it('falls back to HSL-bucket name when catalog miss', async () => {
    server.use(
      http.get('/api/v1/inventory/colors/map', () =>
        HttpResponse.json({ colors: { f5b6cd: 'Cherry Pink' } })
      )
    );

    const Wrapper = createWrapper();
    render(
      <Wrapper>
        <div>ok</div>
      </Wrapper>
    );

    // Wait for the provider to load the catalog at least once.
    await waitFor(() => {
      expect(getColorName('f5b6cd')).toBe('Cherry Pink');
    });

    // A hex that isn't in the (limited) catalog must fall through to HSL so
    // unknown colors still get *some* name rather than the raw hex code.
    expect(getColorName('123456')).toBe('Blue');
  });
});
