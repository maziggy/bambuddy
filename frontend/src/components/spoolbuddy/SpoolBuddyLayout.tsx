import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { useQuery, useQueries } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { SpoolBuddyTopBar } from './SpoolBuddyTopBar';
import { SpoolBuddyBottomNav } from './SpoolBuddyBottomNav';
import { SpoolBuddyStatusBar } from './SpoolBuddyStatusBar';
import { SpoolBuddyQuickMenu } from './SpoolBuddyQuickMenu';
import { useSpoolBuddyState } from '../../hooks/useSpoolBuddyState';
import { useColorCatalogVersion } from '../../hooks/useColorCatalogVersion';
import { api, spoolbuddyApi, type Printer, type PrinterStatus } from '../../api/client';
import { VirtualKeyboard } from '../VirtualKeyboard';
import { useToast } from '../../contexts/ToastContext';

export function SpoolBuddyLayout() {
  // Cascade a re-render into all SpoolBuddy pages when the color catalog
  // loads, for the same reason as the main Layout — SpoolBuddyInventoryPage
  // renders spool color names on mount. See #857.
  useColorCatalogVersion();
  const [selectedPrinterId, setSelectedPrinterId] = useState<number | null>(null);
  const [alert, setAlert] = useState<{ type: 'warning' | 'error' | 'info'; message: string } | null>(null);
  const [displayBrightness, setDisplayBrightness] = useState(100);
  const { i18n } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const sbState = useSpoolBuddyState();

  // Hide the global toast viewport (background-dispatch progress, etc.) on the
  // kiosk display. Restore on unmount so navigating back to the main app sees
  // its toasts again.
  const { setViewportSuppressed } = useToast();
  useEffect(() => {
    setViewportSuppressed(true);
    return () => setViewportSuppressed(false);
  }, [setViewportSuppressed]);

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

  // Auto-navigate to dashboard when a NEW tag is detected (transition from no-tag to tag).
  // Blanking itself is handled by swayidle/wlopm at the OS level on the kiosk device —
  // when the HDMI output powers off and the user taps the screen, labwc delivers the
  // input event to swayidle's `resume` command which re-powers HDMI. See issue #937.
  const tagDetected = Boolean(sbState.matchedSpool || sbState.unknownTagUid || sbState.unknownTrayUuid);
  const prevTagDetected = useRef(false);
  useEffect(() => {
    if (tagDetected && !prevTagDetected.current) {
      if (location.pathname !== '/spoolbuddy') {
        navigate('/spoolbuddy');
      }
    }
    prevTagDetected.current = tagDetected;
  }, [tagDetected, location.pathname, navigate]);

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
    if (!touchStartRef.current) return;
    const dx = e.changedTouches[0].clientX - touchStartRef.current.x;
    const dy = e.changedTouches[0].clientY - touchStartRef.current.y;
    const startY = touchStartRef.current.y;
    touchStartRef.current = null;
    swipeLockedRef.current = false;

    // Vertical swipe down from top area → open quick menu
    // Top bar is 48px; allow starting swipe up to 120px from top to account for finger size
    if (dy >= SWIPE_THRESHOLD && Math.abs(dy) > Math.abs(dx) && startY < 120) {
      setQuickMenuOpen(true);
      return;
    }

    // Horizontal swipe: cycle printers
    if (onlinePrinters.length < 2) return;
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

  // Quick menu (swipe down to open)
  const [quickMenuOpen, setQuickMenuOpen] = useState(false);

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
        {/* Pull-down handle — tap or swipe to open quick menu */}
        <button
          onClick={() => setQuickMenuOpen(true)}
          className="w-full h-1.5 bg-bambu-dark-secondary flex justify-center items-center shrink-0 touch-none"
          aria-label="Open quick menu"
        >
          <div className="w-8 h-0.5 rounded-full bg-zinc-600" />
        </button>

        <SpoolBuddyTopBar
          selectedPrinterId={selectedPrinterId}
          onPrinterChange={setSelectedPrinterId}
          deviceOnline={effectiveDeviceOnline}
        />

        <main className="flex-1 overflow-y-auto">
          <Outlet context={{
            selectedPrinterId, setSelectedPrinterId, sbState: sbStateForUi, setAlert,
            displayBrightness, setDisplayBrightness,
          }} />
        </main>

        {!keyboardVisible && <SpoolBuddyStatusBar alert={alert} />}
        {!keyboardVisible && <SpoolBuddyBottomNav />}
        <VirtualKeyboard onVisibilityChange={setKeyboardVisible} />
      </div>

      {/* Quick menu (swipe down from top) */}
      <SpoolBuddyQuickMenu
        isOpen={quickMenuOpen}
        onClose={() => setQuickMenuOpen(false)}
        deviceId={device?.device_id ?? null}
        deviceOnline={effectiveDeviceOnline}
      />
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
}
