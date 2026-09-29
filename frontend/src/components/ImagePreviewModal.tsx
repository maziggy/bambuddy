import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Image as ImageIcon, Loader2, RotateCcw, ZoomIn, ZoomOut } from 'lucide-react';
import { api, getAuthToken } from '../api/client';
import { formatFileSize } from '../utils/file';
import { PreviewModalShell, previewIconButtonClass } from './PreviewModalShell';
import { usePreviewFullscreen } from '../hooks/usePreviewFullscreen';

// The whole file is fetched into memory before it is shown, so a multi-hundred
// megabyte scan gets the same notice the other previews give instead of a tab
// that stops responding (#2976).
export const IMAGE_PREVIEW_MAX_BYTES = 50 * 1024 * 1024;

const MIN_ZOOM = 0.4;
// Higher than the PDF's ceiling: a photo or a scan is worth inspecting at the
// pixel, and unlike a PDF page there is no sharper raster to fall back on.
const MAX_ZOOM = 8;
// One button press, one key press, one mouse-wheel notch.
const ZOOM_STEP = 1.25;
// A pinch arrives as many small ctrlKey wheel events, a mouse notch as one
// ±100; clamping the delta before the exponent gives the notch one ZOOM_STEP
// and the pinch a smooth ramp.
const WHEEL_DELTA_CLAMP = 30;
const WHEEL_ZOOM_RATE = Math.log(ZOOM_STEP) / WHEEL_DELTA_CLAMP;

const clampZoom = (zoom: number) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom));

interface Point {
  x: number;
  y: number;
}

const ORIGIN: Point = { x: 0, y: 0 };

interface ImagePreviewModalProps {
  libraryFileId: number;
  filename: string;
  fileSize: number;
  onClose: () => void;
}

// deltaMode 1 is lines (Firefox mouse wheel), 2 is pages; both scaled to pixels.
function wheelDeltaPixels(e: WheelEvent): number {
  if (e.deltaMode === 1) return e.deltaY * 16;
  if (e.deltaMode === 2) return e.deltaY * 100;
  return e.deltaY;
}

export function ImagePreviewModal({ libraryFileId, filename, fileSize, onClose }: ImagePreviewModalProps) {
  const { t } = useTranslation();
  const fullscreen = usePreviewFullscreen();
  const { isFullscreen, toggleFullscreen } = fullscreen;
  const containerRef = useRef<HTMLDivElement>(null);
  const imageRef = useRef<HTMLImageElement>(null);

  const [src, setSrc] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The image is laid out fitted to the panel; zoom and pan are a transform on
  // top of that, so zoom 1 is always "fits the window".
  const [zoom, setZoom] = useState(1);
  const [offset, setOffset] = useState<Point>(ORIGIN);
  const [dragging, setDragging] = useState(false);

  const zoomRef = useRef(1);
  const offsetRef = useRef<Point>(ORIGIN);
  const pointersRef = useRef<Map<number, Point>>(new Map());
  const pinchDistanceRef = useRef<number | null>(null);

  // Panning past the edges would strand the image off-screen, so the offset is
  // bounded by however much of it the zoom pushes outside the viewport.
  const clampOffset = useCallback((next: Point, atZoom: number): Point => {
    const container = containerRef.current;
    const image = imageRef.current;
    if (!container || !image) return next;
    const maxX = Math.max(0, (image.offsetWidth * atZoom - container.clientWidth) / 2);
    const maxY = Math.max(0, (image.offsetHeight * atZoom - container.clientHeight) / 2);
    return {
      x: Math.min(maxX, Math.max(-maxX, next.x)),
      y: Math.min(maxY, Math.max(-maxY, next.y)),
    };
  }, []);

  const setView = useCallback((nextZoom: number, nextOffset: Point) => {
    zoomRef.current = nextZoom;
    offsetRef.current = nextOffset;
    setZoom(nextZoom);
    setOffset(nextOffset);
  }, []);

  /** Zoom by `factor`, keeping the point under the pointer where it is. */
  const zoomAt = useCallback(
    (factor: number, clientX?: number, clientY?: number) => {
      const current = zoomRef.current;
      const next = clampZoom(current * factor);
      if (next === current) return;
      const container = containerRef.current;
      const previous = offsetRef.current;
      let nextOffset = { x: previous.x * (next / current), y: previous.y * (next / current) };
      if (container) {
        const rect = container.getBoundingClientRect();
        // The transform grows the image around the container's centre, so an
        // anchored point is expressed relative to that centre.
        const ax = (clientX == null ? rect.left + rect.width / 2 : clientX) - (rect.left + rect.width / 2);
        const ay = (clientY == null ? rect.top + rect.height / 2 : clientY) - (rect.top + rect.height / 2);
        const k = next / current;
        nextOffset = { x: ax - (ax - previous.x) * k, y: ay - (ay - previous.y) * k };
      }
      setView(next, clampOffset(nextOffset, next));
    },
    [clampOffset, setView]
  );

  const resetView = useCallback(() => setView(1, ORIGIN), [setView]);

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    setSrc(null);
    setLoaded(false);
    setError(null);
    setView(1, ORIGIN);

    if (fileSize > IMAGE_PREVIEW_MAX_BYTES) {
      setError(t('fileManager.preview.tooLarge', { size: formatFileSize(fileSize) }));
      return;
    }

    const headers: HeadersInit = {};
    const token = getAuthToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;

    (async () => {
      // Fetched rather than pointed at with <img src>: the download endpoint
      // wants the bearer token, which an <img> cannot send.
      const res = await fetch(api.getLibraryFileDownloadUrl(libraryFileId), { headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      if (cancelled) return;
      objectUrl = URL.createObjectURL(blob);
      setSrc(objectUrl);
    })().catch((err: unknown) => {
      console.error('[image-preview] load failed', err);
      if (!cancelled) setError(t('fileManager.preview.error'));
    });

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [libraryFileId, fileSize, t, setView]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Ctrl/⌘ combinations are the browser's own zoom; leave them alone.
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === '+' || e.key === '=') {
        e.preventDefault();
        zoomAt(ZOOM_STEP);
      } else if (e.key === '-') {
        e.preventDefault();
        zoomAt(1 / ZOOM_STEP);
      } else if (e.key === '0') {
        e.preventDefault();
        resetView();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [resetView, zoomAt]);

  // Native listener: React registers wheel as passive, so preventDefault —
  // which keeps Ctrl+wheel from zooming the whole page — would be ignored.
  // Nothing scrolls here, so a plain wheel zooms as well.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const handleWheel = (e: WheelEvent) => {
      e.preventDefault();
      const delta = Math.max(-WHEEL_DELTA_CLAMP, Math.min(WHEEL_DELTA_CLAMP, wheelDeltaPixels(e)));
      zoomAt(Math.exp(-delta * WHEEL_ZOOM_RATE), e.clientX, e.clientY);
    };
    container.addEventListener('wheel', handleWheel, { passive: false });
    return () => container.removeEventListener('wheel', handleWheel);
  }, [zoomAt, src]);

  // One pointer drags the image once it is bigger than the window, two fingers
  // pinch-zoom around their midpoint. `touch-action: none` on the container
  // hands both gestures here instead of to the browser's scroll / page zoom.
  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    pointersRef.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    pinchDistanceRef.current = null;
    if (pointersRef.current.size === 1 && zoomRef.current > 1) {
      e.currentTarget.setPointerCapture(e.pointerId);
      setDragging(true);
    }
  };

  const handlePointerEnd = (e: React.PointerEvent<HTMLDivElement>) => {
    pointersRef.current.delete(e.pointerId);
    pinchDistanceRef.current = null;
    if (pointersRef.current.size === 0) setDragging(false);
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const pointers = pointersRef.current;
    const previous = pointers.get(e.pointerId);
    if (!previous) return;
    // A button released outside this container never reaches onPointerUp (no
    // capture is taken below zoom 1), and the stale entry would then pan the
    // image under a bare cursor — or count as a second finger on a hybrid
    // device, turning the next one-finger drag into a pinch (#2976).
    if (e.pointerType === 'mouse' && e.buttons === 0) {
      handlePointerEnd(e);
      return;
    }
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

    if (pointers.size === 1) {
      if (zoomRef.current <= 1) return;
      const moved = { x: offsetRef.current.x + (e.clientX - previous.x), y: offsetRef.current.y + (e.clientY - previous.y) };
      setView(zoomRef.current, clampOffset(moved, zoomRef.current));
      return;
    }
    if (pointers.size !== 2) return;
    const [a, b] = Array.from(pointers.values());
    const distance = Math.hypot(a.x - b.x, a.y - b.y);
    const last = pinchDistanceRef.current;
    pinchDistanceRef.current = distance;
    if (last != null && last > 0 && distance > 0) {
      zoomAt(distance / last, (a.x + b.x) / 2, (a.y + b.y) / 2);
    }
  };

  const canPan = zoom > 1;

  return (
    <PreviewModalShell
      title={filename}
      fullscreen={fullscreen}
      onClose={onClose}
      icon={<ImageIcon className="w-5 h-5 text-bambu-green flex-shrink-0" />}
      actions={
        src ? (
          <>
            <button
              onClick={() => zoomAt(1 / ZOOM_STEP)}
              className={previewIconButtonClass}
              aria-label={t('fileManager.preview.zoomOut')}
            >
              <ZoomOut className="w-4 h-4" />
            </button>
            <button
              onClick={() => zoomAt(ZOOM_STEP)}
              className={previewIconButtonClass}
              aria-label={t('fileManager.preview.zoomIn')}
            >
              <ZoomIn className="w-4 h-4" />
            </button>
            <button onClick={resetView} className={previewIconButtonClass} aria-label={t('fileManager.preview.resetZoom')}>
              <RotateCcw className="w-4 h-4" />
            </button>
          </>
        ) : undefined
      }
    >
      <div
        ref={containerRef}
        data-testid="image-preview-content"
        className={`relative flex-1 min-h-0 overflow-hidden bg-bambu-dark flex items-center justify-center touch-none ${
          isFullscreen ? '' : 'rounded-b-lg'
        } ${canPan ? (dragging ? 'cursor-grabbing' : 'cursor-grab') : ''}`}
        onDoubleClick={toggleFullscreen}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerEnd}
        onPointerCancel={handlePointerEnd}
        onPointerLeave={handlePointerEnd}
      >
        {error ? (
          <p className="text-bambu-gray text-center p-6">{error}</p>
        ) : (
          <>
            {src && (
              <img
                ref={imageRef}
                src={src}
                alt={filename}
                draggable={false}
                onLoad={() => setLoaded(true)}
                onError={() => setError(t('fileManager.preview.error'))}
                style={{ transform: `translate(${offset.x}px, ${offset.y}px) scale(${zoom})` }}
                className="max-w-full max-h-full object-contain select-none"
              />
            )}
            {!loaded && (
              <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
              </div>
            )}
          </>
        )}
      </div>
    </PreviewModalShell>
  );
}
