import type { LibraryFileListItem } from '../../api/client';
import type { GroupRow } from '../../pages/fileManager/variantGroups';
import type { ContextMenuItem } from '../ContextMenu';
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
  /** The file's "Preview" menu entry, or null when it has no preview. */
  previewItem: (file: LibraryFileListItem) => ContextMenuItem | null;
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
  /** The group whose versions are open in a popover (grid view): one at a time on the page. */
  openGroupId: number | null;
  onOpenGroupChange: (id: number | null) => void;
  /** Dragging a group onto a folder moves the versions in this folder. */
  groupDrag?: {
    canDrag: (row: GroupRow) => boolean;
    onDragStart: (row: GroupRow, e: React.DragEvent) => void;
    onDragEnd: () => void;
  };
}

// The attributes that make a group's root element a drag source.
export function dragSourceProps(row: GroupRow, drag: VariantGroupViewProps['groupDrag']) {
  if (!drag || !drag.canDrag(row)) return {};
  return {
    draggable: true,
    onDragStart: (e: React.DragEvent) => drag.onDragStart(row, e),
    onDragEnd: drag.onDragEnd,
  };
}
