import type { LibraryFileListItem } from '../../api/client';
import {
  compareFiles,
  displayName,
  fileDateValue,
  filterFiles,
  type FileFilterOptions,
  type FileSortOptions,
} from './filterSort';

// One row of the file list: a plain file, or a variant group folded into one
// row (#3628). `members` are the group's visible members in version order,
// including those listed only because they sit in another folder or outside
// the current filter.
export type LibraryRow =
  | { kind: 'file'; file: LibraryFileListItem }
  | {
      kind: 'group';
      id: number;
      name: string;
      members: LibraryFileListItem[];
      thumbnailFile: LibraryFileListItem;
      newest: LibraryFileListItem;
    };

export type GroupRow = Extract<LibraryRow, { kind: 'group' }>;

function byPosition(a: LibraryFileListItem, b: LibraryFileListItem): number {
  const pa = a.variant_position ?? Number.MAX_SAFE_INTEGER;
  const pb = b.variant_position ?? Number.MAX_SAFE_INTEGER;
  return pa - pb || a.id - b.id;
}

function buildGroupRow(id: number, members: LibraryFileListItem[]): GroupRow {
  const ordered = [...members].sort(byPosition);
  const first = ordered[0];
  const newest = ordered.reduce((best, m) => (fileDateValue(m) > fileDateValue(best) ? m : best), first);
  return {
    kind: 'group',
    id,
    name: first.variant_group_name || displayName(first),
    members: ordered,
    thumbnailFile: first,
    newest,
  };
}

// The file a row stands for when it is sorted on `field`: a group's newest
// member for the date, its first member for everything else.
function representative(row: LibraryRow, field: FileSortOptions['sortField']): LibraryFileListItem {
  if (row.kind === 'file') return row.file;
  return field === 'date' ? row.newest : row.thumbnailFile;
}

function compareRows(a: LibraryRow, b: LibraryRow, field: FileSortOptions['sortField']): number {
  if (field === 'name') {
    const name = (r: LibraryRow) => (r.kind === 'group' ? r.name : displayName(r.file));
    return name(a).localeCompare(name(b));
  }
  return compareFiles(representative(a, field), representative(b, field), field);
}

// Folds the listing into rows. A group row exists when at least one member
// passes the filter and at least two members are visible at all; it keeps all
// of its visible members, whatever the filter says of them. Members listed
// only because they sit elsewhere never make a row of their own, and a group
// with a single visible member is a plain file.
export function groupLibraryRows(
  files: LibraryFileListItem[],
  filter: FileFilterOptions,
  sort: FileSortOptions,
): LibraryRow[] {
  const byGroup = new Map<number, LibraryFileListItem[]>();
  for (const file of files) {
    if (file.variant_group_id == null) continue;
    const list = byGroup.get(file.variant_group_id);
    if (list) list.push(file);
    else byGroup.set(file.variant_group_id, [file]);
  }
  const isGroupMember = (file: LibraryFileListItem) =>
    file.variant_group_id != null && (byGroup.get(file.variant_group_id)?.length ?? 0) >= 2;

  const here = files.filter((f) => !f.variant_elsewhere);
  const passing = filterFiles(here, filter);

  const rows: LibraryRow[] = [];
  const seenGroups = new Set<number>();
  for (const file of passing) {
    if (!isGroupMember(file)) {
      rows.push({ kind: 'file', file });
      continue;
    }
    const id = file.variant_group_id!;
    if (seenGroups.has(id)) continue;
    seenGroups.add(id);
    rows.push(buildGroupRow(id, byGroup.get(id)!));
  }

  rows.sort((a, b) => {
    const comparison = compareRows(a, b, sort.sortField);
    return sort.sortDirection === 'asc' ? comparison : -comparison;
  });
  return rows;
}

// Every file behind the rows, in row order.
export function rowFiles(rows: LibraryRow[]): LibraryFileListItem[] {
  return rows.flatMap((row) => (row.kind === 'file' ? [row.file] : row.members));
}

// The file a row stands for where one file is needed: the keyboard focus of
// the columns view, a preview. A group's is its first version.
export function rowLeadFile(row: LibraryRow): LibraryFileListItem {
  return row.kind === 'file' ? row.file : row.thumbnailFile;
}

// The group row among `rows` that `fileId` leads, if any. A group listed in
// several columns has one row per column, so the caller passes the rows of the
// column it means.
export function groupRowLedBy(rows: readonly LibraryRow[] | undefined, fileId: number | null): GroupRow | undefined {
  if (!rows || fileId === null) return undefined;
  return rows.find((row): row is GroupRow => row.kind === 'group' && row.thumbnailFile.id === fileId);
}
