import type { QueryClient } from '@tanstack/react-query';

/**
 * Every React Query key whose data is derived from which archives belong to a
 * project. All are prefixes: `['project']` matches `['project', 42]`, so one
 * entry covers every project id currently in the cache.
 */
const PROJECT_VIEW_QUERY_KEYS = [
  ['projects'],
  ['project'],
  ['project-archives'],
  ['project-timeline'],
  ['project-file-progress'],
] as const;

/**
 * Refresh everything a project shows after an archive is deleted, or moved
 * into or out of a project.
 *
 * The default `staleTime` is 60s, so without this a project page visited
 * within a minute of the change serves its cached answer and keeps showing a
 * print that is no longer there — the user has to reload by hand (#2731).
 * Deletes previously invalidated only `['archives']`, and the project-assign
 * mutations only `['projects']`, which refreshed the overview cards but never
 * the detail page they were most likely looking at.
 */
export function invalidateProjectViews(queryClient: QueryClient) {
  return Promise.all(
    PROJECT_VIEW_QUERY_KEYS.map((queryKey) => queryClient.invalidateQueries({ queryKey: [...queryKey] })),
  );
}

/**
 * Every key read from an archive or its prints, beside the list: the archive
 * page's archive and print list and the calendar's prints (archive redesign
 * P4). Prefixes, as above.
 */
export const ARCHIVE_VIEW_QUERY_KEYS = [['archive'], ['archive-runs'], ['calendar-runs']] as const;

/** As above, plus the archive list and pages — for mutations that change both. */
export function invalidateArchiveAndProjectViews(queryClient: QueryClient) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: ['archives'] }),
    ...ARCHIVE_VIEW_QUERY_KEYS.map((queryKey) => queryClient.invalidateQueries({ queryKey: [...queryKey] })),
    invalidateProjectViews(queryClient),
  ]);
}
