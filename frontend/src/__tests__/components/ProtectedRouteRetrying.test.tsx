/**
 * A stored sign-in the server can't check yet waits on the page it asked for
 * instead of landing on /login (#3360): a SpoolBuddy kiosk is touch-only and
 * has no way to sign in there, so it stayed on the login page until a reboot.
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { AuthProvider } from '../../contexts/AuthContext';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { PermissionRoute, ProtectedRoute } from '../../App';
import { CamWallPage } from '../../pages/CamWallPage';
import { setAuthToken } from '../../api/client';

function renderKiosk() {
  return render(
    <MemoryRouter initialEntries={['/spoolbuddy']}>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<div>login page</div>} />
          <Route path="/spoolbuddy" element={<ProtectedRoute><div>kiosk page</div></ProtectedRoute>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>
  );
}

describe('ProtectedRoute while the sign-in is unconfirmed (#3360)', () => {
  let down: boolean;

  beforeEach(() => {
    down = true;
    setAuthToken('bb_kiosk', 'persistent');
    server.use(
      http.get('/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: true, requires_setup: false })),
      http.get('/api/v1/auth/me', () =>
        down
          ? HttpResponse.json({ detail: 'Authentication service temporarily unavailable' }, { status: 503 })
          : HttpResponse.json({ id: 1, username: 'kiosk', is_active: true, permissions: [], groups: [] })
      )
    );
  });

  afterEach(() => {
    setAuthToken(null);
  });

  it('shows the retry screen, then the page once Bambuddy answers', async () => {
    renderKiosk();

    expect(await screen.findByText("Can't reach Bambuddy. Retrying…", {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.queryByText('login page')).not.toBeInTheDocument();

    down = false;
    expect(await screen.findByText('kiosk page', {}, { timeout: 6000 })).toBeInTheDocument();
  }, 12000);

  it('waits the same way on a permission-gated page', async () => {
    render(
      <MemoryRouter initialEntries={['/settings']}>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<div>login page</div>} />
            <Route path="/settings" element={<PermissionRoute permission="settings:read"><div>settings</div></PermissionRoute>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText("Can't reach Bambuddy. Retrying…", {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.queryByText('login page')).not.toBeInTheDocument();
  });

  it('waits the same way on the Cam Wall opened with a session', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={['/camwall']}>
          <AuthProvider>
            <Routes>
              <Route path="/login" element={<div>login page</div>} />
              <Route path="/camwall" element={<CamWallPage />} />
            </Routes>
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(await screen.findByText("Can't reach Bambuddy. Retrying…", {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.queryByText('login page')).not.toBeInTheDocument();
  });

  it('still goes to the login page on a definite 401', async () => {
    server.use(http.get('/api/v1/auth/me', () => HttpResponse.json({ detail: 'Invalid API key' }, { status: 401 })));

    renderKiosk();

    expect(await screen.findByText('login page', {}, { timeout: 4000 })).toBeInTheDocument();
  });
});
