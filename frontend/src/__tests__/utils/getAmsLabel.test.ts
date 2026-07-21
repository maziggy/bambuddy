import { describe, it, expect } from 'vitest';

import { getAmsLabel } from '../../utils/amsHelpers';

describe('getAmsLabel', () => {
  it('labels regular AMS units A/B/C by id', () => {
    expect(getAmsLabel(0, 4)).toBe('AMS-A');
    expect(getAmsLabel(1, 4)).toBe('AMS-B');
  });

  it('labels AMS-HT units (single tray, id >= 128)', () => {
    expect(getAmsLabel(128, 1)).toBe('HT-A');
    expect(getAmsLabel(129, 1)).toBe('HT-B');
  });

  it('labels the external spool', () => {
    expect(getAmsLabel(255, 1)).toBe('External');
  });

  it('labels the A2L AMS Lite (normalised unit id 6) distinctly', () => {
    // The backend normalises the A2L Lite's physical unit 16 -> 6; no regular
    // AMS uses id 6, so it never collides with the A/B/C range.
    expect(getAmsLabel(6, 4)).toBe('AMS Lite');
  });
});
