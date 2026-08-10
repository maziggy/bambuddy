/**
 * The Stats filter-by-user dropdown sources names from the slim listing (#1894).
 *
 * `stats:filter_by_user` is a permission an operator can be granted on its own,
 * but the dropdown used to be populated from the admin-level `users:read`
 * listing. An operator who had been granted the filter therefore saw an empty
 * control -- the filter renders only when the user list is non-empty -- and had
 * no way to tell whether that meant "no users" or "not allowed to look".
 */

import { describe, it, expect, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { StatsPage } from '../../pages/StatsPage';
import { setAuthToken } from '../../api/client';

function signInAs(permissions: string[]) {
  setAuthToken('test-token', 'session');
  server.use(
    http.get('*/api/v1/auth/status', () =>
      HttpResponse.json({ auth_enabled: true, requires_setup: false }),
    ),
    http.get('*/api/v1/auth/me', () =>
      HttpResponse.json({ id: 1, username: 'operator', is_admin: false, permissions }),
    ),
  );
}

afterEach(() => {
  setAuthToken(null);
});

describe('stats filter-by-user (#1894)', () => {
  it('populates from /users/slim without the admin-level users:read', async () => {
    signInAs(['stats:read', 'stats:filter_by_user']);
    server.use(
      // The admin listing is exactly what such an operator cannot call.
      http.get('*/api/v1/users/', () => new HttpResponse(null, { status: 403 })),
      http.get('*/api/v1/users/slim', () =>
        HttpResponse.json([
          { id: 1, username: 'operator' },
          { id: 2, username: 'colleague' },
        ]),
      ),
    );

    render(<StatsPage />);

    // The control only renders once names have arrived, so its presence is
    // the assertion -- an empty list leaves it out of the tree entirely.
    await waitFor(() => {
      expect(screen.getByText('All Users')).toBeInTheDocument();
    });
  });

  it('stays hidden when the user has no filter permission', async () => {
    signInAs(['stats:read']);
    server.use(
      http.get('*/api/v1/users/slim', () =>
        HttpResponse.json([{ id: 1, username: 'operator' }]),
      ),
    );

    render(<StatsPage />);

    await waitFor(() => {
      expect(screen.getByText('Quick Stats')).toBeInTheDocument();
    });
    expect(screen.queryByText('All Users')).not.toBeInTheDocument();
  });
});
