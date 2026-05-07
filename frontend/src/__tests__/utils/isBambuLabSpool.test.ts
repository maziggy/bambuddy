/**
 * Tests for isBambuLabSpool helper.
 *
 * The function is permissive: any non-empty non-zero value of tray_uuid OR
 * tag_uid returns true. It does NOT validate hex-length or character set —
 * its job is solely to suppress assign/unassign actions on RFID-managed slots
 * whose state is owned by the printer firmware.
 */

import { describe, it, expect } from 'vitest';

import { isBambuLabSpool } from '../../utils/amsHelpers';

describe('isBambuLabSpool', () => {
  it('returns false for null', () => {
    expect(isBambuLabSpool(null)).toBe(false);
  });

  it('returns false for undefined', () => {
    expect(isBambuLabSpool(undefined)).toBe(false);
  });

  it('returns false for an empty object', () => {
    expect(isBambuLabSpool({})).toBe(false);
  });

  it('returns true for a valid 32-hex non-zero tray_uuid', () => {
    expect(
      isBambuLabSpool({ tray_uuid: '11223344556677880011223344556677' }),
    ).toBe(true);
  });

  it('returns false for the zero-string 32-char tray_uuid', () => {
    expect(
      isBambuLabSpool({ tray_uuid: '00000000000000000000000000000000' }),
    ).toBe(false);
  });

  it('returns true for a valid 16-hex non-zero tag_uid', () => {
    expect(isBambuLabSpool({ tag_uid: 'AABBCC1122334400' })).toBe(true);
  });

  it('returns false for the zero-string 16-char tag_uid', () => {
    expect(isBambuLabSpool({ tag_uid: '0000000000000000' })).toBe(false);
  });

  it('returns false when both fields are explicitly null', () => {
    expect(isBambuLabSpool({ tray_uuid: null, tag_uid: null })).toBe(false);
  });

  it('returns true when only tag_uid is set and tray_uuid is null', () => {
    expect(
      isBambuLabSpool({ tray_uuid: null, tag_uid: 'AABBCC1122334400' }),
    ).toBe(true);
  });

  it('returns true when only tray_uuid is set and tag_uid is null', () => {
    expect(
      isBambuLabSpool({
        tray_uuid: '11223344556677880011223344556677',
        tag_uid: null,
      }),
    ).toBe(true);
  });
});
