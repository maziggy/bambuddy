import type { SpoolUsageRecord } from '../api/client';
import { parseUTCDate } from './date';

/** A timestamp in ms, or null when missing or unreadable (naive = UTC). */
export function timeOf(value: string | null | undefined): number | null {
  const at = value ? parseUTCDate(value)?.getTime() : undefined;
  return at === undefined || Number.isNaN(at) ? null : at;
}

/** The records of this spool, not of an earlier one that had its id: Spoolman
 *  can give a deleted spool's id to a new one, and the usage rows Bambuddy
 *  recorded under it stay. A row from before the spool was created is not its
 *  own (#3407). Built-in ids are never handed on. Same as
 *  stock_forecast.own_records in the backend. */
export function ownRecords(spool: { created_at: string | null }, records: SpoolUsageRecord[]): SpoolUsageRecord[] {
  const created = timeOf(spool.created_at);
  if (created === null) return records;
  return records.filter(r => (timeOf(r.created_at) ?? created) >= created);
}
