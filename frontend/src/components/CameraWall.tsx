import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQueries } from '@tanstack/react-query';
import { Settings as SettingsIcon } from 'lucide-react';
import { CameraTile, type CameraTileMode, type CameraTileStatusMode } from './CameraTile';
import { filterKnownHMSErrors } from './HMSErrorModal';
import { api, type PrinterStatus } from '../api/client';

// The wall only ever reads these three fields off a printer, so it asks for no
// more than that. Printer[] satisfies this structurally, and so does the
// smaller payload the token-authenticated kiosk feed returns (#2531) — which
// deliberately carries neither serial number nor IP.
export interface CameraWallPrinter {
  id: number;
  name: string;
  camera_rotation?: number;
}

// What a tile draws from a printer's status. PrinterStatus satisfies it; so
// does CamWallPrinter, which is how the kiosk page feeds the same component
// without the JWT-gated per-printer status endpoint.
export interface CameraWallStatus {
  connected?: boolean;
  state?: string | null;
  progress?: number | null;
  remaining_time?: number | null;
  layer_num?: number | null;
  total_layers?: number | null;
  subtask_name?: string | null;
  gcode_file?: string | null;
  hms_errors?: PrinterStatus['hms_errors'];
}

interface CameraWallProps {
  printers: CameraWallPrinter[];
  maxLive: number;
  snapshotIntervalSec: number;
  statusMode: CameraTileStatusMode;
  onChangeMaxLive: (next: number) => void;
  onChangeSnapshotIntervalSec: (next: number) => void;
  onChangeStatusMode: (next: CameraTileStatusMode) => void;
  // Omitted on a kiosk wall: a TV has no pointer, and click-through would open
  // a page the wall's token cannot authenticate. Tiles render inert instead.
  onTileClick?: (printerId: number, printerName: string) => void;
  // Supplied by the kiosk page, which polls one feed for the whole wall. When
  // absent the component fetches per-printer status itself, reusing the
  // ['printerStatus', id] cache the printer cards already populate.
  statuses?: Map<number, CameraWallStatus | undefined>;
  // Kiosk walls hide the settings popover — the knobs come from the URL, and
  // there is nobody standing at the screen to turn them.
  showSettings?: boolean;
}

const MIN_MAX_LIVE = 1;
const MAX_MAX_LIVE = 16;
const MIN_SNAPSHOT_SEC = 2;
const MAX_SNAPSHOT_SEC = 60;
const STATUS_MODES: CameraTileStatusMode[] = ['off', 'compact', 'full'];

export function CameraWall({
  printers,
  maxLive,
  snapshotIntervalSec,
  statusMode,
  onTileClick,
  onChangeMaxLive,
  onChangeSnapshotIntervalSec,
  onChangeStatusMode,
  statuses,
  showSettings: settingsEnabled = true,
}: CameraWallProps) {
  const { t } = useTranslation();
  const tileRefs = useRef<Map<number, HTMLDivElement | null>>(new Map());

  // Reuses the same ['printerStatus', id] cache that each PrinterCard
  // populates, so flipping between Cards and Cam Wall is instant. Skipped
  // entirely when the caller already has the statuses — the kiosk page polls
  // one feed for the whole wall, and its token cannot reach this endpoint.
  const ownQueries = statuses ? [] : printers;
  const statusQueries = useQueries({
    queries: ownQueries.map((p) => ({
      queryKey: ['printerStatus', p.id],
      queryFn: () => api.getPrinterStatus(p.id),
      staleTime: 5000,
    })),
  });
  const fetchedStatuses = useMemo(() => {
    const map = new Map<number, CameraWallStatus | undefined>();
    ownQueries.forEach((p, i) => {
      map.set(p.id, statusQueries[i]?.data);
    });
    return map;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [printers, statusQueries, statuses]);
  const statusByPrinter = statuses ?? fetchedStatuses;
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
  // snapshot polling. Off-screen tiles render paused (no network). Disconnected
  // printers also render paused regardless of visibility — there's nothing to
  // stream and burning a live-budget slot on them would starve a working tile.
  const modeByPrinter = useMemo(() => {
    const map = new Map<number, CameraTileMode>();
    let liveBudget = Math.max(0, maxLive);
    for (const p of printers) {
      const connected = statusByPrinter.get(p.id)?.connected ?? false;
      if (!visibleIds.has(p.id) || !connected) {
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
  }, [printers, visibleIds, maxLive, statusByPrinter]);

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
        {/* Not merely hidden — a kiosk wall must not carry a focusable control
            it cannot act on. CSS-hiding would leave it tabbable. */}
        {settingsEnabled && (
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
              <div className="space-y-1">
                <span className="block text-xs font-medium text-white">
                  {t('printers.camWall.settings.statusOverlay')}
                </span>
                <div
                  role="radiogroup"
                  aria-label={t('printers.camWall.settings.statusOverlay')}
                  className="flex overflow-hidden rounded-md border border-bambu-dark-tertiary"
                >
                  {STATUS_MODES.map((m) => (
                    <button
                      key={m}
                      type="button"
                      role="radio"
                      aria-checked={statusMode === m}
                      onClick={() => onChangeStatusMode(m)}
                      className={`flex-1 px-2 py-1 text-xs ${
                        statusMode === m
                          ? 'bg-bambu-green text-black font-semibold'
                          : 'bg-bambu-dark text-white hover:bg-bambu-dark-tertiary'
                      }`}
                    >
                      {t(`printers.camWall.statusMode.${m}`)}
                    </button>
                  ))}
                </div>
                <span className="block text-[11px] text-bambu-gray">
                  {t('printers.camWall.settings.statusOverlayHint')}
                </span>
              </div>
            </div>
          )}
        </div>
        )}
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
                connected={statusByPrinter.get(p.id)?.connected ?? false}
                statusMode={statusMode}
                printerState={statusByPrinter.get(p.id)?.state ?? null}
                progress={statusByPrinter.get(p.id)?.progress ?? null}
                remainingMin={statusByPrinter.get(p.id)?.remaining_time ?? null}
                layerNum={statusByPrinter.get(p.id)?.layer_num ?? null}
                totalLayers={statusByPrinter.get(p.id)?.total_layers ?? null}
                printName={
                  statusByPrinter.get(p.id)?.subtask_name ??
                  statusByPrinter.get(p.id)?.gcode_file ??
                  null
                }
                hmsErrorCount={
                  filterKnownHMSErrors(statusByPrinter.get(p.id)?.hms_errors ?? []).length
                }
                onClick={onTileClick ? () => onTileClick(p.id, p.name) : undefined}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}
