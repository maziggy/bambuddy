import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { AlertCircle, Box, CheckSquare, Loader2, Maximize2, Square, X } from 'lucide-react';
import { api, withStreamToken } from '../api/client';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import { pickObjectIdAt, plateClickToMaskPoint } from '../utils/skipObjects';
import { ConfirmModal } from './ConfirmModal';

export const SkipObjectsIcon = ({ className }: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
    <rect x="2" y="15" width="5" height="5" rx="0.5" />
    <rect x="9.5" y="15" width="5" height="5" rx="0.5" fill="currentColor" opacity="0.3" />
    <rect x="17" y="15" width="5" height="5" rx="0.5" />
    <path d="M4 12 C4 6, 14 6, 14 12" />
    <polyline points="12,10 14,12 12,14" />
  </svg>
);

interface SkipObjectsModalProps {
  printerId: number;
  isOpen: boolean;
  onClose: () => void;
}

interface PrintableObject {
  id: number;
  name: string;
  x: number | null;
  y: number | null;
  skipped: boolean;
}

export function SkipObjectsModal({ printerId, isOpen, onClose }: SkipObjectsModalProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const { hasPermission } = useAuth();
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [confirming, setConfirming] = useState(false);
  const [enlarged, setEnlarged] = useState(false);
  const [pickReady, setPickReady] = useState(false);
  const pickDataRef = useRef<ImageData | null>(null);
  const overlayRef = useRef<HTMLCanvasElement | null>(null);
  const enlargedOverlayRef = useRef<HTMLCanvasElement | null>(null);

  const { data: status } = useQuery({
    queryKey: ['printerStatus', printerId],
    queryFn: () => api.getPrinterStatus(printerId),
    refetchInterval: 30000,
    enabled: isOpen,
  });

  const { data: objectsData, refetch: refetchObjects } = useQuery({
    queryKey: ['printableObjects', printerId],
    queryFn: () => api.getPrintableObjects(printerId),
    enabled: isOpen,
    refetchInterval: isOpen ? 5000 : false,
  });

  const hasObjects = (objectsData?.objects.length ?? 0) > 0;
  const topViewUrl = hasObjects && status?.cover_url
    ? withStreamToken(`${status.cover_url}?view=top`)
    : null;
  const pickViewUrl = hasObjects && status?.cover_url
    ? withStreamToken(`${status.cover_url}?view=pick`)
    : null;

  const activeObjects = useMemo(
    () => (objectsData?.objects ?? []).filter((object) => !object.skipped),
    [objectsData],
  );
  const selectedObjects = useMemo(
    () => activeObjects.filter((object) => selectedIds.has(object.id)),
    [activeObjects, selectedIds],
  );
  const allSelected = activeObjects.length > 0 && selectedObjects.length === activeObjects.length;
  const skippingAllRemaining = allSelected && selectedIds.size > 0;
  const canSubmit = selectedIds.size > 0
    && (status?.layer_num ?? 0) > 1
    && hasPermission('printers:control');

  const skipObjectsMutation = useMutation({
    mutationFn: (objectIds: number[]) => api.skipObjects(printerId, objectIds),
    onSuccess: async (data) => {
      showToast(data.message || t('printers.skipObjects.objectsSkipped'));
      // Refresh before closing: this modal is the only on-demand refetch of the
      // shared printableObjects query, so the printer card behind it would keep
      // showing the pre-skip count otherwise.
      await refetchObjects();
      setConfirming(false);
      setSelectedIds(new Set());
      onClose();
    },
    onError: (error: Error) => {
      setConfirming(false);
      showToast(error.message || t('printers.toast.failedToSkipObjects'), 'error');
    },
  });

  useEffect(() => {
    if (isOpen) return;
    setSelectedIds(new Set());
    setConfirming(false);
    setEnlarged(false);
  }, [isOpen]);

  useEffect(() => {
    pickDataRef.current = null;
    setPickReady(false);
    if (!isOpen || !pickViewUrl) return;

    let cancelled = false;
    const image = new Image();
    image.onload = () => {
      if (cancelled) return;
      const canvas = document.createElement('canvas');
      canvas.width = image.naturalWidth || 512;
      canvas.height = image.naturalHeight || 512;
      const context = canvas.getContext('2d', { willReadFrequently: true });
      if (!context) return;
      context.drawImage(image, 0, 0);
      pickDataRef.current = context.getImageData(0, 0, canvas.width, canvas.height);
      setPickReady(true);
    };
    image.onerror = () => {
      if (!cancelled) setPickReady(false);
    };
    image.src = pickViewUrl;

    return () => {
      cancelled = true;
    };
  }, [isOpen, pickViewUrl]);

  useEffect(() => {
    const pickData = pickDataRef.current;
    if (!pickData || !objectsData) return;

    const skippedIds = new Set(objectsData.objects.filter((object) => object.skipped).map((object) => object.id));
    for (const canvas of [overlayRef.current, enlargedOverlayRef.current]) {
      if (!canvas) continue;
      canvas.width = pickData.width;
      canvas.height = pickData.height;
      const context = canvas.getContext('2d');
      if (!context) continue;
      const overlay = context.createImageData(pickData.width, pickData.height);

      for (let offset = 0; offset < pickData.data.length; offset += 4) {
        const objectId = pickData.data[offset] + (pickData.data[offset + 1] << 8) + (pickData.data[offset + 2] << 16);
        const selected = selectedIds.has(objectId);
        const skipped = skippedIds.has(objectId);
        if (!selected && !skipped) continue;
        const pixel = offset / 4;
        const x = pixel % pickData.width;
        const y = Math.floor(pixel / pickData.width);
        const stripe = ((x + y) % 14) < 7;
        overlay.data[offset] = selected ? (stripe ? 37 : 74) : 148;
        overlay.data[offset + 1] = selected ? (stripe ? 199 : 222) : 163;
        overlay.data[offset + 2] = selected ? (stripe ? 91 : 128) : 184;
        overlay.data[offset + 3] = selected ? (stripe ? 205 : 145) : 175;
      }
      context.putImageData(overlay, 0, 0);
    }
  }, [enlarged, objectsData, pickReady, selectedIds]);

  const toggleObject = (object: PrintableObject) => {
    if (object.skipped) return;
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(object.id)) next.delete(object.id);
      else next.add(object.id);
      return next;
    });
  };

  const toggleFromPlate = (event: React.MouseEvent<HTMLCanvasElement>) => {
    const pickData = pickDataRef.current;
    if (!pickData) return;
    const point = plateClickToMaskPoint(
      event.currentTarget.getBoundingClientRect(),
      pickData.width,
      pickData.height,
      event.clientX,
      event.clientY,
    );
    if (!point) return;
    const objectId = pickObjectIdAt(pickData, point.x, point.y);
    const object = objectsData?.objects.find((candidate) => candidate.id === objectId);
    if (object) toggleObject(object);
  };

  const renderPlate = (large = false) => (
    <div className={`relative aspect-square overflow-hidden rounded-lg border border-gray-300 bg-gray-900 dark:border-gray-600 ${pickReady ? 'cursor-crosshair' : ''}`}>
      {topViewUrl ? (
        <img src={topViewUrl} alt={t('printers.printPreview')} className="absolute inset-0 h-full w-full object-contain" />
      ) : (
        <div className="absolute inset-0 flex items-center justify-center">
          <Box className="h-10 w-10 text-gray-500" />
        </div>
      )}
      <canvas
        ref={large ? enlargedOverlayRef : overlayRef}
        onClick={toggleFromPlate}
        className="absolute inset-0 h-full w-full object-contain"
        aria-label={t('printers.skipObjects.selectObjectsToSkip')}
      />
      {!large && topViewUrl && (
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            setEnlarged(true);
          }}
          className="absolute right-2 top-2 rounded bg-black/65 p-1.5 text-white/80 hover:text-white"
          title={t('common.expand')}
        >
          <Maximize2 className="h-4 w-4" />
        </button>
      )}
      {!pickReady && topViewUrl && (
        <div className="absolute bottom-2 left-2 rounded bg-black/70 px-2 py-1 text-[10px] text-white/80">
          {t('common.unavailable')}
        </div>
      )}
    </div>
  );

  if (!isOpen) return null;

  return (
    <>
      <div
        className="fixed inset-0 z-50 flex items-center justify-center p-4"
        onClick={onClose}
        onKeyDown={(event) => {
          if (event.key !== 'Escape') return;
          if (enlarged) setEnlarged(false);
          else onClose();
        }}
        tabIndex={-1}
        ref={(element) => element?.focus()}
      >
        <div className="absolute inset-0 bg-black/55" />
        <section
          className="relative z-10 flex max-h-[88vh] w-full max-w-[980px] flex-col overflow-hidden rounded-lg border border-gray-200 bg-white shadow-2xl dark:border-bambu-dark-tertiary dark:bg-bambu-dark"
          onClick={(event) => event.stopPropagation()}
          aria-label={t('printers.skipObjects.title')}
        >
          <header className="flex items-center justify-between border-b border-gray-200 bg-gray-50 px-4 py-3 dark:border-bambu-dark-tertiary dark:bg-bambu-dark">
            <div className="flex items-center gap-2">
              <SkipObjectsIcon className="h-5 w-5 text-bambu-green" />
              <div>
                <h2 className="text-sm font-semibold text-gray-900 dark:text-white">{t('printers.skipObjects.title')}</h2>
                <p className="text-xs text-gray-500 dark:text-bambu-gray">{t('printers.skipObjects.selectObjectsToSkip')}</p>
              </div>
            </div>
            <button type="button" onClick={onClose} className="rounded p-1 text-gray-500 hover:text-gray-900 dark:text-bambu-gray dark:hover:text-white">
              <X className="h-5 w-5" />
            </button>
          </header>

          {!objectsData ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="h-6 w-6 animate-spin text-bambu-gray" />
            </div>
          ) : objectsData.objects.length === 0 ? (
            <div className="px-4 py-12 text-center text-bambu-gray">
              <p className="text-sm">{t('printers.noObjectsFound')}</p>
              <p className="mt-1 text-xs opacity-70">{t('printers.objectsLoadedOnPrintStart')}</p>
            </div>
          ) : (
            <>
              {(status?.layer_num ?? 0) <= 1 && (
                <div className="flex items-center gap-2 border-b border-amber-400/20 bg-amber-500/10 px-4 py-2 text-xs text-amber-400">
                  <AlertCircle className="h-4 w-4 flex-shrink-0" />
                  {t('printers.skipObjects.waitForLayer', { layer: status?.layer_num ?? 0 })}
                </div>
              )}
              <div className="grid min-h-0 flex-1 grid-cols-[minmax(320px,1fr)_minmax(340px,0.9fr)] overflow-hidden max-md:grid-cols-1 max-md:overflow-y-auto">
                <div className="border-r border-gray-200 bg-gray-50 p-4 dark:border-bambu-dark-tertiary dark:bg-bambu-dark-secondary max-md:border-b-0 max-md:border-r-0">
                  {renderPlate()}
                  <div className="mt-3 flex items-center justify-between text-xs text-gray-500 dark:text-bambu-gray">
                    <span>{selectedIds.size}/{activeObjects.length}</span>
                    <span>{objectsData.skipped_count} {t('printers.skipObjects.skipped')}</span>
                  </div>
                </div>

                <div className="flex min-h-0 flex-col">
                  <button
                    type="button"
                    onClick={() => setSelectedIds(allSelected ? new Set() : new Set(activeObjects.map((object) => object.id)))}
                    className="flex items-center gap-3 border-b border-gray-200 px-4 py-3 text-left text-sm font-semibold text-gray-900 hover:bg-gray-50 dark:border-bambu-dark-tertiary dark:text-white dark:hover:bg-white/5"
                  >
                    {allSelected ? <CheckSquare className="h-5 w-5 text-bambu-green" /> : <Square className="h-5 w-5 text-bambu-gray" />}
                    <span className="flex-1">{allSelected ? t('common.deselectAll') : t('common.selectAll')}</span>
                    <span className="text-xs font-normal text-bambu-gray">{activeObjects.length}</span>
                  </button>
                  <div className="min-h-0 flex-1 overflow-y-auto">
                    {objectsData.objects.map((object, index) => {
                      const selected = selectedIds.has(object.id);
                      return (
                        <button
                          type="button"
                          key={object.id}
                          onClick={() => toggleObject(object)}
                          disabled={object.skipped}
                          className={`flex w-full items-center gap-3 border-b border-gray-200 px-4 py-2.5 text-left transition-colors dark:border-bambu-dark-tertiary/60 ${
                            object.skipped
                              ? 'cursor-not-allowed bg-red-500/5 opacity-55'
                              : selected
                                ? 'bg-bambu-green/15 hover:bg-bambu-green/20'
                                : 'hover:bg-gray-50 dark:hover:bg-white/5'
                          }`}
                        >
                          {object.skipped || selected
                            ? <CheckSquare className={`h-5 w-5 flex-shrink-0 ${object.skipped ? 'text-red-400' : 'text-bambu-green'}`} />
                            : <Square className="h-5 w-5 flex-shrink-0 text-bambu-gray" />}
                          <span className="w-8 flex-shrink-0 text-xs font-bold text-bambu-gray">{index + 1}</span>
                          <span className={`min-w-0 flex-1 truncate text-sm ${object.skipped ? 'text-red-400 line-through' : 'text-gray-900 dark:text-white'}`}>{object.name}</span>
                          <span className="text-[10px] text-bambu-gray">ID {object.id}</span>
                        </button>
                      );
                    })}
                  </div>
                </div>
              </div>

              <footer className="flex items-center gap-3 border-t border-gray-200 bg-gray-50 px-4 py-3 dark:border-bambu-dark-tertiary dark:bg-bambu-dark">
                <span className="mr-auto text-sm text-gray-600 dark:text-bambu-gray">
                  {selectedIds.size === 0 ? t('printers.skipObjects.noObjectsSelected') : `${selectedIds.size}/${activeObjects.length}`}
                </span>
                <button type="button" onClick={onClose} className="rounded-md border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 dark:border-bambu-dark-tertiary dark:text-white dark:hover:bg-white/5">
                  {t('common.cancel')}
                </button>
                <button
                  type="button"
                  onClick={() => setConfirming(true)}
                  disabled={!canSubmit || skipObjectsMutation.isPending}
                  className="rounded-md bg-red-500 px-4 py-2 text-sm font-semibold text-white hover:bg-red-600 disabled:cursor-not-allowed disabled:opacity-35"
                >
                  {skipObjectsMutation.isPending ? t('printers.skipObjects.skipping') : t('printers.skipObjects.skipSelected')}
                </button>
              </footer>
            </>
          )}
        </section>
      </div>

      {confirming && (
        <ConfirmModal
          variant="warning"
          title={t('printers.skipObjects.confirmTitle')}
          message={skippingAllRemaining
            ? t('printers.skipObjects.confirmAllMessage')
            : selectedObjects.length === 1
              // Naming one object is useful; joining 30 is a wall of text, and
              // plates of clones share a name, so the list identifies nothing.
              ? t('printers.skipObjects.confirmMessage', { name: selectedObjects[0].name })
              : t('printers.skipObjects.confirmMultipleMessage', { count: selectedObjects.length })}
          confirmText={t('printers.skipObjects.skipSelected')}
          isLoading={skipObjectsMutation.isPending}
          onConfirm={() => skipObjectsMutation.mutate([...selectedIds])}
          onCancel={() => setConfirming(false)}
        />
      )}

      {enlarged && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/90 p-8" onClick={() => setEnlarged(false)}>
          <button type="button" onClick={() => setEnlarged(false)} className="absolute right-4 top-4 rounded p-2 text-white/70 hover:text-white">
            <X className="h-6 w-6" />
          </button>
          <div className="aspect-square max-h-[86vh] w-full max-w-[86vh]" onClick={(event) => event.stopPropagation()}>
            {renderPlate(true)}
          </div>
        </div>
      )}
    </>
  );
}
