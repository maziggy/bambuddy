/**
 * The Stats presets are local calendar days (DB-C12).
 *
 * They were built from UTC dates: at 01:30 in Berlin, "Today" asked for the
 * previous day, and This Week / This Month started a day early or late near
 * midnight. The backend reads a date as a day in the browser's zone, which the
 * API client sends with it.
 */

import { describe, it, expect, beforeAll, afterAll, vi } from 'vitest';
import { computeDateRange } from '../../utils/statsDateRange';

const originalTz = process.env.TZ;

beforeAll(() => {
  process.env.TZ = 'Europe/Berlin';
  vi.useFakeTimers();
  // 01:30 on Friday 16 October in Berlin -- still the 15th in UTC.
  vi.setSystemTime(new Date('2026-10-15T23:30:00Z'));
});

afterAll(() => {
  vi.useRealTimers();
  // Assigning undefined would set the string "undefined", a zone of its own.
  if (originalTz === undefined) delete process.env.TZ;
  else process.env.TZ = originalTz;
});

describe('computeDateRange', () => {
  it('takes today as the local date', () => {
    expect(computeDateRange('today')).toEqual({ dateFrom: '2026-10-16', dateTo: '2026-10-16' });
  });

  it('starts this week on the local Monday', () => {
    expect(computeDateRange('this-week')).toEqual({ dateFrom: '2026-10-12', dateTo: '2026-10-16' });
  });

  it('counts the last 7 days back from the local date', () => {
    expect(computeDateRange('last-7')).toEqual({ dateFrom: '2026-10-10', dateTo: '2026-10-16' });
  });

  it('starts this month and this year on their local first days', () => {
    expect(computeDateRange('this-month').dateFrom).toBe('2026-10-01');
    expect(computeDateRange('this-year').dateFrom).toBe('2026-01-01');
  });

  it('has no bounds for all time', () => {
    expect(computeDateRange('all-time')).toEqual({ dateFrom: undefined, dateTo: undefined });
  });
});
