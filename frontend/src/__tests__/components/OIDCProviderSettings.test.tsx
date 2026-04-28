/**
 * Tests for OIDCProviderSettings — focused on the auto_link / require_email_verified
 * toggle interaction (SEC-1/SEC-6 UI enforcement).
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { OIDCProviderSettings } from '../../components/OIDCProviderSettings';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const mockProviders = [
  {
    id: 1,
    name: 'TestIdP',
    issuer_url: 'https://idp.example.com',
    client_id: 'test-client',
    scopes: 'openid email profile',
    is_enabled: true,
    auto_create_users: false,
    auto_link_existing_accounts: false,
    email_claim: 'email',
    require_email_verified: true,
    icon_url: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
];

beforeEach(() => {
  server.use(
    http.get('/api/v1/auth/oidc/providers/all', () => HttpResponse.json(mockProviders))
  );
});

describe('OIDCProviderSettings', () => {
  describe('ProviderForm — require_email_verified description logic', () => {
    it('shows standard description when require_email_verified is on and auto_link is off', async () => {
      server.use(http.get('/api/v1/auth/oidc/providers/all', () => HttpResponse.json([])));
      render(<OIDCProviderSettings />);

      await waitFor(() => {
        expect(screen.getAllByRole('button', { name: /Add Provider/i })[0]).toBeInTheDocument();
      });
      await userEvent.click(screen.getAllByRole('button', { name: /Add Provider/i })[0]);

      await waitFor(() => {
        // Default state: require_email_verified=true, auto_link=false → standard description
        expect(
          screen.getByText(/only.*accept.*email.*verified/i)
        ).toBeInTheDocument();
      });
    });

    it('shows "Disable auto-link first" description when auto_link is enabled', async () => {
      server.use(http.get('/api/v1/auth/oidc/providers/all', () => HttpResponse.json([])));
      const user = userEvent.setup();
      render(<OIDCProviderSettings />);

      await waitFor(() => {
        expect(screen.getAllByRole('button', { name: /Add Provider/i })[0]).toBeInTheDocument();
      });
      await user.click(screen.getAllByRole('button', { name: /Add Provider/i })[0]);

      await waitFor(() => {
        expect(screen.getByText(/Auto.*Link/i)).toBeInTheDocument();
      });

      // Find the Auto Link switch by aria-label or by position
      const switches = screen.getAllByRole('switch');
      // Switches order in form: Enabled, AutoCreate, AutoLink, RequireEmailVerified
      // AutoLink is the 3rd switch (index 2)
      const autoLinkSwitch = switches[2];
      await user.click(autoLinkSwitch);

      await waitFor(() => {
        expect(
          screen.getByText(/disable auto.?link first/i)
        ).toBeInTheDocument();
      });
    });

    it('shows warning text when require_email_verified is toggled off', async () => {
      server.use(http.get('/api/v1/auth/oidc/providers/all', () => HttpResponse.json([])));
      const user = userEvent.setup();
      render(<OIDCProviderSettings />);

      await waitFor(() => {
        expect(screen.getAllByRole('button', { name: /Add Provider/i })[0]).toBeInTheDocument();
      });
      await user.click(screen.getAllByRole('button', { name: /Add Provider/i })[0]);

      await waitFor(() => {
        expect(screen.getByText(/Require Email Verified/i)).toBeInTheDocument();
      });

      // RequireEmailVerified is the 4th switch (index 3)
      const switches = screen.getAllByRole('switch');
      const reqEvSwitch = switches[3];
      await user.click(reqEvSwitch);

      await waitFor(() => {
        expect(
          screen.getByText(/warning.*accept.*without.*verif/i)
        ).toBeInTheDocument();
      });
    });

    it('shows security warning when auto_link is enabled with a custom email claim', async () => {
      server.use(http.get('/api/v1/auth/oidc/providers/all', () => HttpResponse.json([])));
      const user = userEvent.setup();
      render(<OIDCProviderSettings />);

      await waitFor(() => {
        expect(screen.getAllByRole('button', { name: /Add Provider/i })[0]).toBeInTheDocument();
      });
      await user.click(screen.getAllByRole('button', { name: /Add Provider/i })[0]);

      await waitFor(() => {
        expect(screen.getByText(/Auto.*Link/i)).toBeInTheDocument();
      });

      // Enable auto_link (switch index 2)
      const autoLinkSwitch = screen.getAllByRole('switch')[2];
      await user.click(autoLinkSwitch);

      // Change email claim to a custom value via fireEvent to bypass the onChange fallback
      const emailClaimInput = screen.getByPlaceholderText('email');
      fireEvent.change(emailClaimInput, { target: { value: 'preferred_username' } });

      await waitFor(() => {
        expect(screen.getByText(/tenant-administered/i)).toBeInTheDocument();
      });
    });
  });

  describe('Provider info view', () => {
    it('renders email_claim and require_email_verified fields in provider details', async () => {
      render(<OIDCProviderSettings />);

      await waitFor(() => {
        expect(screen.getByText('TestIdP')).toBeInTheDocument();
      });

      // The provider card shows field labels in the details section
      expect(screen.getByText(/Email Claim/i)).toBeInTheDocument();
      expect(screen.getByText(/Require Email Verified/i)).toBeInTheDocument();
    });
  });
});
