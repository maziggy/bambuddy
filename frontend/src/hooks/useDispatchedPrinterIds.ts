/**
 * Subscribes to background-dispatch WebSocket events and returns the set of
 * printer IDs that currently have a queued or active dispatch job.
 *
 * Used by PrinterSelector to disable printers between the moment Bambuddy
 * accepts a dispatch (FTP upload, print command) and the moment the printer
 * itself reports PRINT_START. The backend already rejects double-sends with
 * HTTP 409, but the UI gap still let operators pick a printer the server would
 * refuse — surfaced by a corporate user running multi-operator farm shifts.
 *
 * Module-level state + useSyncExternalStore so every PrinterSelector instance
 * sees the same snapshot, and component mounts mid-batch pick up the latest
 * state without re-fetching.
 */
import { useSyncExternalStore } from 'react';

interface DispatchEventJob {
  printer_id?: unknown;
}

interface DispatchEventDetail {
  dispatched_jobs?: DispatchEventJob[];
  active_jobs?: DispatchEventJob[];
  total?: number;
  dispatched?: number;
  processing?: number;
}

const EMPTY: ReadonlySet<number> = new Set();
let currentSet: ReadonlySet<number> = EMPTY;
const subscribers = new Set<() => void>();
let attached = false;

function recompute(detail: DispatchEventDetail): ReadonlySet<number> {
  const next = new Set<number>();
  for (const job of detail.dispatched_jobs ?? []) {
    if (typeof job.printer_id === 'number') next.add(job.printer_id);
  }
  for (const job of detail.active_jobs ?? []) {
    if (typeof job.printer_id === 'number') next.add(job.printer_id);
  }
  return next;
}

function handleEvent(event: Event) {
  const detail = (event as CustomEvent<DispatchEventDetail>).detail ?? {};
  const next = recompute(detail);
  // Keep reference stable when content didn't change — useSyncExternalStore
  // compares snapshots via Object.is and re-renders on any new reference.
  if (next.size === currentSet.size && [...next].every((id) => currentSet.has(id))) {
    return;
  }
  currentSet = next;
  subscribers.forEach((cb) => cb());
}

function ensureAttached() {
  if (attached || typeof window === 'undefined') return;
  window.addEventListener('background-dispatch', handleEvent);
  attached = true;
}

const subscribe = (callback: () => void): (() => void) => {
  ensureAttached();
  subscribers.add(callback);
  return () => {
    subscribers.delete(callback);
  };
};

const getSnapshot = (): ReadonlySet<number> => currentSet;

export function useDispatchedPrinterIds(): ReadonlySet<number> {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

/** Test-only helper — resets the module-level singleton between tests. */
export function __resetDispatchedPrinterIdsForTests(): void {
  currentSet = EMPTY;
  subscribers.clear();
  if (attached && typeof window !== 'undefined') {
    window.removeEventListener('background-dispatch', handleEvent);
    attached = false;
  }
}
