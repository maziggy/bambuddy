/**
 * Tests for folder deletion permission gating in the File Manager tree (#1781).
 *
 * Users with only library:delete_own may delete empty, unlinked, non-external
 * folders; everything else stays behind library:delete_all.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { FileManagerPage } from '../../pages/FileManagerPage';
import { setAuthToken } from '../../api/client';

const mockFolders = [
  {
    id: 1,
    name: 'EmptyOne',
    parent_id: null,
    file_count: 0,
    project_id: null,
    archive_id: null,
    project_name: null,
    archive_name: null,
    is_external: false,
    children: [],
  },
  {
    id: 2,
    name: 'HasFiles',
    parent_id: null,
    file_count: 3,
    project_id: null,
    archive_id: null,
    project_name: null,
    archive_name: null,
    is_external: false,
    children: [],
  },
  {
    id: 3,
    name: 'LinkedEmpty',
    parent_id: null,
    file_count: 0,
    project_id: 1,
    archive_id: null,
    project_name: 'My Project',
    archive_name: null,
    is_external: false,
    children: [],
  },
];

function mockAuthUser(permissions: string[]) {
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
        permissions,
      }),
    ),
  );
}

async function openFolderMenu(user: ReturnType<typeof userEvent.setup>, folderName: string) {
  // Walk up to the row itself rather than assuming the name is its direct
  // child — the name sits in a wrapper that also holds the optional
  // last-activity line (#2680).
  const row = screen.getByText(folderName).closest('div.group')!;
  const buttons = within(row).getAllByRole('button');
  // The kebab (MoreVertical) menu toggle is the last button in the row
  await user.click(buttons[buttons.length - 1]);
  return row;
}

describe('FileManager folder deletion gating (#1781)', () => {
  beforeEach(() => {
    localStorage.clear();
    server.use(
      http.get('/api/v1/library/folders', () => HttpResponse.json(mockFolders)),
      http.get('/api/v1/library/files', () => HttpResponse.json([])),
      http.get('/api/v1/library/stats', () =>
        HttpResponse.json({
          total_files: 3,
          total_folders: 3,
          total_size_bytes: 1024,
          disk_free_bytes: 10737418240,
          disk_total_bytes: 107374182400,
        }),
      ),
      http.get('/api/v1/projects/', () => HttpResponse.json([{ id: 1, name: 'My Project', color: '#00ae42' }])),
      http.get('/api/v1/archives/', () => HttpResponse.json([])),
    );
  });

  afterEach(() => {
    setAuthToken(null);
  });

  it('enables delete on an empty folder for a delete_own user', async () => {
    mockAuthUser(['library:read_own', 'library:delete_own']);
    render(<FileManagerPage />);
    await waitFor(() => expect(screen.getByText('EmptyOne')).toBeInTheDocument());

    const user = userEvent.setup();
    const row = await openFolderMenu(user, 'EmptyOne');
    const deleteButton = within(row).getByRole('button', { name: 'Delete' });
    expect(deleteButton).not.toBeDisabled();
  });

  it('disables delete on a non-empty folder for a delete_own user, with empty-only tooltip', async () => {
    mockAuthUser(['library:read_own', 'library:delete_own']);
    render(<FileManagerPage />);
    await waitFor(() => expect(screen.getByText('HasFiles')).toBeInTheDocument());

    const user = userEvent.setup();
    const row = await openFolderMenu(user, 'HasFiles');
    const deleteButton = within(row).getByRole('button', { name: 'Delete' });
    expect(deleteButton).toBeDisabled();
    expect(deleteButton).toHaveAttribute('title', 'You can only delete empty folders');
  });

  it('disables delete on a linked folder for a delete_own user, with no-permission tooltip', async () => {
    mockAuthUser(['library:read_own', 'library:delete_own']);
    render(<FileManagerPage />);
    await waitFor(() => expect(screen.getByText('LinkedEmpty')).toBeInTheDocument());

    const user = userEvent.setup();
    const row = await openFolderMenu(user, 'LinkedEmpty');
    const deleteButton = within(row).getByRole('button', { name: 'Delete' });
    expect(deleteButton).toBeDisabled();
    expect(deleteButton).toHaveAttribute('title', 'You do not have permission to delete folders');
  });

  it('disables delete entirely for a user without any delete permission', async () => {
    mockAuthUser(['library:read_own']);
    render(<FileManagerPage />);
    await waitFor(() => expect(screen.getByText('EmptyOne')).toBeInTheDocument());

    const user = userEvent.setup();
    const row = await openFolderMenu(user, 'EmptyOne');
    const deleteButton = within(row).getByRole('button', { name: 'Delete' });
    expect(deleteButton).toBeDisabled();
    expect(deleteButton).toHaveAttribute('title', 'You do not have permission to delete folders');
  });

  it('keeps delete enabled on non-empty folders for a delete_all user', async () => {
    mockAuthUser(['library:read_all', 'library:delete_all']);
    render(<FileManagerPage />);
    await waitFor(() => expect(screen.getByText('HasFiles')).toBeInTheDocument());

    const user = userEvent.setup();
    const row = await openFolderMenu(user, 'HasFiles');
    const deleteButton = within(row).getByRole('button', { name: 'Delete' });
    expect(deleteButton).not.toBeDisabled();
  });
});
