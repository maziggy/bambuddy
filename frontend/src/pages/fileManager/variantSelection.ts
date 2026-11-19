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

export function planBulkPrint(
  selectedIds: readonly number[],
  groupRows: readonly GroupRow[],
  filesById: ReadonlyMap<number, LibraryFileListItem>,
): BulkPrintPlan {
  const selected = new Set(selectedIds);
  const seen = new Set<number>();
  const fullGroups: GroupRow[] = [];
  const partialGroups: BulkPrintPlan['partialGroups'] = [];
  const inFullGroup = new Set<number>();
  for (const row of groupRows) {
    if (seen.has(row.id)) continue;
    seen.add(row.id);
    const count = row.members.filter((m) => selected.has(m.id)).length;
    if (count === 0) continue;
    if (count === row.members.length) {
      if (row.members.some(isSlicedLibraryFile)) {
        fullGroups.push(row);
        for (const m of row.members) inFullGroup.add(m.id);
      }
    } else if (row.members.some((m) => selected.has(m.id) && isSlicedLibraryFile(m))) {
      partialGroups.push({ row, selected: count });
    }
  }
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

// The number of print jobs the Print button stands for.
export function bulkPrintJobCount(plan: BulkPrintPlan): number {
  return plan.fullGroups.length + (plan.looseFiles.length > 0 ? 1 : 0);
}

// The versions a drag of the group card moves: those in the folder on screen
// (not listed only because they sit elsewhere), outside external folders,
// and that the user may change.
export function draggedMemberIds(row: GroupRow, canUpdate: (file: LibraryFileListItem) => boolean): number[] {
  return row.members
    .filter((m) => !m.variant_elsewhere && !m.is_external && canUpdate(m))
    .map((m) => m.id);
}
