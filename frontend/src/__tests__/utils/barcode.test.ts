import { describe, it, expect } from 'vitest';
import { extractGtinFromManualEntry, extractGtinFromScan, isValidUpcEanBarcode } from '../../utils/barcode';

describe('isValidUpcEanBarcode', () => {
  it('accepts a valid UPC-A code', () => {
    expect(isValidUpcEanBarcode('036000291452')).toBe(true);
  });

  it('accepts a valid EAN-13 code', () => {
    expect(isValidUpcEanBarcode('4006381333931')).toBe(true);
    expect(isValidUpcEanBarcode('6938936716785')).toBe(true);
  });

  it('accepts a valid EAN-8 code', () => {
    expect(isValidUpcEanBarcode('96385074')).toBe(true);
  });

  it('accepts a valid GTIN-14 code', () => {
    // GTIN-14 uses the same mod-10 algorithm, just one digit longer than EAN-13.
    expect(isValidUpcEanBarcode('10012345678902')).toBe(true);
  });

  it('rejects a single digit', () => {
    // The exact false-positive symptom this validator was added to catch —
    // a stray camera misread returning a single digit as "the barcode".
    expect(isValidUpcEanBarcode('5')).toBe(false);
    expect(isValidUpcEanBarcode('0')).toBe(false);
  });

  it('rejects short non-barcode-length numbers', () => {
    expect(isValidUpcEanBarcode('123')).toBe(false);
    expect(isValidUpcEanBarcode('1234567')).toBe(false); // 7 digits, not a valid length
  });

  it('rejects a correct-length code with a wrong check digit', () => {
    expect(isValidUpcEanBarcode('4006381333930')).toBe(false);
    expect(isValidUpcEanBarcode('036000291453')).toBe(false);
  });

  it('rejects non-digit characters', () => {
    expect(isValidUpcEanBarcode('4006381333A31')).toBe(false);
    expect(isValidUpcEanBarcode('')).toBe(false);
    expect(isValidUpcEanBarcode('  ')).toBe(false);
  });

  it('rejects lengths outside the GTIN family (8/12/13/14)', () => {
    expect(isValidUpcEanBarcode('12345678901')).toBe(false); // 11 digits
    expect(isValidUpcEanBarcode('123456789012345')).toBe(false); // 15 digits
  });
});

describe('extractGtinFromScan', () => {
  it('passes through a plain digit string (UPC/EAN barcode decode)', () => {
    expect(extractGtinFromScan('6938936716785')).toBe('6938936716785');
  });

  it('extracts the GTIN from a GS1 Digital Link QR payload', () => {
    expect(extractGtinFromScan('https://id.gs1.org/01/06938936716785')).toBe('06938936716785');
  });

  it('extracts the GTIN when a batch/lot AI follows it in the path', () => {
    expect(extractGtinFromScan('https://id.gs1.org/01/06938936716785/10/LOT123')).toBe('06938936716785');
  });

  it('matches a bare Digital Link path with no scheme/host', () => {
    expect(extractGtinFromScan('id.gs1.org/01/06938936716785')).toBe('06938936716785');
  });

  it('returns null for a QR payload that is not a GS1 Digital Link', () => {
    expect(extractGtinFromScan('https://example.com/product/spool-123')).toBeNull();
  });

  it('returns null for non-digit, non-URL text', () => {
    expect(extractGtinFromScan('not a barcode')).toBeNull();
    expect(extractGtinFromScan('')).toBeNull();
  });
});

describe('extractGtinFromManualEntry', () => {
  it('passes through a plain digit string', () => {
    expect(extractGtinFromManualEntry('6938936716785')).toBe('6938936716785');
  });

  it('strips spaces typed for readability', () => {
    expect(extractGtinFromManualEntry('693 8936 716785')).toBe('6938936716785');
  });

  it('strips dashes typed for readability', () => {
    expect(extractGtinFromManualEntry('6938-9367-16785')).toBe('6938936716785');
  });

  it('extracts the GTIN from a pasted GS1 Digital Link URL', () => {
    expect(extractGtinFromManualEntry('https://id.gs1.org/01/06938936716785')).toBe('06938936716785');
  });

  it('returns null for text with no digits at all', () => {
    expect(extractGtinFromManualEntry('not a barcode')).toBeNull();
  });

  it('returns null for empty/whitespace-only input', () => {
    expect(extractGtinFromManualEntry('')).toBeNull();
    expect(extractGtinFromManualEntry('   ')).toBeNull();
  });
});
