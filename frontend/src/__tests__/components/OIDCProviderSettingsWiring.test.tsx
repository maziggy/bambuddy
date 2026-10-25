/**
 * OIDC provider list: edit, delete, enable toggle and icon actions send
 * their request for the provider that was clicked, the edit leaves the
 * stored client secret alone unless one is typed, and cancelling the delete
 * confirm sends nothing.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { OIDCProviderSettings } from '../../components/OIDCProviderSettings';

type Captured = { method: string; path: string; body: Record<string, unknown> | null };
let captured: Captured[];

function provider(id: number, name: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    name,
    issuer_url: `https://${name.toLowerCase()}.idp.example`,
    client_id: `${name.toLowerCase()}-client`,
    scopes: 'openid email profile',
    is_enabled: true,
    auto_create_users: false,
    auto_link_existing_accounts: false,
    email_claim: 'email',
    require_email_verified: true,
    group_claim: 'groups',
    group_mapping: {},
    icon_url: `https://${name.toLowerCase()}.idp.example/icon.png`,
    has_icon: true,
    default_group_id: null,
    is_autologin: false,
    is_env_managed: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...extra,
  };
}

async function capture(request: Request) {
  const text = await request.text();
  captured.push({
    method: request.method,
    path: new URL(request.url).pathname,
    body: text ? JSON.parse(text) : null,
  });
}

beforeEach(() => {
  captured = [];
  server.use(
    http.get('*/api/v1/auth/oidc/providers/all', () =>
      HttpResponse.json([provider(7, 'Alpha'), provider(9, 'Beta')]),
    ),
    http.get('*/api/v1/groups/', () => HttpResponse.json([])),
    http.put('*/api/v1/auth/oidc/providers/:id', async ({ request }) => {
      await capture(request);
      return HttpResponse.json(provider(9, 'Beta'));
    }),
    http.delete('*/api/v1/auth/oidc/providers/:id', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({ message: 'deleted' });
    }),
    http.delete('*/api/v1/auth/oidc/providers/:id/icon', async ({ request }) => {
      await capture(request);
      return new HttpResponse(null, { status: 204 });
    }),
    http.post('*/api/v1/auth/oidc/providers/:id/icon/refresh', async ({ request }) => {
      await capture(request);
      return HttpResponse.json(provider(9, 'Beta'));
    }),
  );
});

describe('OIDCProviderSettings wiring', () => {
  it('editing a provider PUTs to its id without client_secret when the secret is left blank', async () => {
    const user = userEvent.setup();
    render(<OIDCProviderSettings />);

    await user.click(await screen.findByTestId('edit-provider-9'));
    const scopes = screen.getByDisplayValue('openid email profile');
    await user.clear(scopes);
    await user.type(scopes, 'openid email');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(captured).toHaveLength(1));
    expect(captured[0].method).toBe('PUT');
    expect(captured[0].path).toBe('/api/v1/auth/oidc/providers/9');
    expect(captured[0].body).toMatchObject({
      name: 'Beta',
      issuer_url: 'https://beta.idp.example',
      client_id: 'beta-client',
      scopes: 'openid email',
    });
    expect(captured[0].body).not.toHaveProperty('client_secret');
  });

  it('a typed client secret is sent with the edit', async () => {
    const user = userEvent.setup();
    render(<OIDCProviderSettings />);

    await user.click(await screen.findByTestId('edit-provider-9'));
    await user.type(document.querySelector('input[type="password"]') as HTMLInputElement, 'test-secret-1');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(captured).toHaveLength(1));
    expect(captured[0].body).toMatchObject({ client_secret: 'test-secret-1' });
  });

  it('delete asks first, then DELETEs the clicked provider', async () => {
    const user = userEvent.setup();
    render(<OIDCProviderSettings />);

    // Second row, so a delete of "the first provider" would not pass
    await user.click(await screen.findByTestId('delete-provider-9'));
    expect(captured).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: /^delete$/i }));

    await waitFor(() => expect(captured).toHaveLength(1));
    expect(captured[0]).toMatchObject({ method: 'DELETE', path: '/api/v1/auth/oidc/providers/9' });
  });

  it('cancelling the delete confirm sends nothing', async () => {
    const user = userEvent.setup();
    render(<OIDCProviderSettings />);

    await user.click(await screen.findByTestId('delete-provider-7'));
    await user.click(screen.getByRole('button', { name: /^cancel$/i }));

    await waitFor(() => expect(screen.queryByRole('button', { name: /^delete$/i })).not.toBeInTheDocument());
    expect(captured).toHaveLength(0);
  });

  it('refresh icon and remove icon call the clicked provider id', async () => {
    const user = userEvent.setup();
    render(<OIDCProviderSettings />);

    await user.click(await screen.findByTestId('refresh-icon-9'));
    await waitFor(() => expect(captured).toHaveLength(1));
    expect(captured[0]).toMatchObject({ method: 'POST', path: '/api/v1/auth/oidc/providers/9/icon/refresh' });

    await user.click(screen.getByTestId('remove-icon-7'));
    await waitFor(() => expect(captured).toHaveLength(2));
    expect(captured[1]).toMatchObject({ method: 'DELETE', path: '/api/v1/auth/oidc/providers/7/icon' });
  });

  it('the enable toggle sends only {is_enabled:false} for that provider', async () => {
    const user = userEvent.setup();
    render(<OIDCProviderSettings />);

    await screen.findByTestId('edit-provider-9');
    // One toggle per provider, in list order
    const toggles = screen.getAllByRole('switch');
    await user.click(toggles[1]);

    await waitFor(() => expect(captured).toHaveLength(1));
    expect(captured[0]).toEqual({
      method: 'PUT',
      path: '/api/v1/auth/oidc/providers/9',
      body: { is_enabled: false },
    });
  });
});
