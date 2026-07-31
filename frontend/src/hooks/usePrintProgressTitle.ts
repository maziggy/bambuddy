import { useQueries, useQuery } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { api } from '../api/client';
import { useTheme } from '../contexts/ThemeContext';

const FALLBACK_ACCENT = '#00ae42'; // Bambuddy green, if --accent can't be read (e.g. jsdom)

// A remaining_time <= 0 means "ETA not known yet" (the backend defaults it to 0,
// not null), so treat it as unknown rather than "finishes now".
const eta = (t: number | null): number => (t != null && t > 0 ? t : Infinity);

// Only the fields we need — keeps pickActivePrint decoupled from the full
// PrinterStatus type so the test can pass plain objects.
export interface ProgressStatus {
  state: string | null;
  progress: number | null;
  remaining_time: number | null;
}

/**
 * Of all connected printers, pick the RUNNING print to surface in the tab:
 * the one finishing soonest (smallest remaining_time), tie-broken by highest
 * progress. Returns null when nothing is actively printing.
 */
export function pickActivePrint<T extends ProgressStatus>(statuses: (T | undefined)[]): T | null {
  let best: T | null = null;
  for (const s of statuses) {
    if (!s || s.state !== 'RUNNING' || s.progress == null) continue;
    if (best === null) {
      best = s;
      continue;
    }
    const sr = eta(s.remaining_time);
    const br = eta(best.remaining_time);
    if (sr < br || (sr === br && (s.progress ?? 0) > (best.progress ?? 0))) {
      best = s;
    }
  }
  return best;
}

// Draw a 32x32 progress ring in the current theme accent colour, return a PNG
// data URL (or null if the browser has no 2d canvas, e.g. under jsdom — the
// caller then falls back to updating the title only).
function drawProgressFavicon(pct: number): string | null {
  const canvas = document.createElement('canvas');
  canvas.width = 32;
  canvas.height = 32;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;

  const accent =
    getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() ||
    FALLBACK_ACCENT;

  const cx = 16;
  const cy = 16;
  const r = 13;
  const frac = Math.max(0, Math.min(100, pct)) / 100;
  const start = -Math.PI / 2; // 12 o'clock

  ctx.lineWidth = 4;
  // Track
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.strokeStyle = 'rgba(128,128,128,0.3)';
  ctx.stroke();
  // Progress arc, clockwise from the top
  ctx.beginPath();
  ctx.arc(cx, cy, r, start, start + frac * Math.PI * 2);
  ctx.strokeStyle = accent;
  ctx.lineCap = 'round';
  ctx.stroke();

  return canvas.toDataURL('image/png');
}

// Point the <link rel="icon"> tags at the ring (remembering originals), or
// restore them when dataUrl is null. apple-touch-icon is a different rel token
// so the `rel~="icon"` selector leaves it alone.
function setFavicon(dataUrl: string | null, originals: Map<HTMLLinkElement, string>) {
  const links = document.querySelectorAll<HTMLLinkElement>('link[rel~="icon"]');
  links.forEach((link) => {
    if (dataUrl) {
      if (!originals.has(link)) originals.set(link, link.href);
      link.href = dataUrl;
    } else {
      const orig = originals.get(link);
      if (orig !== undefined) link.href = orig;
    }
  });
  if (!dataUrl) originals.clear();
}

/**
 * When the "progress in tab" preference is on, reflect the soonest-finishing
 * print's percentage in document.title and draw a progress ring favicon in the
 * theme accent colour. Stays fully inert until enabled, and hands the tab back
 * to its defaults once disabled, idle, or unmounted.
 * Mounted once, globally, inside WebSocketProvider.
 */
export function usePrintProgressTitle() {
  const { progressInTitle, resolvedMode, darkAccent, lightAccent } = useTheme();
  // Re-draw the ring when the active accent changes.
  const accent = resolvedMode === 'dark' ? darkAccent : lightAccent;

  const { data: printers } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
    enabled: progressInTitle,
  });

  // No refetchInterval here on purpose. This hook is mounted globally, so a
  // poll would add one request per printer every interval on every page — the
  // Printers page already runs its own 30s fallback on this exact key. The
  // WebSocket writes ['printerStatus', id] directly (useWebSocket), which keeps
  // the tab live; a cosmetic title going stale during a WS outage is fine.
  const statusQueries = useQueries({
    queries: (progressInTitle ? printers ?? [] : []).map((p) => ({
      queryKey: ['printerStatus', p.id],
      queryFn: () => api.getPrinterStatus(p.id),
    })),
  });

  const originalsRef = useRef<Map<HTMLLinkElement, string>>(new Map());
  // The tab's own title, captured before we ever touch it, so restoring doesn't
  // depend on a constant matching index.html.
  const defaultTitleRef = useRef(document.title);
  // Whether we currently own the tab title/favicon. Lets us stay inert while
  // off (never touch the tab) yet still restore once if we ever took it over.
  const ownsRef = useRef(false);

  const active = progressInTitle ? pickActivePrint(statusQueries.map((q) => q.data)) : null;
  const pct = active && active.progress != null ? Math.round(active.progress) : null;

  useEffect(() => {
    if (progressInTitle && pct != null) {
      document.title = `${pct}% · ${defaultTitleRef.current}`;
      setFavicon(drawProgressFavicon(pct), originalsRef.current);
      ownsRef.current = true;
    } else if (ownsRef.current) {
      // Disabled or idle after having taken over — hand the tab back.
      document.title = defaultTitleRef.current;
      setFavicon(null, originalsRef.current);
      ownsRef.current = false;
    }
    // else: never owned the tab → leave it entirely alone.
  }, [progressInTitle, pct, accent]);

  // Restore the tab to defaults on unmount, but only if we own it.
  useEffect(() => {
    const originals = originalsRef.current;
    const owns = ownsRef;
    const defaultTitle = defaultTitleRef.current;
    return () => {
      if (owns.current) {
        document.title = defaultTitle;
        setFavicon(null, originals);
      }
    };
  }, []);
}
