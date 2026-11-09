import type { PrintQueueItemCreate } from '../../api/client';
import type { VariantCandidate } from './VariantCandidates';

/** What `queueSeparateJobs` got into the queue, and where it stopped. */
export interface SeparateJobsResult {
  /** Successful POSTs. */
  itemsCreated: number;
  /** ASAP items inserted so far, counting `asapInsertedBefore`. */
  asapInserted: number;
  /** Files now queued on every printer, counting pairs from `alreadyQueued`. */
  queuedFileIds: number[];
  /** `queuedPairKey`s of this call's successful POSTs. */
  queuedPairs: string[];
  /** The refused file and the server's message, null when everything went through. */
  failure: { file: VariantCandidate; message: string } | null;
}

/**
 * What a partly queued run leaves for the next dialog on the same files
 * (#3471 review): closing the dialog must not forget what went in.
 */
export interface SeparateJobsResume {
  /** `queuedPairKey`s already in the queue. */
  queuedPairs: string[];
  /** ASAP positions taken so far, so the rest goes in behind them. */
  asapInserted: number;
}

/** Reported after a refusal: the resume state, and which files are done. */
export interface SeparateJobsProgress extends SeparateJobsResume {
  /** Files queued on every printer; they leave the selection. */
  queuedFileIds: number[];
  /** Files still listed, in list order. */
  remainingFileIds: number[];
}

/** One queue item: a file on a printer (`null` = any printer of the model). */
export function queuedPairKey(fileId: number, printerId: number | null): string {
  return `${fileId}:${printerId}`;
}

/** Whether any printer has `fileId` among the `queuedPairKey`s in `pairs`. */
export function fileHasQueuedPair(pairs: Iterable<string>, fileId: number): boolean {
  const prefix = `${fileId}:`;
  for (const key of pairs) {
    if (key.startsWith(prefix)) return true;
  }
  return false;
}

/**
 * Queue loop for "Separate jobs" when several sliced files are selected
 * (#3462): one queue item per file (per selected printer), in list order.
 *
 * It stops at the first refusal. That keeps the queue order intact and lets a
 * second submit continue with the rest. The caller passes the pairs earlier
 * submits queued as `alreadyQueued`, and they are skipped: a file one printer
 * took and another refused stays in the list, and without the pairs a second
 * submit would queue it on the first printer again.
 */
export async function queueSeparateJobs(args: {
  files: VariantCandidate[];
  plateByFile: Record<number, number | null>;
  /** [null] for "any printer of the model". */
  printerIds: (number | null)[];
  /** `queuedPairKey`s queued by earlier submits of this dialog. */
  alreadyQueued?: ReadonlySet<string>;
  /** ASAP items earlier submits inserted; this call's positions follow them. */
  asapInsertedBefore?: number;
  quantity: number;
  asap: boolean;
  buildItem: (
    file: VariantCandidate,
    plateId: number | null,
    printerId: number | null,
    printerIndex: number,
  ) => PrintQueueItemCreate;
  addToQueue: (data: PrintQueueItemCreate) => Promise<unknown>;
  onProgress?: (current: number, total: number) => void;
}): Promise<SeparateJobsResult> {
  const { files, plateByFile, printerIds, quantity, asap, buildItem, addToQueue, onProgress } = args;
  const alreadyQueued = args.alreadyQueued ?? new Set<string>();
  const isDone = (fileId: number, printerId: number | null) => alreadyQueued.has(queuedPairKey(fileId, printerId));
  const total = files.reduce(
    (sum, file) => sum + printerIds.filter((printerId) => !isDone(file.id, printerId)).length,
    0,
  );
  const queuedFileIds: number[] = [];
  const queuedPairs: string[] = [];
  let itemsCreated = 0;
  // Same single counter as applyAsapInsertion in index.tsx: position = items
  // inserted so far + 1, then advance by this call's quantity. It starts at
  // what earlier submits inserted: the backend moves everything at or below a
  // position down, so restarting at 1 would put the rest ahead of them.
  let asapInserted = args.asapInsertedBefore ?? 0;

  for (const file of files) {
    const plateId = plateByFile[file.id] ?? null;
    for (let printerIndex = 0; printerIndex < printerIds.length; printerIndex++) {
      // printerIndex still counts every printer, so stagger groups stay put.
      if (isDone(file.id, printerIds[printerIndex])) continue;
      const data = buildItem(file, plateId, printerIds[printerIndex], printerIndex);
      if (quantity > 1) data.quantity = quantity;
      if (asap) {
        data.insert_at_top = true;
        data.insert_position = asapInserted + 1;
      }
      try {
        await addToQueue(data);
      } catch (error) {
        return {
          itemsCreated,
          asapInserted,
          queuedFileIds,
          queuedPairs,
          failure: { file, message: error instanceof Error ? error.message : String(error) },
        };
      }
      if (asap) asapInserted += quantity;
      queuedPairs.push(queuedPairKey(file.id, printerIds[printerIndex]));
      itemsCreated++;
      onProgress?.(itemsCreated, total);
    }
    queuedFileIds.push(file.id);
  }
  return { itemsCreated, asapInserted, queuedFileIds, queuedPairs, failure: null };
}
