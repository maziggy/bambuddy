/**
 * Tests for the GroupsPage component.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { waitFor } from '@testing-library/react';
import { render } from '../utils';
import { GroupsPage } from '../../pages/GroupsPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const mockGroups = [
  {
    id: 1,
    name: 'Administrators',
    description: 'Full access to all features',
    permissions: ['printers:read', 'printers:control', 'settings:read', 'settings:update', 'users:read', 'users:create'],
    is_system: true,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  },
  {
    id: 2,
    name: 'Operators',
    description: 'Control printers and manage content',
    permissions: ['printers:read', 'printers:control', 'archives:read', 'queue:read', 'queue:create'],
    is_system: true,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  },
  {
    id: 3,
    name: 'Viewers',
    description: 'Read-only access',
    permissions: ['printers:read', 'archives:read', 'queue:read'],
    is_system: true,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  },
];

const mockPermissions = {
  'Printers': ['printers:read', 'printers:create', 'printers:update', 'printers:delete', 'printers:control'],
  'Archives': ['archives:read', 'archives:create', 'archives:update', 'archives:delete'],
  'Queue': ['queue:read', 'queue:create', 'queue:update', 'queue:delete'],
  'Settings': ['settings:read', 'settings:update'],
  'Users': ['users:read', 'users:create', 'users:update', 'users:delete'],
};

describe('GroupsPage', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/groups/', () => {
        return HttpResponse.json(mockGroups);
      }),
      http.get('/api/v1/groups/permissions', () => {
        return HttpResponse.json(mockPermissions);
      }),
      http.get('/api/v1/auth/status', () => {
        return HttpResponse.json({
          auth_enabled: false,
          requires_setup: false,
        });
      }),
      http.get('/api/v1/users/', () => {
        return HttpResponse.json([]);
      })
    );
  });

  describe('rendering', () => {
    it('renders the page', async () => {
      render(<GroupsPage />);

      // Page should render without errors
      await waitFor(() => {
        expect(document.body).toBeInTheDocument();
      });
    });

    it('renders group names from API', async () => {
      render(<GroupsPage />);

      await waitFor(() => {
        // Check that the groups are rendered
        expect(document.body.textContent).toContain('Administrators');
        expect(document.body.textContent).toContain('Operators');
        expect(document.body.textContent).toContain('Viewers');
      });
    });

    it('shows group descriptions', async () => {
      render(<GroupsPage />);

      await waitFor(() => {
        expect(document.body.textContent).toContain('Full access to all features');
      });
    });
  });

  describe('API integration', () => {
    it('fetches groups on mount', async () => {
      let groupsFetched = false;

      server.use(
        http.get('/api/v1/groups/', () => {
          groupsFetched = true;
          return HttpResponse.json(mockGroups);
        })
      );

      render(<GroupsPage />);

      await waitFor(() => {
        expect(groupsFetched).toBe(true);
      });
    });

    it('fetches permissions on mount', async () => {
      let permissionsFetched = false;

      server.use(
        http.get('/api/v1/groups/permissions', () => {
          permissionsFetched = true;
          return HttpResponse.json(mockPermissions);
        })
      );

      render(<GroupsPage />);

      await waitFor(() => {
        expect(permissionsFetched).toBe(true);
      });
    });
  });
});
