// AI Failure Detection modal (#1546) — opened from the printer card's AI badge.
// Shows the live classification for this printer and the detection service's
// last error, without leaving the Printers page (mirrors the HMS error modal).
import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { X, ScanEye, AlertCircle, Settings } from 'lucide-react';

interface AiDetectionModalProps {
  printerName: string;
  detection?: { class: string; frame_count: number; score: number };
  // null = no error, or the viewer lacks settings:read (the backend withholds
  // error strings from non-settings users because they can embed config URLs)
  lastError: string | null;
  onClose: () => void;
}

export function AiDetectionModal({ printerName, detection, lastError, onClose }: AiDetectionModalProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const cls = detection
    ? (detection.class === 'failure' || detection.class === 'warning' ? detection.class : 'safe')
    : 'idle';
  const statusColor =
    cls === 'failure'
      ? 'text-status-error'
      : cls === 'warning'
        ? 'text-status-warning'
        : cls === 'safe'
          ? 'text-status-ok'
          : 'text-bambu-gray';

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-bambu-dark-secondary rounded-lg shadow-xl max-w-lg w-full max-h-[80vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
          <div className="flex items-center gap-2">
            <ScanEye className="w-5 h-5 text-bambu-green" />
            <h2 className="text-lg font-semibold text-white">
              {t('printers.aiDetection.modalTitle', { name: printerName })}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="p-1 hover:bg-bambu-dark-tertiary rounded-lg transition-colors"
            aria-label={t('common.close')}
          >
            <X className="w-5 h-5 text-bambu-gray" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4 text-sm">
          <div className="space-y-2">
            <div className="flex justify-between">
              <span className="text-bambu-gray">{t('printers.aiDetection.currentStatus')}</span>
              <span className={`font-medium ${statusColor}`}>{t(`printers.aiDetection.${cls}`)}</span>
            </div>
            {detection ? (
              <>
                <div className="flex justify-between">
                  <span className="text-bambu-gray">{t('printers.aiDetection.score')}</span>
                  <span className="text-white font-mono">{detection.score.toFixed(3)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-bambu-gray">{t('printers.aiDetection.framesAnalyzed')}</span>
                  <span className="text-white font-mono">{detection.frame_count}</span>
                </div>
              </>
            ) : (
              <p className="text-bambu-gray">{t('printers.aiDetection.idleHint')}</p>
            )}
          </div>

          {lastError && (
            <div className="flex items-start gap-2 p-3 rounded-lg bg-red-50 dark:bg-red-500/10 border border-red-300 dark:border-red-500/30">
              <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0 text-red-700 dark:text-red-400" />
              <div className="min-w-0">
                <div className="font-medium text-red-700 dark:text-red-400">
                  {t('printers.aiDetection.lastError')}
                </div>
                <p className="text-red-700/80 dark:text-red-300/80 break-words mt-1">{lastError}</p>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-bambu-dark-tertiary flex items-center justify-end">
          <button
            onClick={() => navigate('/settings?tab=failure-detection')}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-lg bg-bambu-dark-tertiary text-bambu-gray hover:text-white transition-colors"
          >
            <Settings className="w-4 h-4" />
            {t('printers.aiDetection.openSettings')}
          </button>
        </div>
      </div>
    </div>
  );
}
