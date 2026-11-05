/**
 * Scan-to-location assignment (#1574).
 *
 * The user picks a target (an AMS slot or a storage location), then scans a
 * spool's label QR with the in-page camera. The scanned QR encodes a deeplink
 * such as `https://<host>/inventory?spool=42`; we extract the spool id from it
 * and assign that spool to the chosen target — no navigation, no edit modal.
 */

import type { SlotFilament } from './assignMismatch';

export type AssignTarget =
  | {
      kind: 'ams';
      printerId: number;
      printerName: string;
      amsId: number;
      trayId: number;
      isExternal: boolean;
      /** Human-readable slot label, e.g. "A2", "HT-A", "Ext". */
      label: string;
      /** What the printer reported in the slot when it was picked, for the material check. */
      filament: SlotFilament;
    }
  | {
      kind: 'storage';
      /** Structured Location id — the canonical storage model (`location_id`). */
      locationId: number;
      /** Location name, for the target chip and the success toast. */
      locationName: string;
    };

/**
 * The spool a scanned label names. A Bambuddy label (`…/inventory?spool=42`)
 * carries the id of the inventory it was printed from — built-in or Spoolman,
 * whichever Bambuddy was using. Spoolman's own labels carry a Spoolman id:
 * `web+spoolman:s-42`, or `…/spool/show/42` when Spoolman prints URLs.
 */
export type ScannedSpool = { source: 'bambuddy' | 'spoolman'; id: number };

function positiveId(text: string | null | undefined): number | null {
  if (!text || !/^\d+$/.test(text)) return null;
  const n = Number(text);
  return Number.isSafeInteger(n) && n > 0 ? n : null;
}

/**
 * Read a scanned QR payload. Returns null for anything that isn't a spool
 * label — including 0, negatives, and non-spool QRs — so the caller can keep
 * scanning instead of acting on garbage.
 *
 * A payload marked as Spoolman's (its scheme, or a /spool/show/ path) is read
 * as a Spoolman label or not at all: `web+spoolman:s-x?spool=5` is not
 * Bambuddy spool 5.
 *
 * A Bambuddy label is the deeplink the label printer emits (any host/scheme),
 * a bare query fragment (`?spool=42` / `spool=42`), or a bare numeric string.
 */
export function parseSpoolQr(raw: string): ScannedSpool | null {
  const text = (raw ?? '').trim();
  if (!text) return null;

  if (/^web\+spoolman:/i.test(text)) {
    const id = positiveId(text.match(/^web\+spoolman:s-(\d+)$/i)?.[1]);
    return id ? { source: 'spoolman', id } : null;
  }
  if (/\/spool\/show\//i.test(text)) {
    let path = text;
    try {
      path = new URL(text).pathname;
    } catch {
      /* not a full URL — match the path as written */
    }
    const id = positiveId(path.match(/\/spool\/show\/(\d+)\/?$/i)?.[1]);
    return id ? { source: 'spoolman', id } : null;
  }

  // Whole payload is just digits.
  if (/^\d+$/.test(text)) {
    const id = positiveId(text);
    return id ? { source: 'bambuddy', id } : null;
  }

  // Try a real URL first (handles any host/scheme + extra params).
  let spoolParam: string | null = null;
  try {
    spoolParam = new URL(text).searchParams.get('spool');
  } catch {
    /* not a full URL — handled by the regex fallback below */
  }
  // Fall back to a query-style match anywhere in the string — also covers a URL
  // that parsed but carried `spool` in a hash fragment (e.g. a hash router).
  if (!spoolParam) {
    const m = text.match(/(?:[?&]|\b)spool=(\d+)/);
    spoolParam = m ? m[1] : null;
  }
  const id = positiveId(spoolParam);
  return id ? { source: 'bambuddy', id } : null;
}

/**
 * The id to assign in the inventory in use, or null when the label is for an
 * inventory that isn't: a Spoolman label means nothing to the built-in one. A
 * Bambuddy label is taken in either mode, as it carries the id of whichever
 * inventory printed it.
 */
export function scannedSpoolId(spool: ScannedSpool, spoolmanMode: boolean): number | null {
  return spool.source === 'spoolman' && !spoolmanMode ? null : spool.id;
}
