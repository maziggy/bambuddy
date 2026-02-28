import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation } from '@tanstack/react-query';
import { X, Check, Loader2 } from 'lucide-react';
import type { MatchedSpool } from '../../hooks/useSpoolBuddyState';
import { api, type AMSUnit, type AMSTray } from '../../api/client';

function getAmsName(id: number): string {
  if (id <= 3) return `AMS ${String.fromCharCode(65 + id)}`;
  if (id >= 128 && id <= 135) return `AMS HT ${String.fromCharCode(65 + id - 128)}`;
  return `AMS ${id}`;
}

function trayColorToCSS(color: string | null): string {
  if (!color) return '#808080';
  return `#${color.slice(0, 6)}`;
}

function isTrayEmpty(tray: AMSTray): boolean {
  return !tray.tray_type || tray.tray_type === '';
}

interface AssignToAmsModalProps {
  isOpen: boolean;
  onClose: () => void;
  spool: MatchedSpool;
  printerId: number | null;
}

export function AssignToAmsModal({ isOpen, onClose, spool, printerId }: AssignToAmsModalProps) {
  const { t } = useTranslation();
  const [selectedPrinter, setSelectedPrinter] = useState<number | null>(printerId);
  const [selectedSlot, setSelectedSlot] = useState<{ amsId: number; trayId: number } | null>(null);
  const [showSuccess, setShowSuccess] = useState(false);

  // Reset state when modal opens
  useEffect(() => {
    if (isOpen) {
      setSelectedPrinter(printerId);
      setSelectedSlot(null);
      setShowSuccess(false);
    }
  }, [isOpen, printerId]);

  // Escape key handler
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Escape') onClose();
  }, [onClose]);

  useEffect(() => {
    if (isOpen) {
      document.addEventListener('keydown', handleKeyDown);
    }
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [isOpen, handleKeyDown]);

  // Fetch printers
  const { data: printers = [] } = useQuery({
    queryKey: ['printers'],
    queryFn: () => api.getPrinters(),
    enabled: isOpen,
  });

  // Fetch printer status
  const { data: printerStatus } = useQuery({
    queryKey: ['printerStatus', selectedPrinter],
    queryFn: () => api.getPrinterStatus(selectedPrinter!),
    enabled: isOpen && selectedPrinter !== null,
    refetchInterval: 5000,
  });

  // Assignment mutation
  const assignMutation = useMutation({
    mutationFn: (data: { spool_id: number; printer_id: number; ams_id: number; tray_id: number }) =>
      api.assignSpool(data),
    onSuccess: () => {
      setShowSuccess(true);
      setTimeout(() => {
        onClose();
      }, 1500);
    },
  });

  if (!isOpen) return null;

  const handleAssign = () => {
    if (!selectedPrinter || !selectedSlot) return;
    assignMutation.mutate({
      spool_id: spool.id,
      printer_id: selectedPrinter,
      ams_id: selectedSlot.amsId,
      tray_id: selectedSlot.trayId,
    });
  };

  const amsUnits: AMSUnit[] = printerStatus?.ams ?? [];
  const colorHex = spool.rgba ? `#${spool.rgba.slice(0, 6)}` : '#808080';

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/80 animate-fade-in" onClick={onClose}>
      <div
        className="bg-zinc-800 rounded-2xl shadow-2xl w-full max-w-xl mx-4 animate-slide-up"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-6">
          {/* Header */}
          <div className="flex items-center justify-between mb-5">
            <h2 className="text-lg font-semibold text-zinc-100">
              {t('spoolbuddy.modal.assignToAmsTitle', 'Assign to AMS')}
            </h2>
            <button onClick={onClose} className="p-2 rounded-lg text-zinc-500 hover:text-zinc-300 hover:bg-zinc-700 transition-colors">
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Spool summary */}
          <div className="flex items-center gap-3 p-3 bg-zinc-900/50 rounded-lg mb-5">
            <div className="w-8 h-8 rounded-full shrink-0" style={{ backgroundColor: colorHex }} />
            <div className="flex-1 min-w-0">
              <span className="text-sm font-medium text-zinc-200 truncate block">
                {spool.color_name || 'Unknown'} &bull; {spool.material}
                {spool.subtype && ` ${spool.subtype}`}
              </span>
              <span className="text-xs text-zinc-500">{spool.brand}</span>
            </div>
          </div>

          {/* Printer selector */}
          {printers.length > 1 && (
            <div className="mb-4">
              <select
                value={selectedPrinter ?? ''}
                onChange={(e) => {
                  setSelectedPrinter(e.target.value ? Number(e.target.value) : null);
                  setSelectedSlot(null);
                }}
                className="w-full px-3 py-2.5 bg-zinc-900 border border-zinc-700 rounded-lg text-sm text-zinc-200 min-h-[44px]"
              >
                <option value="">{t('spoolbuddy.modal.noPrinterSelected', 'Select a printer...')}</option>
                {printers.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </div>
          )}

          {/* AMS slot grid */}
          {selectedPrinter === null ? (
            <div className="text-center py-8 text-zinc-500 text-sm">
              {t('spoolbuddy.modal.noPrinterSelected', 'Select a printer...')}
            </div>
          ) : amsUnits.length === 0 ? (
            <div className="text-center py-8 text-zinc-500 text-sm">
              {t('spoolbuddy.modal.noAmsDetected', 'No AMS detected on this printer')}
            </div>
          ) : (
            <div className="space-y-3 max-h-[300px] overflow-y-auto">
              {amsUnits.map((unit) => (
                <AmsSlotSelector
                  key={unit.id}
                  unit={unit}
                  selectedSlot={selectedSlot}
                  onSelectSlot={(trayId) => setSelectedSlot({ amsId: unit.id, trayId })}
                />
              ))}
            </div>
          )}

          {/* Error message */}
          {assignMutation.isError && (
            <div className="mt-4 p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-400">
              {t('spoolbuddy.modal.assignError', 'Failed to assign spool. Please try again.')}
            </div>
          )}

          {/* Action buttons */}
          <div className="flex gap-3 mt-5">
            <button
              onClick={handleAssign}
              disabled={!selectedSlot || assignMutation.isPending || showSuccess}
              className={`flex-1 px-5 py-3 rounded-xl text-sm font-medium transition-colors min-h-[44px] ${
                showSuccess
                  ? 'bg-green-600/20 text-green-400'
                  : 'bg-green-600 text-white hover:bg-green-700 disabled:opacity-40 disabled:cursor-not-allowed'
              }`}
            >
              {assignMutation.isPending ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin inline-block mr-1.5" />
                  {t('spoolbuddy.modal.assigning', 'Assigning...')}
                </>
              ) : showSuccess ? (
                <>
                  <Check className="w-4 h-4 inline-block mr-1.5" />
                  {t('spoolbuddy.modal.assignSuccess', 'Assigned!')}
                </>
              ) : (
                t('spoolbuddy.modal.assign', 'Assign')
              )}
            </button>
            <button
              onClick={onClose}
              className="px-5 py-3 rounded-xl text-sm font-medium bg-zinc-700 text-zinc-300 hover:bg-zinc-600 transition-colors min-h-[44px]"
            >
              {t('spoolbuddy.dashboard.close', 'Close')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// --- AMS Unit slot selector ---

interface AmsSlotSelectorProps {
  unit: AMSUnit;
  selectedSlot: { amsId: number; trayId: number } | null;
  onSelectSlot: (trayId: number) => void;
}

function AmsSlotSelector({ unit, selectedSlot, onSelectSlot }: AmsSlotSelectorProps) {
  const { t } = useTranslation();
  const trays = unit.tray || [];
  const isHt = unit.is_ams_ht;
  const slotCount = isHt ? 1 : 4;

  return (
    <div className="bg-zinc-900/50 rounded-lg p-3">
      <div className="text-xs font-medium text-zinc-400 uppercase tracking-wide mb-2">
        {getAmsName(unit.id)}
      </div>
      <div className={`grid ${isHt ? 'grid-cols-1 max-w-[100px]' : 'grid-cols-4'} gap-2`}>
        {Array.from({ length: slotCount }).map((_, i) => {
          const tray: AMSTray = trays[i] || {
            id: i,
            tray_color: null,
            tray_type: '',
            tray_sub_brands: null,
            tray_id_name: null,
            tray_info_idx: null,
            remain: -1,
            k: null,
            cali_idx: null,
            tag_uid: null,
            tray_uuid: null,
            nozzle_temp_min: null,
            nozzle_temp_max: null,
          };
          const isEmpty = isTrayEmpty(tray);
          const color = trayColorToCSS(tray.tray_color);
          const isSelected = selectedSlot?.amsId === unit.id && selectedSlot.trayId === i;

          return (
            <button
              key={i}
              type="button"
              onClick={() => onSelectSlot(i)}
              className={`relative flex flex-col items-center p-2.5 rounded-lg transition-all min-h-[44px] ${
                isSelected
                  ? 'ring-2 ring-green-500 bg-green-500/10'
                  : 'hover:bg-white/5'
              }`}
            >
              {/* Color circle */}
              <div className="relative w-10 h-10 mb-1">
                {isEmpty ? (
                  <div className="w-full h-full rounded-full border-2 border-dashed border-zinc-600 flex items-center justify-center">
                    <div className="w-2 h-2 rounded-full bg-zinc-600" />
                  </div>
                ) : (
                  <div className="w-full h-full rounded-full" style={{ backgroundColor: color }} />
                )}
              </div>

              {/* Material */}
              <span className="text-xs text-zinc-400 truncate max-w-full">
                {isEmpty ? t('spoolbuddy.ams.empty', 'Empty') : tray.tray_type || '?'}
              </span>

              {/* Slot number */}
              <span className="absolute top-0.5 right-1 text-[10px] text-zinc-600">
                {t('spoolbuddy.modal.slot', 'Slot')} {i + 1}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
