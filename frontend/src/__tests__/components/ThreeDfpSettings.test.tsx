/**
 * The 3D Filament Profiles settings card (#2981): Connect, the code shown while
 * the user approves it, the connected account with its plan and quota, and
 * disconnecting.
 */
import { describe, it, expect } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { http, HttpResponse } from 'msw';

import { server } from '../mocks/server';
import { render } from '../utils';
import { ThreeDfpSettings } from '../../components/ThreeDfpSettings';

const DEVICE_START = {
  user_code: 'WDJB-MJHT',
  verification_uri: 'https://3dfilamentprofiles.com/connect',
  verification_uri_complete: 'https://3dfilamentprofiles.com/connect?code=WDJB-MJHT',
  interval: 5,
  expires_in: 600,
};

const FREE_ACCOUNT = {
  email: 'maker@example.com',
  plan: 'free',
  active_scopes: ['filaments:read'],
  quota: { limit: 100, remaining: 97, resets_at: '2026-12-01T00:00:00.000Z' },
  upgrade_url: 'https://3dfilamentprofiles.com/my/api',
};

describe('ThreeDfpSettings', () => {
  it('shows the code and the approval link after Connect', async () => {
    server.use(
      http.get('/api/v1/threedfp/status', () => HttpResponse.json({ connected: false, email: null })),
      http.post('/api/v1/threedfp/device/start', () => HttpResponse.json(DEVICE_START)),
      http.post('/api/v1/threedfp/device/poll', () =>
        HttpResponse.json({ status: 'authorization_pending', connected: false }),
      ),
    );
    render(<ThreeDfpSettings />);

    fireEvent.click(await screen.findByRole('button', { name: /connect 3d filament profiles/i }));

    // Shown even though the link carries it: 3DFP asks the user to compare.
    expect(await screen.findByText('WDJB-MJHT')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /open 3d filament profiles/i })).toHaveAttribute(
      'href',
      DEVICE_START.verification_uri_complete,
    );
    expect(screen.getByText(/code expires in (10:00|9:5\d)/)).toBeInTheDocument();
  });

  it('flips to connected once the poll completes', async () => {
    let connected = false;
    server.use(
      http.get('/api/v1/threedfp/status', () =>
        HttpResponse.json({ connected, email: connected ? 'maker@example.com' : null }),
      ),
      http.get('/api/v1/threedfp/account', () => HttpResponse.json(FREE_ACCOUNT)),
      http.post('/api/v1/threedfp/device/start', () => HttpResponse.json({ ...DEVICE_START, interval: 1 })),
      http.post('/api/v1/threedfp/device/poll', () => {
        connected = true;
        return HttpResponse.json({ status: 'complete', connected: true });
      }),
    );
    render(<ThreeDfpSettings />);

    fireEvent.click(await screen.findByRole('button', { name: /connect 3d filament profiles/i }));

    expect(await screen.findByText('Connected as maker@example.com', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(await screen.findByText('97 of 100 requests left this month')).toBeInTheDocument();
    // A free account can look up but not sync: point at the upgrade.
    expect(screen.getByRole('link', { name: /get api access/i })).toHaveAttribute('href', FREE_ACCOUNT.upgrade_url);
  });

  it('says why the attempt ended when the user denies it', async () => {
    server.use(
      http.get('/api/v1/threedfp/status', () => HttpResponse.json({ connected: false, email: null })),
      http.post('/api/v1/threedfp/device/start', () => HttpResponse.json({ ...DEVICE_START, interval: 1 })),
      http.post('/api/v1/threedfp/device/poll', () => HttpResponse.json({ status: 'access_denied', connected: false })),
    );
    render(<ThreeDfpSettings />);

    fireEvent.click(await screen.findByRole('button', { name: /connect 3d filament profiles/i }));

    expect(
      await screen.findByText('The connection was denied on 3D Filament Profiles.', {}, { timeout: 4000 }),
    ).toBeInTheDocument();
    expect(screen.queryByText('WDJB-MJHT')).not.toBeInTheDocument();
  });

  it('hides the upgrade line for an account with API access, and disconnects', async () => {
    let connected = true;
    let disconnected = false;
    server.use(
      http.get('/api/v1/threedfp/status', () => HttpResponse.json({ connected, email: 'maker@example.com' })),
      http.get('/api/v1/threedfp/account', () =>
        HttpResponse.json({ ...FREE_ACCOUNT, plan: 'api', active_scopes: ['filaments:read', 'spools:read'] }),
      ),
      http.post('/api/v1/threedfp/disconnect', () => {
        disconnected = true;
        connected = false;
        return HttpResponse.json({ success: true });
      }),
    );
    render(<ThreeDfpSettings />);

    expect(await screen.findByText('Plan: API access')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /get api access/i })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /disconnect/i }));

    await waitFor(() => expect(disconnected).toBe(true));
    expect(await screen.findByRole('button', { name: /connect 3d filament profiles/i })).toBeInTheDocument();
  });

  it('drops back to Connect when 3DFP no longer accepts the token', async () => {
    let connected = true;
    server.use(
      http.get('/api/v1/threedfp/status', () => HttpResponse.json({ connected, email: 'maker@example.com' })),
      http.get('/api/v1/threedfp/account', () => {
        connected = false;
        return HttpResponse.json({ detail: 'no longer accepted' }, { status: 409 });
      }),
    );
    render(<ThreeDfpSettings />);

    expect(await screen.findByRole('button', { name: /connect 3d filament profiles/i })).toBeInTheDocument();
  });
});
