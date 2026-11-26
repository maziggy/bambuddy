import type { LibraryFileListItem } from '../../api/client';
import { parseUTCDate } from '../../utils/date';

export type SortField = 'name' | 'date' | 'size' | 'type' | 'prints';
export type SortDirection = 'asc' | 'desc';

export interface FileFilterOptions {
  query?: string;
  filterType: string;
  filterUsername: string;
}

export interface FileSortOptions {
  sortField: SortField;
  sortDirection: SortDirection;
}

export function displayName(file: LibraryFileListItem): string {
  return file.print_name || file.filename;
}

// #2680: sort by real on-disk mtime (matches `ls -t`), falling back to the DB
// created_at for managed uploads that have no filesystem mtime.
export function fileDateValue(file: LibraryFileListItem): number {
  return parseUTCDate(file.fs_modified_at ?? file.created_at)?.getTime() ?? 0;
}

// Search / type / user filtering. `query` matches the filename and the
// embedded print name.
export function filterFiles(files: LibraryFileListItem[], opts: FileFilterOptions): LibraryFileListItem[] {
  let result = [...files];

  const query = opts.query?.trim().toLowerCase();
  if (query) {
    result = result.filter(
      (f) =>
        f.filename.toLowerCase().includes(query) ||
        (f.print_name && f.print_name.toLowerCase().includes(query))
    );
  }

  if (opts.filterType !== 'all') {
    result = result.filter((f) => f.file_type === opts.filterType);
  }

  const userQuery = opts.filterUsername.trim().toLowerCase();
  if (userQuery) {
    result = result.filter(
      (f) => f.created_by_username && f.created_by_username.toLowerCase().includes(userQuery)
    );
  }

  return result;
}

// Ascending comparison of two files on one sort field.
export function compareFiles(a: LibraryFileListItem, b: LibraryFileListItem, field: SortField): number {
  switch (field) {
    case 'name':
      return displayName(a).localeCompare(displayName(b));
    case 'date':
      return fileDateValue(a) - fileDateValue(b);
    case 'size':
      return a.file_size - b.file_size;
    case 'type':
      return a.file_type.localeCompare(b.file_type);
    case 'prints':
      return a.print_count - b.print_count;
  }
}

// Filtering plus the sort, shared by the main listing and by each Miller
// column's file list so a column never sorts differently from the pane beside
// it.
export function filterAndSortFiles(
  files: LibraryFileListItem[],
  opts: FileFilterOptions & FileSortOptions,
): LibraryFileListItem[] {
  const result = filterFiles(files, opts);
  result.sort((a, b) => {
    const comparison = compareFiles(a, b, opts.sortField);
    return opts.sortDirection === 'asc' ? comparison : -comparison;
  });
  return result;
}
