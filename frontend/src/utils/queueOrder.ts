/**
 * The order the scheduler will actually dispatch pending queue items in.
 *
 * The backend decides this in SQL (`print_scheduler.check_queue`):
 *
 *     ORDER BY printer_id, target_model,
 *              been_jumped DESC,
 *              print_time_seconds ASC NULLS LAST,
 *              position
 *
 * with the first two columns acting purely as a grouping — a row carries
 * either a `printer_id` or a `target_model`, never both — and the rest
 * deciding who goes first inside that group. Every UI surface that claims to
 * show queue order has to reproduce it, and each one that reproduced it
 * privately drifted: the timeline sorted by `position` alone and so ignored
 * Shortest-Job-First entirely (#3043). One comparator, three callers.
 */

interface OrderableQueueItem {
  printer_id?: number | null;
  target_model?: string | null;
  been_jumped?: boolean;
  print_time_seconds?: number | null;
  position: number;
}

/**
 * Which dispatch group an item belongs to: a named printer, a printer model,
 * or neither. Items only compete with others in their own group, so this is
 * both the timeline's swimlane and the outer sort key of a flat pending list.
 *
 * Returned as a string rather than a number because the group is a name, not
 * a magnitude. The flat list used to fold `target_model` down to
 * `-charCodeAt(0)`, which gave `X1C` and `X2D` (and `P1S` and `P1P`) the same
 * key and interleaved two lanes into one.
 */
export function queueLaneKey(item: OrderableQueueItem): string {
  if (item.printer_id != null) return `printer:${item.printer_id}`;
  if (item.target_model) return `model:${item.target_model}`;
  return 'unassigned';
}

/**
 * Order two items competing for the same printer or model.
 *
 * @param sjfEnabled the `queue_shortest_first` setting. When off, the
 *                   scheduler orders by position alone and so does this.
 */
export function compareQueueOrder(
  a: OrderableQueueItem,
  b: OrderableQueueItem,
  sjfEnabled: boolean,
): number {
  if (sjfEnabled) {
    // Starvation guard: an item something else was allowed to jump ahead of
    // goes first next time, whatever the print times say.
    const aJumped = a.been_jumped ? 1 : 0;
    const bJumped = b.been_jumped ? 1 : 0;
    if (aJumped !== bJumped) return bJumped - aJumped;

    // Shortest first, and an item whose duration we don't know yet sorts last
    // rather than winning by looking like a zero-second print (NULLS LAST).
    const aTime = a.print_time_seconds ?? Infinity;
    const bTime = b.print_time_seconds ?? Infinity;
    if (aTime !== bTime) return aTime - bTime;
  }

  return a.position - b.position;
}

/**
 * Order a flat list that spans several groups -- a pending list rather than a
 * per-lane one. Groups stay contiguous; within each, the scheduler's own order
 * applies.
 *
 * Groups themselves are ordered for reading, not to mirror the backend: named
 * printers by id, then model lanes by name, then unassigned. The backend's own
 * answer here is `ORDER BY printer_id` with a NULL in it, which SQLite sorts
 * first and PostgreSQL sorts last -- nothing worth reproducing.
 */
export function compareQueueOrderAcrossLanes(
  a: OrderableQueueItem,
  b: OrderableQueueItem,
  sjfEnabled: boolean,
): number {
  const aLane = queueLaneKey(a);
  const bLane = queueLaneKey(b);
  if (aLane !== bLane) {
    if (a.printer_id != null && b.printer_id != null) return a.printer_id - b.printer_id;
    const aRank = laneRank(a);
    const bRank = laneRank(b);
    if (aRank !== bRank) return aRank - bRank;
    return aLane < bLane ? -1 : 1;
  }
  return compareQueueOrder(a, b, sjfEnabled);
}

function laneRank(item: OrderableQueueItem): number {
  if (item.printer_id != null) return 0;
  if (item.target_model) return 1;
  return 2;
}
