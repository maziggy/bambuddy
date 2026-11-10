import { useCallback, useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { spoolbuddyApi, type SpoolBuddyDevice } from '../api/client';

/**
 * Which SpoolBuddy device this kiosk works with (#3504).
 *
 * The kiosk page doesn't know which device it runs on: its URL carries an API
 * key, which several devices may share, and the device list is ordered by
 * registration. Taking the first device sent writes to a reader somewhere
 * else, often an old one that went offline long ago.
 */

const STORAGE_KEY = 'spoolbuddy.kioskDeviceId';
// Tells the other users of the hook on this page that the choice changed.
const CHANGE_EVENT = 'spoolbuddy-kiosk-device-changed';

// The choice when the browser won't store it, so this page still shares it.
let unstoredId: string | null = null;

function readStoredId(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY) ?? unstoredId;
  } catch {
    return unstoredId;
  }
}

/**
 * The device chosen on this kiosk while it is still registered, else the
 * only online device, else the first online one, else the first registered.
 */
export function pickKioskDevice(devices: SpoolBuddyDevice[], storedId: string | null): SpoolBuddyDevice | undefined {
  const stored = storedId ? devices.find((d) => d.device_id === storedId) : undefined;
  return stored ?? devices.find((d) => d.online) ?? devices[0];
}

export function useKioskDevice(refetchInterval = 10000) {
  const { data: devices = [] } = useQuery({
    queryKey: ['spoolbuddy-devices'],
    queryFn: () => spoolbuddyApi.getDevices(),
    refetchInterval,
  });
  const [storedId, setStoredId] = useState<string | null>(readStoredId);

  useEffect(() => {
    const sync = () => setStoredId(readStoredId());
    window.addEventListener(CHANGE_EVENT, sync);
    return () => window.removeEventListener(CHANGE_EVENT, sync);
  }, []);

  const chooseDevice = useCallback((deviceId: string) => {
    unstoredId = deviceId;
    try {
      localStorage.setItem(STORAGE_KEY, deviceId);
    } catch {
      // Not stored: the choice lasts until the page reloads.
    }
    setStoredId(deviceId);
    window.dispatchEvent(new Event(CHANGE_EVENT));
  }, []);

  const device = useMemo(() => pickKioskDevice(devices, storedId), [devices, storedId]);
  return { device, devices, chooseDevice };
}
