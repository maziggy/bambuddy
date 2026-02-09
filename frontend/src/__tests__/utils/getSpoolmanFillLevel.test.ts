/**
 * Tests for getSpoolmanFillLevel helper function.
 * This function is defined in PrintersPage.tsx but tested here for isolation.
 * We replicate the logic to test it independently.
 */

import { describe, it, expect } from 'vitest';

// Replicate the function from PrintersPage.tsx for testing
interface LinkedSpoolInfo {
  id: number;
  remaining_weight: number | null;
  filament_weight: number | null;
}

function getSpoolmanFillLevel(
  linkedSpool: LinkedSpoolInfo | undefined
): number | null {
  if (!linkedSpool?.remaining_weight || !linkedSpool?.filament_weight
      || linkedSpool.filament_weight <= 0) return null;
  return Math.min(100, Math.round(
    (linkedSpool.remaining_weight / linkedSpool.filament_weight) * 100
  ));
}

describe('getSpoolmanFillLevel', () => {
  it('returns null for undefined spool', () => {
    expect(getSpoolmanFillLevel(undefined)).toBeNull();
  });

  it('returns null when remaining_weight is null', () => {
    expect(getSpoolmanFillLevel({ id: 1, remaining_weight: null, filament_weight: 1000 })).toBeNull();
  });

  it('returns null when filament_weight is null', () => {
    expect(getSpoolmanFillLevel({ id: 1, remaining_weight: 500, filament_weight: null })).toBeNull();
  });

  it('returns null when remaining_weight is 0', () => {
    expect(getSpoolmanFillLevel({ id: 1, remaining_weight: 0, filament_weight: 1000 })).toBeNull();
  });

  it('returns null when filament_weight is 0', () => {
    expect(getSpoolmanFillLevel({ id: 1, remaining_weight: 500, filament_weight: 0 })).toBeNull();
  });

  it('returns null when filament_weight is negative', () => {
    expect(getSpoolmanFillLevel({ id: 1, remaining_weight: 500, filament_weight: -100 })).toBeNull();
  });

  it('calculates correct percentage for half-full spool', () => {
    expect(getSpoolmanFillLevel({ id: 1, remaining_weight: 500, filament_weight: 1000 })).toBe(50);
  });

  it('calculates correct percentage for full spool', () => {
    expect(getSpoolmanFillLevel({ id: 1, remaining_weight: 1000, filament_weight: 1000 })).toBe(100);
  });

  it('calculates correct percentage for nearly empty spool', () => {
    expect(getSpoolmanFillLevel({ id: 1, remaining_weight: 50, filament_weight: 1000 })).toBe(5);
  });

  it('caps at 100% when remaining exceeds filament weight', () => {
    // This can happen if user manually sets remaining_weight higher
    expect(getSpoolmanFillLevel({ id: 1, remaining_weight: 1200, filament_weight: 1000 })).toBe(100);
  });

  it('rounds to nearest integer', () => {
    // 333/1000 = 33.3% -> 33%
    expect(getSpoolmanFillLevel({ id: 1, remaining_weight: 333, filament_weight: 1000 })).toBe(33);
    // 666/1000 = 66.6% -> 67%
    expect(getSpoolmanFillLevel({ id: 1, remaining_weight: 666, filament_weight: 1000 })).toBe(67);
  });

  it('handles small weights correctly', () => {
    expect(getSpoolmanFillLevel({ id: 1, remaining_weight: 1, filament_weight: 100 })).toBe(1);
  });
});
