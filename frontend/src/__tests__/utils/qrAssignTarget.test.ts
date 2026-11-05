import { describe, it, expect } from 'vitest';
import { parseSpoolQr, scannedSpoolId } from '../../utils/qrAssignTarget';

const bambuddy = (id: number) => ({ source: 'bambuddy', id });
const spoolman = (id: number) => ({ source: 'spoolman', id });

describe('parseSpoolQr', () => {
  it('reads a Bambuddy label deeplink (any host/scheme)', () => {
    expect(parseSpoolQr('https://bambuddy.example.com/inventory?spool=42')).toEqual(bambuddy(42));
    expect(parseSpoolQr('http://raspi:8094/inventory?spool=1')).toEqual(bambuddy(1));
    // extra params, order-independent
    expect(parseSpoolQr('https://host/inventory?foo=bar&spool=7&x=9')).toEqual(bambuddy(7));
    // a hash router carries the query in the fragment
    expect(parseSpoolQr('https://host/#/inventory?spool=8')).toEqual(bambuddy(8));
  });

  it('accepts a bare query fragment or bare number as a Bambuddy label', () => {
    expect(parseSpoolQr('?spool=12')).toEqual(bambuddy(12));
    expect(parseSpoolQr('spool=5')).toEqual(bambuddy(5));
    expect(parseSpoolQr('216')).toEqual(bambuddy(216));
    expect(parseSpoolQr('  216  ')).toEqual(bambuddy(216));
  });

  it("reads Spoolman's own labels as Spoolman spools", () => {
    expect(parseSpoolQr('web+spoolman:s-42')).toEqual(spoolman(42));
    expect(parseSpoolQr('WEB+SPOOLMAN:S-7')).toEqual(spoolman(7));
    expect(parseSpoolQr('https://spoolman.local:7912/spool/show/42')).toEqual(spoolman(42));
    expect(parseSpoolQr('https://spoolman.local/spool/show/42/')).toEqual(spoolman(42));
    // Spoolman served under a path prefix, or printed without a scheme
    expect(parseSpoolQr('https://home.local/spoolman/spool/show/9?x=1')).toEqual(spoolman(9));
    expect(parseSpoolQr('spoolman.local/spool/show/3')).toEqual(spoolman(3));
  });

  it('turns down a malformed Spoolman label instead of reading a spool= in it', () => {
    expect(parseSpoolQr('web+spoolman:s-abc?spool=5')).toBeNull();
    expect(parseSpoolQr('web+spoolman:s-12x')).toBeNull();
    expect(parseSpoolQr('web+spoolman:s-0')).toBeNull();
    expect(parseSpoolQr('web+spoolman:f-3')).toBeNull(); // a filament, not a spool
    expect(parseSpoolQr('web+spoolman:spool=5')).toBeNull();
    expect(parseSpoolQr('https://spoolman.local/spool/show/abc?spool=5')).toBeNull();
    expect(parseSpoolQr('https://spoolman.local/spool/show/?spool=5')).toBeNull();
    expect(parseSpoolQr('https://spoolman.local/spool/show/12/edit')).toBeNull();
  });

  it('rejects non-spool / invalid payloads', () => {
    expect(parseSpoolQr('')).toBeNull();
    expect(parseSpoolQr('https://example.com/')).toBeNull();
    expect(parseSpoolQr('https://host/inventory?spool=0')).toBeNull();
    expect(parseSpoolQr('https://host/inventory?spool=-3')).toBeNull();
    expect(parseSpoolQr('https://host/inventory?spool=1.5')).toBeNull();
    expect(parseSpoolQr('https://host/inventory?spool=abc')).toBeNull();
    expect(parseSpoolQr('https://host/inventory?spool=12abc')).toBeNull();
    expect(parseSpoolQr('99999999999999999999')).toBeNull();
    expect(parseSpoolQr('random text')).toBeNull();
  });

  it('does not match a substring like "spoolman=3"', () => {
    expect(parseSpoolQr('https://host/x?spoolman=3')).toBeNull();
  });
});

describe('scannedSpoolId', () => {
  it('takes a Bambuddy label in either inventory', () => {
    expect(scannedSpoolId({ source: 'bambuddy', id: 4 }, false)).toBe(4);
    expect(scannedSpoolId({ source: 'bambuddy', id: 4 }, true)).toBe(4);
  });

  it('takes a Spoolman label only when the inventory is Spoolman', () => {
    expect(scannedSpoolId({ source: 'spoolman', id: 4 }, true)).toBe(4);
    expect(scannedSpoolId({ source: 'spoolman', id: 4 }, false)).toBeNull();
  });
});
