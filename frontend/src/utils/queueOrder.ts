/**
 * The order the scheduler will actually dispatch pending queue items in.
 *
 * The backend decides this in SQL (`print_scheduler.check_queue`):
 *
 *     ORDER BY been_jumped DESC,
 *              print_time_seconds ASC NULLS LAST,
 *              position, id
 *
 * with the first two keys only when Shortest-Job-First is on. It is one order
 * across the whole queue, not one per printer (#3200): a job pinned to a
 * printer and an "Any <model>" job both want that printer, and the one higher
 * in this order gets it. Every UI surface that claims to show queue order has
 * to reproduce it, and each one that reproduced it privately drifted: the
 * timeline sorted by `position` alone and so ignored Shortest-Job-First
 * entirely (#3043).
 */

interface OrderableQueueItem {
  printer_id?: number | null;
  target_model?: string | null;
  been_jumped?: boolean;
  print_time_seconds?: number | null;
  position: number;
  id?: number;
}

/**
 * Which swimlane an item is drawn in on the timeline: a named printer, a
 * printer model, or neither. Display only -- lanes do not decide dispatch
 * order, which is global (#3200).
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
 * Order two pending items the way the scheduler takes them, whichever printer
 * or model each is queued for.
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

  if (a.position !== b.position) return a.position - b.position;
  return (a.id ?? 0) - (b.id ?? 0);
}
