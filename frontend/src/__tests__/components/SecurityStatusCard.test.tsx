/**
 * Tests for SecurityStatusCard — verifies the five severity levels
 * (green / yellow / orange / red / grey) are rendered for the right
 * combinations of key_source, legacy_plaintext_rows, and decryption_broken.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { render } from '../utils';
import { SecurityStatusCard } from '../../components/SecurityStatusCard';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import type { EncryptionStatus } from '../../api/client';

const STATUS_URL = '/api/v1/auth/encryption-status';

function makeStatus(overrides: Partial<EncryptionStatus> = {}): EncryptionStatus {
  return {
    key_configured: true,
    key_source: 'env',
    legacy_plaintext_rows: { oidc_providers: 0, user_totp: 0 },
    encrypted_rows: { oidc_providers: 0, user_totp: 0 },
    decryption_broken: false,
    ...overrides,
  };
}

describe('SecurityStatusCard', () => {
  beforeEach(() => {
    server.use(http.get(STATUS_URL, () => HttpResponse.json(makeStatus())));
  });

  // E2: loading state
  it('shows loading indicator while query is pending', () => {
    // Delay the response so the component renders in loading state first.
    server.use(http.get(STATUS_URL, async () => {
      await new Promise(() => { /* never resolves — keeps loading state */ });
      return HttpResponse.json(makeStatus());
    }));
    render(<SecurityStatusCard />);
    expect(screen.getByTestId('encryption-loading')).toBeInTheDocument();
  });

  // E2: error state
  it('shows error state when API returns 500', async () => {
    server.use(http.get(STATUS_URL, () => new HttpResponse(null, { status: 500 })));
    render(<SecurityStatusCard />);
    await waitFor(() => {
      expect(screen.getByTestId('encryption-error')).toBeInTheDocument();
    });
  });

  // E1: data-testid on status div
  it('renders encryption-status testid after data loads', async () => {
    render(<SecurityStatusCard />);
    await waitFor(() => {
      expect(screen.getByTestId('encryption-status')).toBeInTheDocument();
    });
  });

  it('renders enabled state with env source', async () => {
    server.use(
      http.get(STATUS_URL, () =>
        HttpResponse.json(makeStatus({ key_source: 'env', encrypted_rows: { oidc_providers: 2, user_totp: 5 } })),
      ),
    );
    render(<SecurityStatusCard />);
    await waitFor(() => {
      expect(screen.getByTestId('encryption-status')).toBeInTheDocument();
    });
    expect(screen.getByText(/MFA_ENCRYPTION_KEY environment variable/i)).toBeInTheDocument();
  });

  it('renders enabled state with file source', async () => {
    server.use(
      http.get(STATUS_URL, () => HttpResponse.json(makeStatus({ key_source: 'file' }))),
    );
    render(<SecurityStatusCard />);
    await waitFor(() => {
      expect(screen.getByTestId('encryption-status')).toBeInTheDocument();
    });
    expect(screen.getByText(/key loaded from data directory/i)).toBeInTheDocument();
  });

  it('renders orange backup hint when key_source is generated', async () => {
    server.use(
      http.get(STATUS_URL, () =>
        HttpResponse.json(makeStatus({ key_source: 'generated' })),
      ),
    );
    render(<SecurityStatusCard />);
    await waitFor(() => {
      expect(screen.getByTestId('encryption-status')).toBeInTheDocument();
    });
    expect(screen.getByText(/included in local backup ZIPs/i)).toBeInTheDocument();
    expect(screen.getByText(/DATA_DIR\/\.mfa_encryption_key/i)).toBeInTheDocument();
  });

  // E4: concurrent warnings — generated key + legacy rows
  it('shows backup hint AND legacy-rows warning when key is generated and legacy rows exist', async () => {
    server.use(
      http.get(STATUS_URL, () =>
        HttpResponse.json(
          makeStatus({
            key_source: 'generated',
            legacy_plaintext_rows: { oidc_providers: 2, user_totp: 0 },
          }),
        ),
      ),
    );
    render(<SecurityStatusCard />);
    await waitFor(() => {
      expect(screen.getByTestId('encryption-status')).toBeInTheDocument();
    });
    // Primary status: backup hint
    expect(screen.getByText(/included in local backup ZIPs/i)).toBeInTheDocument();
    // Secondary: legacy-rows warning
    expect(screen.getByTestId('encryption-legacy-warning')).toBeInTheDocument();
  });

  it('renders yellow warning when legacy plaintext rows exist', async () => {
    server.use(
      http.get(STATUS_URL, () =>
        HttpResponse.json(
          makeStatus({
            key_source: 'env',
            legacy_plaintext_rows: { oidc_providers: 3, user_totp: 0 },
          }),
        ),
      ),
    );
    render(<SecurityStatusCard />);
    await waitFor(() => {
      expect(screen.getByText(/3 legacy plaintext row/i)).toBeInTheDocument();
    });
  });

  it('renders red decryption-broken state when key missing but encrypted rows exist', async () => {
    server.use(
      http.get(STATUS_URL, () =>
        HttpResponse.json(
          makeStatus({
            key_configured: false,
            key_source: 'none',
            encrypted_rows: { oidc_providers: 2, user_totp: 1 },
            decryption_broken: true,
          }),
        ),
      ),
    );
    render(<SecurityStatusCard />);
    await waitFor(() => {
      expect(screen.getByText(/Encryption key missing/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/3 encrypted record/i)).toBeInTheDocument();
  });

  it('renders disabled (not configured) state', async () => {
    server.use(
      http.get(STATUS_URL, () =>
        HttpResponse.json(makeStatus({ key_configured: false, key_source: 'none' })),
      ),
    );
    render(<SecurityStatusCard />);
    await waitFor(() => {
      expect(screen.getByText(/At-rest encryption not configured/i)).toBeInTheDocument();
    });
  });
});
