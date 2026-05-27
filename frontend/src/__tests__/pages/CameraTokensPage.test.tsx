/**
 * Frontend tests for the Camera API Tokens page (#1108).
 *
 * Coverage:
 * - List populates the "My tokens" table.
 * - Create flow shows the plaintext exactly once in a copy modal.
 * - Days input is clamped to the 365-day cap.
 * - Revoke triggers the confirm prompt and refreshes the list.
 * - Listing endpoints never return the plaintext (`token` field is null in the
 *   refreshed view; covered indirectly via the create-then-refresh flow).
 */

import { describe, it, expect, afterEach, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import CameraTokensPage from '../../pages/CameraTokensPage';

function token(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: 1,
    user_id: 7,
    name: 'Home Assistant',
    scope: 'camera_stream',
    lookup_prefix: 'abcd1234',
    created_at: '2026-04-01T10:00:00Z',
    expires_at: '2026-07-01T10:00:00Z',
    last_used_at: null,
    token: null,
    ...overrides,
  };
}

afterEach(() => {
  server.resetHandlers();
  vi.restoreAllMocks();
});

describe('CameraTokensPage', () => {
  it('renders the user\'s tokens', async () => {
    server.use(
      http.get('*/api/v1/auth/tokens', ({ request }) => {
        // No `user_id` query → caller's own tokens.
        const url = new URL(request.url);
        if (url.searchParams.has('user_id')) {
          return HttpResponse.json([]);
        }
        return HttpResponse.json([token({ id: 1, name: 'Home Assistant' })]);
      }),
    );

    render(<CameraTokensPage />);

    expect(await screen.findByText('Home Assistant')).toBeInTheDocument();
    expect(screen.getByText('abcd1234…')).toBeInTheDocument();
  });

  it('shows the empty state when the user has no tokens', async () => {
    server.use(
      http.get('*/api/v1/auth/tokens', () => HttpResponse.json([])),
      http.get('*/api/v1/auth/tokens/all', () => HttpResponse.json([])),
      http.get('*/api/v1/users/', () => HttpResponse.json([])),
    );

    render(<CameraTokensPage />);

    // Auth-disabled test environment treats user as admin → "No tokens yet"
    // renders once per panel (My tokens + admin view).
    await waitFor(() =>
      expect(screen.getAllByText(/no tokens yet/i).length).toBeGreaterThan(0),
    );
  });

  it('creates a token and displays the plaintext exactly once', async () => {
    let getCount = 0;
    server.use(
      http.get('*/api/v1/auth/tokens', () => {
        getCount += 1;
        // First load = empty, post-create reload = the new row WITHOUT the
        // plaintext (the listing API never returns it).
        return HttpResponse.json(
          getCount === 1 ? [] : [token({ id: 42, name: 'My Frigate', token: null })],
        );
      }),
      http.post('*/api/v1/auth/tokens', async ({ request }) => {
        const body = await request.json();
        expect(body).toMatchObject({
          name: 'My Frigate',
          expires_in_days: 90,
          scope: 'camera_stream',
        });
        return HttpResponse.json(
          token({ id: 42, name: 'My Frigate', token: 'bblt_abcd1234_secretsecretsecretsecretsecret' }),
          { status: 201 },
        );
      }),
    );

    const user = userEvent.setup();
    render(<CameraTokensPage />);

    await screen.findByText(/no tokens yet/i);
    await user.type(screen.getByLabelText(/token name/i), 'My Frigate');
    await user.click(screen.getByRole('button', { name: /^create$/i }));

    // Plaintext shown once in the modal.
    expect(
      await screen.findByText('bblt_abcd1234_secretsecretsecretsecretsecret'),
    ).toBeInTheDocument();
    expect(screen.getByText(/only time this token will be visible/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /i've saved it/i }));

    // After dismissing, the listing reload shows the row but NOT the plaintext.
    expect(await screen.findByText('My Frigate')).toBeInTheDocument();
    expect(
      screen.queryByText('bblt_abcd1234_secretsecretsecretsecretsecret'),
    ).not.toBeInTheDocument();
  });

  it('clamps the days input to the 365-day policy cap', async () => {
    server.use(
      http.get('*/api/v1/auth/tokens', () => HttpResponse.json([])),
    );

    const user = userEvent.setup();
    render(<CameraTokensPage />);
    await screen.findByText(/no tokens yet/i);

    const daysInput = screen.getByLabelText(/days until expiry/i) as HTMLInputElement;
    await user.clear(daysInput);
    await user.type(daysInput, '500');
    expect(Number(daysInput.value)).toBe(365);
  });

  it('revokes a token after confirming in the styled modal', async () => {
    let revoked = false;
    server.use(
      http.get('*/api/v1/auth/tokens', () =>
        HttpResponse.json(revoked ? [] : [token({ id: 9, name: 'kiosk' })]),
      ),
      // Auth-disabled test env treats the user as admin, so the page also
      // calls /tokens/all and /users/. Stub them out so the refresh path
      // doesn't try to hit unmocked endpoints.
      http.get('*/api/v1/auth/tokens/all', () => HttpResponse.json([])),
      http.get('*/api/v1/users/', () => HttpResponse.json([])),
      http.delete('*/api/v1/auth/tokens/9', () => {
        revoked = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    const user = userEvent.setup();
    render(<CameraTokensPage />);

    await screen.findByText('kiosk');
    // Open the confirm modal.
    await user.click(screen.getByRole('button', { name: /revoke/i }));
    // Modal shows the token name and a Cancel + Revoke pair.
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent(/kiosk/);
    // Confirm — scope to the dialog so we don't match the row's revoke
    // button still rendered in the table behind the modal.
    await user.click(within(dialog).getByRole('button', { name: /^revoke$/i }));

    await waitFor(() => {
      expect(screen.queryByText('kiosk')).not.toBeInTheDocument();
      // "No tokens yet" appears once for "My tokens" and (in admin mode) once
      // for the all-users panel — at least one match is sufficient.
      expect(screen.getAllByText(/no tokens yet/i).length).toBeGreaterThan(0);
    });
  });

  it('does not revoke when the user cancels in the modal', async () => {
    let revokeCalled = false;
    server.use(
      http.get('*/api/v1/auth/tokens', () =>
        HttpResponse.json([token({ id: 9, name: 'kiosk' })]),
      ),
      http.get('*/api/v1/auth/tokens/all', () => HttpResponse.json([])),
      http.get('*/api/v1/users/', () => HttpResponse.json([])),
      http.delete('*/api/v1/auth/tokens/9', () => {
        revokeCalled = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    const user = userEvent.setup();
    render(<CameraTokensPage />);

    await screen.findByText('kiosk');
    await user.click(screen.getByRole('button', { name: /revoke/i }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: /cancel/i }));

    // Modal closed, listing untouched, DELETE never sent.
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByText('kiosk')).toBeInTheDocument();
    expect(revokeCalled).toBe(false);
  });
});
