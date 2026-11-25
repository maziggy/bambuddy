/**
 * Coalesces per-id reads into one bulk request.
 *
 * A page that shows every printer asks for each printer's status, slot presets,
 * plugs and so on, one request per printer. On a print farm that was hundreds
 * of requests per page load, all queued behind each other on the server. A
 * batched read collects the ids asked for within a few milliseconds and sends
 * them as one request to a bulk endpoint, then hands each caller its own
 * answer. Callers, their query keys, intervals and invalidations are unchanged.
 *
 * A window that ends up with a single id uses the single-id request, so one
 * printer refreshed after an action, or a page that shows one printer, behaves
 * exactly as it did.
 */
// Long enough to catch every card's interval refetch firing in the same
// moment, short enough not to be noticed.
const WINDOW_MS = 10;
// Keeps the bulk request's URL short; ids beyond this go in another request.
const MAX_BATCH = 200;

// Windows still collecting ids or waiting for their requests; see batchesSettled
const active = new Set<Promise<unknown>>();

/**
 * Resolves once every batch already started has sent its requests and had
 * them answered. Tests wait on it between cases, so a request one case
 * started doesn't land on the next case's mocks.
 */
export function batchesSettled(): Promise<void> {
  return Promise.allSettled([...active]).then(() => undefined);
}

interface Waiter<T> {
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
}

/**
 * @param single the single-id request, used for a window with one id, for an
 *   id the bulk answer left out, and when the bulk request fails for any
 *   reason but a refusal (401/403).
 * @param many the bulk request; returns an answer for every id it knows.
 */
export function batched<T>(
  single: (id: number) => Promise<T>,
  many: (ids: number[]) => Promise<Map<number, T>>,
): (id: number) => Promise<T> {
  let pending = new Map<number, Waiter<T>[]>();
  let timer: ReturnType<typeof setTimeout> | null = null;

  const settleOne = (id: number, waiters: Waiter<T>[]) =>
    single(id).then(
      (value) => waiters.forEach((w) => w.resolve(value)),
      (error) => waiters.forEach((w) => w.reject(error)),
    );

  const flush = (): Promise<unknown> => {
    const batch = pending;
    pending = new Map();
    timer = null;
    const ids = [...batch.keys()];
    if (ids.length === 1) {
      return settleOne(ids[0], batch.get(ids[0])!);
    }
    const requests: Promise<unknown>[] = [];
    for (let i = 0; i < ids.length; i += MAX_BATCH) {
      const chunk = ids.slice(i, i + MAX_BATCH);
      requests.push(many(chunk).then(
        (answers) => {
          // An id the bulk answer leaves out (unknown, or outside the
          // caller's scope) is asked for on its own, which gives exactly the
          // single request's answer: its 404.
          const followUps: Promise<unknown>[] = [];
          for (const id of chunk) {
            const waiters = batch.get(id)!;
            if (answers.has(id)) {
              const value = answers.get(id) as T;
              waiters.forEach((w) => w.resolve(value));
            } else {
              followUps.push(settleOne(id, waiters));
            }
          }
          return Promise.all(followUps);
        },
        (error) => {
          // Not allowed to read these at all: the single requests would be
          // refused the same way, so don't send them.
          const status = (error as { status?: number } | null)?.status;
          if (status === 401 || status === 403) {
            for (const id of chunk) batch.get(id)!.forEach((w) => w.reject(error));
            return undefined;
          }
          // Anything else (a backend without the bulk route, which answers
          // 404, 405 or 422; a server error; a dropped connection): ask one by
          // one, as before, so one printer the server can't answer for fails
          // only its own card rather than every card on the page.
          return Promise.all(chunk.map((id) => settleOne(id, batch.get(id)!)));
        },
      ));
    }
    return Promise.all(requests);
  };

  return (id: number) =>
    new Promise<T>((resolve, reject) => {
      const waiters = pending.get(id);
      if (waiters) waiters.push({ resolve, reject });
      else pending.set(id, [{ resolve, reject }]);
      if (timer === null) {
        let done: () => void = () => {};
        const settled = new Promise<void>((resolve) => {
          done = resolve;
        });
        active.add(settled);
        timer = setTimeout(() => {
          void flush().finally(() => {
            active.delete(settled);
            done();
          });
        }, WINDOW_MS);
      }
    });
}

/** A JSON object keyed by id (keys arrive as strings) as a Map. */
export function byId<T>(record: Record<string, T>): Map<number, T> {
  return new Map(Object.entries(record).map(([key, value]) => [Number(key), value]));
}

/** A list of rows carrying their own id as a Map. */
export function byField<T>(rows: T[], field: (row: T) => number): Map<number, T> {
  return new Map(rows.map((row) => [field(row), row]));
}
