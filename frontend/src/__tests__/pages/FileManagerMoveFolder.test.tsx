/**
 * File Manager: move a folder to root or into another folder (#3305).
 *
 * The kebab gets a "Move folder…" entry (not for external folders, disabled
 * without the right to change the folder). The picker offers Root and every
 * folder except the one being moved, its subtree and external folders, and
 * the move goes through PUT /library/folders/{id} with parent_id (0 = root).
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { FileManagerPage } from '../../pages/FileManagerPage';
import { setAuthToken } from '../../api/client';
import { FOLDER_DRAG_TYPE } from '../../utils/libraryFolders';

function folder(overrides: Record<string, unknown>) {
  return {
    parent_id: null,
    file_count: 0,
    project_id: null,
    archive_id: null,
    project_name: null,
    archive_name: null,
    is_external: false,
    external_path: null,
    external_readonly: false,
    latest_activity_at: null,
    created_by_id: 1,
    shared: false,
    can_write: true,
    can_rename: true,
    can_delete: true,
    children: [],
    ...overrides,
  };
}

// Parent
// └── Child
//     └── Grandchild
// Other
// NAS (external)
const folders = [
  folder({
    id: 1,
    name: 'Parent',
    children: [
      folder({
        id: 2,
        name: 'Child',
        parent_id: 1,
        children: [folder({ id: 3, name: 'Grandchild', parent_id: 2 })],
      }),
    ],
  }),
  folder({ id: 4, name: 'Other' }),
  folder({ id: 9, name: 'NAS', is_external: true, external_path: '/mnt/nas' }),
];

function mockAuthUser(permissions: string[]) {
  setAuthToken('test-token', 'session');
  server.use(
    http.get('*/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: true, requires_setup: false })),
    http.get('*/api/v1/auth/me', () =>
      HttpResponse.json({ id: 1, username: 'admin', is_admin: true, permissions }),
    ),
  );
}

async function openFolderMenu(user: ReturnType<typeof userEvent.setup>, folderName: string) {
  const row = screen.getByText(folderName).closest('div.group')!;
  const buttons = within(row).getAllByRole('button');
  await user.click(buttons[buttons.length - 1]);
  return row;
}

describe('FileManager move folder (#3305)', () => {
  let putBodies: { id: number; body: unknown }[] = [];

  beforeEach(() => {
    localStorage.clear();
    // The tree is expanded by default, so Child and Grandchild are rendered.
    putBodies = [];
    server.use(
      http.get('/api/v1/library/folders', () => HttpResponse.json(folders)),
      http.get('/api/v1/library/files', () => HttpResponse.json([])),
      http.get('/api/v1/library/stats', () =>
        HttpResponse.json({
          total_files: 0,
          total_folders: 5,
          total_size_bytes: 0,
          disk_free_bytes: 10737418240,
          disk_total_bytes: 107374182400,
        }),
      ),
      http.put('/api/v1/library/folders/:id', async ({ request, params }) => {
        putBodies.push({ id: Number(params.id), body: await request.json() });
        return HttpResponse.json(folder({ id: Number(params.id), name: 'x', created_at: '', updated_at: '' }));
      }),
    );
  });

  afterEach(() => {
    setAuthToken(null);
  });

  it('moves a nested folder to the root with parent_id 0', async () => {
    mockAuthUser(['library:read_all', 'library:update_all']);
    render(<FileManagerPage />);
    await waitFor(() => expect(screen.getByText('Child')).toBeInTheDocument());

    const user = userEvent.setup();
    const row = await openFolderMenu(user, 'Child');
    await user.click(within(row).getByRole('button', { name: 'Move folder…' }));

    const dialog = screen.getByText('Move "Child" to').closest('.fixed')!;
    // Root is offered; the folder itself, its subtree and the external folder are not.
    expect(within(dialog).getByRole('button', { name: 'Root' })).not.toBeDisabled();
    expect(within(dialog).getByRole('button', { name: /^Child$/ })).toBeDisabled();
    expect(within(dialog).getByRole('button', { name: /^Grandchild$/ })).toBeDisabled();
    expect(within(dialog).getByRole('button', { name: /^NAS$/ })).toBeDisabled();
    expect(within(dialog).getByRole('button', { name: /^Other$/ })).not.toBeDisabled();
    // Its current parent is marked and cannot be picked again.
    expect(within(dialog).getByRole('button', { name: /^Parent/ })).toBeDisabled();
    expect(within(dialog).getByText('(current)')).toBeInTheDocument();

    // Nothing selected yet: Move stays disabled.
    expect(within(dialog).getByRole('button', { name: 'Move' })).toBeDisabled();
    await user.click(within(dialog).getByRole('button', { name: 'Root' }));
    await user.click(within(dialog).getByRole('button', { name: 'Move' }));

    await waitFor(() => expect(putBodies).toEqual([{ id: 2, body: { parent_id: 0 } }]));
    await waitFor(() => expect(screen.getByText('Folder moved')).toBeInTheDocument());
  });

  it('moves a folder into another folder', async () => {
    mockAuthUser(['library:read_all', 'library:update_all']);
    render(<FileManagerPage />);
    await waitFor(() => expect(screen.getByText('Other')).toBeInTheDocument());

    const user = userEvent.setup();
    const row = await openFolderMenu(user, 'Other');
    await user.click(within(row).getByRole('button', { name: 'Move folder…' }));

    const dialog = screen.getByText('Move "Other" to').closest('.fixed')!;
    await user.click(within(dialog).getByRole('button', { name: /^Child$/ }));
    await user.click(within(dialog).getByRole('button', { name: 'Move' }));

    await waitFor(() => expect(putBodies).toEqual([{ id: 4, body: { parent_id: 2 } }]));
  });

  it('does not offer to move an external folder', async () => {
    mockAuthUser(['library:read_all', 'library:update_all']);
    render(<FileManagerPage />);
    await waitFor(() => expect(screen.getByText('NAS')).toBeInTheDocument());

    const user = userEvent.setup();
    const row = await openFolderMenu(user, 'NAS');
    expect(within(row).getByRole('button', { name: 'Rename' })).toBeInTheDocument();
    expect(within(row).queryByRole('button', { name: 'Move folder…' })).not.toBeInTheDocument();
  });

  it('disables the action for a user who may not change the folder', async () => {
    mockAuthUser(['library:read_all']);
    server.use(
      http.get('/api/v1/library/folders', () =>
        HttpResponse.json(folders.map((f) => ({ ...f, can_rename: false, children: [] }))),
      ),
    );
    render(<FileManagerPage />);
    await waitFor(() => expect(screen.getByText('Other')).toBeInTheDocument());

    const user = userEvent.setup();
    const row = await openFolderMenu(user, 'Other');
    const move = within(row).getByRole('button', { name: 'Move folder…' });
    expect(move).toBeDisabled();
    expect(move).toHaveAttribute('title', 'You do not have permission to move folders');
  });

  // jsdom has no DataTransfer; this is the slice of it the handlers read.
  function fakeDataTransfer() {
    const data: Record<string, string> = {};
    return {
      types: [] as string[],
      files: [] as File[],
      effectAllowed: 'uninitialized',
      dropEffect: 'none',
      setData(type: string, value: string) {
        data[type] = value;
        this.types = Object.keys(data);
      },
      getData(type: string) {
        return data[type] ?? '';
      },
    };
  }

  function treeRow(name: string) {
    return screen.getByText(name).closest('div.group')!;
  }

  it('moves a dragged folder onto the folder it is dropped on', async () => {
    mockAuthUser(['library:read_all', 'library:update_all']);
    render(<FileManagerPage />);
    await waitFor(() => expect(screen.getByText('Child')).toBeInTheDocument());

    const dataTransfer = fakeDataTransfer();
    const source = treeRow('Child');
    expect(source).toHaveAttribute('draggable', 'true');
    fireEvent.dragStart(source, { dataTransfer });
    expect(dataTransfer.getData(FOLDER_DRAG_TYPE)).toBe('2');

    const target = treeRow('Other');
    fireEvent.dragOver(target, { dataTransfer });
    fireEvent.drop(target, { dataTransfer });

    await waitFor(() => expect(putBodies).toEqual([{ id: 2, body: { parent_id: 4 } }]));
  });

  it('moves a dragged folder to the root when dropped on All Files', async () => {
    mockAuthUser(['library:read_all', 'library:update_all']);
    render(<FileManagerPage />);
    await waitFor(() => expect(screen.getByText('Grandchild')).toBeInTheDocument());

    const dataTransfer = fakeDataTransfer();
    fireEvent.dragStart(treeRow('Grandchild'), { dataTransfer });
    const allFiles = screen.getByText('All Files', { selector: 'span' }).parentElement!;
    fireEvent.dragOver(allFiles, { dataTransfer });
    fireEvent.drop(allFiles, { dataTransfer });

    await waitFor(() => expect(putBodies).toEqual([{ id: 3, body: { parent_id: 0 } }]));
  });

  it('ignores a drop onto the folder itself, a descendant, its parent or an external folder', async () => {
    mockAuthUser(['library:read_all', 'library:update_all']);
    render(<FileManagerPage />);
    await waitFor(() => expect(screen.getByText('Grandchild')).toBeInTheDocument());

    const dataTransfer = fakeDataTransfer();
    fireEvent.dragStart(treeRow('Child'), { dataTransfer });
    for (const name of ['Child', 'Grandchild', 'Parent', 'NAS']) {
      const target = treeRow(name);
      fireEvent.dragOver(target, { dataTransfer });
      fireEvent.drop(target, { dataTransfer });
    }
    // Give a stray request time to show up before asserting none did.
    await new Promise((r) => setTimeout(r, 50));
    expect(putBodies).toEqual([]);
  });

  it('does not let an external folder be dragged', async () => {
    mockAuthUser(['library:read_all', 'library:update_all']);
    render(<FileManagerPage />);
    await waitFor(() => expect(screen.getByText('NAS')).toBeInTheDocument());
    expect(treeRow('NAS')).not.toHaveAttribute('draggable');
  });
});
