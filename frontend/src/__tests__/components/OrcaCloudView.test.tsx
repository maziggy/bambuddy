/**
 * Tests for OrcaCloudView — the RFC 8628 device-pairing flow: disconnected
 * (Connect button), pairing (code + approval link + waiting), a completed
 * poll flipping to connected, a denied poll surfacing an error, and disconnect.
 */
import { describe, it, expect } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { http, HttpResponse } from 'msw';

import { server } from '../mocks/server';
import { render } from '../utils';
import { OrcaCloudView } from '../../components/OrcaCloudView';

const DEVICE_START = {
  user_code: 'ABCD-EF12',
  verification_uri: 'https://cloud.orcaslicer.com/app/settings',
  verification_uri_complete: 'https://cloud.orcaslicer.com/app/settings?user_code=ABCD-EF12',
  interval: 5,
  expires_in: 600,
};

const NO_PROFILES = { filament: [], printer: [], process: [] };

const disconnectedStatus = () =>
  http.get('/api/v1/orca-cloud/status', () =>
    HttpResponse.json({ connected: false, email: null, user_id: null }),
  );

describe('OrcaCloudView', () => {
  it('shows the Connect button when not connected', async () => {
    server.use(disconnectedStatus());
    render(<OrcaCloudView />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Connect Orca Cloud/i })).toBeInTheDocument();
    });
  });

  it('shows the pairing code and approval link after clicking Connect', async () => {
    server.use(
      disconnectedStatus(),
      http.post('/api/v1/orca-cloud/device/start', () => HttpResponse.json(DEVICE_START)),
      http.post('/api/v1/orca-cloud/device/poll', () =>
        HttpResponse.json({ status: 'authorization_pending', connected: false, email: null, user_id: null }),
      ),
    );
    render(<OrcaCloudView />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Connect Orca Cloud/i })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: /Connect Orca Cloud/i }));

    await waitFor(() => {
      expect(screen.getByText('ABCD-EF12')).toBeInTheDocument();
    });
    expect(screen.getByText(/Waiting for you to approve/i)).toBeInTheDocument();
    // The approval link points at the verification_uri_complete.
    expect(screen.getByRole('link', { name: /Open Orca Cloud approval page/i })).toHaveAttribute(
      'href',
      DEVICE_START.verification_uri_complete,
    );
  });

  it('flips to connected once a poll returns complete', async () => {
    let connected = false;
    server.use(
      http.get('/api/v1/orca-cloud/status', () =>
        HttpResponse.json(
          connected
            ? { connected: true, email: null, user_id: 'user-123' }
            : { connected: false, email: null, user_id: null },
        ),
      ),
      http.post('/api/v1/orca-cloud/device/start', () => HttpResponse.json(DEVICE_START)),
      http.post('/api/v1/orca-cloud/device/poll', () => {
        connected = true;
        return HttpResponse.json({ status: 'complete', connected: true, email: null, user_id: 'user-123' });
      }),
      http.get('/api/v1/orca-cloud/profiles', () => HttpResponse.json(NO_PROFILES)),
    );
    render(<OrcaCloudView />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Connect Orca Cloud/i })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: /Connect Orca Cloud/i }));

    // The immediate first poll returns complete → status invalidates and the
    // connected view (with the Disconnect control) appears. We key off the
    // Disconnect button rather than the banner text, since the success toast
    // also renders "Connected to Orca Cloud".
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Disconnect/i })).toBeInTheDocument();
    });
  });

  it('surfaces an error and returns to Connect when the pairing is denied', async () => {
    server.use(
      disconnectedStatus(),
      http.post('/api/v1/orca-cloud/device/start', () => HttpResponse.json(DEVICE_START)),
      http.post('/api/v1/orca-cloud/device/poll', () =>
        HttpResponse.json({ status: 'access_denied', connected: false, email: null, user_id: null }),
      ),
    );
    render(<OrcaCloudView />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Connect Orca Cloud/i })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: /Connect Orca Cloud/i }));

    await waitFor(() => {
      expect(screen.getByText(/denied/i)).toBeInTheDocument();
    });
    // Back on the Connect card, not stuck on the waiting screen.
    expect(screen.getByRole('button', { name: /Connect Orca Cloud/i })).toBeInTheDocument();
    expect(screen.queryByText(/Waiting for you to approve/i)).not.toBeInTheDocument();
  });

  it('clears the connection on Disconnect', async () => {
    let connected = true;
    server.use(
      http.get('/api/v1/orca-cloud/status', () =>
        HttpResponse.json(
          connected
            ? { connected: true, email: null, user_id: 'user-123' }
            : { connected: false, email: null, user_id: null },
        ),
      ),
      http.get('/api/v1/orca-cloud/profiles', () => HttpResponse.json(NO_PROFILES)),
      http.post('/api/v1/orca-cloud/logout', () => {
        connected = false;
        return HttpResponse.json({ success: true });
      }),
    );
    render(<OrcaCloudView />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Disconnect/i })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: /Disconnect/i }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Connect Orca Cloud/i })).toBeInTheDocument();
    });
  });
});
