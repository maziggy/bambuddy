/**
 * Tests for the NotificationsPage component.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { NotificationsPage } from '../../pages/NotificationsPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const mockPreferences = {
  notify_print_start: true,
  notify_print_complete: true,
  notify_print_failed: true,
  notify_print_stopped: true,
};

const mockAdvancedAuthEnabled = {
  advanced_auth_enabled: true,
  smtp_configured: true,
};

const mockSettingsWithNotifications = {
  auto_archive: true,
  user_notifications_enabled: true,
};

describe('NotificationsPage', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/auth/advanced-auth/status', () => {
        return HttpResponse.json(mockAdvancedAuthEnabled);
      }),
      http.get('/api/v1/user-notifications/preferences', () => {
        return HttpResponse.json(mockPreferences);
      }),
      http.put('/api/v1/user-notifications/preferences', async ({ request }) => {
        const body = await request.json();
        return HttpResponse.json(body);
      }),
      http.get('/api/v1/settings/', () => {
        return HttpResponse.json(mockSettingsWithNotifications);
      }),
      http.get('*/api/v1/auth/status', () => {
        return HttpResponse.json({ auth_enabled: false, requires_setup: false });
      }),
      http.get('/api/v1/auth/me', () => {
        return HttpResponse.json({
          id: 1,
          username: 'testuser',
          email: 'test@example.com',
          role: 'admin',
          is_active: true,
          is_admin: true,
          groups: [{ id: 1, name: 'Administrators' }],
          permissions: [],
          created_at: '2024-01-01T00:00:00Z',
        });
      })
    );
  });

  describe('rendering', () => {
    it('renders the page heading', async () => {
      render(<NotificationsPage />);

      await waitFor(() => {
        expect(screen.getByText('Notifications')).toBeInTheDocument();
      });
    });

    it('renders all four notification toggle options', async () => {
      render(<NotificationsPage />);

      await waitFor(() => {
        expect(screen.getByText('Print Job Starts')).toBeInTheDocument();
        expect(screen.getByText('Print Job Finishes')).toBeInTheDocument();
        expect(screen.getByText('Print Errors')).toBeInTheDocument();
        expect(screen.getByText('Print Job Stops')).toBeInTheDocument();
      });
    });

    it('renders four toggle switches', async () => {
      render(<NotificationsPage />);

      await waitFor(() => {
        const switches = screen.getAllByRole('switch');
        expect(switches).toHaveLength(4);
      });
    });

    it('renders save button', async () => {
      render(<NotificationsPage />);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /save/i })).toBeInTheDocument();
      });
    });

    it('shows loading spinner initially', () => {
      render(<NotificationsPage />);
      expect(document.querySelector('.animate-spin')).toBeInTheDocument();
    });
  });

  describe('toggle interaction', () => {
    it('toggles switch state when clicked', async () => {
      const user = userEvent.setup();
      render(<NotificationsPage />);

      await waitFor(() => {
        expect(screen.getAllByRole('switch')).toHaveLength(4);
      });

      const switches = screen.getAllByRole('switch');
      // All should start checked (matching mock preferences)
      expect(switches[0]).toHaveAttribute('aria-checked', 'true');

      await user.click(switches[0]); // Toggle print start off

      expect(switches[0]).toHaveAttribute('aria-checked', 'false');
    });
  });

  describe('redirect behavior', () => {
    it('does not redirect when advanced auth is enabled and notifications are enabled', async () => {
      render(<NotificationsPage />);

      await waitFor(() => {
        expect(screen.getByText('Notifications')).toBeInTheDocument();
      });
    });
  });
});
