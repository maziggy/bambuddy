import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { useQuery, useQueries } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { SpoolBuddyTopBar } from './SpoolBuddyTopBar';
import { SpoolBuddyBottomNav } from './SpoolBuddyBottomNav';
import { SpoolBuddyStatusBar } from './SpoolBuddyStatusBar';
import { useSpoolBuddyState } from '../../hooks/useSpoolBuddyState';
import { api, spoolbuddyApi, type Printer, type PrinterStatus } from '../../api/client';
import { VirtualKeyboard } from '../VirtualKeyboard';

export function SpoolBuddyLayout() {
  const [selectedPrinterId, setSelectedPrinterId] = useState<number | null>(null);
  const [alert, setAlert] = useState<{ type: 'warning' | 'error' | 'info'; message: string } | null>(null);
  const [blanked, setBlanked] = useState(false);
  const [displayBrightness, setDisplayBrightness] = useState(100);
  const [displayBlankTimeout, setDisplayBlankTimeout] = useState(0);
  const lastActivityRef = useRef(Date.now());
  const { i18n } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
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
    queryFn: () => device ? spoolbuddyApi.checkDaemonUpdate(device.device_id) : Promise.resolve(null),
    enabled: !!device,
    refetchInterval: 5 * 60 * 1000, // re-check every 5 minutes
    staleTime: 0,
  });

  // Update alert based on device state and available updates.
  // Only clear alerts that the layout itself set (not alerts from child pages).
  const layoutAlertRef = useRef<string | null>(null);
  useEffect(() => {
    if (!effectiveDeviceOnline) {
      const msg = 'SpoolBuddy device disconnected';
      setAlert({ type: 'warning', message: msg });
      layoutAlertRef.current = msg;
    } else if (updateCheck?.update_available && updateCheck.latest_version) {
      const msg = `Update available: v${updateCheck.latest_version}`;
      setAlert({ type: 'info', message: msg });
      layoutAlertRef.current = msg;
    } else if (layoutAlertRef.current) {
      setAlert(null);
      layoutAlertRef.current = null;
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

  // Auto-navigate to dashboard when a NEW tag is detected (transition from no-tag to tag)
  const tagDetected = Boolean(sbState.matchedSpool || sbState.unknownTagUid);
  const prevTagDetected = useRef(false);
  useEffect(() => {
    if (tagDetected && !prevTagDetected.current) {
      resetActivity();
      if (location.pathname !== '/spoolbuddy') {
        navigate('/spoolbuddy');
      }
    }
    prevTagDetected.current = tagDetected;
  }, [tagDetected, location.pathname, navigate, resetActivity]);

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

  // Online printers list for swipe-to-switch
  const { data: printers = [] } = useQuery({
    queryKey: ['printers'],
    queryFn: () => api.getPrinters(),
  });
  const statusQueries = useQueries({
    queries: printers.map((printer: Printer) => ({
      queryKey: ['printerStatus', printer.id],
      queryFn: () => api.getPrinterStatus(printer.id),
      refetchInterval: 10000,
      select: (data: PrinterStatus) => ({ connected: data?.connected }),
    })),
  });
  const onlinePrinters = useMemo(() => {
    return printers.filter((_: Printer, i: number) => statusQueries[i]?.data?.connected);
  }, [printers, statusQueries]);

  // Swipe left/right to cycle through online printers
  const touchStartRef = useRef<{ x: number; y: number } | null>(null);
  const swipeLockedRef = useRef(false);
  const SWIPE_THRESHOLD = 50;
  const rootRef = useRef<HTMLDivElement>(null);

  const handleTouchStart = useCallback((e: React.TouchEvent) => {
    touchStartRef.current = { x: e.touches[0].clientX, y: e.touches[0].clientY };
    swipeLockedRef.current = false;
  }, []);
  const handleTouchEnd = useCallback((e: React.TouchEvent) => {
    if (!touchStartRef.current || onlinePrinters.length < 2) return;
    const dx = e.changedTouches[0].clientX - touchStartRef.current.x;
    const dy = e.changedTouches[0].clientY - touchStartRef.current.y;
    touchStartRef.current = null;
    swipeLockedRef.current = false;
    if (Math.abs(dx) < SWIPE_THRESHOLD || Math.abs(dy) > Math.abs(dx)) return;
    const currentIdx = onlinePrinters.findIndex((p: Printer) => p.id === selectedPrinterId);
    const nextIdx = dx < 0
      ? (currentIdx + 1) % onlinePrinters.length          // swipe left → next
      : (currentIdx - 1 + onlinePrinters.length) % onlinePrinters.length; // swipe right → prev
    setSelectedPrinterId(onlinePrinters[nextIdx].id);
  }, [onlinePrinters, selectedPrinterId, setSelectedPrinterId]);

  // Block browser back/forward swipe gesture with non-passive touchmove listener
  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const onTouchMove = (e: TouchEvent) => {
      if (!touchStartRef.current) return;
      const dx = Math.abs(e.touches[0].clientX - touchStartRef.current.x);
      const dy = Math.abs(e.touches[0].clientY - touchStartRef.current.y);
      // Once locked as horizontal, prevent default for the rest of this gesture
      if (swipeLockedRef.current) { e.preventDefault(); return; }
      if (dx > 10 && dx > dy) { swipeLockedRef.current = true; e.preventDefault(); }
    };
    el.addEventListener('touchmove', onTouchMove, { passive: false });
    return () => el.removeEventListener('touchmove', onTouchMove);
  }, []);

  // Track virtual keyboard visibility to hide bottom bars
  const [keyboardVisible, setKeyboardVisible] = useState(false);

  // CSS brightness filter (software dimming)
  const brightnessStyle = displayBrightness < 100
    ? { filter: `brightness(${displayBrightness / 100})` } as const
    : undefined;

  return (
    <>
      <div
        ref={rootRef}
        data-spoolbuddy-kiosk
        className="w-screen h-screen bg-bambu-dark text-white flex flex-col overflow-hidden"
        style={{ ...brightnessStyle, overscrollBehaviorX: 'none' }}
        onTouchStart={handleTouchStart}
        onTouchEnd={handleTouchEnd}
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

        {!keyboardVisible && <SpoolBuddyStatusBar alert={alert} />}
        {!keyboardVisible && <SpoolBuddyBottomNav />}
        <VirtualKeyboard onVisibilityChange={setKeyboardVisible} />
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
