import { useEffect, useState } from 'react';
import {
  detectSystemDateFormat, getTimePlaceholder, formatDateInput, formatTimeInput, parseDateInput, parseTimeInput,
  type DateFormat, type TimeFormat,
} from '../../utils/date';

// Keep the text the user is editing, including incomplete input. The timestamp
// used by the submit handler always comes from these same two fields.
export function useScheduledStart(
  scheduled: boolean,
  initialValue: string,
  preferredDateFormat: DateFormat,
  preferredTimeFormat: TimeFormat,
) {
  // The editable fields use the syntax advertised by their placeholders,
  // including on systems whose date and time strings use localised numerals.
  const [draft, setDraft] = useState<{ date: string; time: string; format: DateFormat } | null>(null);
  // A settings query finishing mid-edit must not reinterpret 04/05 as May 4.
  const dateFormat = draft?.format ?? (preferredDateFormat === 'system' ? detectSystemDateFormat() : preferredDateFormat);
  const timeFormat = preferredTimeFormat === 'system'
    ? (getTimePlaceholder() === 'HH:MM' ? '24h' : '12h')
    : preferredTimeFormat;
  const initial = initialValue ? new Date(initialValue) : null;
  const dateText = draft?.date ?? (initial ? formatDateInput(initial, dateFormat) : '');
  const timeText = draft?.time ?? (initial ? formatTimeInput(initial, timeFormat) : '');
  const date = scheduled ? parseDateInput(dateText, dateFormat) : null;
  const time = scheduled ? parseTimeInput(timeText) : null;
  const timestamp = date && time ? date.setHours(time.hours, time.minutes, 0, 0) : NaN;
  // A local time skipped by a daylight-saving transition rolls forward in Date.
  const timeValid = !!time && (!date || (date.getHours() === time.hours && date.getMinutes() === time.minutes));
  const start = timeValid ? timestamp : NaN;
  const future = start > Date.now();
  const [clock, setClock] = useState(0);
  useEffect(() => {
    const remaining = start - Date.now();
    if (!future) return;
    // Long schedules exceed the browser's signed 32-bit timeout limit.
    const timer = window.setTimeout(() => setClock(tick => tick + 1), Math.min(remaining, 2_147_483_647));
    return () => window.clearTimeout(timer);
  }, [start, future, clock]);

  return {
    dateText, timeText, dateFormat, timeFormat, start, future,
    setFields: (date: string, time: string) => setDraft({ date, time, format: dateFormat }),
    dateValid: !!date, timeValid,
    past: Number.isFinite(start) && !future,
  };
}
