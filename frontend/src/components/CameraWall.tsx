import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQueries } from '@tanstack/react-query';
import { Settings as SettingsIcon } from 'lucide-react';
import { CameraTile, type CameraTileMode } from './CameraTile';
import { api, type Printer } from '../api/client';

interface CameraWallProps {
  printers: Printer[];
  maxLive: number;
  snapshotIntervalSec: number;
  onTileClick: (printerId: number, printerName: string) => void;
  onChangeMaxLive: (next: number) => void;
  onChangeSnapshotIntervalSec: (next: number) => void;
}

const MIN_MAX_LIVE = 1;
const MAX_MAX_LIVE = 16;
const MIN_SNAPSHOT_SEC = 2;
const MAX_SNAPSHOT_SEC = 60;

export function CameraWall({
  printers,
  maxLive,
  snapshotIntervalSec,
  onTileClick,
  onChangeMaxLive,
  onChangeSnapshotIntervalSec,
}: CameraWallProps) {
  const { t } = useTranslation();
  const tileRefs = useRef<Map<number, HTMLDivElement | null>>(new Map());

  // Reuses the same ['printerStatus', id] cache that each PrinterCard
  // populates, so flipping between Cards and Cam Wall is instant.
  const statusQueries = useQueries({
    queries: printers.map((p) => ({
      queryKey: ['printerStatus', p.id],
      queryFn: () => api.getPrinterStatus(p.id),
      staleTime: 5000,
    })),
  });
  const printerConnected = useMemo(() => {
    const map = new Map<number, boolean>();
    printers.forEach((p, i) => {
      map.set(p.id, statusQueries[i]?.data?.connected ?? false);
    });
    return map;
  }, [printers, statusQueries]);
  const [visibleIds, setVisibleIds] = useState<Set<number>>(() => new Set());
  const [showSettings, setShowSettings] = useState(false);
  const settingsRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!showSettings) return;
    const handler = (e: MouseEvent) => {
      if (settingsRef.current && !settingsRef.current.contains(e.target as Node)) {
        setShowSettings(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [showSettings]);

  // IntersectionObserver: a tile is "visible" when ≥40% of it is on-screen.
  // 40% (not 0%) avoids flicker at scroll boundaries where a tile is fractionally
  // visible — we don't want to spin up a live stream for a 5-pixel sliver.
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        setVisibleIds((prev) => {
          const next = new Set(prev);
          for (const entry of entries) {
            const id = Number((entry.target as HTMLElement).dataset.printerId);
            if (!Number.isFinite(id)) continue;
            if (entry.isIntersecting) next.add(id);
            else next.delete(id);
          }
          return next;
        });
      },
      { threshold: 0.4 },
    );

    for (const [, el] of tileRefs.current) {
      if (el) observer.observe(el);
    }
    return () => observer.disconnect();
  }, [printers]);

  // Live slot allocation: visible tiles get live up to `maxLive`, in printer
  // list order so the assignment is stable. Visible-but-over-cap fall back to
  // snapshot polling. Off-screen tiles render paused (no network).
  const modeByPrinter = useMemo(() => {
    const map = new Map<number, CameraTileMode>();
    let liveBudget = Math.max(0, maxLive);
    for (const p of printers) {
      if (!visibleIds.has(p.id)) {
        map.set(p.id, 'paused');
        continue;
      }
      if (liveBudget > 0) {
        map.set(p.id, 'live');
        liveBudget -= 1;
      } else {
        map.set(p.id, 'snapshot');
      }
    }
    return map;
  }, [printers, visibleIds, maxLive]);

  if (printers.length === 0) {
    return (
      <div className="rounded-lg border border-bambu-dark-tertiary bg-bambu-dark p-6 text-center text-bambu-gray">
        {t('printers.camWall.noPrinters')}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between text-xs text-bambu-gray">
        <span>
          {t('printers.camWall.summary', {
            live: Array.from(modeByPrinter.values()).filter((m) => m === 'live').length,
            snap: Array.from(modeByPrinter.values()).filter((m) => m === 'snapshot').length,
            total: printers.length,
          })}
        </span>
        <div className="relative" ref={settingsRef}>
          <button
            type="button"
            onClick={() => setShowSettings((v) => !v)}
            className="flex h-7 items-center gap-1 rounded-md border border-bambu-dark-tertiary bg-bambu-dark px-2 text-white hover:bg-bambu-dark-tertiary"
            title={t('printers.camWall.settings.title')}
          >
            <SettingsIcon className="h-3.5 w-3.5" />
            <span>{t('printers.camWall.settings.title')}</span>
          </button>
          {showSettings && (
            <div className="absolute right-0 top-9 z-30 w-72 space-y-3 rounded-lg border border-bambu-dark-tertiary bg-bambu-dark-secondary p-3 shadow-xl">
              <label className="block space-y-1">
                <span className="text-xs font-medium text-white">
                  {t('printers.camWall.settings.maxLive')}
                </span>
                <input
                  type="number"
                  min={MIN_MAX_LIVE}
                  max={MAX_MAX_LIVE}
                  value={maxLive}
                  onChange={(e) => {
                    const n = Math.min(
                      MAX_MAX_LIVE,
                      Math.max(MIN_MAX_LIVE, Number(e.target.value) || MIN_MAX_LIVE),
                    );
                    onChangeMaxLive(n);
                  }}
                  className="w-full rounded-md border border-bambu-dark-tertiary bg-bambu-dark px-2 py-1 text-sm text-white"
                />
                <span className="block text-[11px] text-bambu-gray">
                  {t('printers.camWall.settings.maxLiveHint')}
                </span>
              </label>
              <label className="block space-y-1">
                <span className="text-xs font-medium text-white">
                  {t('printers.camWall.settings.snapshotInterval')}
                </span>
                <input
                  type="number"
                  min={MIN_SNAPSHOT_SEC}
                  max={MAX_SNAPSHOT_SEC}
                  value={snapshotIntervalSec}
                  onChange={(e) => {
                    const n = Math.min(
                      MAX_SNAPSHOT_SEC,
                      Math.max(MIN_SNAPSHOT_SEC, Number(e.target.value) || MIN_SNAPSHOT_SEC),
                    );
                    onChangeSnapshotIntervalSec(n);
                  }}
                  className="w-full rounded-md border border-bambu-dark-tertiary bg-bambu-dark px-2 py-1 text-sm text-white"
                />
                <span className="block text-[11px] text-bambu-gray">
                  {t('printers.camWall.settings.snapshotIntervalHint')}
                </span>
              </label>
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {printers.map((p) => {
          const mode = modeByPrinter.get(p.id) ?? 'paused';
          return (
            <div
              key={p.id}
              ref={(el) => {
                tileRefs.current.set(p.id, el);
              }}
              data-printer-id={p.id}
            >
              <CameraTile
                printerId={p.id}
                printerName={p.name}
                cameraRotation={p.camera_rotation}
                mode={mode}
                snapshotIntervalMs={snapshotIntervalSec * 1000}
                connected={printerConnected.get(p.id) ?? false}
                onClick={() => onTileClick(p.id, p.name)}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}
