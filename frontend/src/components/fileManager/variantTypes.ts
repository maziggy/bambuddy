import type { LibraryFileListItem } from '../../api/client';
import type { GroupRow } from '../../pages/fileManager/variantGroups';
import type { VariantGroupActions } from './useVariantGroupActions';

// What a version line can do to its file: the same actions a plain file has,
// handed in by the page so the lines never reach into its state (#3628).
export interface VariantMemberActions {
  onDetails: (file: LibraryFileListItem) => void;
  onDownload: (id: number) => void;
  onRename: (file: LibraryFileListItem) => void;
  onDelete: (id: number) => void;
  /** Print just this version, without offering the rest of the group. */
  onPrintFile: (file: LibraryFileListItem) => void;
}

// Everything the three views need to draw and drive a group row.
export interface VariantGroupViewProps {
  selectedIds: readonly number[];
  highlightedFileId: number | null;
  thumbnailVersions: Record<number, number>;
  showModified: boolean;
  authEnabled: boolean;
  onToggleSelect: (row: GroupRow) => void;
  onTagClick?: (tagId: number) => void;
  memberActions: VariantMemberActions;
  groupActions: VariantGroupActions;
}

export function selectedMemberCount(row: GroupRow, selectedIds: readonly number[]): number {
  return row.members.filter((m) => selectedIds.includes(m.id)).length;
}
