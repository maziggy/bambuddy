import { describe, expect, it } from 'vitest';
import { computeStartAfter } from '../../lib/scheduledDrying';

describe('computeStartAfter', () => {
  const now = new Date('2026-07-23T10:00:00.000Z');

  it('returns null for immediate start', () => {
    expect(computeStartAfter('now', 120, '', now)).toBeNull();
  });

  it('adds the delay in minutes', () => {
    expect(computeStartAfter('delay', 120, '', now)).toBe('2026-07-23T12:00:00.000Z');
  });

  it('converts a datetime-local value (local tz) to UTC ISO', () => {
    const result = computeStartAfter('at_time', 0, '2026-07-23T18:30', now);
    // new Date('YYYY-MM-DDTHH:MM') parses in the local timezone; the ISO
    // output must be that instant in UTC.
    expect(result).toBe(new Date('2026-07-23T18:30').toISOString());
  });
});
