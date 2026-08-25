/**
 * useLocationSensorColorPrefs stays live across already-mounted callers.
 *
 * Regression: InventoryPage and every SpoolLocationFooter used to read these
 * four colour preferences once, in a useState initializer — changing them in
 * Settings did nothing to an already-open Inventory page until a reload. This
 * hook re-reads on a save from anywhere, so InventoryPage and SettingsPage
 * (both of which now consume it) see the same value at the same time without
 * either page needing a reload.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import {
  useLocationSensorColorPrefs,
  saveLocationSensorAlertAboveColor,
  saveLocationSensorColorizeValues,
} from '../../utils/locationSensorDefaults';

describe('useLocationSensorColorPrefs', () => {
  beforeEach(() => {
    const store = new Map<string, string>();
    vi.mocked(window.localStorage.getItem).mockReset();
    vi.mocked(window.localStorage.setItem).mockReset();
    vi.mocked(window.localStorage.getItem).mockImplementation((key: string) => store.get(key) ?? null);
    vi.mocked(window.localStorage.setItem).mockImplementation((key: string, value: string) => {
      store.set(key, value);
    });
  });

  it('picks up a save from elsewhere without remounting', () => {
    const { result } = renderHook(() => useLocationSensorColorPrefs());

    expect(result.current.aboveColor).toBe('purple');

    act(() => {
      saveLocationSensorAlertAboveColor('blue');
    });

    expect(result.current.aboveColor).toBe('blue');
  });

  it('updates every mounted caller at once — the Inventory page and Settings both watching', () => {
    const inventoryPage = renderHook(() => useLocationSensorColorPrefs());
    const settingsPage = renderHook(() => useLocationSensorColorPrefs());

    act(() => {
      saveLocationSensorColorizeValues(false);
    });

    expect(inventoryPage.result.current.colorize).toBe(false);
    expect(settingsPage.result.current.colorize).toBe(false);
  });

  it('removes its event listener on unmount', () => {
    const addSpy = vi.spyOn(window, 'addEventListener');
    const removeSpy = vi.spyOn(window, 'removeEventListener');

    const { unmount } = renderHook(() => useLocationSensorColorPrefs());
    const [eventName] = addSpy.mock.calls.find(([name]) => name.startsWith('bambuddy:'))!;

    unmount();

    expect(removeSpy).toHaveBeenCalledWith(eventName, expect.any(Function));
    addSpy.mockRestore();
    removeSpy.mockRestore();
  });
});
