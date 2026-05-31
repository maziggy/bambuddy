/**
 * Scan-to-location assignment (#1574).
 *
 * The user picks a target (an AMS slot or a storage location), then scans a
 * spool's label QR with the in-page camera. The scanned QR encodes a deeplink
 * such as `https://<host>/inventory?spool=42`; we extract the spool id from it
 * and assign that spool to the chosen target — no navigation, no edit modal.
 */

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
    }
  | {
      kind: 'storage';
      storageLocation: string;
    };

/**
 * Extract a positive integer spool id from a scanned QR payload.
 *
 * Accepts the deeplink form the label printer emits (`…/inventory?spool=42`,
 * any host/scheme), a bare query fragment (`?spool=42` / `spool=42`), or a bare
 * numeric string. Returns null for anything else — including 0, negatives, and
 * non-spool QRs — so the caller can keep scanning instead of acting on garbage.
 */
export function parseSpoolIdFromQr(raw: string): number | null {
  if (!raw) return null;
  const text = raw.trim();

  // Whole payload is just digits.
  if (/^\d+$/.test(text)) {
    const n = Number(text);
    return Number.isInteger(n) && n > 0 ? n : null;
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
    const m = text.match(/[?&]spool=(\d+)\b/) ?? text.match(/\bspool=(\d+)\b/);
    spoolParam = m ? m[1] : null;
  }

  if (spoolParam && /^\d+$/.test(spoolParam)) {
    const n = Number(spoolParam);
    return Number.isInteger(n) && n > 0 ? n : null;
  }
  return null;
}
