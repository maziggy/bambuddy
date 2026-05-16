import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { X, History } from 'lucide-react';
import { PrintLogTable } from './PrintLogTable';

interface PrintLogModalProps {
  archiveId: number;
  archiveName: string | null;
  onClose: () => void;
}

export function PrintLogModal({ archiveId, archiveName, onClose }: PrintLogModalProps) {
  const { t } = useTranslation();

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary w-full max-w-2xl max-h-[85vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-bambu-dark-tertiary">
          <div className="flex items-center gap-2 min-w-0">
            <History className="w-5 h-5 text-bambu-green flex-shrink-0" />
            <h2 className="text-lg font-semibold text-white truncate" title={archiveName || ''}>
              {t('archives.runLog.modalTitle', { name: archiveName || t('archives.runLog.modalTitleFallback') })}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="text-bambu-gray hover:text-white transition-colors"
            title={t('common.close')}
          >
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="p-6 overflow-y-auto flex-1">
          <PrintLogTable archiveId={archiveId} />
        </div>
      </div>
    </div>
  );
}
