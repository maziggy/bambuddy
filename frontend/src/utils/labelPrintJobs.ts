import type { TFunction } from 'i18next';
import {
  ApiError,
  MAX_PRINT_LABELS_PER_REQUEST,
  MAX_PRINT_TAPE_MM,
  api,
  type DirectPrintLabelsRequest,
  type DirectPrintProjectLabelsRequest,
  type LabelPrintJob,
} from '../api/client';

// Printing on the label printer is a background job on the server (#3612): the
// print request answers 202 with a job id, and this module polls that job.
// It lives at module level, not in a component, so labels printed after the
// Add spool dialog closed still finish with their toast.

const POLL_INTERVAL_MS = 1000;
// Generous: a long job on a slow printer, but not forever. The clock starts when
// the job begins printing, so time spent waiting in the queue does not count.
const GIVE_UP_AFTER_MS = 30 * 60 * 1000;
// Polls that may fail in a row (network blip) before the job is given up on.
const MAX_POLL_FAILURES = 5;

// What is printed: spool labels of either inventory, or project labels (#3655).
type PrintSource = 'inventory' | 'spoolman' | 'projects';
type PrintBody = DirectPrintLabelsRequest | DirectPrintProjectLabelsRequest;

type ShowToast = (message: string, type?: 'success' | 'error' | 'warning' | 'info' | 'loading') => void;
export interface LabelJobDeps {
  t: TFunction;
  showToast: ShowToast;
}

/** Whether `count` labels of `lengthMm` each fit the server's caps for one print request. */
export function printCapsProblem(count: number, lengthMm: number): 'tooMany' | 'tooLong' | null {
  if (count > MAX_PRINT_LABELS_PER_REQUEST) return 'tooMany';
  if (count * lengthMm > MAX_PRINT_TAPE_MM) return 'tooLong';
  return null;
}

/** Splits spool ids into pieces that each fit both caps for labels of `lengthMm`. */
export function chunkForPrint(ids: number[], lengthMm: number): number[][] {
  const perChunk = Math.max(1, Math.min(MAX_PRINT_LABELS_PER_REQUEST, Math.floor(MAX_PRINT_TAPE_MM / lengthMm)));
  const chunks: number[][] = [];
  for (let i = 0; i < ids.length; i += perChunk) chunks.push(ids.slice(i, i + perChunk));
  return chunks;
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** A print job that did not finish; `printed` is how many labels came out before it failed. */
export class LabelJobError extends Error {
  printed: number;
  constructor(message: string, printed: number) {
    super(message);
    this.name = 'LabelJobError';
    this.printed = printed;
  }
}

/**
 * Polls a job once a second until it is done. Throws a `LabelJobError` with a
 * user-facing message when it failed, vanished or took too long (the give-up
 * clock starts when the job first reports "printing").
 */
export async function waitForLabelJob(jobId: string, t: TFunction): Promise<LabelPrintJob> {
  let deadline: number | null = null;
  let printed = 0;
  let failures = 0;
  for (;;) {
    let job: LabelPrintJob;
    try {
      job = await api.getLabelPrintJob(jobId);
      failures = 0;
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        throw new LabelJobError(
          t('inventory.labels.direct.jobUnknown', 'Bambuddy no longer knows this print job. Check whether the labels printed before trying again.'),
          printed,
        );
      }
      if (++failures >= MAX_POLL_FAILURES) throw new LabelJobError(err instanceof Error ? err.message : String(err), printed);
      await sleep(POLL_INTERVAL_MS);
      continue;
    }
    printed = job.printed;
    if (job.state === 'done') return job;
    if (job.state === 'failed') {
      throw new LabelJobError(job.error || t('inventory.labels.direct.jobFailed', 'The labels were not printed.'), job.printed);
    }
    if (job.state === 'printing' && deadline === null) deadline = Date.now() + GIVE_UP_AFTER_MS;
    if (deadline !== null && Date.now() >= deadline) {
      throw new LabelJobError(
        t('inventory.labels.direct.jobTimedOut', 'Stopped waiting for the label printer after {{minutes}} minutes. Check the printer; the labels may still print.', {
          minutes: GIVE_UP_AFTER_MS / 60000,
        }),
        printed,
      );
    }
    await sleep(POLL_INTERVAL_MS);
  }
}

// The print POST, with a reverse proxy's bare 504 (no JSON body) made readable.
async function postJob(source: PrintSource, body: PrintBody, t: TFunction) {
  try {
    return await api.printLabelsToPrinter(source, body);
  } catch (err) {
    if (err instanceof ApiError && err.status === 504 && err.code === null && err.message === 'HTTP 504') {
      throw new Error(
        t(
          'inventory.labels.direct.gatewayTimeout',
          'The request timed out on the way to Bambuddy; check whether the label printed before trying again.',
        ),
      );
    }
    throw err;
  }
}

/**
 * Sends one print request. Resolves once the server has accepted the job and
 * throws if it refused; the returned promise settles with the finished job.
 * Nothing is toasted here.
 */
async function submit(source: PrintSource, body: PrintBody, t: TFunction) {
  const accepted = await postJob(source, body, t);
  return { total: accepted.total, finished: waitForLabelJob(accepted.job_id, t) };
}

const printedText = (t: TFunction, count: number) =>
  t('inventory.labels.direct.printed', 'Printed {{count}} labels', { count });

/**
 * Print from the labels dialog: resolves when the job is accepted (throws when
 * refused, for the caller's error toast), then follows it with toasts.
 */
export async function printLabelsWithToasts(
  source: PrintSource,
  body: PrintBody,
  { t, showToast }: LabelJobDeps,
): Promise<void> {
  const { total, finished } = await submit(source, body, t);
  showToast(t('inventory.labels.direct.printing', 'Printing {{count}} labels…', { count: total }), 'info');
  finished.then(
    (job) => showToast(printedText(t, job.printed), 'success'),
    (err: unknown) =>
      showToast(
        t('inventory.labels.direct.error', 'Could not print: {{msg}}', { msg: err instanceof Error ? err.message : String(err) }),
        'error',
      ),
  );
}

/**
 * Labels for spools just saved: split into requests within the caps, printed
 * one after the other, one toast at the end. A failure only warns: the spools
 * are saved. Fire and forget; it keeps running after the dialog closed.
 */
export function printLabelsAfterSave(
  source: 'inventory' | 'spoolman',
  requests: DirectPrintLabelsRequest[],
  { t, showToast }: LabelJobDeps,
): void {
  void (async () => {
    let printed = 0; // labels of the chunks that finished before
    const total = requests.reduce((sum, body) => sum + body.spool_ids.length, 0);
    try {
      for (const body of requests) {
        const { finished } = await submit(source, body, t);
        printed += (await finished).printed;
      }
      showToast(printedText(t, printed), 'success');
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      const done = printed + (err instanceof LabelJobError ? err.printed : 0);
      showToast(
        done > 0
          ? t(
              'inventory.labelPrintFailedPartial',
              'Spool saved, but the label printing stopped after {{printed}} of {{total}} labels: {{msg}}',
              { printed: done, total, msg },
            )
          : t('inventory.labelPrintFailed', 'Spool saved, but the label could not be printed: {{msg}}', { msg }),
        'error',
      );
    }
  })();
}
