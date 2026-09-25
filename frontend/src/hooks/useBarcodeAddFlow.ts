import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { spoolbuddyApi } from '../api/client';
import { useToast } from '../contexts/ToastContext';
import type { MatchedSpool, ScannedBarcode } from './useSpoolBuddyState';

/**
 * Barcode add-to-inventory flow wiring for the SpoolBuddy dashboard:
 * per-device scanner availability plus auto-opening the add modal when a
 * fresh hardware scan arrives (same shape as useUnknownTagPrompt — WS-event
 * driven prompt logic kept out of the page component).
 */
export function useBarcodeAddFlow(input: {
  deviceId: string | null;
  lastScan: ScannedBarcode | null;
  matchedSpool: MatchedSpool | null;
}) {
  const { deviceId, lastScan, matchedSpool } = input;
  const { t } = useTranslation();
  const { showToast } = useToast();
  const [isOpen, setIsOpen] = useState(false);

  // Barcode scanner availability for this device — gates the whole flow.
  const { data: sbDevices = [] } = useQuery({
    queryKey: ['spoolbuddy-devices'],
    queryFn: () => spoolbuddyApi.getDevices(),
    staleTime: 30 * 1000,
  });
  const thisDevice = useMemo(
    () => sbDevices.find((d) => d.device_id === deviceId) ?? sbDevices[0] ?? null,
    [sbDevices, deviceId],
  );
  const scannerAvailable = !!thisDevice?.has_barcode && thisDevice.barcode_enabled !== false;

  // Auto-open the barcode add flow when a fresh scan arrives. A scan while an
  // unknown tag sits on the scale jumps straight to confirm; a scan with no
  // tag lands on the barcode-first screen. Suppressed when a *known* spool is
  // already showing (it's already in inventory) and when the scan is stale
  // (surfaced by a late WS reconnect rather than a real trigger).
  useEffect(() => {
    if (!lastScan || !scannerAvailable) return;
    if (isOpen) return; // already open — modal consumes it live
    if (Date.now() - lastScan.receivedAt > 10_000) return;
    if (matchedSpool) {
      // Known spool already on screen — surface a hint instead of a modal.
      if (lastScan.matched) {
        showToast(t('spoolbuddy.barcode.alreadyInInventory', 'This spool is already in your inventory'), 'info');
      }
      return;
    }
    setIsOpen(true);
  }, [lastScan, scannerAvailable, isOpen, matchedSpool, showToast, t]);

  return {
    scannerAvailable,
    isOpen,
    open: () => setIsOpen(true),
    close: () => setIsOpen(false),
  };
}
