import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronLeft, ChevronRight, FileText, Loader2, ZoomIn, ZoomOut } from 'lucide-react';
import type { PDFDocumentLoadingTask, PDFDocumentProxy, RenderTask } from 'pdfjs-dist';
import { api, getAuthToken } from '../api/client';
import { formatFileSize } from '../utils/file';
import { PreviewModalShell, previewIconButtonClass } from './PreviewModalShell';
import { usePreviewFullscreen } from '../hooks/usePreviewFullscreen';

// Fetching and parsing happen fully in the browser; beyond this size the
// preview shows a notice instead of stalling the tab on a giant download.
export const PDF_PREVIEW_MAX_BYTES = 50 * 1024 * 1024;

const MIN_ZOOM = 0.4;
const MAX_ZOOM = 4;
// One button press, one key press, one mouse-wheel notch.
const ZOOM_STEP = 1.25;
// A pinch arrives as many small ctrlKey wheel events, a mouse notch as one
// ±100; clamping the delta before the exponent gives the notch one ZOOM_STEP
// and the pinch a smooth ramp.
const WHEEL_DELTA_CLAMP = 30;
const WHEEL_ZOOM_RATE = Math.log(ZOOM_STEP) / WHEEL_DELTA_CLAMP;
// Wheel zoom changes the displayed size at once (CSS) and re-rasterises after
// the gesture settles, so a scroll burst costs one pdf.js render, not twenty.
const RERENDER_DEBOUNCE_MS = 150;
// iOS Safari refuses to back a canvas past roughly 16.7M pixels and hands back
// a blank one instead of failing; zoom 4 on a dpr-2 screen crosses that on any
// ordinary page. Past the cap the raster stops getting sharper, which costs
// detail rather than the whole page.
const MAX_CANVAS_PIXELS = 16 * 1024 * 1024;
// pdf.js fetches its CMaps, ICC profiles, standard fonts and wasm decoders at
// runtime instead of bundling them; vite.config.ts publishes them here. Left
// unset, CJK text, JPEG2000/JBIG2 images and ICC colour silently fail (#2976).
const PDFJS_ASSET_BASE = `${import.meta.env.BASE_URL}assets/pdfjs/`;

const clampZoom = (zoom: number) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom));

interface PdfPreviewModalProps {
  libraryFileId: number;
  filename: string;
  fileSize: number;
  onClose: () => void;
  /** Called once with a 256px PNG of the first page, for the grid thumbnail (#2976). */
  onSnapshot?: (blob: Blob) => void;
}

// Where a zoom step should keep the page still: a point on the canvas
// (fractions) pinned to a point in the scroll viewport (pixels).
interface ZoomAnchor {
  fx: number;
  fy: number;
  px: number;
  py: number;
}

interface ActivePointer {
  x: number;
  y: number;
}

// Square 256px crop of the rendered page, white-backed like a paper page.
function snapshotFromCanvas(source: HTMLCanvasElement): Promise<Blob | null> {
  const size = 256;
  const target = document.createElement('canvas');
  target.width = size;
  target.height = size;
  const ctx = target.getContext('2d');
  if (!ctx) return Promise.resolve(null);
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, size, size);
  const scale = size / Math.max(source.width, source.height);
  const w = source.width * scale;
  const h = source.height * scale;
  ctx.drawImage(source, (size - w) / 2, (size - h) / 2, w, h);
  return new Promise((resolve) => target.toBlob(resolve, 'image/png'));
}

// deltaMode 1 is lines (Firefox mouse wheel), 2 is pages; both scaled to pixels.
function wheelDeltaPixels(e: WheelEvent): number {
  if (e.deltaMode === 1) return e.deltaY * 16;
  if (e.deltaMode === 2) return e.deltaY * 100;
  return e.deltaY;
}

export function PdfPreviewModal({ libraryFileId, filename, fileSize, onClose, onSnapshot }: PdfPreviewModalProps) {
  const { t } = useTranslation();
  const fullscreen = usePreviewFullscreen();
  const { panelRef, isFullscreen, toggleFullscreen } = fullscreen;
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const renderTaskRef = useRef<RenderTask | null>(null);
  const snapshotSentRef = useRef(false);
  const onSnapshotRef = useRef(onSnapshot);
  useEffect(() => {
    onSnapshotRef.current = onSnapshot;
  });

  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [pageNum, setPageNum] = useState(1);
  // `zoom` is what the user sees (applied as CSS size immediately);
  // `renderZoom` trails it and drives the pdf.js raster.
  const [zoom, setZoom] = useState(1);
  const [renderZoom, setRenderZoom] = useState(1);
  const [layoutVersion, setLayoutVersion] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [rendering, setRendering] = useState(true);

  const zoomRef = useRef(zoom);
  // CSS size of the page at zoom 1, known once a page has been rendered.
  const baseCssSizeRef = useRef<{ width: number; height: number } | null>(null);
  const anchorRef = useRef<ZoomAnchor | null>(null);
  const pointersRef = useRef<Map<number, ActivePointer>>(new Map());
  const pinchDistanceRef = useRef<number | null>(null);

  const zoomAt = useCallback((factor: number, clientX?: number, clientY?: number) => {
    const container = containerRef.current;
    const canvas = canvasRef.current;
    if (container && canvas) {
      const viewport = container.getBoundingClientRect();
      const page = canvas.getBoundingClientRect();
      // Without a pointer, keep whatever is in the middle of the viewport.
      const px = clientX == null ? viewport.width / 2 : clientX - viewport.left;
      const py = clientY == null ? viewport.height / 2 : clientY - viewport.top;
      anchorRef.current =
        page.width > 0 && page.height > 0
          ? {
              fx: (px + viewport.left - page.left) / page.width,
              fy: (py + viewport.top - page.top) / page.height,
              px,
              py,
            }
          : null;
    }
    setZoom((current) => clampZoom(current * factor));
  }, []);

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
        anchorRef.current = null;
        setZoom(1);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [zoomAt]);

  // Wheel: Ctrl/⌘ (which is also how a trackpad pinch arrives) always zooms;
  // a plain wheel zooms only while the page fits the viewport, because then
  // there is nothing to scroll. Native listener: React registers wheel as
  // passive, so preventDefault — needed to keep the browser from zooming the
  // whole page — would be ignored there.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const handleWheel = (e: WheelEvent) => {
      if (!canvasRef.current || !baseCssSizeRef.current) return;
      const modified = e.ctrlKey || e.metaKey;
      if (!modified) {
        const fits =
          container.scrollHeight <= container.clientHeight && container.scrollWidth <= container.clientWidth;
        if (!fits) return;
      }
      e.preventDefault();
      const delta = Math.max(-WHEEL_DELTA_CLAMP, Math.min(WHEEL_DELTA_CLAMP, wheelDeltaPixels(e)));
      zoomAt(Math.exp(-delta * WHEEL_ZOOM_RATE), e.clientX, e.clientY);
    };
    container.addEventListener('wheel', handleWheel, { passive: false });
    return () => container.removeEventListener('wheel', handleWheel);
  }, [zoomAt]);

  // Touch: one finger pans the page, two fingers pinch-zoom around their
  // midpoint. `touch-action: none` on the container hands both gestures to
  // these handlers instead of the browser's scroll / page-zoom.
  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.pointerType !== 'touch') return;
    pointersRef.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    pinchDistanceRef.current = null;
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.pointerType !== 'touch') return;
    const pointers = pointersRef.current;
    const previous = pointers.get(e.pointerId);
    if (!previous) return;
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

    if (pointers.size === 1) {
      const container = containerRef.current;
      if (container) {
        container.scrollLeft -= e.clientX - previous.x;
        container.scrollTop -= e.clientY - previous.y;
      }
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

  const handlePointerEnd = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.pointerType !== 'touch') return;
    pointersRef.current.delete(e.pointerId);
    pinchDistanceRef.current = null;
  };

  // Show the new zoom right away by resizing the existing raster, and keep
  // the anchored point under the pointer. The sharp re-render follows.
  useLayoutEffect(() => {
    zoomRef.current = zoom;
    const canvas = canvasRef.current;
    const container = containerRef.current;
    const base = baseCssSizeRef.current;
    if (!canvas || !container || !base) return;
    canvas.style.width = `${base.width * zoom}px`;
    canvas.style.height = `${base.height * zoom}px`;
    const anchor = anchorRef.current;
    anchorRef.current = null;
    if (!anchor) return;
    container.scrollLeft = canvas.offsetLeft + anchor.fx * canvas.offsetWidth - anchor.px;
    container.scrollTop = canvas.offsetTop + anchor.fy * canvas.offsetHeight - anchor.py;
  }, [zoom]);

  useEffect(() => {
    if (zoom === renderZoom) return;
    const id = window.setTimeout(() => setRenderZoom(zoom), RERENDER_DEBOUNCE_MS);
    return () => window.clearTimeout(id);
  }, [zoom, renderZoom]);

  // The page is fitted to the panel width, so a panel resize (fullscreen,
  // window resize) needs a fresh fit. Watching the panel rather than the
  // scroll container keeps a scrollbar appearing from re-fitting the page.
  useEffect(() => {
    const panel = panelRef.current;
    if (!panel) return;
    const observer = new ResizeObserver(() => setLayoutVersion((v) => v + 1));
    observer.observe(panel);
    return () => observer.disconnect();
  }, [panelRef]);

  // Load the document. pdf.js is imported on demand so the viewer and its
  // worker stay out of the main bundle.
  useEffect(() => {
    let cancelled = false;
    let loadingTask: PDFDocumentLoadingTask | null = null;
    setDoc(null);
    setError(null);
    setPageNum(1);
    setRendering(true);

    if (fileSize > PDF_PREVIEW_MAX_BYTES) {
      setError(t('fileManager.preview.tooLarge', { size: formatFileSize(fileSize) }));
      setRendering(false);
      return;
    }

    const headers: HeadersInit = {};
    const token = getAuthToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;

    (async () => {
      const res = await fetch(api.getLibraryFileDownloadUrl(libraryFileId), { headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const buffer = await res.arrayBuffer();
      const pdfjs = await import('pdfjs-dist');
      if (!pdfjs.GlobalWorkerOptions.workerSrc) {
        pdfjs.GlobalWorkerOptions.workerSrc = (await import('pdfjs-dist/build/pdf.worker.min.mjs?url')).default;
      }
      loadingTask = pdfjs.getDocument({
        data: new Uint8Array(buffer),
        cMapUrl: `${PDFJS_ASSET_BASE}cmaps/`,
        iccUrl: `${PDFJS_ASSET_BASE}iccs/`,
        standardFontDataUrl: `${PDFJS_ASSET_BASE}standard_fonts/`,
        wasmUrl: `${PDFJS_ASSET_BASE}wasm/`,
      });
      if (cancelled) {
        // The modal closed during the fetch/import above, so cleanup ran while
        // `loadingTask` was still null and left this task — and its worker —
        // running. Nothing else will destroy it.
        loadingTask.destroy();
        return;
      }
      const loaded = await loadingTask.promise;
      if (cancelled) {
        // Cleanup ran after the assignment above, so it destroyed the task
        // already — and with it the document.
        return;
      }
      setDoc(loaded);
    })().catch((err: unknown) => {
      // The reason never reaches the UI beyond a generic line, so leave it in
      // the console: an HTTP status, a refused worker or a parser failure each
      // need a different fix, and "cannot be previewed" hides which one it was.
      console.error('[pdf-preview] load failed', err);
      if (!cancelled) {
        setError(t('fileManager.preview.error'));
        setRendering(false);
      }
    });

    return () => {
      cancelled = true;
      renderTaskRef.current?.cancel();
      renderTaskRef.current = null;
      // Destroying the loading task also destroys the document and worker.
      loadingTask?.destroy();
    };
  }, [libraryFileId, fileSize, t]);

  // Render the current page into the canvas.
  useEffect(() => {
    if (!doc) return;
    let cancelled = false;
    setRendering(true);

    (async () => {
      const page = await doc.getPage(pageNum);
      const canvas = canvasRef.current;
      const container = containerRef.current;
      if (!canvas || !container || cancelled) return;

      const baseViewport = page.getViewport({ scale: 1 });
      // Fit the page width to the panel at zoom 1; render at device pixels.
      const fitScale = Math.max((container.clientWidth - 32) / baseViewport.width, 0.1);
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const wanted = fitScale * renderZoom * dpr;
      const pixels = baseViewport.width * wanted * (baseViewport.height * wanted);
      const scale = pixels > MAX_CANVAS_PIXELS ? wanted * Math.sqrt(MAX_CANVAS_PIXELS / pixels) : wanted;
      const viewport = page.getViewport({ scale });
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      baseCssSizeRef.current = { width: baseViewport.width * fitScale, height: baseViewport.height * fitScale };
      // The displayed zoom may already be ahead of this raster.
      canvas.style.width = `${baseCssSizeRef.current.width * zoomRef.current}px`;
      canvas.style.height = `${baseCssSizeRef.current.height * zoomRef.current}px`;

      renderTaskRef.current?.cancel();
      const task = page.render({ canvas, viewport });
      renderTaskRef.current = task;
      await task.promise;
      if (cancelled) return;
      setRendering(false);

      if (pageNum === 1 && onSnapshotRef.current && !snapshotSentRef.current) {
        snapshotSentRef.current = true;
        const blob = await snapshotFromCanvas(canvas);
        if (blob && !cancelled) onSnapshotRef.current?.(blob);
      }
    })().catch((err: unknown) => {
      // A cancelled render throws RenderingCancelledException — not an error.
      if (!cancelled && (err as { name?: string })?.name !== 'RenderingCancelledException') {
        console.error('[pdf-preview] render failed', err);
        setError(t('fileManager.preview.error'));
        setRendering(false);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [doc, pageNum, renderZoom, layoutVersion, t]);

  const pageCount = doc?.numPages ?? 0;
  const iconButtonClass = previewIconButtonClass;

  return (
    <PreviewModalShell
      title={filename}
      fullscreen={fullscreen}
      onClose={onClose}
      icon={<FileText className="w-5 h-5 text-bambu-green flex-shrink-0" />}
      actions={
        <>
          {doc && pageCount > 1 && (
            <div className="flex items-center gap-1 mr-2">
              <button
                onClick={() => setPageNum((p) => Math.max(1, p - 1))}
                disabled={pageNum <= 1}
                className={`${iconButtonClass} disabled:opacity-40 disabled:cursor-not-allowed`}
                aria-label={t('fileManager.preview.prevPage')}
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              <span className="text-sm text-bambu-gray whitespace-nowrap">
                {t('fileManager.preview.page', { current: pageNum, total: pageCount })}
              </span>
              <button
                onClick={() => setPageNum((p) => Math.min(pageCount, p + 1))}
                disabled={pageNum >= pageCount}
                className={`${iconButtonClass} disabled:opacity-40 disabled:cursor-not-allowed`}
                aria-label={t('fileManager.preview.nextPage')}
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          )}
          {doc && (
            <>
              <button
                onClick={() => zoomAt(1 / ZOOM_STEP)}
                className={iconButtonClass}
                aria-label={t('fileManager.preview.zoomOut')}
              >
                <ZoomOut className="w-4 h-4" />
              </button>
              <button
                onClick={() => zoomAt(ZOOM_STEP)}
                className={iconButtonClass}
                aria-label={t('fileManager.preview.zoomIn')}
              >
                <ZoomIn className="w-4 h-4" />
              </button>
            </>
          )}
        </>
      }
    >
      {/* Page */}
      <div
        ref={containerRef}
        data-testid="pdf-preview-page"
        className={`relative flex-1 min-h-0 overflow-auto bg-bambu-dark p-4 touch-none ${isFullscreen ? '' : 'rounded-b-lg'}`}
        onDoubleClick={toggleFullscreen}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerEnd}
        onPointerCancel={handlePointerEnd}
        onPointerLeave={handlePointerEnd}
      >
        {error ? (
          <div className="h-full flex items-center justify-center">
            <p className="text-bambu-gray text-center">{error}</p>
          </div>
        ) : (
          <div className="flex justify-center min-w-fit">
            <canvas ref={canvasRef} className="shadow-lg" />
          </div>
        )}
        {!error && rendering && (
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
          </div>
        )}
      </div>
    </PreviewModalShell>
  );
}
