/**
 * Folding a listing into rows: a variant group is one row (#3628).
 */

import { describe, it, expect } from 'vitest';
import type { LibraryFileListItem } from '../../../api/client';
import { groupLibraryRows, rowFiles, type LibraryRow } from '../../../pages/fileManager/variantGroups';
import { filterAndSortFiles } from '../../../pages/fileManager/filterSort';

let nextId = 1;
function file(over: Partial<LibraryFileListItem> & { filename: string }): LibraryFileListItem {
  const id = over.id ?? nextId++;
  return {
    id,
    folder_id: null,
    is_external: false,
    file_type: 'gcode.3mf',
    file_size: 1000,
    thumbnail_path: null,
    print_count: 0,
    duplicate_count: 0,
    created_by_id: 1,
    created_by_username: 'admin',
    created_at: '2024-01-01T00:00:00Z',
    fs_modified_at: null,
    print_name: null,
    print_time_seconds: null,
    filament_used_grams: null,
    sliced_for_model: null,
    ...over,
  };
}

const filter = { query: '', filterType: 'all', filterUsername: '' };
const byName = { sortField: 'name' as const, sortDirection: 'asc' as const };

const names = (rows: LibraryRow[]) => rows.map((r) => (r.kind === 'group' ? `[${r.name}]` : r.file.filename));

describe('groupLibraryRows', () => {
  it('leaves ungrouped files as file rows, sorted like filterAndSortFiles', () => {
    const files = [
      file({ filename: 'b.stl', file_type: 'stl', file_size: 5 }),
      file({ filename: 'a.stl', file_type: 'stl', file_size: 9 }),
      file({ filename: 'c.3mf', file_type: '3mf', file_size: 1 }),
    ];
    for (const sortField of ['name', 'date', 'size', 'type', 'prints'] as const) {
      for (const sortDirection of ['asc', 'desc'] as const) {
        const opts = { ...filter, sortField, sortDirection };
        const rows = groupLibraryRows(files, filter, { sortField, sortDirection });
        expect(rows.every((r) => r.kind === 'file')).toBe(true);
        expect(rowFiles(rows).map((f) => f.id)).toEqual(filterAndSortFiles(files, opts).map((f) => f.id));
      }
    }
  });

  it('folds the members of a group into one row, ordered by variant_position', () => {
    const files = [
      file({ id: 1, filename: 'vase_p1s.3mf', variant_group_id: 5, variant_position: 1, variant_group_name: 'Vase' }),
      file({ id: 2, filename: 'vase_x1c.3mf', variant_group_id: 5, variant_position: 0, variant_group_name: 'Vase' }),
      file({ id: 3, filename: 'plain.3mf' }),
    ];
    const rows = groupLibraryRows(files, filter, byName);
    expect(names(rows)).toEqual(['plain.3mf', '[Vase]']);
    const group = rows[1];
    if (group.kind !== 'group') throw new Error('expected a group row');
    expect(group.id).toBe(5);
    expect(group.members.map((m) => m.id)).toEqual([2, 1]);
    expect(group.thumbnailFile.id).toBe(2);
  });

  it('names the group after the first member when it has no name', () => {
    const files = [
      file({ id: 1, filename: 'b.3mf', print_name: 'Bracket', variant_group_id: 5, variant_position: 1 }),
      file({ id: 2, filename: 'a.3mf', print_name: 'Alpha', variant_group_id: 5, variant_position: 0 }),
    ];
    const [row] = groupLibraryRows(files, filter, byName);
    expect(row.kind === 'group' && row.name).toBe('Alpha');
  });

  it('shows a group with a single visible member as a plain file', () => {
    const files = [file({ id: 1, filename: 'only.3mf', variant_group_id: 5, variant_position: 0, variant_count: 1 })];
    const rows = groupLibraryRows(files, filter, byName);
    expect(rows).toHaveLength(1);
    expect(rows[0].kind).toBe('file');
  });

  it('keeps a group when one member passes the filter, with all its visible members', () => {
    const files = [
      file({ id: 1, filename: 'vase_x1c.3mf', variant_group_id: 5, variant_position: 0 }),
      file({ id: 2, filename: 'vase_p1s.3mf', variant_group_id: 5, variant_position: 1 }),
      file({ id: 3, filename: 'other.3mf' }),
    ];
    const rows = groupLibraryRows(files, { ...filter, query: 'p1s' }, byName);
    expect(rows).toHaveLength(1);
    expect(rows[0].kind === 'group' && rows[0].members.map((m) => m.id)).toEqual([1, 2]);
  });

  it('drops a group no member of which passes the filter', () => {
    const files = [
      file({ id: 1, filename: 'a.3mf', variant_group_id: 5, variant_position: 0 }),
      file({ id: 2, filename: 'b.3mf', variant_group_id: 5, variant_position: 1 }),
    ];
    expect(groupLibraryRows(files, { ...filter, query: 'zzz' }, byName)).toEqual([]);
    expect(groupLibraryRows(files, { ...filter, filterUsername: 'nobody' }, byName)).toEqual([]);
  });

  it('applies the type filter per member', () => {
    const files = [
      file({ id: 1, filename: 'a.3mf', file_type: '3mf', variant_group_id: 5, variant_position: 0 }),
      file({ id: 2, filename: 'b.gcode', file_type: 'gcode', variant_group_id: 5, variant_position: 1 }),
    ];
    const rows = groupLibraryRows(files, { ...filter, filterType: 'gcode' }, byName);
    expect(rows).toHaveLength(1);
    expect(rows[0].kind === 'group' && rows[0].members).toHaveLength(2);
  });

  it('never shows a member from elsewhere as a row of its own', () => {
    const files = [
      file({ id: 1, filename: 'here.3mf', variant_group_id: 5, variant_position: 0 }),
      file({
        id: 2,
        filename: 'there.3mf',
        folder_id: 9,
        folder_name: 'Archive',
        variant_group_id: 5,
        variant_position: 1,
        variant_elsewhere: true,
      }),
      // The only visible member of its group, and it is elsewhere: no row.
      file({ id: 3, filename: 'lonely.3mf', variant_group_id: 6, variant_position: 0, variant_elsewhere: true }),
    ];
    const rows = groupLibraryRows(files, filter, byName);
    expect(rows).toHaveLength(1);
    expect(rows[0].kind === 'group' && rows[0].members.map((m) => m.id)).toEqual([1, 2]);
  });

  it('does not let an elsewhere member alone keep a group when the filter hides the rest', () => {
    const files = [
      file({ id: 1, filename: 'here.3mf', variant_group_id: 5, variant_position: 0 }),
      file({ id: 2, filename: 'matching.3mf', variant_group_id: 5, variant_position: 1, variant_elsewhere: true }),
    ];
    expect(groupLibraryRows(files, { ...filter, query: 'matching' }, byName)).toEqual([]);
  });

  it('makes a group of a file here and a member elsewhere (two visible members)', () => {
    const files = [
      file({ id: 1, filename: 'here.3mf', variant_group_id: 5, variant_position: 0 }),
      file({ id: 2, filename: 'there.3mf', variant_group_id: 5, variant_position: 1, variant_elsewhere: true }),
    ];
    expect(groupLibraryRows(files, filter, byName)[0].kind).toBe('group');
  });

  describe('sorting', () => {
    const files = [
      file({ id: 1, filename: 'm1.3mf', variant_group_id: 5, variant_position: 0, variant_group_name: 'Mango',
        created_at: '2024-01-01T00:00:00Z', file_size: 10, print_count: 1 }),
      file({ id: 2, filename: 'm2.3mf', variant_group_id: 5, variant_position: 1, variant_group_name: 'Mango',
        created_at: '2024-06-01T00:00:00Z', file_size: 500, print_count: 9 }),
      file({ id: 3, filename: 'apple.3mf', created_at: '2024-03-01T00:00:00Z', file_size: 100, print_count: 4 }),
      file({ id: 4, filename: 'zebra.3mf', created_at: '2024-02-01T00:00:00Z', file_size: 50, print_count: 2 }),
    ];

    it('sorts by group name for the name sort', () => {
      expect(names(groupLibraryRows(files, filter, byName))).toEqual(['apple.3mf', '[Mango]', 'zebra.3mf']);
      expect(names(groupLibraryRows(files, filter, { sortField: 'name', sortDirection: 'desc' }))).toEqual([
        'zebra.3mf',
        '[Mango]',
        'apple.3mf',
      ]);
    });

    it('sorts by the newest member for the date sort', () => {
      // Mango is newest through m2 (June), so it is last ascending.
      expect(names(groupLibraryRows(files, filter, { sortField: 'date', sortDirection: 'asc' }))).toEqual([
        'zebra.3mf',
        'apple.3mf',
        '[Mango]',
      ]);
      const group = groupLibraryRows(files, filter, byName).find((r) => r.kind === 'group');
      expect(group?.kind === 'group' && group.newest.id).toBe(2);
    });

    it('prefers fs_modified_at over created_at for the newest member', () => {
      const withMtime = files.map((f) => (f.id === 1 ? { ...f, fs_modified_at: '2025-01-01T00:00:00Z' } : f));
      const group = groupLibraryRows(withMtime, filter, byName).find((r) => r.kind === 'group');
      expect(group?.kind === 'group' && group.newest.id).toBe(1);
    });

    it('sorts the other fields by the first member', () => {
      // First member of Mango: size 10, prints 1.
      expect(names(groupLibraryRows(files, filter, { sortField: 'size', sortDirection: 'asc' }))).toEqual([
        '[Mango]',
        'zebra.3mf',
        'apple.3mf',
      ]);
      expect(names(groupLibraryRows(files, filter, { sortField: 'prints', sortDirection: 'desc' }))).toEqual([
        'apple.3mf',
        'zebra.3mf',
        '[Mango]',
      ]);
    });

    it('sorts a nameless group by its first member display name', () => {
      const nameless = [
        file({ id: 1, filename: 'z.3mf', print_name: 'Beta', variant_group_id: 5, variant_position: 0 }),
        file({ id: 2, filename: 'y.3mf', variant_group_id: 5, variant_position: 1 }),
        file({ id: 3, filename: 'alpha.3mf' }),
        file({ id: 4, filename: 'gamma.3mf' }),
      ];
      expect(names(groupLibraryRows(nameless, filter, byName))).toEqual(['alpha.3mf', '[Beta]', 'gamma.3mf']);
    });
  });

  it('handles many versions and long names', () => {
    const long = 'A'.repeat(300);
    const files = Array.from({ length: 40 }, (_, i) =>
      file({ id: 100 + i, filename: `v${i}.3mf`, variant_group_id: 9, variant_position: 39 - i, variant_group_name: long }),
    );
    const [row] = groupLibraryRows(files, filter, byName);
    expect(row.kind === 'group' && row.members).toHaveLength(40);
    expect(row.kind === 'group' && row.members[0].id).toBe(139);
    expect(row.kind === 'group' && row.name).toBe(long);
  });

  it('treats null positions as last', () => {
    const files = [
      file({ id: 1, filename: 'a.3mf', variant_group_id: 5, variant_position: null }),
      file({ id: 2, filename: 'b.3mf', variant_group_id: 5, variant_position: 0 }),
    ];
    const [row] = groupLibraryRows(files, filter, byName);
    expect(row.kind === 'group' && row.members.map((m) => m.id)).toEqual([2, 1]);
  });
});

describe('rowFiles', () => {
  it('lists the files behind the rows in row order', () => {
    const files = [
      file({ id: 1, filename: 'b.3mf', variant_group_id: 5, variant_position: 1 }),
      file({ id: 2, filename: 'a.3mf', variant_group_id: 5, variant_position: 0 }),
      file({ id: 3, filename: '0.3mf' }),
    ];
    expect(rowFiles(groupLibraryRows(files, filter, byName)).map((f) => f.id)).toEqual([3, 2, 1]);
  });
});
