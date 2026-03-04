/**
 * Date utilities for handling UTC timestamps from the backend.
 *
 * The backend stores all timestamps in UTC without timezone indicators.
 * These utilities ensure dates are properly interpreted as UTC and
 * displayed in the user's local timezone.
 */

export type TimeFormat = 'system' | '12h' | '24h';
export type DateFormat = 'system' | 'us' | 'eu' | 'iso';

/**
 * Get the date input placeholder based on format setting.
 */
export function getDatePlaceholder(dateFormat: DateFormat = 'system'): string {
  const resolved = dateFormat === 'system' ? detectSystemDateFormat() : dateFormat;
  switch (resolved) {
    case 'us': return 'MM/DD/YYYY';
    case 'eu': return 'DD/MM/YYYY';
    case 'iso': return 'YYYY-MM-DD';
    default: return resolved satisfies never;
  }
}

/**
 * Get the time input placeholder based on format setting.
 */
export function getTimePlaceholder(timeFormat: TimeFormat = 'system'): string {
  switch (timeFormat) {
    case '12h':
      return 'HH:MM AM/PM';
    case '24h':
      return 'HH:MM';
    case 'system':
    default: {
      // Try to detect system format
      const testDate = new Date(2000, 0, 1, 14, 30);
      const formatted = testDate.toLocaleTimeString();
      if (formatted.includes('PM') || formatted.includes('AM')) return 'HH:MM AM/PM';
      return 'HH:MM';
    }
  }
}

/**
 * Format a Date object to a date string based on format setting.
 */
export function formatDateInput(date: Date, dateFormat: DateFormat = 'system'): string {
  const day = String(date.getDate()).padStart(2, '0');
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const year = date.getFullYear();

  switch (dateFormat) {
    case 'us':
      return `${month}/${day}/${year}`;
    case 'eu':
      return `${day}/${month}/${year}`;
    case 'iso':
      return `${year}-${month}-${day}`;
    case 'system':
    default:
      return date.toLocaleDateString();
  }
}

/**
 * Format a Date object to a time string based on format setting.
 */
export function formatTimeInput(date: Date, timeFormat: TimeFormat = 'system'): string {
  const hours24 = date.getHours();
  const minutes = String(date.getMinutes()).padStart(2, '0');

  switch (timeFormat) {
    case '12h': {
      const hours12 = hours24 % 12 || 12;
      const ampm = hours24 < 12 ? 'AM' : 'PM';
      return `${hours12}:${minutes} ${ampm}`;
    }
    case '24h':
      return `${String(hours24).padStart(2, '0')}:${minutes}`;
    case 'system':
    default:
      return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
}

/**
 * Split a date string by common separators (/, ., -).
 */
function splitDateParts(value: string): string[] | null {
  for (const sep of ['/', '.', '-']) {
    const parts = value.split(sep);
    if (parts.length === 3) return parts;
  }
  return null;
}

function detectSystemDateFormat(): 'us' | 'eu' | 'iso' {
  const formatted = new Date(2000, 11, 31).toLocaleDateString();
  if (formatted.startsWith('12')) return 'us';
  if (formatted.startsWith('31')) return 'eu';
  return 'iso';
}

/**
 * Parse a date string based on format setting.
 * Returns null if parsing fails.
 * Supports common separators: / . -
 */
export function parseDateInput(value: string, dateFormat: DateFormat = 'system'): Date | null {
  if (!value) return null;

  const parts = splitDateParts(value);
  if (!parts) return null;

  const resolved = dateFormat === 'system' ? detectSystemDateFormat() : dateFormat;
  let day: number, month: number, year: number;

  switch (resolved) {
    case 'us':
      month = parseInt(parts[0], 10);
      day = parseInt(parts[1], 10);
      year = parseInt(parts[2], 10);
      break;
    case 'eu':
      day = parseInt(parts[0], 10);
      month = parseInt(parts[1], 10);
      year = parseInt(parts[2], 10);
      break;
    case 'iso':
      year = parseInt(parts[0], 10);
      month = parseInt(parts[1], 10);
      day = parseInt(parts[2], 10);
      break;
  }

  if (isNaN(day) || isNaN(month) || isNaN(year)) return null;
  if (month < 1 || month > 12) return null;
  if (day < 1 || day > 31) return null;
  if (year < 1900 || year > 2100) return null;

  return new Date(year, month - 1, day);
}

/**
 * Parse a time string. Handles both 12h (with AM/PM) and 24h formats.
 * Returns { hours, minutes } or null if parsing fails.
 */
export function parseTimeInput(value: string): { hours: number; minutes: number } | null {
  if (!value) return null;

  const trimmed = value.trim();

  const match = trimmed.match(/^(\d{1,2}):(\d{2})\s*(AM|PM)?$/i);
  if (!match) return null;

  let hours = parseInt(match[1], 10);
  const minutes = parseInt(match[2], 10);
  const ampm = match[3]?.toUpperCase();

  if (ampm === 'PM' && hours < 12) hours += 12;
  if (ampm === 'AM' && hours === 12) hours = 0;

  if (hours < 0 || hours > 23) return null;
  if (minutes < 0 || minutes > 59) return null;

  return { hours, minutes };
}

/**
 * Convert a Date object to datetime-local input value (ISO format).
 */
export function toDateTimeLocalValue(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  return `${year}-${month}-${day}T${hours}:${minutes}`;
}

/**
 * Apply time format setting to Intl.DateTimeFormatOptions.
 * This modifies the options object in place and returns it.
 */
export function applyTimeFormat(
  options: Intl.DateTimeFormatOptions,
  timeFormat: TimeFormat = 'system'
): Intl.DateTimeFormatOptions {
  if (timeFormat === '12h') {
    options.hour12 = true;
  } else if (timeFormat === '24h') {
    options.hour12 = false;
  }
  // 'system' leaves hour12 undefined, letting the browser decide
  return options;
}

/**
 * Parse a date string from the backend as UTC.
 * Handles ISO 8601 strings with or without timezone indicators.
 *
 * @param dateStr - Date string from backend (e.g., "2026-01-09T12:03:36.288768")
 * @returns Date object in local timezone
 */
export function parseUTCDate(dateStr: string | null | undefined): Date | null {
  if (!dateStr) return null;

  // If the string already has a timezone indicator, parse as-is
  if (dateStr.endsWith('Z') || /[+-]\d{2}:\d{2}$/.test(dateStr)) {
    return new Date(dateStr);
  }

  // Otherwise, append 'Z' to interpret as UTC
  return new Date(dateStr + 'Z');
}

/**
 * Format a UTC date string to a localized date/time string.
 *
 * @param dateStr - Date string from backend
 * @param options - Intl.DateTimeFormat options (defaults to showing date and time)
 * @returns Formatted date string in user's locale and timezone
 */
export function formatDate(
  dateStr: string | null | undefined,
  options?: Intl.DateTimeFormatOptions
): string {
  const date = parseUTCDate(dateStr);
  if (!date) return '';

  const defaultOptions: Intl.DateTimeFormatOptions = {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  };

  return date.toLocaleString(undefined, options ?? defaultOptions);
}

/**
 * Format a UTC date string to a localized date-only string.
 *
 * @param dateStr - Date string from backend
 * @param options - Intl.DateTimeFormat options
 * @returns Formatted date string in user's locale and timezone
 */
export function formatDateOnly(
  dateStr: string | null | undefined,
  options?: Intl.DateTimeFormatOptions
): string {
  const date = parseUTCDate(dateStr);
  if (!date) return '';

  const defaultOptions: Intl.DateTimeFormatOptions = {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  };

  return date.toLocaleDateString(undefined, options ?? defaultOptions);
}

/**
 * Format a UTC date string to a localized date/time string with time format support.
 *
 * @param dateStr - Date string from backend
 * @param timeFormat - Time format setting ('system', '12h', '24h')
 * @param options - Intl.DateTimeFormat options (defaults to showing date and time)
 * @returns Formatted date string in user's locale and timezone
 */
export function formatDateTime(
  dateStr: string | null | undefined,
  timeFormat: TimeFormat = 'system',
  options?: Intl.DateTimeFormatOptions
): string {
  const date = parseUTCDate(dateStr);
  if (!date) return '';

  const defaultOptions: Intl.DateTimeFormatOptions = {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  };

  const finalOptions = applyTimeFormat(options ?? defaultOptions, timeFormat);
  return date.toLocaleString(undefined, finalOptions);
}

/**
 * Format a Date object to a localized time string with time format support.
 *
 * @param date - Date object
 * @param timeFormat - Time format setting ('system', '12h', '24h')
 * @param options - Additional Intl.DateTimeFormat options
 * @returns Formatted time string
 */
export function formatTimeOnly(
  date: Date,
  timeFormat: TimeFormat = 'system',
  options?: Intl.DateTimeFormatOptions
): string {
  const defaultOptions: Intl.DateTimeFormatOptions = {
    hour: '2-digit',
    minute: '2-digit',
  };

  const finalOptions = applyTimeFormat({ ...defaultOptions, ...options }, timeFormat);
  return date.toLocaleTimeString([], finalOptions);
}

/**
 * Calculate and format an ETA based on remaining minutes from now.
 *
 * @param remainingMinutes - Minutes until completion
 * @param timeFormat - Time format setting ('system', '12h', '24h')
 * @param t - Optional i18n translation function
 * @returns Formatted ETA string (e.g., "3:45 PM", "Tomorrow 9:30 AM", "Wed 2:00 PM")
 */
export function formatETA(
  remainingMinutes: number,
  timeFormat: TimeFormat = 'system',
  t?: (key: string) => string
): string {
  const now = new Date();
  const eta = new Date(now.getTime() + remainingMinutes * 60 * 1000);

  const today = new Date(now);
  today.setHours(0, 0, 0, 0);
  const etaDay = new Date(eta);
  etaDay.setHours(0, 0, 0, 0);

  const timeOptions = applyTimeFormat({ hour: '2-digit', minute: '2-digit' }, timeFormat);
  const timeStr = eta.toLocaleTimeString([], timeOptions);
  const dayDiff = Math.floor((etaDay.getTime() - today.getTime()) / 86400000);

  if (dayDiff === 0) return timeStr;
  if (dayDiff === 1) return `${t?.('common.tomorrow') ?? 'Tomorrow'} ${timeStr}`;
  return `${eta.toLocaleDateString([], { weekday: 'short' })} ${timeStr}`;
}

/**
 * Format a duration in seconds to a human-readable string, with null handling.
 *
 * @param seconds - Duration in seconds, or null/undefined
 * @returns Formatted string (e.g., "2h 30m", "45m") or "--" if no value
 */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || seconds < 0) return '--';

  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);

  return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
}

type TranslateFunction = (key: string, options?: Record<string, unknown>) => string;

/**
 * Format a date string as a human-readable relative time expression.
 *
 * @param dateStr - UTC date string, or null
 * @param timeFormat - Time format preference ('12h', '24h', or 'system')
 * @param t - Optional translation function for i18n support
 * @returns Relative string (e.g., "5m ago", "in 2h", "3d ago") or formatted date if older than 7 days
 */
export function formatRelativeTime(
  dateStr: string | null,
  timeFormat: TimeFormat = 'system',
  t?: TranslateFunction
): string {
  if (!dateStr) return t?.('time.unknown') ?? '-';

  const date = parseUTCDate(dateStr);
  if (!date) return t?.('time.unknown') ?? '-';

  const now = new Date();
  const diffMs = date.getTime() - now.getTime();
  const isPast = diffMs < 0;
  const absDiffMs = Math.abs(diffMs);

  const minutes = Math.floor(absDiffMs / 60000);
  const hours = Math.floor(absDiffMs / 3600000);
  const days = Math.floor(absDiffMs / 86400000);

  // Less than 1 minute
  if (minutes < 1) {
    return isPast
      ? t?.('time.justNow') ?? 'Just now'
      : t?.('time.now') ?? 'Now';
  }

  // Less than 1 hour
  if (hours < 1) {
    return isPast
      ? t?.('time.minsAgo', { count: minutes }) ?? `${minutes}m ago`
      : t?.('time.inMins', { count: minutes }) ?? `in ${minutes}m`;
  }

  // Less than 1 day
  if (days < 1) {
    return isPast
      ? t?.('time.hoursAgo', { count: hours }) ?? `${hours}h ago`
      : t?.('time.inHours', { count: hours }) ?? `in ${hours}h`;
  }

  // Less than 7 days
  if (days < 7) {
    return isPast
      ? t?.('time.daysAgo', { count: days }) ?? `${days}d ago`
      : t?.('time.inDays', { count: days }) ?? `in ${days}d`;
  }

  // Older than 7 days
  return formatDateTime(dateStr, timeFormat);
}

/**
 * Format seconds as MM:SS for media/video player display.
 *
 * @param seconds - Total seconds
 * @returns Formatted string (e.g., "2:05", "0:30")
 */
export function formatMediaTime(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

/**
 * Format a duration given in hours to a human-readable string.
 *
 * @param hours - Duration in hours (e.g., 2.5)
 * @returns Formatted string (e.g., "2h 30m", "45m", "3h")
 */
export function formatDurationFromHours(hours: number): string {
  if (hours < 1) return `${Math.round(hours * 60)}m`;
  const h = Math.floor(hours);
  const m = Math.round((hours - h) * 60);
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}
