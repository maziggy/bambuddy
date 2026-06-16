/**
 * Tests for OrcaCloudView component — covers the four UI phases of the
 * paste-based PKCE handshake: disconnected, awaiting-paste, connected,
 * and disconnect.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { http, HttpResponse } from 'msw';

import { server } from '../mocks/server';
import { render } from '../utils';
import { OrcaCloudView } from '../../components/OrcaCloudView';

// JSDOM doesn't implement window.open; the connect flow opens the auth URL
// in a new tab so we stub it to capture the call.
beforeEach(() => {
  vi.stubGlobal('open', vi.fn());
});

const noProfilesResponse = { profiles: [] };

describe('OrcaCloudView', () => {
  it('shows all four sign-in options when not connected', async () => {
    server.use(
      http.get('/api/v1/orca-cloud/status', () =>
        HttpResponse.json({ connected: false, email: null, user_id: null }),
      ),
    );
    render(<OrcaCloudView />);

    await waitFor(() => {
      expect(screen.getByText(/Connect to Orca Cloud/i)).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: /Sign in with Google/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Sign in with Apple/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Sign in with GitHub/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Sign in with email and password/i })).toBeInTheDocument();
  });

  it('passes the selected OAuth provider to auth/start', async () => {
    let receivedProvider: string | undefined;
    server.use(
      http.get('/api/v1/orca-cloud/status', () =>
        HttpResponse.json({ connected: false, email: null, user_id: null }),
      ),
      http.post('/api/v1/orca-cloud/auth/start', async ({ request }) => {
        const body = (await request.json()) as { provider?: string };
        receivedProvider = body.provider;
        return HttpResponse.json({ auth_url: 'https://auth.orcaslicer.com/auth/v1/authorize?test=1' });
      }),
    );
    render(<OrcaCloudView />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Sign in with Apple/i })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: /Sign in with Apple/i }));

    await waitFor(() => {
      expect(receivedProvider).toBe('apple');
    });
    expect(window.open).toHaveBeenCalledWith(
      'https://auth.orcaslicer.com/auth/v1/authorize?test=1',
      '_blank',
      'noopener,noreferrer',
    );
  });

  it('connects via email and password without the paste flow', async () => {
    let connected = false;
    let receivedCreds: { email?: string; password?: string } = {};
    server.use(
      http.get('/api/v1/orca-cloud/status', () =>
        HttpResponse.json(
          connected
            ? { connected: true, email: 'martin@example.com', user_id: 'u1' }
            : { connected: false, email: null, user_id: null },
        ),
      ),
      http.post('/api/v1/orca-cloud/auth/password', async ({ request }) => {
        receivedCreds = (await request.json()) as { email?: string; password?: string };
        connected = true;
        return HttpResponse.json({ connected: true, email: 'martin@example.com', user_id: 'u1' });
      }),
      http.get('/api/v1/orca-cloud/profiles', () => HttpResponse.json(noProfilesResponse)),
    );
    render(<OrcaCloudView />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Sign in with email and password/i })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: /Sign in with email and password/i }));

    // The password form replaces the provider picker.
    await waitFor(() => {
      expect(screen.getByLabelText(/^Email$/i)).toBeInTheDocument();
    });
    fireEvent.change(screen.getByLabelText(/^Email$/i), { target: { value: 'martin@example.com' } });
    fireEvent.change(screen.getByLabelText(/^Password$/i), { target: { value: 'hunter2' } });
    // Click the submit button inside the form (not the picker's email button).
    const submitButtons = screen.getAllByRole('button', { name: /^Sign in$/i });
    fireEvent.click(submitButtons[submitButtons.length - 1]);

    await waitFor(() => {
      expect(screen.getByText('martin@example.com')).toBeInTheDocument();
    });
    expect(receivedCreds).toEqual({ email: 'martin@example.com', password: 'hunter2' });
  });

  it('rejects a URL without a code parameter with a client-side error', async () => {
    server.use(
      http.get('/api/v1/orca-cloud/status', () =>
        HttpResponse.json({ connected: false, email: null, user_id: null }),
      ),
      http.post('/api/v1/orca-cloud/auth/start', () =>
        HttpResponse.json({ auth_url: 'https://auth.orcaslicer.com/auth/v1/authorize?test=1' }),
      ),
    );
    render(<OrcaCloudView />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Sign in with Google/i })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: /Sign in with Google/i }));
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/http:\/\/localhost:41172/i)).toBeInTheDocument();
    });

    const textarea = screen.getByPlaceholderText(/http:\/\/localhost:41172/i);
    // Paste something with no ?code= — the client-side guard should fire
    // before we hit the server.
    fireEvent.change(textarea, { target: { value: 'http://localhost:41172/callback?error=denied' } });
    fireEvent.click(screen.getByRole('button', { name: /Finish connecting/i }));

    await waitFor(() => {
      expect(screen.getByText(/does not look like an Orca Cloud callback/i)).toBeInTheDocument();
    });
  });

  it('shows the connected state with the email after a successful paste', async () => {
    // Start disconnected; after a successful finish the status query refetches
    // and returns connected. MSW lets us swap handlers mid-test.
    let connected = false;
    server.use(
      http.get('/api/v1/orca-cloud/status', () => {
        return HttpResponse.json(
          connected
            ? { connected: true, email: 'martin@example.com', user_id: 'u1' }
            : { connected: false, email: null, user_id: null },
        );
      }),
      http.post('/api/v1/orca-cloud/auth/start', () =>
        HttpResponse.json({ auth_url: 'https://auth.orcaslicer.com/auth/v1/authorize?test=1' }),
      ),
      http.post('/api/v1/orca-cloud/auth/finish', () => {
        connected = true;
        return HttpResponse.json({ connected: true, email: 'martin@example.com', user_id: 'u1' });
      }),
      http.get('/api/v1/orca-cloud/profiles', () => HttpResponse.json(noProfilesResponse)),
    );
    render(<OrcaCloudView />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Sign in with Google/i })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: /Sign in with Google/i }));
    await waitFor(() => {
      expect(screen.getByPlaceholderText(/http:\/\/localhost:41172/i)).toBeInTheDocument();
    });

    fireEvent.change(screen.getByPlaceholderText(/http:\/\/localhost:41172/i), {
      target: { value: 'http://localhost:41172/callback?code=ABC&state=XYZ' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Finish connecting/i }));

    // After the finish call resolves, the status query is invalidated and
    // refetches connected=true → the connection banner appears with the email.
    await waitFor(() => {
      expect(screen.getByText('martin@example.com')).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: /Disconnect/i })).toBeInTheDocument();
  });

  it('clears the connection on Disconnect', async () => {
    let connected = true;
    server.use(
      http.get('/api/v1/orca-cloud/status', () =>
        HttpResponse.json(
          connected
            ? { connected: true, email: 'martin@example.com', user_id: 'u1' }
            : { connected: false, email: null, user_id: null },
        ),
      ),
      http.get('/api/v1/orca-cloud/profiles', () => HttpResponse.json(noProfilesResponse)),
      http.post('/api/v1/orca-cloud/logout', () => {
        connected = false;
        return HttpResponse.json({ success: true });
      }),
    );
    render(<OrcaCloudView />);

    await waitFor(() => {
      expect(screen.getByText('martin@example.com')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: /Disconnect/i }));

    await waitFor(() => {
      expect(screen.getByText(/Connect to Orca Cloud/i)).toBeInTheDocument();
    });
  });
});
