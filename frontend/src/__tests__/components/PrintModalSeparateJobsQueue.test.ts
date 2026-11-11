/**
 * Queue loop for "Separate jobs" when several sliced files are selected (#3462).
 */

import { describe, it, expect } from 'vitest';
import { fileHasQueuedPair, queueSeparateJobs, runAsapTail } from '../../components/PrintModal/separateJobs';
import type { VariantCandidate } from '../../components/PrintModal/VariantCandidates';
import type { PrintQueueItemCreate } from '../../api/client';

const file = (id: number): VariantCandidate => ({
  id,
  filename: `f${id}.gcode.3mf`,
  sliced_for_model: 'A1',
});

const buildItem = (f: VariantCandidate, _plate: number | null, printerId: number | null) =>
  ({ library_file_id: f.id, printer_id: printerId }) as PrintQueueItemCreate;

function setup(failOnFileId?: number) {
  const calls: PrintQueueItemCreate[] = [];
  const addToQueue = async (data: PrintQueueItemCreate) => {
    if (failOnFileId !== undefined && data.library_file_id === failOnFileId) {
      throw new Error('nope');
    }
    calls.push(data);
  };
  return { calls, addToQueue };
}

describe('queueSeparateJobs', () => {
  it('queues files outside, printers inside', async () => {
    const { calls, addToQueue } = setup();
    const result = await queueSeparateJobs({
      files: [file(11), file(12)],
      plateByFile: {},
      printerIds: [1, 2],
      quantity: 1,
      asap: false,
      buildItem,
      addToQueue,
    });
    expect(calls.map((c) => [c.library_file_id, c.printer_id])).toEqual([
      [11, 1],
      [11, 2],
      [12, 1],
      [12, 2],
    ]);
    expect(result).toEqual({
      itemsCreated: 4,
      asapInserted: 0,
      queuedFileIds: [11, 12],
      queuedPairs: ['11:1', '11:2', '12:1', '12:2'],
      // The test's addToQueue returns nothing, so there are no ids to keep.
      queuedItemIds: [],
      queuedBatchIds: [],
      failure: null,
    });
  });

  it('skips file and printer pairs a previous submit already queued', async () => {
    const { calls, addToQueue } = setup();
    const progress: Array<[number, number]> = [];
    const result = await queueSeparateJobs({
      files: [file(11), file(12)],
      plateByFile: {},
      printerIds: [1, 2],
      alreadyQueued: new Set(['11:1']),
      quantity: 1,
      asap: false,
      buildItem,
      addToQueue,
      onProgress: (c, t) => progress.push([c, t]),
    });
    expect(calls.map((c) => [c.library_file_id, c.printer_id])).toEqual([
      [11, 2],
      [12, 1],
      [12, 2],
    ]);
    expect(result.itemsCreated).toBe(3);
    expect(result.queuedFileIds).toEqual([11, 12]);
    expect(result.queuedPairs).toEqual(['11:2', '12:1', '12:2']);
    expect(progress).toEqual([[1, 3], [2, 3], [3, 3]]);
  });

  it('reports the pairs queued before a refusal', async () => {
    const calls: PrintQueueItemCreate[] = [];
    const result = await queueSeparateJobs({
      files: [file(11), file(12)],
      plateByFile: {},
      printerIds: [1, 2],
      quantity: 1,
      asap: false,
      buildItem,
      addToQueue: async (data) => {
        if (data.library_file_id === 11 && data.printer_id === 2) throw new Error('nope');
        calls.push(data);
      },
    });
    expect(result.queuedPairs).toEqual(['11:1']);
    expect(result.queuedFileIds).toEqual([]);
    expect(result.failure?.file.id).toBe(11);
  });

  it('passes the chosen plate, null when the file has none', async () => {
    const plates: Array<number | null> = [];
    const { addToQueue } = setup();
    await queueSeparateJobs({
      files: [file(11), file(12)],
      plateByFile: { 11: 3 },
      printerIds: [null],
      quantity: 1,
      asap: false,
      buildItem: (f, plateId, printerId) => {
        plates.push(plateId);
        return buildItem(f, plateId, printerId);
      },
      addToQueue,
    });
    expect(plates).toEqual([3, null]);
  });

  it('sets quantity only above 1', async () => {
    const two = setup();
    await queueSeparateJobs({
      files: [file(11)], plateByFile: {}, printerIds: [1], quantity: 2, asap: false,
      buildItem, addToQueue: two.addToQueue,
    });
    expect(two.calls[0].quantity).toBe(2);

    const one = setup();
    await queueSeparateJobs({
      files: [file(11)], plateByFile: {}, printerIds: [1], quantity: 1, asap: false,
      buildItem, addToQueue: one.addToQueue,
    });
    expect('quantity' in one.calls[0]).toBe(false);
  });

  it('numbers ASAP positions by quantity, and omits them otherwise', async () => {
    const asap = setup();
    await queueSeparateJobs({
      files: [file(11), file(12)], plateByFile: {}, printerIds: [1], quantity: 2, asap: true,
      buildItem, addToQueue: asap.addToQueue,
    });
    expect(asap.calls.map((c) => [c.insert_at_top, c.insert_position])).toEqual([
      [true, 1],
      [true, 3],
    ]);

    const plain = setup();
    await queueSeparateJobs({
      files: [file(11), file(12)], plateByFile: {}, printerIds: [1], quantity: 2, asap: false,
      buildItem, addToQueue: plain.addToQueue,
    });
    for (const c of plain.calls) {
      expect('insert_at_top' in c).toBe(false);
      expect('insert_position' in c).toBe(false);
    }
  });

  it('continues the ASAP positions of an earlier submit and reports the new count', async () => {
    // Two items went in at 1 and 2 before a refusal; the rest must follow
    // them, not jump ahead at 1.
    const { calls, addToQueue } = setup();
    const result = await queueSeparateJobs({
      files: [file(13)], plateByFile: {}, printerIds: [1], quantity: 1, asap: true,
      asapInsertedBefore: 2,
      buildItem, addToQueue,
    });
    expect(calls[0].insert_position).toBe(3);
    expect(result.asapInserted).toBe(3);
  });

  it('reports the ASAP count reached before a refusal', async () => {
    const { addToQueue } = setup(12);
    const result = await queueSeparateJobs({
      files: [file(11), file(12)], plateByFile: {}, printerIds: [1], quantity: 2, asap: true,
      buildItem, addToQueue,
    });
    expect(result.asapInserted).toBe(2);
  });

  it('stops at the first refusal and reports it', async () => {
    const { calls, addToQueue } = setup(12);
    const result = await queueSeparateJobs({
      files: [file(11), file(12), file(13)],
      plateByFile: {},
      printerIds: [1],
      quantity: 1,
      asap: false,
      buildItem,
      addToQueue,
    });
    expect(result.itemsCreated).toBe(1);
    expect(result.queuedFileIds).toEqual([11]);
    expect(result.failure?.file.id).toBe(12);
    expect(result.failure?.message).toBe('nope');
    expect(calls.map((c) => c.library_file_id)).toEqual([11]);
  });

  it('reports progress as current/total', async () => {
    const { addToQueue } = setup();
    const progress: Array<[number, number]> = [];
    await queueSeparateJobs({
      files: [file(11), file(12)],
      plateByFile: {},
      printerIds: [1, 2],
      quantity: 1,
      asap: false,
      buildItem,
      addToQueue,
      onProgress: (c, t) => progress.push([c, t]),
    });
    expect(progress).toEqual([[1, 4], [2, 4], [3, 4], [4, 4]]);
  });
});

describe('fileHasQueuedPair', () => {
  it('finds a file on any printer, and only that file', () => {
    const pairs = new Set(['11:2', '112:1']);
    expect(fileHasQueuedPair(pairs, 11)).toBe(true);
    expect(fileHasQueuedPair(pairs, 1)).toBe(false);
    expect(fileHasQueuedPair(pairs, 12)).toBe(false);
    expect(fileHasQueuedPair(new Set(['13:null']), 13)).toBe(true);
  });
});

describe('runAsapTail (#3471 review)', () => {
  const queue = [
    { id: 1, position: 1, batch_id: null },
    { id: 2, position: 2, batch_id: 40 },
    { id: 3, position: 3, batch_id: 40 },
    { id: 4, position: 4, batch_id: null },
  ];

  it("is the last position of the run's own items", () => {
    expect(runAsapTail(queue, [1], [])).toBe(1);
  });

  it('counts the copies of a batch the run created', () => {
    expect(runAsapTail(queue, [2], [40])).toBe(3);
  });

  it('is 0 when none of them is pending any more', () => {
    expect(runAsapTail(queue, [99], [77])).toBe(0);
  });
});

describe('queueSeparateJobs records what it created (#3471 review)', () => {
  it("keeps each POST's item id and batch", async () => {
    let next = 0;
    const result = await queueSeparateJobs({
      files: [file(11), file(12)],
      plateByFile: {},
      printerIds: [1],
      quantity: 2,
      asap: false,
      buildItem,
      addToQueue: async () => {
        next++;
        return { id: 100 + next, batch_id: 200 + next };
      },
    });
    expect(result.queuedItemIds).toEqual([101, 102]);
    expect(result.queuedBatchIds).toEqual([201, 202]);
  });
});
