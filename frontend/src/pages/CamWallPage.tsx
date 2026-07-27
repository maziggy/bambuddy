/**
 * Standalone Cam Wall at ``/camwall`` (#2531).
 *
 * Two ways in, and they authenticate differently:
 *
 * - **Signed in.** Same wall the Printers page shows, on a URL you can
 *   bookmark. Data comes from the ordinary printers API behind the session
 *   JWT, and tiles stay clickable.
 *
 * - **``?token=<camwall token>``.** For a screen with no login — a shop TV, a
 *   Raspberry Pi in kiosk mode. The token authenticates both the tile feed and
 *   the video, and the wall drops to what a passive display needs: no settings
 *   popover, no click-through, and the compact status overlay only. A URL taped
 *   to a wall display is not a secret, so the page behind it shows a printer's
 *   name and state and nothing else — never the filename of the part on the bed.
 *
 * There is no WebSocket in either mode: this page renders outside the app
 * layout and its provider, so statuses are polled. A wall is watched, not
 * operated — a few seconds of latency costs nothing, and a kiosk token cannot
 * mint the WS ticket anyway.
 */
import { useEffect, useMemo, useState } from 'react';
import { Navigate, useLocation, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { CameraWall, type CameraWallStatus } from '../components/CameraWall';
import { type CameraTileStatusMode } from '../components/CameraTile';
import { api, setStreamToken } from '../api/client';
import { useAuth } from '../contexts/AuthContext';

// Kiosk polling cadence. Matches the staleTime the in-page wall runs at, so a
// tile's chip is never more stale than it would be on the Printers page.
const KIOSK_POLL_MS = 5000;

const DEFAULT_MAX_LIVE = 4;
const DEFAULT_SNAPSHOT_SEC = 8;
const MIN_MAX_LIVE = 1;
const MAX_MAX_LIVE = 16;
const MIN_SNAPSHOT_SEC = 2;
const MAX_SNAPSHOT_SEC = 60;

function clampInt(raw: string | null, fallback: number, min: number, max: number): number {
  const n = parseInt(raw ?? '', 10);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}

/** URL wins, then the knob the user last set on the Printers page, then the default. */
function fromUrlOrStorage(
  params: URLSearchParams,
  urlKey: string,
  storageKey: string,
  fallback: number,
  min: number,
  max: number,
): number {
  if (params.has(urlKey)) return clampInt(params.get(urlKey), fallback, min, max);
  return clampInt(localStorage.getItem(storageKey), fallback, min, max);
}

export function CamWallPage() {
  const { t } = useTranslation();
  const location = useLocation();
  const { authEnabled, loading: authLoading, user } = useAuth();
  const [searchParams] = useSearchParams();

  const token = searchParams.get('token');
  const kiosk = token != null && token !== '';

  // Seeded from the URL, then from whatever the user last chose on the Printers
  // page, then the defaults. Held as state so the settings popover on a
  // signed-in wall actually moves them; a kiosk wall never shows the popover.
  const [maxLive, setMaxLive] = useState(() =>
    fromUrlOrStorage(searchParams, 'maxLive', 'camWallMaxLive', DEFAULT_MAX_LIVE, MIN_MAX_LIVE, MAX_MAX_LIVE),
  );
  const [snapshotIntervalSec, setSnapshotIntervalSec] = useState(() =>
    fromUrlOrStorage(
      searchParams,
      'interval',
      'camWallSnapshotSec',
      DEFAULT_SNAPSHOT_SEC,
      MIN_SNAPSHOT_SEC,
      MAX_SNAPSHOT_SEC,
    ),
  );
  const [statusMode, setStatusMode] = useState<CameraTileStatusMode>(() => {
    const requested = searchParams.get('status') ?? localStorage.getItem('camWallStatusMode');
    // 'full' names the file on the bed. Fine on a signed-in screen, wrong on one
    // anybody can walk past — so a kiosk wall caps at 'compact', and the feed
    // behind it declines to serve the filename in the first place.
    const allowed: CameraTileStatusMode[] = kiosk ? ['off', 'compact'] : ['off', 'compact', 'full'];
    return allowed.includes(requested as CameraTileStatusMode)
      ? (requested as CameraTileStatusMode)
      : 'compact';
  });

  // The MJPEG <img> tags cannot send an Authorization header, so they carry the
  // token in the query string. Handing it to the client module means CameraTile
  // picks it up through the same withStreamToken() path a signed-in wall uses.
  // Safe against the app-wide stream-token sync: that query is disabled while
  // no user is signed in, which is precisely the kiosk case.
  useEffect(() => {
    if (!kiosk) return;
    setStreamToken(token);
    return () => setStreamToken(null);
  }, [kiosk, token]);

  const kioskQuery = useQuery({
    queryKey: ['camwall-printers', token],
    queryFn: () => api.getCamWallPrinters(token ?? undefined),
    enabled: kiosk,
    refetchInterval: KIOSK_POLL_MS,
  });

  // Signed-in mode: same source as the Printers page, so the wall and the cards
  // agree. CameraWall fetches the per-printer statuses itself from here.
  const printersQuery = useQuery({
    queryKey: ['printers'],
    queryFn: () => api.getPrinters(),
    enabled: !kiosk && !authLoading && (!authEnabled || user !== null),
    refetchInterval: KIOSK_POLL_MS,
  });

  const kioskStatuses = useMemo(() => {
    const map = new Map<number, CameraWallStatus | undefined>();
    for (const p of kioskQuery.data ?? []) {
      map.set(p.id, {
        connected: p.connected,
        state: p.state,
        progress: p.progress,
        remaining_time: p.remaining_time,
        layer_num: p.layer_num,
        total_layers: p.total_layers,
        hms_errors: p.hms_errors,
      });
    }
    return map;
  }, [kioskQuery.data]);

  if (!kiosk && authLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bambu-dark text-bambu-gray">
        {t('common.loading')}
      </div>
    );
  }

  // No token and no session: this is just a normal page of the app.
  if (!kiosk && authEnabled && !user) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  const query = kiosk ? kioskQuery : printersQuery;
  const printers = kiosk ? (kioskQuery.data ?? []) : (printersQuery.data ?? []);

  if (query.isError) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bambu-dark p-6 text-center">
        <p className="max-w-md text-sm text-red-400">
          {kiosk ? t('printers.camWall.page.tokenRejected') : t('printers.camWall.page.loadFailed')}
        </p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-bambu-dark p-4">
      <CameraWall
        printers={printers}
        maxLive={maxLive}
        snapshotIntervalSec={snapshotIntervalSec}
        statusMode={statusMode}
        statuses={kiosk ? kioskStatuses : undefined}
        showSettings={!kiosk}
        onTileClick={kiosk ? undefined : (id) => window.open(`/camera/${id}`, `camera-${id}`)}
        // Writes to the same localStorage keys the Printers page reads, so a
        // change made here follows the user back there. A kiosk wall hides the
        // popover, so these never fire.
        onChangeMaxLive={(next) => {
          setMaxLive(next);
          localStorage.setItem('camWallMaxLive', String(next));
        }}
        onChangeSnapshotIntervalSec={(next) => {
          setSnapshotIntervalSec(next);
          localStorage.setItem('camWallSnapshotSec', String(next));
        }}
        onChangeStatusMode={(next) => {
          setStatusMode(next);
          localStorage.setItem('camWallStatusMode', next);
        }}
      />
    </div>
  );
}

export default CamWallPage;
