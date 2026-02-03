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
  switch (dateFormat) {
    case 'us':
      return 'MM/DD/YYYY';
    case 'eu':
      return 'DD/MM/YYYY';
    case 'iso':
      return 'YYYY-MM-DD';
    case 'system':
    default: {
      // Try to detect system format
      const testDate = new Date(2000, 11, 31); // Dec 31, 2000
      const formatted = testDate.toLocaleDateString();
      if (formatted.startsWith('12')) return 'MM/DD/YYYY';
      if (formatted.startsWith('31')) return 'DD/MM/YYYY';
      return 'YYYY-MM-DD';
    }
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
 * Parse a date string based on format setting.
 * Returns null if parsing fails.
 */
export function parseDateInput(value: string, dateFormat: DateFormat = 'system'): Date | null {
  if (!value) return null;

  let day: number, month: number, year: number;

  try {
    switch (dateFormat) {
      case 'us': {
        // MM/DD/YYYY
        const parts = value.split('/');
        if (parts.length !== 3) return null;
        month = parseInt(parts[0], 10);
        day = parseInt(parts[1], 10);
        year = parseInt(parts[2], 10);
        break;
      }
      case 'eu': {
        // DD/MM/YYYY
        const parts = value.split('/');
        if (parts.length !== 3) return null;
        day = parseInt(parts[0], 10);
        month = parseInt(parts[1], 10);
        year = parseInt(parts[2], 10);
        break;
      }
      case 'iso': {
        // YYYY-MM-DD
        const parts = value.split('-');
        if (parts.length !== 3) return null;
        year = parseInt(parts[0], 10);
        month = parseInt(parts[1], 10);
        day = parseInt(parts[2], 10);
        break;
      }
      case 'system':
      default: {
        // Try common formats
        const date = new Date(value);
        if (!isNaN(date.getTime())) return date;
        // Try EU format
        const euParts = value.split('/');
        if (euParts.length === 3) {
          day = parseInt(euParts[0], 10);
          month = parseInt(euParts[1], 10);
          year = parseInt(euParts[2], 10);
          break;
        }
        return null;
      }
    }

    if (isNaN(day) || isNaN(month) || isNaN(year)) return null;
    if (month < 1 || month > 12) return null;
    if (day < 1 || day > 31) return null;
    if (year < 1900 || year > 2100) return null;

    return new Date(year, month - 1, day);
  } catch {
    return null;
  }
}

/**
 * Parse a time string. Handles both 12h (with AM/PM) and 24h formats.
 * Returns { hours, minutes } or null if parsing fails.
 */
export function parseTimeInput(value: string): { hours: number; minutes: number } | null {
  if (!value) return null;

  try {
    const trimmed = value.trim().toUpperCase();

    // Check for 12h format with AM/PM
    const ampmMatch = trimmed.match(/^(\d{1,2}):(\d{2})\s*(AM|PM)?$/i);
    if (ampmMatch) {
      let hours = parseInt(ampmMatch[1], 10);
      const minutes = parseInt(ampmMatch[2], 10);
      const ampm = ampmMatch[3]?.toUpperCase();

      if (ampm === 'PM' && hours < 12) hours += 12;
      if (ampm === 'AM' && hours === 12) hours = 0;

      if (hours < 0 || hours > 23) return null;
      if (minutes < 0 || minutes > 59) return null;

      return { hours, minutes };
    }

    // Try 24h format HH:MM
    const match24 = trimmed.match(/^(\d{1,2}):(\d{2})$/);
    if (match24) {
      const hours = parseInt(match24[1], 10);
      const minutes = parseInt(match24[2], 10);

      if (hours < 0 || hours > 23) return null;
      if (minutes < 0 || minutes > 59) return null;

      return { hours, minutes };
    }

    return null;
  } catch {
    return null;
  }
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
