import { formatDateTime, parseUTCDate, type TimeFormat } from './date';

export interface RelativeTimeLabelKeys {
  asap: string;
  overdue: string;
  now: string;
  inLessThanMin: string;
  inMinutes: string;
  inHours: string;
}

export interface RelativeTimeOptions {
  t: (key: string, options?: Record<string, unknown>) => string;
  timeFormat?: TimeFormat;
  labels?: Partial<RelativeTimeLabelKeys>;
}

const DEFAULT_LABEL_KEYS: RelativeTimeLabelKeys = {
  asap: 'printerQueue.asap',
  overdue: 'queue.overdue',
  now: 'printerQueue.now',
  inLessThanMin: 'printerQueue.inLessThanMin',
  inMinutes: 'printerQueue.inMinutes',
  inHours: 'printerQueue.inHours',
};

export function formatRelativeTime(
  dateString: string | null | undefined,
  { t, timeFormat = 'system', labels = {} }: RelativeTimeOptions
): string {
  const labelKeys: RelativeTimeLabelKeys = { ...DEFAULT_LABEL_KEYS, ...labels };

  if (!dateString) return t(labelKeys.asap);
  const date = parseUTCDate(dateString);
  if (!date) return t(labelKeys.asap);

  const now = new Date();
  const diff = date.getTime() - now.getTime();

  if (diff < -60000) return t(labelKeys.overdue);
  if (diff < 0) return t(labelKeys.now);
  if (diff < 60000) return t(labelKeys.inLessThanMin);
  if (diff < 3600000) return t(labelKeys.inMinutes, { count: Math.round(diff / 60000) });
  if (diff < 86400000) return t(labelKeys.inHours, { count: Math.round(diff / 3600000) });

  return formatDateTime(dateString, timeFormat);
}
