/**
 * Date ranges for the Stats page's timeframe presets.
 *
 * Local calendar days, as YYYY-MM-DD, sent with the browser's zone (the API
 * client adds it as `tz`), which the backend reads them in (DB-C12). They used
 * to be UTC dates, so at 01:30 in Berlin "Today" asked for the previous day.
 */

export type TimeframePreset =
  | 'today'
  | 'this-week'
  | 'this-month'
  | 'last-7'
  | 'last-30'
  | 'last-90'
  | 'this-year'
  | 'all-time'
  | 'custom';

const pad = (n: number) => String(n).padStart(2, '0');

/** A local date as YYYY-MM-DD (toISOString would give the UTC one). */
export function localDate(dt: Date): string {
  return `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}`;
}

export function computeDateRange(preset: TimeframePreset): { dateFrom?: string; dateTo?: string } {
  const now = new Date();
  const y = now.getFullYear(), m = now.getMonth(), d = now.getDate();
  const todayStr = localDate(now);

  switch (preset) {
    case 'today':
      return { dateFrom: todayStr, dateTo: todayStr };
    case 'this-week': {
      const day = now.getDay();
      return { dateFrom: localDate(new Date(y, m, d - (day === 0 ? 6 : day - 1))), dateTo: todayStr };
    }
    case 'this-month':
      return { dateFrom: localDate(new Date(y, m, 1)), dateTo: todayStr };
    case 'last-7':
      return { dateFrom: localDate(new Date(y, m, d - 6)), dateTo: todayStr };
    case 'last-30':
      return { dateFrom: localDate(new Date(y, m, d - 29)), dateTo: todayStr };
    case 'last-90':
      return { dateFrom: localDate(new Date(y, m, d - 89)), dateTo: todayStr };
    case 'this-year':
      return { dateFrom: localDate(new Date(y, 0, 1)), dateTo: todayStr };
    case 'all-time':
      return { dateFrom: undefined, dateTo: undefined };
    case 'custom':
      return {};
  }
}
