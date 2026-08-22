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

/**
 * Projects a picker should offer when filing something away (#2888).
 *
 * An archived project is one its owner has explicitly put out of the way, so
 * leaving it in a picker only lengthens a list they then have to search --
 * the reporter had five active projects behind thirty-odd finished ones.
 * Completed projects stay: filing a reprint against a finished project is
 * ordinary, and "completed" says the work is done, not that it should be
 * hidden.
 *
 * `keepId` names one project that survives whatever its status -- the one the
 * thing being edited already belongs to. Without it a controlled `<select>`
 * holds a value no option matches, and the browser resets it to the first
 * option, which here is "No project": an archive filed in an archived project
 * would state, in as many words, that it is filed nowhere.
 */
export function assignableProjects(
  projects: ProjectListItem[],
  keepId?: number | null,
): ProjectListItem[] {
  return projects.filter((p) => p.status !== 'archived' || p.id === keepId);
}
