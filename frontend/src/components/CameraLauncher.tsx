import { useCallback, useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type FocusEvent } from 'react';
import { createPortal } from 'react-dom';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Camera, Check, Loader2 } from 'lucide-react';
import { api } from '../api/client';
import { useAuth } from '../contexts/AuthContext';
import { useEmbeddedCameras } from '../contexts/EmbeddedCameraContext';

interface CameraLauncherProps {
  /** Where the printer list opens: beside the Printers row of the sidebar, or down from the compact header. */
  placement: 'side' | 'down';
  /** Smaller on the sidebar's Printers row (xs in the collapsed sidebar); on that row's green when it is the current page. */
  size?: 'md' | 'sm' | 'xs';
  onAccent?: boolean;
  /** Controlled by the layout's keyboard shortcut when given. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  className?: string;
}

const MENU_WIDTH = 256;
const EDGE = 8;

/**
 * Where the list goes, in viewport coordinates. It is rendered into
 * document.body: inside the sidebar (z-30) or the compact header (z-40) it
 * would sit under the camera overlays (z-40, later in the page), and the
 * collapsed sidebar's scrolling footer would clip it to 48px.
 */
function menuPosition(trigger: DOMRect, placement: 'side' | 'down'): CSSProperties {
  const maxLeft = Math.max(EDGE, window.innerWidth - MENU_WIDTH - EDGE);
  if (placement === 'down') {
    return { top: trigger.bottom + EDGE, left: Math.min(Math.max(EDGE, trigger.right - MENU_WIDTH), maxLeft) };
  }
  // Out to the right of the sidebar, level with the row.
  return { top: Math.max(EDGE, trigger.top - EDGE), left: Math.min(trigger.right + EDGE, maxLeft) };
}

/**
 * A printer's camera as an overlay from any page (#2737), without going back
 * to the Printers page for its card. Lists the printers the user can see; one
 * already open closes again from here.
 */
export function CameraLauncher({
  placement,
  size = 'md',
  onAccent = false,
  open: openProp,
  onOpenChange,
  className = '',
}: CameraLauncherProps) {
  const { t } = useTranslation();
  const { hasPermission } = useAuth();
  const { openCameras, openEmbeddedCamera, closeEmbeddedCamera } = useEmbeddedCameras();
  const [ownOpen, setOwnOpen] = useState(false);
  const open = openProp ?? ownOpen;
  const setOpen = useCallback(
    (next: boolean) => {
      if (openProp === undefined) setOwnOpen(next);
      onOpenChange?.(next);
    },
    [openProp, onOpenChange],
  );
  const [position, setPosition] = useState<CSSProperties>({});
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  // As the overlays: the printer list takes printers:read.
  const canView = hasPermission('camera:view') && hasPermission('printers:read');

  const { data: printers, isLoading } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
    enabled: open && canView,
  });

  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      if (buttonRef.current) setPosition(menuPosition(buttonRef.current.getBoundingClientRect(), placement));
    };
    place();
    window.addEventListener('resize', place);
    return () => window.removeEventListener('resize', place);
  }, [open, placement]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      const target = e.target as Node;
      if (!rootRef.current?.contains(target) && !menuRef.current?.contains(target)) setOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open, setOpen]);

  // The list is drawn at the end of the page, so Tab from the button would
  // not reach it: focus moves into it, and leaving it closes it.
  const hasItems = !!printers && printers.length > 0;
  useEffect(() => {
    if (open && hasItems) menuRef.current?.querySelector<HTMLButtonElement>('button')?.focus();
  }, [open, hasItems]);
  const onMenuBlur = (e: FocusEvent<HTMLDivElement>) => {
    const next = e.relatedTarget as Node | null;
    if (next && !menuRef.current?.contains(next) && !rootRef.current?.contains(next)) setOpen(false);
  };

  if (!canView) return null;

  const openIds = new Set(openCameras.map(c => c.id));

  return (
    <div ref={rootRef} className="relative">
      <button
        ref={buttonRef}
        onClick={() => setOpen(!open)}
        className={`${{ md: 'p-2 rounded-lg', sm: 'p-1 rounded-md', xs: 'p-0.5 rounded' }[size]} transition-colors ${
          onAccent
            ? 'text-white hover:bg-white/20'
            : `hover:bg-bambu-dark-tertiary ${open || openIds.size > 0 ? 'text-bambu-green' : 'text-bambu-gray-light hover:text-white'}`
        } ${className}`}
        title={t('cameraLauncher.title')}
        aria-label={t('cameraLauncher.title')}
        aria-expanded={open}
      >
        <Camera className={{ md: 'w-5 h-5', sm: 'w-4 h-4', xs: 'w-3 h-3' }[size]} />
      </button>
      {open && createPortal(
        <div
          ref={menuRef}
          onBlur={onMenuBlur}
          role="region"
          aria-label={t('cameraLauncher.title')}
          style={position}
          className="fixed w-64 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-xl shadow-xl z-50"
        >
          <div className="px-4 py-3 border-b border-bambu-dark-tertiary">
            <h3 className="text-sm font-semibold text-white flex items-center gap-2">
              <Camera className="w-4 h-4 text-bambu-green" />
              {t('cameraLauncher.title')}
            </h3>
          </div>
          <div className="p-2 max-h-80 overflow-y-auto">
            {isLoading ? (
              <div className="flex items-center justify-center py-6">
                <Loader2 className="w-5 h-5 text-bambu-gray animate-spin" />
              </div>
            ) : !printers || printers.length === 0 ? (
              <p className="text-sm text-bambu-gray text-center py-4 px-2">{t('cameraLauncher.noPrinters')}</p>
            ) : (
              printers.map(printer => {
                const isOpen = openIds.has(printer.id);
                return (
                  <button
                    key={printer.id}
                    aria-pressed={isOpen}
                    onClick={() => {
                      if (isOpen) {
                        closeEmbeddedCamera(printer.id);
                      } else {
                        openEmbeddedCamera(printer.id, printer.name);
                        setOpen(false);
                      }
                    }}
                    className="w-full flex items-center justify-between gap-2 py-2 px-3 text-left text-sm text-white hover:bg-bambu-dark-tertiary rounded-lg transition-colors"
                    title={isOpen ? t('cameraLauncher.close') : t('cameraLauncher.open')}
                  >
                    <span className="truncate">{printer.name}</span>
                    {isOpen && <Check className="w-4 h-4 text-bambu-green flex-shrink-0" />}
                  </button>
                );
              })
            )}
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}
