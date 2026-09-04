import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronLeft, ChevronRight, FileText, Loader2, X, ZoomIn, ZoomOut } from 'lucide-react';
import type { PDFDocumentLoadingTask, PDFDocumentProxy, RenderTask } from 'pdfjs-dist';
import { api, getAuthToken } from '../api/client';
import { formatFileSize } from '../utils/file';

// Fetching and parsing happen fully in the browser; beyond this size the
// preview shows a notice instead of stalling the tab on a giant download.
export const PDF_PREVIEW_MAX_BYTES = 50 * 1024 * 1024;

interface PdfPreviewModalProps {
  libraryFileId: number;
  filename: string;
  fileSize: number;
  onClose: () => void;
  /** Called once with a 256px PNG of the first page, for the grid thumbnail (#2976). */
  onSnapshot?: (blob: Blob) => void;
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

export function PdfPreviewModal({ libraryFileId, filename, fileSize, onClose, onSnapshot }: PdfPreviewModalProps) {
  const { t } = useTranslation();
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
  const [zoom, setZoom] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [rendering, setRendering] = useState(true);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

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
      loadingTask = pdfjs.getDocument({ data: new Uint8Array(buffer) });
      const loaded = await loadingTask.promise;
      if (cancelled) {
        // Cleanup below already ran; destroying the task tears down the doc.
        return;
      }
      setDoc(loaded);
    })().catch(() => {
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
      const viewport = page.getViewport({ scale: fitScale * zoom * dpr });
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      canvas.style.width = `${viewport.width / dpr}px`;
      canvas.style.height = `${viewport.height / dpr}px`;

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
        setError(t('fileManager.preview.error'));
        setRendering(false);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [doc, pageNum, zoom, t]);

  const pageCount = doc?.numPages ?? 0;

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div className="bg-bambu-dark-secondary rounded-lg w-full max-w-5xl h-[85vh] border border-bambu-dark-tertiary flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
          <div className="flex items-center gap-2 min-w-0">
            <FileText className="w-5 h-5 text-bambu-green flex-shrink-0" />
            <h2 className="text-lg font-semibold text-white truncate">{filename}</h2>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            {doc && pageCount > 1 && (
              <div className="flex items-center gap-1 mr-2">
                <button
                  onClick={() => setPageNum((p) => Math.max(1, p - 1))}
                  disabled={pageNum <= 1}
                  className="p-1.5 rounded hover:bg-bambu-dark text-bambu-gray hover:text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
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
                  className="p-1.5 rounded hover:bg-bambu-dark text-bambu-gray hover:text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                  aria-label={t('fileManager.preview.nextPage')}
                >
                  <ChevronRight className="w-4 h-4" />
                </button>
              </div>
            )}
            {doc && (
              <>
                <button
                  onClick={() => setZoom((z) => Math.max(0.4, z * 0.8))}
                  className="p-1.5 rounded hover:bg-bambu-dark text-bambu-gray hover:text-white transition-colors"
                  aria-label={t('fileManager.preview.zoomOut')}
                >
                  <ZoomOut className="w-4 h-4" />
                </button>
                <button
                  onClick={() => setZoom((z) => Math.min(4, z * 1.25))}
                  className="p-1.5 rounded hover:bg-bambu-dark text-bambu-gray hover:text-white transition-colors"
                  aria-label={t('fileManager.preview.zoomIn')}
                >
                  <ZoomIn className="w-4 h-4" />
                </button>
              </>
            )}
            <button
              onClick={onClose}
              className="p-1.5 rounded hover:bg-bambu-dark text-bambu-gray hover:text-white transition-colors"
              aria-label={t('common.close')}
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Page */}
        <div ref={containerRef} className="relative flex-1 min-h-0 overflow-auto bg-bambu-dark rounded-b-lg p-4">
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
      </div>
    </div>
  );
}
