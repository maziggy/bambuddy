import type { LibraryFileListItem } from '../../api/client';
import { isSlicedLibraryFile } from '../../utils/libraryFiles';
import type { GroupRow } from './variantGroups';

// How a selection of file ids splits into print jobs (#3628, #3462): a group
// whose visible versions are all selected is one job with printer
// alternatives; a group only partly selected is asked about; everything else
// is printed as before.
export interface BulkPrintPlan {
  fullGroups: GroupRow[];
  partialGroups: { row: GroupRow; selected: number }[];
  /** Selected sliced files outside the full groups, in the order ticked. */
  looseFiles: LibraryFileListItem[];
}

// The versions a group's tick selects: those in the folder on screen. Members
// listed only because they sit elsewhere are never selected by it, so a bulk
// Delete, Move or Tag cannot reach into another folder (#3628).
export function selectableMembers(row: GroupRow): LibraryFileListItem[] {
  return row.members.filter((m) => !m.variant_elsewhere);
}

// How many of a group's selectable versions are selected.
export function groupSelection(row: GroupRow, selectedIds: readonly number[]): { count: number; total: number } {
  const members = selectableMembers(row);
  return { count: members.filter((m) => selectedIds.includes(m.id)).length, total: members.length };
}

export function planBulkPrint(
  selectedIds: readonly number[],
  groupRows: readonly GroupRow[],
  filesById: ReadonlyMap<number, LibraryFileListItem>,
): BulkPrintPlan {
  const selected = new Set(selectedIds);
  const fullIds = new Set<number>();
  const fullGroups: GroupRow[] = [];
  const partialGroups: BulkPrintPlan['partialGroups'] = [];
  const inFullGroup = new Set<number>();
  // `groupRows` are the rows on screen, and a group can be on screen in several
  // columns. Each row is judged by what its own tick selects (the versions in
  // its pane), so a group ticked in full in one pane never reads as partial
  // because another pane lists other versions of it (#3628).
  for (const row of groupRows) {
    if (fullIds.has(row.id)) continue;
    const members = selectableMembers(row);
    const count = members.filter((m) => selected.has(m.id)).length;
    if (count === 0) continue;
    if (count === members.length) {
      if (!row.members.some(isSlicedLibraryFile)) continue;
      fullIds.add(row.id);
      fullGroups.push(row);
      for (const m of row.members) inFullGroup.add(m.id);
      const earlier = partialGroups.findIndex((p) => p.row.id === row.id);
      if (earlier !== -1) partialGroups.splice(earlier, 1);
    } else if (
      members.some((m) => selected.has(m.id) && isSlicedLibraryFile(m)) &&
      !partialGroups.some((p) => p.row.id === row.id)
    ) {
      partialGroups.push({ row, selected: count });
    }
  }
  // Jobs come in the order the groups were ticked.
  const tickedAt = (row: GroupRow) =>
    Math.min(...selectableMembers(row).map((m) => selectedIds.indexOf(m.id)).filter((i) => i !== -1));
  fullGroups.sort((a, b) => tickedAt(a) - tickedAt(b));
  const looseFiles = selectedIds
    .map((id) => filesById.get(id))
    .filter((f): f is LibraryFileListItem => !!f && isSlicedLibraryFile(f) && !inFullGroup.has(f.id));
  return { fullGroups, partialGroups, looseFiles };
}

// Treats the partly selected groups as selected in full.
export function promotePartialGroups(plan: BulkPrintPlan): BulkPrintPlan {
  const promoted = new Set(plan.partialGroups.flatMap((p) => p.row.members.map((m) => m.id)));
  return {
    fullGroups: [...plan.fullGroups, ...plan.partialGroups.map((p) => p.row)],
    partialGroups: [],
    looseFiles: plan.looseFiles.filter((f) => !promoted.has(f.id)),
  };
}

// The versions a drag of the group card moves: those in the folder on screen
// (not listed only because they sit elsewhere), outside external folders,
// and that the user may change.
export function draggedMemberIds(row: GroupRow, canUpdate: (file: LibraryFileListItem) => boolean): number[] {
  return row.members
    .filter((m) => !m.variant_elsewhere && !m.is_external && canUpdate(m))
    .map((m) => m.id);
}
