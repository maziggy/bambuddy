import { afterEach, describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setAuthToken } from '../../api/client';
import { server } from '../mocks/server';
import { UsersPage } from '../../pages/UsersPage';
import { render } from '../utils';

const administrator = {
  id: 1,
  username: 'alice',
  role: 'user',
  is_active: true,
  is_admin: true,
  auth_source: 'local',
  groups: [{ id: 1, name: 'Administrators' }],
  permissions: ['users:read', 'groups:read'],
  created_at: '2026-01-01T00:00:00Z',
};

describe('UsersPage', () => {
  afterEach(() => setAuthToken(null));

  it('shows the canonical Administrators group once without an Admin or No groups fallback', async () => {
    setAuthToken('test-token');
    server.use(
      http.get('/api/v1/auth/status', () =>
        HttpResponse.json({ auth_enabled: true, requires_setup: false }),
      ),
      http.get('/api/v1/auth/me', () => HttpResponse.json(administrator)),
      http.get('/api/v1/users/', () => HttpResponse.json([administrator])),
    );

    render(<UsersPage />);

    await screen.findByText('alice');
    expect(screen.getAllByText('Administrators')).toHaveLength(1);
    expect(screen.queryByText('Admin', { exact: true })).not.toBeInTheDocument();
    expect(screen.queryByText('No groups')).not.toBeInTheDocument();
  });
});
