import type { ProjectFileProgress } from '../api/client';

/** Per-file counters, with the ones an older backend omits filled in as 0. */
export interface FileCounts {
  completed: number;
  printing: number;
  queued: number;
  failed: number;
  rejected: number;
}

export const NO_COUNTS: FileCounts = { completed: 0, printing: 0, queued: 0, failed: 0, rejected: 0 };

export function toFileCounts(row: ProjectFileProgress): FileCounts {
  return {
    completed: row.completed_count,
    printing: row.printing_count ?? 0,
    queued: row.queued_count ?? 0,
    failed: row.failed_count ?? 0,
    rejected: row.rejected_count ?? 0,
  };
}

/** Prints done or on their way: completed, printing now, or waiting in the queue. */
export function plannedCount(counts: FileCounts): number {
  return counts.completed + counts.printing + counts.queued;
}

/** Prints that did not give a part: failed runs, and completed runs the user rejected. */
export function unsuccessfulCount(counts: FileCounts): number {
  return counts.failed + counts.rejected;
}

/** True while any of the project's files has a print waiting or running — worth polling for. */
export function hasActivePrints(rows: ProjectFileProgress[] | undefined): boolean {
  return (rows ?? []).some((row) => (row.queued_count ?? 0) > 0 || (row.printing_count ?? 0) > 0);
}

/**
 * Where a file stands, by what is happening to it now:
 * - `done`: completed prints reached the Copies per File target,
 * - `printing`: a print of it is running, whatever else is queued,
 * - `queued`: nothing running, but jobs wait in the queue,
 * - `inProgress`: some prints completed, short of the target, nothing running or queued,
 * - `none`: nothing yet.
 * Without a target, any completed print counts as `done` once nothing is running or queued.
 * Whether the running and queued jobs reach the target is a separate question: see
 * the Remaining filter and `plannedSets`.
 */
export type FileStatus = 'done' | 'printing' | 'queued' | 'inProgress' | 'none';

export function fileStatus(counts: FileCounts, target: number | null | undefined): FileStatus {
  if (target && counts.completed >= target) return 'done';
  if (counts.printing > 0) return 'printing';
  if (counts.queued > 0) return 'queued';
  if (counts.completed === 0) return 'none';
  return target ? 'inProgress' : 'done';
}

export const FILE_FILTERS = ['all', 'notStarted', 'queued', 'printing', 'failed', 'done', 'remaining'] as const;
export type FileFilter = (typeof FILE_FILTERS)[number];

/** The filters that make sense for a project: Remaining needs a target to be short of. */
export function availableFileFilters(target: number | null | undefined): FileFilter[] {
  return target ? [...FILE_FILTERS] : FILE_FILTERS.filter((f) => f !== 'remaining');
}

export function matchesFileFilter(counts: FileCounts, target: number | null | undefined, filter: FileFilter): boolean {
  switch (filter) {
    case 'all':
      return true;
    case 'notStarted':
      // A file whose only prints failed or were rejected was started: it is under Failed.
      return plannedCount(counts) === 0 && unsuccessfulCount(counts) === 0;
    case 'queued':
      return counts.queued > 0;
    case 'printing':
      return counts.printing > 0;
    case 'failed':
      return unsuccessfulCount(counts) > 0;
    case 'done':
      return target ? counts.completed >= target : counts.completed > 0;
    case 'remaining':
      return target ? plannedCount(counts) < target : false;
  }
}

/**
 * Sets that will be complete once every printing and queued job finishes:
 * the minimum, across printable files, of completed + printing + queued,
 * capped at the target — the Complete Sets rule with planned prints counted.
 */
export function plannedSets(countsPerFile: FileCounts[], target: number): number | null {
  if (countsPerFile.length === 0) return null;
  return Math.min(...countsPerFile.map((counts) => Math.min(plannedCount(counts), target)));
}
