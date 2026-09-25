import { useState, useEffect, useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { spoolbuddyApi, type SpoolBuddyDevice } from '../../api/client';
import { KioskToggle } from './KioskToggle';

/**
 * Settings → Scanner tab: per-device barcode-scanner enable toggle, detected
 * hardware status, and a live last-scan test row.
 *
 * Lives with the rest of the barcode feature (not inline in
 * SpoolBuddySettingsPage) to keep the shared settings page's diff minimal.
 */
export function ScannerTab({ device }: { device: SpoolBuddyDevice }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [enabled, setEnabled] = useState(device.barcode_enabled);
  const [saving, setSaving] = useState(false);
  const [lastScan, setLastScan] = useState<{ code: string; matched: boolean; at: string } | null>(null);

  // Keep local toggle in sync when the device query refreshes.
  useEffect(() => {
    setEnabled(device.barcode_enabled);
  }, [device.barcode_enabled]);

  // Live test row — echo any scan the daemon forwards while this tab is open.
  useEffect(() => {
    const handler = (e: Event) => {
      const d = (e as CustomEvent).detail ?? {};
      const data = d.data ?? d;
      setLastScan({
        code: data.barcode ?? '',
        matched: !!data.matched,
        at: new Date().toLocaleTimeString(),
      });
    };
    window.addEventListener('spoolbuddy-barcode-scanned', handler);
    return () => window.removeEventListener('spoolbuddy-barcode-scanned', handler);
  }, []);

  const hasScanner = device.has_barcode;

  const toggle = useCallback(async () => {
    if (!hasScanner) return;
    const next = !enabled;
    setEnabled(next);
    setSaving(true);
    try {
      await spoolbuddyApi.setScannerSettings(device.device_id, next);
      queryClient.invalidateQueries({ queryKey: ['spoolbuddy-devices'] });
    } catch {
      setEnabled(!next); // revert on failure
    } finally {
      setSaving(false);
    }
  }, [hasScanner, enabled, device.device_id, queryClient]);

  return (
    <div className="space-y-4">
      <div className="bg-zinc-800/50 rounded-xl p-4 border border-zinc-700/50">
        <div className="flex items-center justify-between gap-4">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-zinc-200">
              {t('spoolbuddy.settings.scannerToggleTitle', 'Use barcode scanner when adding spools')}
            </p>
            <p className="text-xs text-zinc-500 mt-1">
              {t(
                'spoolbuddy.settings.scannerToggleDesc',
                'Replaces the quick-add dialog with Scan Barcode to Add on this device.',
              )}
            </p>
          </div>
          <KioskToggle checked={enabled && hasScanner} disabled={!hasScanner || saving} onToggle={toggle} />
        </div>
        {!hasScanner && (
          <p className="text-xs text-amber-400/80 mt-3">
            {t(
              'spoolbuddy.settings.scannerNotDetected',
              'No scanner detected — connect a USB barcode scanner and it will appear here.',
            )}
          </p>
        )}
      </div>

      {hasScanner && (
        <div className="bg-zinc-800/50 rounded-xl p-4 border border-zinc-700/50">
          <p className="text-sm font-semibold text-zinc-200 mb-2">
            {t('spoolbuddy.settings.scannerHardware', 'Detected hardware')}
          </p>
          <div className="flex items-center gap-2 text-sm text-zinc-300">
            <span className={`w-2 h-2 rounded-full ${device.barcode_ok ? 'bg-green-500' : 'bg-zinc-500'}`} />
            <span>
              {t('spoolbuddy.settings.scannerGeneric', 'USB barcode scanner')}
              {device.barcode_ok
                ? ` — ${t('spoolbuddy.settings.scannerConnected', 'connected')}`
                : ` — ${t('spoolbuddy.settings.scannerDisconnected', 'not connected')}`}
            </span>
          </div>
          <div className="flex items-center justify-between gap-3 mt-3 px-3 py-2.5 rounded-lg bg-zinc-900 border border-dashed border-zinc-600 text-sm">
            <span className="text-zinc-500">{t('spoolbuddy.settings.scannerLastScan', 'Last scan')}</span>
            {lastScan ? (
              <span className={`font-mono text-xs ${lastScan.matched ? 'text-green-400' : 'text-amber-400'}`}>
                {lastScan.code} · {lastScan.at}
              </span>
            ) : (
              <span className="text-zinc-600 text-xs">{t('spoolbuddy.settings.scannerScanToTest', 'Scan to test')}</span>
            )}
          </div>
          <p className="text-xs text-zinc-500 mt-2">
            {t('spoolbuddy.settings.scannerTestHint', 'Scan any barcode to test. The code appears here.')}
          </p>
        </div>
      )}

      <p className="text-xs text-zinc-500 px-1">
        {t(
          'spoolbuddy.settings.scannerDbNote',
          "Community database lookups follow the Barcode Scan setting in the main Bambuddy app. Matches against your own inventory always work, even offline.",
        )}
      </p>
    </div>
  );
}
