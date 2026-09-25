import { useEffect, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Maximize2, Minimize2, X } from 'lucide-react';
import type { PreviewFullscreen } from '../hooks/usePreviewFullscreen';

// One size for every file preview (#2976). Each modal used to carry its own
// max-width (4xl/5xl/6xl at 80-85vh), which left most of a wide screen empty
// for a window the user opened precisely to look at something closely.
// min() keeps the panel off the edges on a laptop and stops it stretching to
// a wall of pixels on an ultrawide.
export const PREVIEW_PANEL_SIZE_CLASS = 'w-[min(1800px,96vw)] h-[94vh]';

// Header icon buttons of every preview.
export const previewIconButtonClass =
  'p-1.5 rounded hover:bg-bambu-dark text-bambu-gray hover:text-white transition-colors';

interface PreviewModalShellProps {
  title: string;
  fullscreen: PreviewFullscreen;
  onClose: () => void;
  /** Type icon left of the title. */
  icon?: ReactNode;
  /** Rendered after the title, e.g. the 3D viewer's object-count badge. */
  titleExtra?: ReactNode;
  /** This preview's own header buttons, left of the fullscreen/close pair. */
  actions?: ReactNode;
  /** The 3D viewer closes on a backdrop click; the document previews do not. */
  closeOnBackdropClick?: boolean;
  /** Panel content: tabs, body and footers, as direct flex children. */
  children: ReactNode;
}

/**
 * Backdrop, panel sizing and header row shared by every file preview (#2976).
 *
 * Esc closes the modal, except while the browser owns it: in fullscreen Esc
 * leaves fullscreen and the preview stays open. Double-click-to-fullscreen is
 * wired by each modal on its own content area, because only the modal knows
 * which part of the panel is the preview.
 */
export function PreviewModalShell({
  title,
  fullscreen,
  onClose,
  icon,
  titleExtra,
  actions,
  closeOnBackdropClick,
  children,
}: PreviewModalShellProps) {
  const { t } = useTranslation();
  const { panelRef, isFullscreen, toggleFullscreen } = fullscreen;

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !document.fullscreenElement) onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  return (
    <div
      className={`fixed inset-0 bg-black/70 flex items-center justify-center z-50 ${isFullscreen ? 'p-0' : 'p-2 sm:p-4'}`}
      onClick={closeOnBackdropClick ? onClose : undefined}
    >
      <div
        ref={panelRef}
        className={`bg-bambu-dark-secondary border border-bambu-dark-tertiary flex flex-col ${
          isFullscreen
            ? 'w-full h-full max-w-none rounded-none'
            : `${PREVIEW_PANEL_SIZE_CLASS} max-w-full max-h-full rounded-lg`
        }`}
        onClick={closeOnBackdropClick ? (e) => e.stopPropagation() : undefined}
      >
        <div className="flex items-center justify-between gap-4 p-4 border-b border-bambu-dark-tertiary flex-shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            {icon}
            <h2 className="text-lg font-semibold text-white truncate">{title}</h2>
            {titleExtra}
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            {actions}
            <button
              onClick={toggleFullscreen}
              className={previewIconButtonClass}
              aria-label={isFullscreen ? t('fileManager.preview.exitFullscreen') : t('fileManager.preview.fullscreen')}
              title={isFullscreen ? t('fileManager.preview.exitFullscreen') : t('fileManager.preview.fullscreen')}
            >
              {isFullscreen ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}
            </button>
            <button onClick={onClose} className={previewIconButtonClass} aria-label={t('common.close')}>
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>
        {children}
      </div>
    </div>
  );
}
