/**
 * Tests for useSpoolBuddyState hook:
 * - Reducer handles all action types correctly
 * - Computed properties (remainingWeight, netWeight) work
 * - Window events dispatch state updates
 */

import { describe, it, expect, vi, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useSpoolBuddyState } from '../../hooks/useSpoolBuddyState';

function dispatchCustomEvent(name: string, detail: Record<string, unknown>) {
  window.dispatchEvent(new CustomEvent(name, { detail }));
}

describe('useSpoolBuddyState', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('starts with initial state', () => {
    const { result } = renderHook(() => useSpoolBuddyState());
    expect(result.current.weight).toBeNull();
    expect(result.current.weightStable).toBe(false);
    expect(result.current.rawAdc).toBeNull();
    expect(result.current.matchedSpool).toBeNull();
    expect(result.current.unknownTagUid).toBeNull();
    expect(result.current.deviceOnline).toBe(false);
    expect(result.current.deviceId).toBeNull();
    expect(result.current.remainingWeight).toBeNull();
    expect(result.current.netWeight).toBeNull();
  });

  it('WEIGHT_UPDATE sets weight, stable, rawAdc, deviceOnline=true', () => {
    const { result } = renderHook(() => useSpoolBuddyState());

    act(() => {
      dispatchCustomEvent('spoolbuddy-weight', {
        weight_grams: 250.5,
        stable: true,
        raw_adc: 12345,
        device_id: 'dev-1',
      });
    });

    expect(result.current.weight).toBe(250.5);
    expect(result.current.weightStable).toBe(true);
    expect(result.current.rawAdc).toBe(12345);
    expect(result.current.deviceOnline).toBe(true);
    expect(result.current.deviceId).toBe('dev-1');
  });

  it('WEIGHT_UPDATE handles nested data format', () => {
    const { result } = renderHook(() => useSpoolBuddyState());

    act(() => {
      dispatchCustomEvent('spoolbuddy-weight', {
        data: {
          weight_grams: 100,
          stable: false,
          raw_adc: 9999,
          device_id: 'dev-2',
        },
      });
    });

    expect(result.current.weight).toBe(100);
    expect(result.current.weightStable).toBe(false);
    expect(result.current.rawAdc).toBe(9999);
    expect(result.current.deviceId).toBe('dev-2');
  });

  it('TAG_MATCHED sets matchedSpool and clears unknownTagUid', () => {
    const { result } = renderHook(() => useSpoolBuddyState());

    // First set an unknown tag
    act(() => {
      dispatchCustomEvent('spoolbuddy-unknown-tag', {
        tag_uid: 'AA:BB:CC',
        device_id: 'dev-1',
      });
    });
    expect(result.current.unknownTagUid).toBe('AA:BB:CC');

    // Now match a spool
    act(() => {
      dispatchCustomEvent('spoolbuddy-tag-matched', {
        tag_uid: 'AA:BB:CC',
        device_id: 'dev-1',
        spool: {
          id: 42,
          material: 'PLA',
          subtype: 'Silk',
          color_name: 'Red',
          rgba: 'FF0000FF',
          brand: 'Bambu',
          label_weight: 1000,
          core_weight: 250,
          weight_used: 100,
        },
      });
    });

    expect(result.current.matchedSpool).not.toBeNull();
    expect(result.current.matchedSpool!.id).toBe(42);
    expect(result.current.matchedSpool!.material).toBe('PLA');
    expect(result.current.matchedSpool!.subtype).toBe('Silk');
    expect(result.current.matchedSpool!.color_name).toBe('Red');
    expect(result.current.matchedSpool!.brand).toBe('Bambu');
    expect(result.current.matchedSpool!.label_weight).toBe(1000);
    expect(result.current.matchedSpool!.core_weight).toBe(250);
    expect(result.current.matchedSpool!.weight_used).toBe(100);
    expect(result.current.unknownTagUid).toBeNull();
  });

  it('UNKNOWN_TAG sets unknownTagUid and clears matchedSpool', () => {
    const { result } = renderHook(() => useSpoolBuddyState());

    // First match a spool
    act(() => {
      dispatchCustomEvent('spoolbuddy-tag-matched', {
        tag_uid: 'AA:BB:CC',
        device_id: 'dev-1',
        spool: {
          id: 1,
          material: 'PLA',
          label_weight: 1000,
          core_weight: 250,
          weight_used: 0,
        },
      });
    });
    expect(result.current.matchedSpool).not.toBeNull();

    // Now detect unknown tag
    act(() => {
      dispatchCustomEvent('spoolbuddy-unknown-tag', {
        tag_uid: 'DD:EE:FF',
        device_id: 'dev-1',
      });
    });

    expect(result.current.unknownTagUid).toBe('DD:EE:FF');
    expect(result.current.matchedSpool).toBeNull();
  });

  it('TAG_REMOVED clears both matchedSpool and unknownTagUid', () => {
    const { result } = renderHook(() => useSpoolBuddyState());

    // Set a matched spool
    act(() => {
      dispatchCustomEvent('spoolbuddy-tag-matched', {
        tag_uid: 'AA:BB:CC',
        device_id: 'dev-1',
        spool: {
          id: 1,
          material: 'PLA',
          label_weight: 1000,
          core_weight: 250,
          weight_used: 0,
        },
      });
    });
    expect(result.current.matchedSpool).not.toBeNull();

    // Remove tag
    act(() => {
      dispatchCustomEvent('spoolbuddy-tag-removed', { device_id: 'dev-1' });
    });

    expect(result.current.matchedSpool).toBeNull();
    expect(result.current.unknownTagUid).toBeNull();
  });

  it('DEVICE_ONLINE sets deviceOnline=true', () => {
    const { result } = renderHook(() => useSpoolBuddyState());
    expect(result.current.deviceOnline).toBe(false);

    act(() => {
      dispatchCustomEvent('spoolbuddy-online', { device_id: 'dev-1' });
    });

    expect(result.current.deviceOnline).toBe(true);
    expect(result.current.deviceId).toBe('dev-1');
  });

  it('DEVICE_OFFLINE sets deviceOnline=false and clears weight/rawAdc', () => {
    const { result } = renderHook(() => useSpoolBuddyState());

    // First get some weight data
    act(() => {
      dispatchCustomEvent('spoolbuddy-weight', {
        weight_grams: 500,
        stable: true,
        raw_adc: 54321,
        device_id: 'dev-1',
      });
    });
    expect(result.current.weight).toBe(500);
    expect(result.current.rawAdc).toBe(54321);
    expect(result.current.deviceOnline).toBe(true);

    // Go offline
    act(() => {
      dispatchCustomEvent('spoolbuddy-offline', { device_id: 'dev-1' });
    });

    expect(result.current.deviceOnline).toBe(false);
    expect(result.current.weight).toBeNull();
    expect(result.current.weightStable).toBe(false);
    expect(result.current.rawAdc).toBeNull();
  });

  it('computes remainingWeight from matchedSpool', () => {
    const { result } = renderHook(() => useSpoolBuddyState());

    act(() => {
      dispatchCustomEvent('spoolbuddy-tag-matched', {
        tag_uid: 'AA:BB:CC',
        device_id: 'dev-1',
        spool: {
          id: 1,
          material: 'PLA',
          label_weight: 1000,
          core_weight: 250,
          weight_used: 300,
        },
      });
    });

    // remainingWeight = label_weight - weight_used = 1000 - 300 = 700
    expect(result.current.remainingWeight).toBe(700);
  });

  it('remainingWeight is clamped to 0 when weight_used exceeds label_weight', () => {
    const { result } = renderHook(() => useSpoolBuddyState());

    act(() => {
      dispatchCustomEvent('spoolbuddy-tag-matched', {
        tag_uid: 'AA:BB:CC',
        device_id: 'dev-1',
        spool: {
          id: 1,
          material: 'PLA',
          label_weight: 1000,
          core_weight: 250,
          weight_used: 1200,
        },
      });
    });

    expect(result.current.remainingWeight).toBe(0);
  });

  it('computes netWeight from weight and matchedSpool core_weight', () => {
    const { result } = renderHook(() => useSpoolBuddyState());

    // Set weight first
    act(() => {
      dispatchCustomEvent('spoolbuddy-weight', {
        weight_grams: 800,
        stable: true,
        raw_adc: 11111,
        device_id: 'dev-1',
      });
    });

    // Match a spool
    act(() => {
      dispatchCustomEvent('spoolbuddy-tag-matched', {
        tag_uid: 'AA:BB:CC',
        device_id: 'dev-1',
        spool: {
          id: 1,
          material: 'PLA',
          label_weight: 1000,
          core_weight: 250,
          weight_used: 0,
        },
      });
    });

    // netWeight = weight - core_weight = 800 - 250 = 550
    expect(result.current.netWeight).toBe(550);
  });

  it('netWeight is null when weight is null', () => {
    const { result } = renderHook(() => useSpoolBuddyState());

    act(() => {
      dispatchCustomEvent('spoolbuddy-tag-matched', {
        tag_uid: 'AA:BB:CC',
        device_id: 'dev-1',
        spool: {
          id: 1,
          material: 'PLA',
          label_weight: 1000,
          core_weight: 250,
          weight_used: 0,
        },
      });
    });

    expect(result.current.netWeight).toBeNull();
  });

  it('netWeight is null when no matchedSpool', () => {
    const { result } = renderHook(() => useSpoolBuddyState());

    act(() => {
      dispatchCustomEvent('spoolbuddy-weight', {
        weight_grams: 800,
        stable: true,
        raw_adc: 11111,
        device_id: 'dev-1',
      });
    });

    expect(result.current.netWeight).toBeNull();
  });

  it('cleans up event listeners on unmount', () => {
    const removeSpy = vi.spyOn(window, 'removeEventListener');
    const { unmount } = renderHook(() => useSpoolBuddyState());

    unmount();

    const removedEvents = removeSpy.mock.calls.map((c) => c[0]);
    expect(removedEvents).toContain('spoolbuddy-weight');
    expect(removedEvents).toContain('spoolbuddy-tag-matched');
    expect(removedEvents).toContain('spoolbuddy-unknown-tag');
    expect(removedEvents).toContain('spoolbuddy-tag-removed');
    expect(removedEvents).toContain('spoolbuddy-online');
    expect(removedEvents).toContain('spoolbuddy-offline');
  });
});
