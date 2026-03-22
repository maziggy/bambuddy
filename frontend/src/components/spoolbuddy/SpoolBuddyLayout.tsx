import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Outlet } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { SpoolBuddyTopBar } from './SpoolBuddyTopBar';
import { SpoolBuddyBottomNav } from './SpoolBuddyBottomNav';
import { SpoolBuddyStatusBar } from './SpoolBuddyStatusBar';
import { useSpoolBuddyState } from '../../hooks/useSpoolBuddyState';
import { api, spoolbuddyApi } from '../../api/client';
import { VirtualKeyboard } from '../VirtualKeyboard';

export function SpoolBuddyLayout() {
  const [selectedPrinterId, setSelectedPrinterId] = useState<number | null>(null);
  const [alert, setAlert] = useState<{ type: 'warning' | 'error' | 'info'; message: string } | null>(null);
  const [blanked, setBlanked] = useState(false);
  const [displayBrightness, setDisplayBrightness] = useState(100);
  const [displayBlankTimeout, setDisplayBlankTimeout] = useState(0);
  const lastActivityRef = useRef(Date.now());
  const { i18n } = useTranslation();
  const sbState = useSpoolBuddyState();

  // Sync language from backend settings (kiosk has its own browser with empty localStorage)
  const { data: appSettings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
  });
  useEffect(() => {
    if (appSettings?.language && appSettings.language !== i18n.language) {
      i18n.changeLanguage(appSettings.language);
    }
  }, [appSettings?.language, i18n]);

  // Query device data to initialize display settings on any page
  const { data: devices = [] } = useQuery({
    queryKey: ['spoolbuddy-devices'],
    queryFn: () => spoolbuddyApi.getDevices(),
    refetchInterval: 30000,
  });
  const device = devices[0];
  const effectiveDeviceOnline = sbState.deviceOnline || Boolean(device?.online);
  const sbStateForUi = useMemo(
    () => ({ ...sbState, deviceOnline: effectiveDeviceOnline }),
    [sbState, effectiveDeviceOnline]
  );

  // Sync display settings from device on initial load
  const initializedRef = useRef(false);
  useEffect(() => {
    if (device && !initializedRef.current) {
      setDisplayBrightness(device.display_brightness);
      setDisplayBlankTimeout(device.display_blank_timeout);
      initializedRef.current = true;
    }
  }, [device]);

  // Force dark theme on mount, restore on unmount
  useEffect(() => {
    const root = document.documentElement;
    const hadDark = root.classList.contains('dark');
    root.classList.add('dark');
    return () => {
      if (!hadDark) root.classList.remove('dark');
    };
  }, []);

  // Auto-check for SpoolBuddy daemon updates
  const { data: updateCheck } = useQuery({
    queryKey: ['spoolbuddy-update-check', device?.device_id],
    queryFn: () => device ? spoolbuddyApi.checkDaemonUpdate(device.device_id, true) : Promise.resolve(null),
    enabled: !!device,
    refetchInterval: 5 * 60 * 1000, // re-check every 5 minutes
    staleTime: 4 * 60 * 1000,
  });

  // Update alert based on device state and available updates
  useEffect(() => {
    if (!effectiveDeviceOnline) {
      setAlert({ type: 'warning', message: 'SpoolBuddy device disconnected' });
    } else if (updateCheck?.update_available && updateCheck.latest_version) {
      setAlert({ type: 'info', message: `Update available: v${updateCheck.latest_version}` });
    } else {
      setAlert(null);
    }
  }, [effectiveDeviceOnline, updateCheck?.update_available, updateCheck?.latest_version]);

  // Track user activity for screen blank
  const resetActivity = useCallback(() => {
    lastActivityRef.current = Date.now();
    setBlanked(false);
  }, []);

  useEffect(() => {
    window.addEventListener('pointerdown', resetActivity);
    window.addEventListener('keydown', resetActivity);
    return () => {
      window.removeEventListener('pointerdown', resetActivity);
      window.removeEventListener('keydown', resetActivity);
    };
  }, [resetActivity]);

  // Screen blank timer
  useEffect(() => {
    if (displayBlankTimeout <= 0) return;
    const interval = setInterval(() => {
      if (Date.now() - lastActivityRef.current >= displayBlankTimeout * 1000) {
        setBlanked(true);
      }
    }, 1000);
    return () => clearInterval(interval);
  }, [displayBlankTimeout]);

  // CSS brightness filter (software dimming)
  const brightnessStyle = displayBrightness < 100
    ? { filter: `brightness(${displayBrightness / 100})` } as const
    : undefined;

  return (
    <>
      <div
        className="w-screen h-screen bg-bambu-dark text-white flex flex-col overflow-hidden"
        style={brightnessStyle}
      >
        <SpoolBuddyTopBar
          selectedPrinterId={selectedPrinterId}
          onPrinterChange={setSelectedPrinterId}
          deviceOnline={effectiveDeviceOnline}
        />

        <main className="flex-1 overflow-y-auto">
          <Outlet context={{
            selectedPrinterId, setSelectedPrinterId, sbState: sbStateForUi, setAlert,
            displayBrightness, setDisplayBrightness,
            displayBlankTimeout, setDisplayBlankTimeout,
          }} />
        </main>

        <SpoolBuddyStatusBar alert={alert} />
        <SpoolBuddyBottomNav />
        <VirtualKeyboard />
      </div>

      {/* Screen blank overlay — touch to wake */}
      {blanked && (
        <div
          className="fixed inset-0 bg-black z-[9999]"
          onPointerDown={(e) => { e.stopPropagation(); resetActivity(); }}
        />
      )}
    </>
  );
}

// Hook for child pages to access shared context
export interface SpoolBuddyOutletContext {
  selectedPrinterId: number | null;
  setSelectedPrinterId: (id: number) => void;
  sbState: ReturnType<typeof useSpoolBuddyState>;
  setAlert: (alert: { type: 'warning' | 'error' | 'info'; message: string } | null) => void;
  displayBrightness: number;
  setDisplayBrightness: (brightness: number) => void;
  displayBlankTimeout: number;
  setDisplayBlankTimeout: (timeout: number) => void;
}
