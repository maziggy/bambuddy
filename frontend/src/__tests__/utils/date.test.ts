import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  getDatePlaceholder,
  getTimePlaceholder,
  formatDateInput,
  formatTimeInput,
  parseDateInput,
  parseTimeInput,
  toDateTimeLocalValue,
  applyTimeFormat,
  parseUTCDate,
  formatDate,
  formatDateOnly,
  formatDateTime,
  formatTimeOnly,
  formatETA,
  formatDuration,
  formatRelativeTime,
} from '../../utils/date';

describe('getDatePlaceholder', () => {
  it('returns MM/DD/YYYY for us format', () => {
    expect(getDatePlaceholder('us')).toBe('MM/DD/YYYY');
  });

  it('returns DD/MM/YYYY for eu format', () => {
    expect(getDatePlaceholder('eu')).toBe('DD/MM/YYYY');
  });

  it('returns YYYY-MM-DD for iso format', () => {
    expect(getDatePlaceholder('iso')).toBe('YYYY-MM-DD');
  });

  it('returns a placeholder for system format', () => {
    const result = getDatePlaceholder('system');
    expect(['MM/DD/YYYY', 'DD/MM/YYYY', 'YYYY-MM-DD']).toContain(result);
  });
});

describe('getTimePlaceholder', () => {
  it('returns HH:MM AM/PM for 12h format', () => {
    expect(getTimePlaceholder('12h')).toBe('HH:MM AM/PM');
  });

  it('returns HH:MM for 24h format', () => {
    expect(getTimePlaceholder('24h')).toBe('HH:MM');
  });

  it('returns a placeholder for system format', () => {
    const result = getTimePlaceholder('system');
    expect(['HH:MM AM/PM', 'HH:MM']).toContain(result);
  });
});

describe('formatDateInput', () => {
  const date = new Date(2025, 5, 15); // June 15, 2025

  it('formats as MM/DD/YYYY for us format', () => {
    expect(formatDateInput(date, 'us')).toBe('06/15/2025');
  });

  it('formats as DD/MM/YYYY for eu format', () => {
    expect(formatDateInput(date, 'eu')).toBe('15/06/2025');
  });

  it('formats as YYYY-MM-DD for iso format', () => {
    expect(formatDateInput(date, 'iso')).toBe('2025-06-15');
  });

  it('uses toLocaleDateString for system format', () => {
    const result = formatDateInput(date, 'system');
    expect(result).toBeTruthy();
  });
});

describe('formatTimeInput', () => {
  it('formats as 12h with AM', () => {
    const date = new Date(2025, 0, 1, 9, 30);
    expect(formatTimeInput(date, '12h')).toBe('9:30 AM');
  });

  it('formats as 12h with PM', () => {
    const date = new Date(2025, 0, 1, 14, 45);
    expect(formatTimeInput(date, '12h')).toBe('2:45 PM');
  });

  it('formats 12:00 as 12:00 PM', () => {
    const date = new Date(2025, 0, 1, 12, 0);
    expect(formatTimeInput(date, '12h')).toBe('12:00 PM');
  });

  it('formats 00:00 as 12:00 AM', () => {
    const date = new Date(2025, 0, 1, 0, 0);
    expect(formatTimeInput(date, '12h')).toBe('12:00 AM');
  });

  it('formats as 24h', () => {
    const date = new Date(2025, 0, 1, 14, 30);
    expect(formatTimeInput(date, '24h')).toBe('14:30');
  });

  it('pads hours in 24h format', () => {
    const date = new Date(2025, 0, 1, 9, 5);
    expect(formatTimeInput(date, '24h')).toBe('09:05');
  });
});

describe('parseDateInput', () => {
  it('parses us format MM/DD/YYYY', () => {
    const result = parseDateInput('06/15/2025', 'us');
    expect(result?.getFullYear()).toBe(2025);
    expect(result?.getMonth()).toBe(5); // June
    expect(result?.getDate()).toBe(15);
  });

  it('parses eu format DD/MM/YYYY', () => {
    const result = parseDateInput('15/06/2025', 'eu');
    expect(result?.getFullYear()).toBe(2025);
    expect(result?.getMonth()).toBe(5);
    expect(result?.getDate()).toBe(15);
  });

  it('parses iso format YYYY-MM-DD', () => {
    const result = parseDateInput('2025-06-15', 'iso');
    expect(result?.getFullYear()).toBe(2025);
    expect(result?.getMonth()).toBe(5);
    expect(result?.getDate()).toBe(15);
  });

  it('accepts different separators', () => {
    expect(parseDateInput('06-15-2025', 'us')?.getDate()).toBe(15);
    expect(parseDateInput('15.06.2025', 'eu')?.getDate()).toBe(15);
    expect(parseDateInput('2025/06/15', 'iso')?.getDate()).toBe(15);
  });

  it('returns null for invalid input', () => {
    expect(parseDateInput('', 'us')).toBeNull();
    expect(parseDateInput('invalid', 'us')).toBeNull();
    expect(parseDateInput('13/32/2025', 'us')).toBeNull(); // invalid month
    expect(parseDateInput('01/01/1800', 'us')).toBeNull(); // year out of range
  });

  it('returns null for invalid month', () => {
    expect(parseDateInput('13/01/2025', 'us')).toBeNull();
    expect(parseDateInput('00/01/2025', 'us')).toBeNull();
  });

  it('returns null for invalid day', () => {
    expect(parseDateInput('01/32/2025', 'us')).toBeNull();
    expect(parseDateInput('01/00/2025', 'us')).toBeNull();
  });
});

describe('parseTimeInput', () => {
  it('parses 24h format', () => {
    expect(parseTimeInput('14:30')).toEqual({ hours: 14, minutes: 30 });
    expect(parseTimeInput('09:05')).toEqual({ hours: 9, minutes: 5 });
    expect(parseTimeInput('0:00')).toEqual({ hours: 0, minutes: 0 });
  });

  it('parses 12h format with AM', () => {
    expect(parseTimeInput('9:30 AM')).toEqual({ hours: 9, minutes: 30 });
    expect(parseTimeInput('12:00 AM')).toEqual({ hours: 0, minutes: 0 });
  });

  it('parses 12h format with PM', () => {
    expect(parseTimeInput('2:45 PM')).toEqual({ hours: 14, minutes: 45 });
    expect(parseTimeInput('12:00 PM')).toEqual({ hours: 12, minutes: 0 });
  });

  it('is case insensitive for AM/PM', () => {
    expect(parseTimeInput('9:30 am')).toEqual({ hours: 9, minutes: 30 });
    expect(parseTimeInput('2:45 pm')).toEqual({ hours: 14, minutes: 45 });
  });

  it('returns null for invalid input', () => {
    expect(parseTimeInput('')).toBeNull();
    expect(parseTimeInput('invalid')).toBeNull();
    expect(parseTimeInput('25:00')).toBeNull();
    expect(parseTimeInput('12:60')).toBeNull();
    expect(parseTimeInput('-1:00')).toBeNull();
  });
});

describe('toDateTimeLocalValue', () => {
  it('formats date to datetime-local value', () => {
    const date = new Date(2025, 5, 15, 14, 30);
    expect(toDateTimeLocalValue(date)).toBe('2025-06-15T14:30');
  });

  it('pads single digit values', () => {
    const date = new Date(2025, 0, 5, 9, 5);
    expect(toDateTimeLocalValue(date)).toBe('2025-01-05T09:05');
  });
});

describe('applyTimeFormat', () => {
  it('sets hour12 true for 12h format', () => {
    const options: Intl.DateTimeFormatOptions = {};
    applyTimeFormat(options, '12h');
    expect(options.hour12).toBe(true);
  });

  it('sets hour12 false for 24h format', () => {
    const options: Intl.DateTimeFormatOptions = {};
    applyTimeFormat(options, '24h');
    expect(options.hour12).toBe(false);
  });

  it('leaves hour12 undefined for system format', () => {
    const options: Intl.DateTimeFormatOptions = {};
    applyTimeFormat(options, 'system');
    expect(options.hour12).toBeUndefined();
  });

  it('returns the modified options object', () => {
    const options: Intl.DateTimeFormatOptions = { hour: '2-digit' };
    const result = applyTimeFormat(options, '12h');
    expect(result).toBe(options);
    expect(result.hour).toBe('2-digit');
  });
});

describe('parseUTCDate', () => {
  it('returns null for null/undefined input', () => {
    expect(parseUTCDate(null)).toBeNull();
    expect(parseUTCDate(undefined)).toBeNull();
    expect(parseUTCDate('')).toBeNull();
  });

  it('parses ISO string with Z suffix as-is', () => {
    const result = parseUTCDate('2025-06-15T12:00:00Z');
    expect(result).toBeInstanceOf(Date);
    expect(result?.getUTCHours()).toBe(12);
  });

  it('parses ISO string with timezone offset as-is', () => {
    const result = parseUTCDate('2025-06-15T12:00:00+05:00');
    expect(result).toBeInstanceOf(Date);
  });

  it('appends Z to strings without timezone indicator', () => {
    const result = parseUTCDate('2025-06-15T12:00:00');
    expect(result).toBeInstanceOf(Date);
    expect(result?.getUTCHours()).toBe(12);
  });
});

describe('formatDate', () => {
  it('returns empty string for null input', () => {
    expect(formatDate(null)).toBe('');
    expect(formatDate(undefined)).toBe('');
  });

  it('formats a valid date string', () => {
    const result = formatDate('2025-06-15T12:00:00Z');
    expect(result).toBeTruthy();
    expect(result).toContain('2025');
  });

  it('accepts custom options', () => {
    const result = formatDate('2025-06-15T12:00:00Z', { year: 'numeric' });
    expect(result).toContain('2025');
  });
});

describe('formatDateOnly', () => {
  it('returns empty string for null input', () => {
    expect(formatDateOnly(null)).toBe('');
  });

  it('formats date without time', () => {
    const result = formatDateOnly('2025-06-15T12:00:00Z');
    expect(result).toBeTruthy();
    expect(result).toContain('2025');
  });
});

describe('formatDateTime', () => {
  it('returns empty string for null input', () => {
    expect(formatDateTime(null)).toBe('');
  });

  it('formats with 12h time format', () => {
    const result = formatDateTime('2025-06-15T14:00:00Z', '12h');
    expect(result).toBeTruthy();
  });

  it('formats with 24h time format', () => {
    const result = formatDateTime('2025-06-15T14:00:00Z', '24h');
    expect(result).toBeTruthy();
  });
});

describe('formatTimeOnly', () => {
  it('formats time with 12h format', () => {
    const date = new Date(2025, 5, 15, 14, 30);
    const result = formatTimeOnly(date, '12h');
    expect(result).toMatch(/2:30|02:30/);
    expect(result.toUpperCase()).toContain('PM');
  });

  it('formats time with 24h format', () => {
    const date = new Date(2025, 5, 15, 14, 30);
    const result = formatTimeOnly(date, '24h');
    expect(result).toContain('14:30');
  });
});

describe('formatETA', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2025-06-15T12:00:00Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('returns time only for same day', () => {
    const result = formatETA(60); // 1 hour from now
    expect(result).toBeTruthy();
  });

  it('includes "Tomorrow" for next day', () => {
    const result = formatETA(60 * 24); // 24 hours from now
    expect(result).toContain('Tomorrow');
  });

  it('uses translation function for tomorrow', () => {
    const t = vi.fn((key: string) => (key === 'common.tomorrow' ? 'Demain' : key));
    const result = formatETA(60 * 24, 'system', t);
    expect(result).toContain('Demain');
  });

  it('shows weekday for dates beyond tomorrow', () => {
    const result = formatETA(60 * 48); // 48 hours from now
    expect(result).not.toContain('Tomorrow');
  });
});

describe('formatDuration', () => {
  it('returns "--" for null/undefined', () => {
    expect(formatDuration(null)).toBe('--');
    expect(formatDuration(undefined)).toBe('--');
  });

  it('returns "--" for negative values', () => {
    expect(formatDuration(-1)).toBe('--');
  });

  it('formats minutes only when under 1 hour', () => {
    expect(formatDuration(0)).toBe('0m');
    expect(formatDuration(60)).toBe('1m');
    expect(formatDuration(2700)).toBe('45m');
  });

  it('formats hours and minutes', () => {
    expect(formatDuration(3600)).toBe('1h 0m');
    expect(formatDuration(5400)).toBe('1h 30m');
    expect(formatDuration(9000)).toBe('2h 30m');
  });
});

describe('formatRelativeTime', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2025-06-15T12:00:00Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('returns "-" for null input', () => {
    expect(formatRelativeTime(null)).toBe('-');
  });

  it('returns translated unknown for null with translation', () => {
    const t = vi.fn((key: string) => (key === 'time.unknown' ? 'Unknown' : key));
    expect(formatRelativeTime(null, 'system', t)).toBe('Unknown');
  });

  it('returns "Just now" for times less than 1 minute ago', () => {
    expect(formatRelativeTime('2025-06-15T11:59:30Z')).toBe('Just now');
  });

  it('returns "Now" for times less than 1 minute in future', () => {
    expect(formatRelativeTime('2025-06-15T12:00:30Z')).toBe('Now');
  });

  it('returns minutes ago for times under 1 hour ago', () => {
    expect(formatRelativeTime('2025-06-15T11:55:00Z')).toBe('5m ago');
    expect(formatRelativeTime('2025-06-15T11:30:00Z')).toBe('30m ago');
  });

  it('returns "in Xm" for times under 1 hour in future', () => {
    expect(formatRelativeTime('2025-06-15T12:05:00Z')).toBe('in 5m');
    expect(formatRelativeTime('2025-06-15T12:30:00Z')).toBe('in 30m');
  });

  it('returns hours ago for times under 1 day ago', () => {
    expect(formatRelativeTime('2025-06-15T10:00:00Z')).toBe('2h ago');
    expect(formatRelativeTime('2025-06-15T06:00:00Z')).toBe('6h ago');
  });

  it('returns "in Xh" for times under 1 day in future', () => {
    expect(formatRelativeTime('2025-06-15T14:00:00Z')).toBe('in 2h');
    expect(formatRelativeTime('2025-06-15T18:00:00Z')).toBe('in 6h');
  });

  it('returns days ago for times under 7 days ago', () => {
    expect(formatRelativeTime('2025-06-14T12:00:00Z')).toBe('1d ago');
    expect(formatRelativeTime('2025-06-10T12:00:00Z')).toBe('5d ago');
  });

  it('returns "in Xd" for times under 7 days in future', () => {
    expect(formatRelativeTime('2025-06-16T12:00:00Z')).toBe('in 1d');
    expect(formatRelativeTime('2025-06-20T12:00:00Z')).toBe('in 5d');
  });

  it('returns formatted date for times older than 7 days', () => {
    const result = formatRelativeTime('2025-06-01T12:00:00Z');
    expect(result).toContain('2025');
  });

  it('uses translation function when provided', () => {
    const t = vi.fn((key: string, options?: Record<string, unknown>) => {
      if (key === 'time.minsAgo') return `${options?.count} minutes ago`;
      if (key === 'time.inMins') return `in ${options?.count} minutes`;
      return key;
    });

    expect(formatRelativeTime('2025-06-15T11:55:00Z', 'system', t)).toBe('5 minutes ago');
    expect(formatRelativeTime('2025-06-15T12:05:00Z', 'system', t)).toBe('in 5 minutes');
  });
});
