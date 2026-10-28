import type { LibraryFolderTree } from '../api/client';

/**
 * Drag payload type for a File Manager folder being moved (#3305). Distinct
 * from the browser's `Files` type so a folder drag never trips the page-wide
 * upload overlay, and from any future file-row drag type.
 */
export const FOLDER_DRAG_TYPE = 'application/x-bambuddy-folder';

function collectSubtreeIds(folder: LibraryFolderTree, into: Set<number>): void {
  into.add(folder.id);
  for (const child of folder.children) collectSubtreeIds(child, into);
}

function findFolder(folders: LibraryFolderTree[], id: number): LibraryFolderTree | null {
  for (const folder of folders) {
    if (folder.id === id) return folder;
    const hit = findFolder(folder.children, id);
    if (hit) return hit;
  }
  return null;
}

/**
 * Whether the current user may put things into `folder`. A library:*_own user
 * sees shared folders they cannot write to (#3201); those stay listed in the
 * pickers but are never a destination. The picker's disabled state and the
 * folder drop targets both use this, so the two cannot drift apart.
 */
export function isWritableFolder(folder: Pick<LibraryFolderTree, 'can_write'>): boolean {
  return folder.can_write !== false;
}

/**
 * Folder ids that may NOT become the new parent of `folderId` (#3305): the
 * folder itself, everything below it (the server rejects those with 400 too,
 * this just keeps them out of the picker), and every external folder with its
 * subtree — external folders mirror a path on disk, so nothing can be moved
 * into one.
 */
export function moveDisallowedFolderIds(folders: LibraryFolderTree[], folderId: number): Set<number> {
  const disallowed = new Set<number>();
  const self = findFolder(folders, folderId);
  if (self) collectSubtreeIds(self, disallowed);
  const walk = (items: LibraryFolderTree[]) => {
    for (const item of items) {
      if (item.is_external) collectSubtreeIds(item, disallowed);
      else walk(item.children);
    }
  };
  walk(folders);
  return disallowed;
}

/**
 * Whether dropping / moving `folder` onto `destinationId` (null = root) would
 * actually change anything the server accepts. False for the folder itself,
 * its descendants, external destinations, destinations the user may not write
 * to, and the parent it already has.
 */
export function isValidFolderMove(
  folders: LibraryFolderTree[],
  folder: Pick<LibraryFolderTree, 'id' | 'parent_id' | 'is_external'>,
  destinationId: number | null,
): boolean {
  if (folder.is_external) return false;
  if ((folder.parent_id ?? null) === destinationId) return false;
  if (destinationId === null) return true;
  const destination = findFolder(folders, destinationId);
  if (!destination || !isWritableFolder(destination)) return false;
  return !moveDisallowedFolderIds(folders, folder.id).has(destinationId);
}
