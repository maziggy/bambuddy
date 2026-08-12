/**
 * Regression tests for slot-order-dependent colour matching (#2804), frontend side.
 *
 * Three matchers in the interface fell back to "first spool within tolerance"
 * when no exact colour was loaded, so the winner depended on AMS slot order.
 * They now share `findNearestSimilar` with the scheduler's equivalent, so the
 * dialog cannot promise a spool the backend would not have picked.
 *
 * The worked example is the maintainer's: required `#3A7BD5`, a purple
 * `#6253AD` in tray 1 (40/40/40 off — admitted, distance ~69) and a
 * near-identical `#3B7AD2` in tray 3 (distance ~3).
 */

import { describe, expect, it } from 'vitest';
import { colorDistance, colorsAreSimilar, findNearestSimilar } from '../../utils/amsHelpers';

const REQUIRED = '#3A7BD5';
const NEAR = '3B7AD2FF';
const FAR_BUT_ADMITTED = '6253ADFF';

const tray = (globalTrayId: number, color: string) => ({ globalTrayId, color });
const pick = (candidates: { globalTrayId: number; color: string }[], required = REQUIRED) =>
  findNearestSimilar(candidates, required, (c) => c.color)?.globalTrayId;

describe('colorDistance', () => {
  it('is zero for the same colour', () => {
    expect(colorDistance('#3A7BD5', '3A7BD5FF')).toBe(0);
  });

  it('ignores alpha so a transparent filament matches itself', () => {
    expect(colorDistance('#76D9F4', '76D9F400')).toBe(0);
  });

  it('is euclidean rather than per-channel', () => {
    expect(colorDistance('#000000', '#282828')).toBeCloseTo(69.28, 1);
  });

  it('returns null for unusable input rather than a number', () => {
    expect(colorDistance(undefined, REQUIRED)).toBeNull();
    expect(colorDistance('', REQUIRED)).toBeNull();
    expect(colorDistance('#abc', REQUIRED)).toBeNull();
  });
});

describe('findNearestSimilar', () => {
  it('picks the closest admitted colour, not the first one', () => {
    expect(pick([tray(1, FAR_BUT_ADMITTED), tray(3, NEAR)])).toBe(3);
  });

  it('gives the same answer whichever order the trays arrive in', () => {
    expect(pick([tray(1, FAR_BUT_ADMITTED), tray(3, NEAR)])).toBe(3);
    expect(pick([tray(3, NEAR), tray(1, FAR_BUT_ADMITTED)])).toBe(3);
  });

  it('keeps the incoming order on a tie, so prefer-lowest still decides', () => {
    // Both 5 away — whichever the caller put first wins.
    expect(pick([tray(2, '3A7BD0FF'), tray(7, '3A7BDAFF')])).toBe(2);
    expect(pick([tray(7, '3A7BDAFF'), tray(2, '3A7BD0FF')])).toBe(7);
  });

  it('admits exactly what the tolerance admitted before', () => {
    // 41 off on one channel is outside the box and must not be ranked in.
    expect(colorsAreSimilar('3A7BFEFF', REQUIRED)).toBe(false);
    expect(pick([tray(5, '3A7BFEFF')])).toBeUndefined();
    // 40 off on all three is inside it, as it was.
    expect(colorsAreSimilar(FAR_BUT_ADMITTED, REQUIRED)).toBe(true);
    expect(pick([tray(5, FAR_BUT_ADMITTED)])).toBe(5);
  });

  it('returns undefined when nothing qualifies', () => {
    expect(pick([tray(1, 'FF0000FF')])).toBeUndefined();
  });

  it('skips candidates with no usable colour instead of throwing', () => {
    expect(pick([{ globalTrayId: 1, color: '' }, tray(3, NEAR)])).toBe(3);
  });
});
