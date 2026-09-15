import { describe, it, expect } from 'vitest';
import { parseSpoolIdFromQr } from '../../utils/qrAssignTarget';

describe('parseSpoolIdFromQr', () => {
  it('extracts the spool id from a full deeplink URL (any host/scheme)', () => {
    expect(parseSpoolIdFromQr('https://bambuddy.example.com/inventory?spool=42')).toBe(42);
    expect(parseSpoolIdFromQr('http://raspi:8094/inventory?spool=1')).toBe(1);
    // extra params, order-independent
    expect(parseSpoolIdFromQr('https://host/inventory?foo=bar&spool=7&x=9')).toBe(7);
  });

  it('accepts a bare query fragment or bare number', () => {
    expect(parseSpoolIdFromQr('?spool=12')).toBe(12);
    expect(parseSpoolIdFromQr('spool=5')).toBe(5);
    expect(parseSpoolIdFromQr('216')).toBe(216);
    expect(parseSpoolIdFromQr('  216  ')).toBe(216);
  });

  it('rejects non-spool / invalid payloads', () => {
    expect(parseSpoolIdFromQr('')).toBeNull();
    expect(parseSpoolIdFromQr('https://example.com/')).toBeNull();
    expect(parseSpoolIdFromQr('https://host/inventory?spool=0')).toBeNull();
    expect(parseSpoolIdFromQr('https://host/inventory?spool=-3')).toBeNull();
    expect(parseSpoolIdFromQr('https://host/inventory?spool=1.5')).toBeNull();
    expect(parseSpoolIdFromQr('https://host/inventory?spool=abc')).toBeNull();
    expect(parseSpoolIdFromQr('random text')).toBeNull();
  });

  it('does not match a substring like "spoolman=3"', () => {
    expect(parseSpoolIdFromQr('https://host/x?spoolman=3')).toBeNull();
  });
});
