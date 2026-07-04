// Standard GS1 check-digit lengths for retail barcodes: EAN-8, UPC-A/EAN-12,
// EAN-13, and GTIN-14. A camera misread (e.g. a false-positive decode from
// video noise) tends to produce something far shorter — a single digit is
// the most common symptom — so gating on both length and checksum rejects
// that before it ever reaches the lookup API.
const VALID_LENGTHS = [8, 12, 13, 14];

/**
 * Validate a UPC/EAN/GTIN barcode via its GS1 modulo-10 check digit.
 *
 * Algorithm: walk the digits right-to-left starting from the one just left
 * of the check digit, alternating weights 3/1, and the check digit must
 * equal `(10 - (sum % 10)) % 10`. The same algorithm applies unchanged
 * across EAN-8/UPC-A/EAN-13/GTIN-14 — only the length differs.
 */
export function isValidUpcEanBarcode(value: string): boolean {
  if (!/^\d+$/.test(value)) return false;
  if (!VALID_LENGTHS.includes(value.length)) return false;

  const checkDigit = Number(value[value.length - 1]);
  let sum = 0;
  let weight = 3;
  for (let i = value.length - 2; i >= 0; i--) {
    sum += Number(value[i]) * weight;
    weight = weight === 3 ? 1 : 3;
  }
  const calculated = (10 - (sum % 10)) % 10;
  return calculated === checkDigit;
}

// GS1 Digital Link QR codes encode the GTIN as the "01" application
// identifier in the URL path, e.g. "https://id.gs1.org/01/06938936716785"
// or "https://id.gs1.org/01/06938936716785/10/LOT123" when a batch/lot AI
// follows. Matches with or without a scheme/host prefix since some printers
// encode just the path.
const GS1_DIGITAL_LINK_GTIN_RE = /(?:^|\/)01\/(\d{8,14})(?:\/|$|\?)/;

/**
 * Pull a candidate GTIN out of a decoded QR/barcode payload.
 *
 * A plain barcode format (UPC/EAN) decodes straight to a digit string, which
 * is returned as-is. A QR code typically encodes a GS1 Digital Link URL
 * instead, so this also recognizes the "01" GTIN application identifier
 * embedded in the path. Returns null when neither shape matches — the
 * caller still must run the result through `isValidUpcEanBarcode` before
 * trusting it, since this only extracts a candidate, it doesn't validate one.
 */
export function extractGtinFromScan(text: string): string | null {
  const trimmed = text.trim();
  if (/^\d+$/.test(trimmed)) return trimmed;

  const match = trimmed.match(GS1_DIGITAL_LINK_GTIN_RE);
  return match ? match[1] : null;
}

/**
 * Like `extractGtinFromScan`, but for the Manual Entry field: also tolerates
 * spaces/dashes a user might type for readability (e.g. "6938-9367-16785"),
 * since a human typing digits isn't held to the same exactness as a decoder
 * output. A pasted GS1 Digital Link URL (e.g. from a phone's native QR
 * scanner) is still recognized the same way as the camera path.
 */
export function extractGtinFromManualEntry(text: string): string | null {
  const trimmed = text.trim();

  const gs1Match = trimmed.match(GS1_DIGITAL_LINK_GTIN_RE);
  if (gs1Match) return gs1Match[1];

  const digitsOnly = trimmed.replace(/[\s-]/g, '');
  return digitsOnly && /^\d+$/.test(digitsOnly) ? digitsOnly : null;
}
