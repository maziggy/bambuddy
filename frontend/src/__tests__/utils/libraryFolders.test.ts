/**
 * Which folders may a folder be moved into (#3305)?
 *
 * The picker and the drop targets both lean on these: the folder itself and
 * its subtree are out (the server would 400 them anyway), external folders are
 * out (they mirror a path on disk), and the parent it already has is a no-op.
 */

import { describe, it, expect } from 'vitest';
import type { LibraryFolderTree } from '../../api/client';
import { isValidFolderMove, isWritableFolder, moveDisallowedFolderIds } from '../../utils/libraryFolders';

function folder(id: number, overrides: Partial<LibraryFolderTree> = {}): LibraryFolderTree {
  return {
    id,
    name: `Folder ${id}`,
    parent_id: null,
    project_id: null,
    archive_id: null,
    project_name: null,
    archive_name: null,
    is_external: false,
    external_path: null,
    external_readonly: false,
    file_count: 0,
    latest_activity_at: null,
    created_by_id: null,
    shared: false,
    can_write: true,
    can_rename: true,
    can_delete: true,
    children: [],
    ...overrides,
  };
}

// 1
// ├── 2
// │   └── 3
// └── 4
// 5
// 9 (external)
// └── 10
const tree: LibraryFolderTree[] = [
  folder(1, {
    children: [
      folder(2, { parent_id: 1, children: [folder(3, { parent_id: 2 })] }),
      folder(4, { parent_id: 1 }),
    ],
  }),
  folder(5),
  folder(9, {
    is_external: true,
    external_path: '/mnt/nas',
    children: [folder(10, { parent_id: 9, is_external: true, external_path: '/mnt/nas/sub' })],
  }),
];

describe('moveDisallowedFolderIds', () => {
  it('excludes the folder itself, its whole subtree and every external folder', () => {
    expect([...moveDisallowedFolderIds(tree, 1)].sort()).toEqual([1, 10, 2, 3, 4, 9].sort());
  });

  it('leaves ancestors and siblings available', () => {
    const disallowed = moveDisallowedFolderIds(tree, 3);
    expect(disallowed.has(3)).toBe(true);
    expect(disallowed.has(2)).toBe(false);
    expect(disallowed.has(1)).toBe(false);
    expect(disallowed.has(4)).toBe(false);
    expect(disallowed.has(5)).toBe(false);
  });

  it('still rules out external folders for an id that is not in the tree', () => {
    expect([...moveDisallowedFolderIds(tree, 999)].sort()).toEqual([10, 9].sort());
  });
});

describe('isValidFolderMove', () => {
  const two = tree[0].children[0];
  const five = tree[1];

  it('allows a nested folder to go to the root', () => {
    expect(isValidFolderMove(tree, two, null)).toBe(true);
  });

  it('treats the current parent as a no-op', () => {
    expect(isValidFolderMove(tree, two, 1)).toBe(false);
    expect(isValidFolderMove(tree, five, null)).toBe(false);
  });

  it('refuses itself, a descendant and an external destination', () => {
    expect(isValidFolderMove(tree, two, 2)).toBe(false);
    expect(isValidFolderMove(tree, two, 3)).toBe(false);
    expect(isValidFolderMove(tree, two, 9)).toBe(false);
    expect(isValidFolderMove(tree, two, 10)).toBe(false);
  });

  it('allows a sibling and an unrelated folder', () => {
    expect(isValidFolderMove(tree, two, 4)).toBe(true);
    expect(isValidFolderMove(tree, two, 5)).toBe(true);
  });

  it('never moves an external folder', () => {
    expect(isValidFolderMove(tree, tree[2], 5)).toBe(false);
    expect(isValidFolderMove(tree, tree[2].children[0], null)).toBe(false);
  });

  it('refuses a destination the user may not write to, and one that is not in the tree', () => {
    const readOnly = [...tree, folder(6, { can_write: false })];
    expect(isValidFolderMove(readOnly, two, 6)).toBe(false);
    expect(isValidFolderMove(tree, two, 999)).toBe(false);
  });
});

describe('isWritableFolder', () => {
  it('follows the can_write flag the server sends', () => {
    expect(isWritableFolder({ can_write: true })).toBe(true);
    expect(isWritableFolder({ can_write: false })).toBe(false);
  });
});
