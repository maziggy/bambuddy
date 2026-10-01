/**
 * The comparator every "queue order" surface shares (#3043).
 *
 * It has to agree with the scheduler's ORDER BY in
 * `print_scheduler.check_queue`, because a timeline or a pending list that
 * disagrees is telling the user the queue will run in an order it won't.
 */

import { describe, it, expect } from 'vitest';
import { compareQueueOrder, queueLaneKey } from '../../utils/queueOrder';

interface Item {
  id: number;
  printer_id?: number | null;
  target_model?: string | null;
  been_jumped?: boolean;
  print_time_seconds?: number | null;
  position: number;
}

const item = (id: number, over: Partial<Item> = {}): Item => ({
  id,
  printer_id: 1,
  position: id,
  print_time_seconds: 3600,
  ...over,
});

const idsInOrder = (items: Item[], sjf: boolean) =>
  [...items].sort((a, b) => compareQueueOrder(a, b, sjf)).map(i => i.id);

describe('compareQueueOrder', () => {
  it('leaves the queue in position order when SJF is off', () => {
    const items = [
      item(1, { position: 3, print_time_seconds: 60 }),
      item(2, { position: 1, print_time_seconds: 99999 }),
      item(3, { position: 2, print_time_seconds: 600 }),
    ];
    expect(idsInOrder(items, false)).toEqual([2, 3, 1]);
  });

  it('puts the shortest print first when SJF is on', () => {
    const items = [
      item(1, { position: 1, print_time_seconds: 7200 }),
      item(2, { position: 2, print_time_seconds: 600 }),
      item(3, { position: 3, print_time_seconds: 3600 }),
    ];
    expect(idsInOrder(items, true)).toEqual([2, 3, 1]);
  });

  it('sorts an item with no known duration last, not first', () => {
    // Treating a missing duration as 0 would make an unsliced job win every
    // comparison it entered -- the SQL says NULLS LAST for the same reason.
    const items = [
      item(1, { position: 1, print_time_seconds: null }),
      item(2, { position: 2, print_time_seconds: 7200 }),
      item(3, { position: 3, print_time_seconds: undefined }),
    ];
    expect(idsInOrder(items, true)).toEqual([2, 1, 3]);
  });

  it('promotes a jumped item ahead of a shorter one', () => {
    // Starvation guard: a long print that keeps being overtaken has to run
    // eventually, so once it has been jumped it outranks print time.
    const items = [
      item(1, { position: 1, print_time_seconds: 36000, been_jumped: true }),
      item(2, { position: 2, print_time_seconds: 300 }),
    ];
    expect(idsInOrder(items, true)).toEqual([1, 2]);
  });

  it('falls back to position when two prints are the same length', () => {
    const items = [
      item(1, { position: 5, print_time_seconds: 3600 }),
      item(2, { position: 2, print_time_seconds: 3600 }),
    ];
    expect(idsInOrder(items, true)).toEqual([2, 1]);
  });
});

describe('queueLaneKey', () => {
  it('names a lane after the printer, the model, or neither', () => {
    expect(queueLaneKey(item(1, { printer_id: 4 }))).toBe('printer:4');
    expect(queueLaneKey(item(2, { printer_id: null, target_model: 'X1C' }))).toBe('model:X1C');
    expect(queueLaneKey(item(3, { printer_id: null, target_model: null }))).toBe('unassigned');
  });

  it('keeps two models apart that share a first letter', () => {
    // The flat pending list used to fold the model down to -charCodeAt(0),
    // which gave X1C and X2D the same key and interleaved the two lanes.
    expect(queueLaneKey(item(1, { printer_id: null, target_model: 'X1C' }))).not.toBe(
      queueLaneKey(item(2, { printer_id: null, target_model: 'X2D' })),
    );
  });
});

describe('compareQueueOrder across printers and models (#3200)', () => {
  it('orders a pinned job and an Any-model job by position, not by lane', () => {
    const items = [
      item(1, { printer_id: null, target_model: 'P2S', position: 2 }),
      item(2, { printer_id: null, target_model: 'P2S', position: 3 }),
      item(3, { printer_id: 1, position: 1 }),
    ];
    expect(idsInOrder(items, false)).toEqual([3, 1, 2]);
  });

  it('puts an Any-model job above a pinned one when it is higher in the queue', () => {
    const items = [
      item(1, { printer_id: 1, position: 2 }),
      item(2, { printer_id: null, target_model: 'P2S', position: 1 }),
    ];
    expect(idsInOrder(items, false)).toEqual([2, 1]);
  });

  it('applies Shortest-Job-First across lanes', () => {
    const items = [
      item(1, { printer_id: 2, position: 1, print_time_seconds: 7200 }),
      item(2, { printer_id: 1, position: 2, print_time_seconds: 7200 }),
      item(3, { printer_id: null, target_model: 'X1C', position: 3, print_time_seconds: 600 }),
      item(4, { printer_id: 1, position: 4, print_time_seconds: 300 }),
    ];
    expect(idsInOrder(items, true)).toEqual([4, 3, 1, 2]);
  });

  it('breaks a position tie by id, like the scheduler', () => {
    const items = [
      item(7, { printer_id: null, target_model: 'P2S', position: 1 }),
      item(5, { printer_id: 1, position: 1 }),
    ];
    expect(idsInOrder(items, false)).toEqual([5, 7]);
  });
});
