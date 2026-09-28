/**
 * "Sign in with Bambuddy" consent page.
 *
 * The assertions that carry weight: the page never redirects before the
 * backend has vouched for the callback URL, and when it does redirect it uses
 * the callback the backend returned, with the code and the app's state.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { api, ApiError } from '../../api/client';

const mockUseAuth = { authEnabled: true, loading: false };
vi.mock('../../contexts/AuthContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/AuthContext')>();
  return { ...actual, useAuth: () => mockUseAuth };
});

import { ConnectAuthorizePage } from '../../pages/ConnectAuthorizePage';

const CALLBACK = 'http://orders.local:8090/auth/callback';
const CHALLENGE = 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM';

function query(overrides: Record<string, string> = {}) {
  const params = new URLSearchParams({
    client_id: 'bba_123',
    redirect_uri: CALLBACK,
    state: 'st4te',
    code_challenge: CHALLENGE,
    code_challenge_method: 'S256',
    ...overrides,
  });
  return `?${params.toString()}`;
}

function renderAt(search: string) {
  return render(
    <MemoryRouter initialEntries={[`/connect/authorize${search}`]}>
      <ConnectAuthorizePage />
    </MemoryRouter>,
  );
}

let replace: ReturnType<typeof vi.fn>;
const realLocation = window.location;

beforeEach(() => {
  mockUseAuth.authEnabled = true;
  replace = vi.fn();
  Object.defineProperty(window, 'location', {
    configurable: true,
    writable: true,
    value: { ...realLocation, href: 'http://localhost:3000/connect/authorize', replace },
  });
});

afterEach(() => {
  Object.defineProperty(window, 'location', { configurable: true, writable: true, value: realLocation });
  vi.restoreAllMocks();
});

describe('ConnectAuthorizePage', () => {
  it('asks for consent, then returns to the registered callback with code and state', async () => {
    vi.spyOn(api, 'getConnectAuthorizeInfo').mockResolvedValue({
      app_name: 'Order Desk',
      username: 'martin',
      already_granted: false,
    });
    const authorize = vi
      .spyOn(api, 'connectAuthorize')
      .mockResolvedValue({ code: 'c0de', redirect_uri: CALLBACK });

    renderAt(query());

    expect(await screen.findByText('Order Desk wants to sign you in')).toBeInTheDocument();
    expect(screen.getByText('Signed in to Bambuddy as martin')).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole('button', { name: 'Allow' }));

    expect(authorize).toHaveBeenCalledWith({
      client_id: 'bba_123',
      redirect_uri: CALLBACK,
      code_challenge: CHALLENGE,
      code_challenge_method: 'S256',
    });
    await waitFor(() => expect(replace).toHaveBeenCalledWith(`${CALLBACK}?code=c0de&state=st4te`));
  });

  it('skips the consent screen for an app the user already allowed', async () => {
    vi.spyOn(api, 'getConnectAuthorizeInfo').mockResolvedValue({
      app_name: 'Order Desk',
      username: 'martin',
      already_granted: true,
    });
    vi.spyOn(api, 'connectAuthorize').mockResolvedValue({ code: 'c0de', redirect_uri: CALLBACK });

    renderAt(query());

    await waitFor(() => expect(replace).toHaveBeenCalledWith(`${CALLBACK}?code=c0de&state=st4te`));
    expect(screen.queryByRole('button', { name: 'Allow' })).not.toBeInTheDocument();
  });

  it('cancel returns access_denied to the app', async () => {
    vi.spyOn(api, 'getConnectAuthorizeInfo').mockResolvedValue({
      app_name: 'Order Desk',
      username: 'martin',
      already_granted: false,
    });
    const authorize = vi.spyOn(api, 'connectAuthorize');

    renderAt(query());
    await userEvent.click(await screen.findByRole('button', { name: 'Cancel' }));

    expect(replace).toHaveBeenCalledWith(`${CALLBACK}?error=access_denied&state=st4te`);
    expect(authorize).not.toHaveBeenCalled();
  });

  it('shows an error and never redirects when the backend rejects the request', async () => {
    vi.spyOn(api, 'getConnectAuthorizeInfo').mockRejectedValue(new ApiError('bad', 400));

    renderAt(query({ redirect_uri: 'http://evil.example/steal' }));

    expect(await screen.findByText("Can't sign you in")).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });

  it('rejects a request without PKCE before asking the backend', async () => {
    const info = vi.spyOn(api, 'getConnectAuthorizeInfo');

    renderAt(query({ code_challenge_method: 'plain' }));

    expect(await screen.findByText("Can't sign you in")).toBeInTheDocument();
    expect(info).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
  });

  it('explains that sign-in is unavailable while authentication is off', async () => {
    mockUseAuth.authEnabled = false;
    const info = vi.spyOn(api, 'getConnectAuthorizeInfo');

    renderAt(query());

    expect(await screen.findByText(/Bambuddy authentication is turned off/)).toBeInTheDocument();
    expect(info).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
  });
});
