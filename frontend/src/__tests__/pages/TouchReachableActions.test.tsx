/**
 * Card and row actions must stay reachable without a hover-capable pointer (#2865).
 *
 * Tailwind v4 compiles `group-hover:` inside `@media (hover: hover)`, so on a
 * touch-only device the reveal rule is never applied and a control written as
 * `opacity-0 group-hover:opacity-100` is invisible for good — which is how the
 * project card's action menu and the File Manager's folder actions became
 * unusable on a phone. The fix moves the HIDING half behind a `can-hover`
 * variant, so with no such pointer the control simply keeps its own opacity.
 *
 * jsdom does not evaluate media queries, so these tests pin the class contract
 * rather than the computed style: a bare `opacity-0` is the defect, because it
 * applies unconditionally while everything that undoes it does not.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { ProjectsPage } from '../../pages/ProjectsPage';
import { FileManagerPage } from '../../pages/FileManagerPage';
import { setAuthToken } from '../../api/client';

/** `opacity-0` on its own — with no variant in front of it. */
const UNCONDITIONALLY_HIDDEN = /(^|\s)opacity-0(\s|$)/;

const mockProjects = [
  {
    id: 1,
    name: 'Functional Parts',
    description: 'Useful household items',
    color: '#00ae42',
    archive_count: 10,
    total_print_time_seconds: 36000,
    total_filament_grams: 500,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-15T00:00:00Z',
  },
];

const mockFolders = [
  {
    id: 1,
    name: 'Brackets',
    parent_id: null,
    file_count: 0,
    project_id: null,
    archive_id: null,
    project_name: null,
    archive_name: null,
    is_external: false,
    children: [],
  },
];

describe('actions that were hover-only (#2865)', () => {
  afterEach(() => {
    setAuthToken(null);
  });

  describe('project card', () => {
    beforeEach(() => {
      server.use(http.get('/api/v1/projects/', () => HttpResponse.json(mockProjects)));
    });

    it('does not hide the action menu from a pointer that cannot hover', async () => {
      render(<ProjectsPage />);
      await waitFor(() => expect(screen.getByText('Functional Parts')).toBeInTheDocument());

      const card = screen.getByText('Functional Parts').closest('div.group')!;
      const menuButton = within(card).getAllByRole('button').slice(-1)[0];

      expect(menuButton.className).not.toMatch(UNCONDITIONALLY_HIDDEN);
      expect(menuButton.className).toContain('can-hover:opacity-0');
    });

    it('still opens Edit and Delete once the menu is tapped', async () => {
      render(<ProjectsPage />);
      await waitFor(() => expect(screen.getByText('Functional Parts')).toBeInTheDocument());

      const card = screen.getByText('Functional Parts').closest('div.group')!;
      const user = userEvent.setup();
      await user.click(within(card).getAllByRole('button').slice(-1)[0]);

      expect(within(card).getByRole('button', { name: 'Edit' })).toBeInTheDocument();
      expect(within(card).getByRole('button', { name: 'Delete' })).toBeInTheDocument();
    });
  });

  describe('file manager folder row', () => {
    beforeEach(() => {
      localStorage.clear();
      setAuthToken('test-token', 'session');
      server.use(
        http.get('*/api/v1/auth/status', () =>
          HttpResponse.json({ auth_enabled: true, requires_setup: false }),
        ),
        http.get('*/api/v1/auth/me', () =>
          HttpResponse.json({
            id: 7,
            username: 'operator1',
            is_admin: false,
            permissions: ['library:read_own', 'library:update_all', 'library:delete_all'],
          }),
        ),
        http.get('/api/v1/library/folders', () => HttpResponse.json(mockFolders)),
        http.get('/api/v1/library/files', () => HttpResponse.json([])),
        http.get('/api/v1/library/stats', () =>
          HttpResponse.json({
            total_files: 0,
            total_folders: 1,
            total_size_bytes: 0,
            disk_free_bytes: 10737418240,
            disk_total_bytes: 107374182400,
          }),
        ),
        http.get('/api/v1/projects/', () => HttpResponse.json([])),
        http.get('/api/v1/archives/', () => HttpResponse.json([])),
      );
    });

    it('does not hide the folder actions from a pointer that cannot hover', async () => {
      render(<FileManagerPage />);
      await waitFor(() => expect(screen.getByText('Brackets')).toBeInTheDocument());

      const row = screen.getByText('Brackets').closest('div.group')!;
      // The kebab menu's wrapper is what carries the visibility classes.
      const actions = within(row).getAllByRole('button').slice(-1)[0].closest('div.flex-shrink-0')!;

      expect(actions.className).not.toMatch(UNCONDITIONALLY_HIDDEN);
      expect(actions.className).toContain('can-hover:opacity-0');
    });
  });
});
