/**
 * Which SpoolBuddy device a kiosk works with (#3504): not simply the first
 * registered one, which may be an old reader long offline.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { createElement, type ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { pickKioskDevice, useKioskDevice } from '../../hooks/useKioskDevice';
import { spoolbuddyApi, type SpoolBuddyDevice } from '../../api/client';

vi.mock('../../api/client', () => ({
  spoolbuddyApi: { getDevices: vi.fn() },
}));

const dev = (device_id: string, online: boolean) => ({ device_id, hostname: `${device_id}.local`, online }) as SpoolBuddyDevice;

describe('pickKioskDevice', () => {
  const oldOffline = dev('old', false);
  const kiosk = dev('kiosk', true);
  const other = dev('other', true);

  it('takes the online device over an offline one listed first', () => {
    expect(pickKioskDevice([oldOffline, kiosk], null)).toBe(kiosk);
  });

  it('takes the device chosen on this kiosk, even while it is offline', () => {
    expect(pickKioskDevice([kiosk, oldOffline], 'old')).toBe(oldOffline);
    expect(pickKioskDevice([kiosk, other], 'other')).toBe(other);
  });

  it('ignores a chosen device that is no longer registered', () => {
    expect(pickKioskDevice([oldOffline, kiosk], 'gone')).toBe(kiosk);
  });

  it('falls back to the first device when none is online, and to none without devices', () => {
    expect(pickKioskDevice([oldOffline, dev('b', false)], null)).toBe(oldOffline);
    expect(pickKioskDevice([], null)).toBeUndefined();
  });
});

describe('useKioskDevice', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(spoolbuddyApi.getDevices).mockResolvedValue([dev('a', true), dev('b', true)]);
  });

  // One client for both hooks, as on the page.
  const client = new QueryClient();
  function wrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client }, children);
  }

  it('remembers a choice and shares it with the other users on the page', async () => {
    const page = renderHook(() => useKioskDevice(), { wrapper });
    const layout = renderHook(() => useKioskDevice(), { wrapper });
    await waitFor(() => expect(page.result.current.device?.device_id).toBe('a'));

    act(() => page.result.current.chooseDevice('b'));

    expect(page.result.current.device?.device_id).toBe('b');
    await waitFor(() => expect(layout.result.current.device?.device_id).toBe('b'));
    expect(localStorage.setItem).toHaveBeenCalledWith('spoolbuddy.kioskDeviceId', 'b');
  });
});
