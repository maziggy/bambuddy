import { useState } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { X, Loader2, Monitor, AlertCircle, Box } from 'lucide-react';
import { api } from '../api/client';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import { ConfirmModal } from './ConfirmModal';

// Custom Skip Objects icon - arrow jumping over boxes
export const SkipObjectsIcon = ({ className }: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
    {/* Three boxes at the bottom */}
    <rect x="2" y="15" width="5" height="5" rx="0.5" />
    <rect x="9.5" y="15" width="5" height="5" rx="0.5" fill="currentColor" opacity="0.3" />
    <rect x="17" y="15" width="5" height="5" rx="0.5" />
    {/* Curved arrow jumping over first box */}
    <path d="M4 12 C4 6, 14 6, 14 12" />
    <polyline points="12,10 14,12 12,14" />
  </svg>
);

interface SkipObjectsModalProps {
  printerId: number;
  isOpen: boolean;
  onClose: () => void;
}

export function SkipObjectsModal({ printerId, isOpen, onClose }: SkipObjectsModalProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const { hasPermission } = useAuth();
  const [pendingSkip, setPendingSkip] = useState<{ id: number; name: string } | null>(null);

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

  const skipObjectsMutation = useMutation({
    mutationFn: (objectIds: number[]) => api.skipObjects(printerId, objectIds),
    onSuccess: (data) => {
      showToast(data.message || t('printers.skipObjects.objectsSkipped'));
      setPendingSkip(null);
      refetchObjects();
    },
    onError: (error: Error) => showToast(error.message || t('printers.toast.failedToSkipObjects'), 'error'),
  });

  if (!isOpen) return null;

  return (
    <>
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      onClick={onClose}
      onKeyDown={(e) => e.key === 'Escape' && onClose()}
      tabIndex={-1}
      ref={(el) => el?.focus()}
    >
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/50 z-0" />
      {/* Modal */}
      <div
        className="relative z-10 bg-white dark:bg-bambu-dark border border-gray-200 dark:border-bambu-dark-tertiary rounded-xl shadow-2xl w-[560px] max-h-[85vh] flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-bambu-dark-tertiary bg-gray-50 dark:bg-bambu-dark">
          <div className="flex items-center gap-2">
            <SkipObjectsIcon className="w-4 h-4 text-bambu-green" />
            <span className="text-sm font-medium text-gray-900 dark:text-white">{t('printers.skipObjects.title')}</span>
          </div>
          <button
            onClick={onClose}
            className="p-1 text-gray-500 dark:text-bambu-gray hover:text-gray-900 dark:hover:text-white rounded transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {!objectsData ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-5 h-5 animate-spin text-bambu-gray" />
          </div>
        ) : objectsData.objects.length === 0 ? (
          <div className="text-center py-8 px-4 text-bambu-gray">
            <p className="text-sm">{t('printers.noObjectsFound')}</p>
            <p className="text-xs mt-1 opacity-70">{t('printers.objectsLoadedOnPrintStart')}</p>
          </div>
        ) : (
          <div className="flex flex-col overflow-hidden">
            {/* Info Banner */}
            <div className="flex items-center gap-3 px-4 py-2.5 bg-blue-50 dark:bg-blue-500/10 border-b border-gray-200 dark:border-bambu-dark-tertiary">
              <div className="flex-shrink-0 w-8 h-8 rounded-lg bg-blue-100 dark:bg-blue-500/20 flex items-center justify-center">
                <Monitor className="w-4 h-4 text-blue-500 dark:text-blue-400" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-xs text-blue-600 dark:text-blue-300">{t('printers.skipObjects.matchIdsInfo')}</p>
                <p className="text-[10px] text-blue-500/70 dark:text-blue-300/60">{t('printers.skipObjects.printerShowsIds')}</p>
              </div>
              <div className="flex-shrink-0 text-xs text-gray-500 dark:text-bambu-gray">
                {objectsData.skipped_count}/{objectsData.total} {t('printers.skipObjects.skipped')}
              </div>
            </div>

            {/* Layer Warning */}
            {(status?.layer_num ?? 0) <= 1 && (
              <div className="flex items-center gap-2 px-4 py-2 bg-amber-50 dark:bg-amber-500/10 border-b border-gray-200 dark:border-bambu-dark-tertiary">
                <AlertCircle className="w-4 h-4 text-amber-500 dark:text-amber-400 flex-shrink-0" />
                <p className="text-xs text-amber-600 dark:text-amber-400">
                  {t('printers.skipObjects.waitForLayer', { layer: status?.layer_num ?? 0 })}
                </p>
              </div>
            )}

            {/* Content: Image + List side by side */}
            <div className="flex flex-1 overflow-hidden">
              {/* Left: Preview Image with object markers */}
              <div className="w-52 flex-shrink-0 p-4 border-r border-gray-200 dark:border-bambu-dark-tertiary bg-gray-50 dark:bg-bambu-dark-secondary overflow-y-auto">
                <div className="relative">
                  {status?.cover_url ? (
                    <img
                      src={`${status.cover_url}?view=top`}
                      alt={t('printers.printPreview')}
                      className="w-full aspect-square object-contain rounded-lg bg-gray-900 dark:bg-gray-900 border border-gray-300 dark:border-gray-600"
                    />
                  ) : (
                    <div className="w-full aspect-square rounded-lg bg-gray-100 dark:bg-bambu-dark flex items-center justify-center">
                      <Box className="w-8 h-8 text-gray-300 dark:text-bambu-gray/30" />
                    </div>
                  )}
                  {/* Object ID markers overlay - positioned based on object data */}
                  {objectsData.objects.length > 0 && (
                    <div className="absolute inset-0 pointer-events-none">
                      {objectsData.objects.map((obj, idx) => {
                        let x: number, y: number;

                        // Use position data if available, otherwise fall back to grid
                        if (obj.x != null && obj.y != null && objectsData.bbox_all) {
                          // bbox_all defines the visible area in the top_N.png image
                          // Format: [x_min, y_min, x_max, y_max] in mm
                          const [xMin, yMin, xMax, yMax] = objectsData.bbox_all;
                          const bboxWidth = xMax - xMin;
                          const bboxHeight = yMax - yMin;

                          // The image shows bbox_all area with some padding (~5-10%)
                          const padding = 8;
                          const contentArea = 100 - (padding * 2);

                          // Map object position to image percentage
                          x = padding + ((obj.x - xMin) / bboxWidth) * contentArea;
                          // Y axis: image Y increases downward, but 3D Y increases toward back
                          y = padding + ((yMax - obj.y) / bboxHeight) * contentArea;

                          // Clamp to valid range
                          x = Math.max(5, Math.min(95, x));
                          y = Math.max(5, Math.min(95, y));
                        } else if (obj.x != null && obj.y != null) {
                          // Fallback: use full build plate (256mm)
                          const buildPlate = 256;
                          x = (obj.x / buildPlate) * 100;
                          y = 100 - (obj.y / buildPlate) * 100;
                          x = Math.max(5, Math.min(95, x));
                          y = Math.max(5, Math.min(95, y));
                        } else {
                          // Fallback: arrange in a grid pattern over the build plate area
                          const cols = Math.ceil(Math.sqrt(objectsData.objects.length));
                          const row = Math.floor(idx / cols);
                          const col = idx % cols;
                          const rows = Math.ceil(objectsData.objects.length / cols);
                          x = 15 + (col * (70 / cols)) + (35 / cols);
                          y = 15 + (row * (70 / rows)) + (35 / rows);
                        }

                        return (
                          <div
                            key={obj.id}
                            className={`absolute flex items-center justify-center w-6 h-6 rounded-full text-[10px] font-bold shadow-lg ${
                              obj.skipped
                                ? 'bg-red-500 text-white line-through'
                                : 'bg-bambu-green text-black'
                            }`}
                            style={{
                              left: `${x}%`,
                              top: `${y}%`,
                              transform: 'translate(-50%, -50%)'
                            }}
                            title={obj.name}
                          >
                            {obj.id}
                          </div>
                        );
                      })}
                    </div>
                  )}
                  {/* Object count overlay */}
                  <div className="absolute bottom-2 right-2 px-2 py-1 bg-white/90 dark:bg-black/80 rounded text-[10px] text-gray-700 dark:text-white shadow-sm">
                    {t('printers.skipObjects.activeCount', { count: objectsData.objects.filter(o => !o.skipped).length })}
                  </div>
                </div>
              </div>

              {/* Right: Object List with prominent IDs */}
              <div className="flex-1 min-w-0 overflow-y-auto">
                {objectsData.objects.map((obj) => (
                  <div
                    key={obj.id}
                    className={`
                      flex items-center gap-3 px-4 py-3 border-b border-gray-200 dark:border-bambu-dark-tertiary/50 last:border-0
                      ${obj.skipped ? 'bg-red-50 dark:bg-red-500/10' : 'hover:bg-gray-50 dark:hover:bg-bambu-dark/50'}
                    `}
                  >
                    {/* Large prominent ID badge */}
                    <div className={`
                      w-12 h-12 flex-shrink-0 rounded-lg flex flex-col items-center justify-center
                      ${obj.skipped
                        ? 'bg-red-100 dark:bg-red-500/20 border border-red-300 dark:border-red-500/40'
                        : 'bg-green-100 dark:bg-bambu-green/20 border border-green-300 dark:border-bambu-green/40'}
                    `}>
                      <span className={`text-lg font-mono font-bold ${obj.skipped ? 'text-red-500 dark:text-red-400' : 'text-green-600 dark:text-bambu-green'}`}>
                        {obj.id}
                      </span>
                      <span className={`text-[8px] uppercase tracking-wider ${obj.skipped ? 'text-red-400/60' : 'text-green-500/60 dark:text-bambu-green/60'}`}>
                        ID
                      </span>
                    </div>

                    {/* Object name and status */}
                    <div className="flex-1 min-w-0">
                      <span className={`block text-sm truncate ${obj.skipped ? 'text-red-500 dark:text-red-400 line-through' : 'text-gray-900 dark:text-white'}`}>
                        {obj.name}
                      </span>
                      {obj.skipped && (
                        <span className="text-[10px] text-red-400/60">{t('printers.willBeSkipped')}</span>
                      )}
                    </div>

                    {/* Skip button */}
                    {!obj.skipped ? (
                      <button
                        onClick={() => setPendingSkip({ id: obj.id, name: obj.name })}
                        disabled={skipObjectsMutation.isPending || (status?.layer_num ?? 0) <= 1 || !hasPermission('printers:control')}
                        className={`px-4 py-2 text-xs font-medium rounded-lg transition-colors ${
                          (status?.layer_num ?? 0) <= 1 || !hasPermission('printers:control')
                            ? 'bg-gray-100 dark:bg-bambu-dark text-gray-400 dark:text-bambu-gray/50 cursor-not-allowed'
                            : 'bg-red-100 dark:bg-red-500/20 text-red-600 dark:text-red-400 hover:bg-red-200 dark:hover:bg-red-500/30 border border-red-300 dark:border-red-500/30'
                        }`}
                        title={!hasPermission('printers:control') ? t('printers.permission.noControl') : ((status?.layer_num ?? 0) <= 1 ? t('printers.skipObjects.waitForLayer', { layer: status?.layer_num ?? 0 }) : t('printers.skipObjects.skip'))}
                      >
                        {t('printers.skipObjects.skip')}
                      </button>
                    ) : (
                      <span className="px-4 py-2 text-xs text-red-500 dark:text-red-400/70 bg-red-100 dark:bg-red-500/10 rounded-lg">
                        {t('printers.skipObjects.skipped')}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
    {pendingSkip && (
      <ConfirmModal
        variant="warning"
        title={t('printers.skipObjects.confirmTitle')}
        message={t('printers.skipObjects.confirmMessage', { name: pendingSkip.name })}
        confirmText={t('printers.skipObjects.skip')}
        isLoading={skipObjectsMutation.isPending}
        onConfirm={() => skipObjectsMutation.mutate([pendingSkip.id])}
        onCancel={() => setPendingSkip(null)}
      />
    )}
  </>
  );
}
