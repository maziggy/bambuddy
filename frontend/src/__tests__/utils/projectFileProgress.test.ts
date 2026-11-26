import { describe, it, expect } from 'vitest';
import {
  NO_COUNTS,
  availableFileFilters,
  fileStatus,
  hasActivePrints,
  matchesFileFilter,
  plannedSets,
  toFileCounts,
  type FileCounts,
} from '../../utils/projectFileProgress';

const counts = (overrides: Partial<FileCounts>): FileCounts => ({ ...NO_COUNTS, ...overrides });

describe('toFileCounts', () => {
  it('fills the counters an older backend omits with 0', () => {
    expect(toFileCounts({ file_id: 1, completed_count: 2 })).toEqual({
      completed: 2,
      printing: 0,
      queued: 0,
      failed: 0,
      rejected: 0,
    });
  });
});

describe('hasActivePrints', () => {
  it('is true only while something is queued or printing', () => {
    expect(hasActivePrints(undefined)).toBe(false);
    expect(hasActivePrints([{ file_id: 1, completed_count: 3, failed_count: 1 }])).toBe(false);
    expect(hasActivePrints([{ file_id: 1, completed_count: 0, queued_count: 1 }])).toBe(true);
    expect(hasActivePrints([{ file_id: 1, completed_count: 0, printing_count: 1 }])).toBe(true);
  });
});

describe('fileStatus', () => {
  it('is done once completed prints reach the target, even with more running', () => {
    expect(fileStatus(counts({ completed: 5 }), 5)).toBe('done');
    expect(fileStatus(counts({ completed: 6, queued: 1 }), 5)).toBe('done');
    expect(fileStatus(counts({ completed: 5, printing: 1 }), 5)).toBe('done');
  });

  it('is printing while a print runs, whatever is queued or completed', () => {
    expect(fileStatus(counts({ printing: 1 }), 5)).toBe('printing');
    expect(fileStatus(counts({ completed: 3, printing: 1, queued: 1 }), 5)).toBe('printing');
  });

  it('is queued when jobs only wait in the queue', () => {
    expect(fileStatus(counts({ queued: 1 }), 5)).toBe('queued');
    expect(fileStatus(counts({ completed: 2, queued: 3 }), 5)).toBe('queued');
  });

  it('is in progress short of the target with nothing running or queued, none with nothing', () => {
    expect(fileStatus(counts({ completed: 4 }), 5)).toBe('inProgress');
    // Failures do not count as progress.
    expect(fileStatus(counts({ failed: 3 }), 5)).toBe('none');
  });

  it('without a target, follows the jobs, then is done after a print', () => {
    expect(fileStatus(counts({ completed: 1 }), null)).toBe('done');
    expect(fileStatus(counts({ completed: 1, queued: 1 }), null)).toBe('queued');
    expect(fileStatus(counts({ completed: 1, printing: 1, queued: 1 }), null)).toBe('printing');
    expect(fileStatus(NO_COUNTS, undefined)).toBe('none');
  });
});

describe('matchesFileFilter', () => {
  const notStarted = NO_COUNTS;
  const queued = counts({ completed: 1, queued: 2 });
  const printing = counts({ printing: 1 });
  const done = counts({ completed: 4 });
  const onlyFailed = counts({ failed: 1 });
  const onlyRejected = counts({ rejected: 1 });

  it.each([
    ['all', [true, true, true, true, true, true]],
    // A file whose only prints failed or were rejected is not "not started".
    ['notStarted', [true, false, false, false, false, false]],
    ['queued', [false, true, false, false, false, false]],
    ['printing', [false, false, true, false, false, false]],
    ['failed', [false, false, false, false, true, true]],
    ['done', [false, false, false, true, false, false]],
    // Remaining counts queued prints: 1 + 2 = 3 of 4 is still short.
    ['remaining', [true, true, true, false, true, true]],
  ] as const)('%s', (filter, expected) => {
    expect(
      [notStarted, queued, printing, done, onlyFailed, onlyRejected].map((c) => matchesFileFilter(c, 4, filter)),
    ).toEqual(expected);
  });

  it('counts queued prints toward the target for Remaining', () => {
    expect(matchesFileFilter(counts({ completed: 2, queued: 2 }), 4, 'remaining')).toBe(false);
  });

  it('without a target, Done is any completed print', () => {
    expect(matchesFileFilter(counts({ completed: 1 }), null, 'done')).toBe(true);
    expect(matchesFileFilter(NO_COUNTS, null, 'done')).toBe(false);
  });
});

describe('availableFileFilters', () => {
  it('offers Remaining only with a target', () => {
    expect(availableFileFilters(4)).toContain('remaining');
    expect(availableFileFilters(null)).not.toContain('remaining');
  });
});

describe('plannedSets', () => {
  it('is the smallest planned count across files, capped at the target', () => {
    expect(plannedSets([counts({ completed: 9 }), counts({ completed: 1, queued: 2 })], 4)).toBe(3);
    expect(plannedSets([counts({ completed: 9 }), counts({ printing: 5 })], 4)).toBe(4);
  });

  it('is null without printable files', () => {
    expect(plannedSets([], 4)).toBeNull();
  });
});
