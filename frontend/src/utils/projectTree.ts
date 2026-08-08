import type { ProjectListItem } from '../api/client';

/**
 * Projects that may legally become `projectId`'s parent (#1264).
 *
 * Its own descendants are excluded as well as itself: nesting a project under
 * something already beneath it makes a cycle, which the API rejects anyway, so
 * offering it would only produce an error the user cannot act on. Walked from
 * the flat list rather than fetched, since every row carries its `parent_id`.
 */
export function eligibleParents(
  projects: ProjectListItem[],
  projectId: number | undefined,
): ProjectListItem[] {
  if (projectId === undefined) return projects;
  const blocked = new Set([projectId]);
  // Repeat until nothing new is blocked: the list is in no particular order, so
  // a grandchild can appear before its parent has been blocked.
  let grew = true;
  while (grew) {
    grew = false;
    for (const candidate of projects) {
      if (candidate.parent_id !== null && blocked.has(candidate.parent_id) && !blocked.has(candidate.id)) {
        blocked.add(candidate.id);
        grew = true;
      }
    }
  }
  return projects.filter((p) => !blocked.has(p.id));
}
