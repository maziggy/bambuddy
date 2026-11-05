import type { PrintQueueItemCreate } from '../../api/client';
import type { VariantCandidate } from './VariantCandidates';

/**
 * Queue loop for "Separate jobs" when several sliced files are selected
 * (#3462): one queue item per file (per selected printer), in list order.
 *
 * It stops at the first refusal. That keeps the queue order intact and lets a
 * second submit continue with the remaining files without duplicating the ones
 * that are already queued (see `queuedFileIds`).
 */
export interface SeparateJobsResult {
  /** Successful POSTs. */
  itemsCreated: number;
  /** Files whose POSTs all went through. */
  queuedFileIds: number[];
  /** The refused file and the server's message, null when everything went through. */
  failure: { file: VariantCandidate; message: string } | null;
}

export async function queueSeparateJobs(args: {
  files: VariantCandidate[];
  plateByFile: Record<number, number | null>;
  /** [null] for "any printer of the model". */
  printerIds: (number | null)[];
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
  const total = files.length * printerIds.length;
  const queuedFileIds: number[] = [];
  let itemsCreated = 0;
  // Same single counter as applyAsapInsertion in index.tsx: position = items
  // inserted so far + 1, then advance by this call's quantity.
  let asapInserted = 0;

  for (const file of files) {
    const plateId = plateByFile[file.id] ?? null;
    for (let printerIndex = 0; printerIndex < printerIds.length; printerIndex++) {
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
          queuedFileIds,
          failure: { file, message: error instanceof Error ? error.message : String(error) },
        };
      }
      if (asap) asapInserted += quantity;
      itemsCreated++;
      onProgress?.(itemsCreated, total);
    }
    queuedFileIds.push(file.id);
  }
  return { itemsCreated, queuedFileIds, failure: null };
}
