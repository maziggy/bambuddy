import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { X } from 'lucide-react';
import type { InventorySpool } from '../../api/client';
import { SpoolIcon } from './SpoolIcon';

interface LinkSpoolModalProps {
  isOpen: boolean;
  onClose: () => void;
  tagId: string;
  untaggedSpools: InventorySpool[];
  onLink: (spool: InventorySpool) => void;
}

export function LinkSpoolModal({
  isOpen,
  onClose,
  tagId,
  untaggedSpools,
  onLink,
}: LinkSpoolModalProps) {
  const { t } = useTranslation();
  const [selectedSpool, setSelectedSpool] = useState<InventorySpool | null>(null);

  const handleClose = useCallback(() => {
    setSelectedSpool(null);
    onClose();
  }, [onClose]);

  // Handle escape key
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      handleClose();
    }
  }, [handleClose]);

  useEffect(() => {
    if (isOpen) {
      document.addEventListener('keydown', handleKeyDown);
      document.body.style.overflow = 'hidden';
    }
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = '';
    };
  }, [isOpen, handleKeyDown]);

  if (!isOpen) return null;

  const handleConfirm = () => {
    if (selectedSpool) {
      onLink(selectedSpool);
      setSelectedSpool(null);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 animate-fade-in" onClick={handleClose}>
      <div
        className="bg-zinc-800 rounded-xl shadow-2xl w-full max-w-lg mx-4 animate-slide-up"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-700">
          <div>
            <h2 className="text-base font-semibold text-zinc-100">
              {t('spoolbuddy.dashboard.linkTagTitle', 'Link Tag to Spool')}
            </h2>
            <p className="text-sm text-zinc-500 font-mono">{tagId}</p>
          </div>
          <button
            onClick={handleClose}
            className="p-2 rounded-lg text-zinc-500 hover:text-zinc-300 hover:bg-zinc-700 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="px-5 py-4 space-y-3 max-h-[400px] overflow-y-auto">
          <p className="text-sm text-zinc-400">
            {t('spoolbuddy.dashboard.selectSpool', 'Select a spool to link this tag to:')}
          </p>

          {untaggedSpools.length === 0 ? (
            <div className="text-center py-8 text-zinc-500">
              {t('spoolbuddy.dashboard.noUntagged', 'No spools without tags found')}
            </div>
          ) : (
            <div className="space-y-2">
              {untaggedSpools.map((spool) => (
                <button
                  key={spool.id}
                  type="button"
                  onClick={() => setSelectedSpool(spool)}
                  className={`w-full flex items-center gap-3 p-3 rounded-lg border-2 transition-all text-left ${
                    selectedSpool?.id === spool.id
                      ? 'border-green-500 bg-green-500/10'
                      : 'border-zinc-700 hover:border-green-500/50 hover:bg-zinc-700/50'
                  }`}
                >
                  <SpoolIcon
                    color={spool.rgba ? `#${spool.rgba.slice(0, 6)}` : '#808080'}
                    isEmpty={false}
                    size={40}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="font-medium text-zinc-100 truncate">
                      {spool.color_name || 'Unknown color'}
                    </div>
                    <div className="text-sm text-zinc-400 truncate">
                      {spool.brand} &bull; {spool.material}
                      {spool.subtype && ` ${spool.subtype}`}
                    </div>
                  </div>
                  <div className="text-sm font-mono text-zinc-500">
                    {Math.max(0, spool.label_weight - spool.weight_used)}g
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 px-5 py-4 border-t border-zinc-700">
          <button
            onClick={handleClose}
            className="px-4 py-2.5 rounded-lg text-sm font-medium bg-zinc-700 text-zinc-300 hover:bg-zinc-600 transition-colors min-h-[44px]"
          >
            {t('common.cancel', 'Cancel')}
          </button>
          <button
            onClick={handleConfirm}
            disabled={!selectedSpool}
            className="px-4 py-2.5 rounded-lg text-sm font-medium bg-green-600 text-white hover:bg-green-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors min-h-[44px]"
          >
            {t('spoolbuddy.dashboard.linkTag', 'Link Tag')}
          </button>
        </div>
      </div>
    </div>
  );
}
