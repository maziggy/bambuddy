import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { X, Loader2, CheckCircle, XCircle, Layers } from 'lucide-react';
import { api, type InventorySpool, type PrinterStatus, type AMSTray } from '../../api/client';
import { AmsUnitCard, NozzleBadge } from './AmsUnitCard';

function getAmsName(id: number): string {
  if (id <= 3) return `AMS ${String.fromCharCode(65 + id)}`;
  if (id >= 128 && id <= 135) return `AMS HT ${String.fromCharCode(65 + id - 128)}`;
  return `AMS ${id}`;
}

function isTrayEmpty(tray: AMSTray): boolean {
  return !tray.tray_type || tray.tray_type === '';
}

function trayColorToCSS(color: string | null): string {
  if (!color) return '#808080';
  return `#${color.slice(0, 6)}`;
}

interface AssignToAmsModalProps {
  isOpen: boolean;
  onClose: () => void;
  spool: InventorySpool;
  printerId: number | null;
}

export function AssignToAmsModal({ isOpen, onClose, spool, printerId }: AssignToAmsModalProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [statusType, setStatusType] = useState<'info' | 'success' | 'error' | null>(null);

  useEffect(() => {
    if (isOpen) {
      setStatusMessage(null);
      setStatusType(null);
    }
  }, [isOpen]);

  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Escape') onClose();
  }, [onClose]);

  useEffect(() => {
    if (isOpen) document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, handleKeyDown]);

  const { data: status } = useQuery<PrinterStatus>({
    queryKey: ['printerStatus', printerId],
    queryFn: () => api.getPrinterStatus(printerId!),
    enabled: isOpen && printerId !== null,
    refetchInterval: 5000,
  });

  const { data: printer } = useQuery({
    queryKey: ['printer', printerId],
    queryFn: () => api.getPrinter(printerId!),
    enabled: isOpen && printerId !== null,
  });

  const isConnected = status?.connected ?? false;
  const amsUnits = useMemo(() => status?.ams ?? [], [status?.ams]);
  const regularAms = useMemo(() => amsUnits.filter(u => !u.is_ams_ht), [amsUnits]);
  const htAms = useMemo(() => amsUnits.filter(u => u.is_ams_ht), [amsUnits]);
  const vtTrays = useMemo(() => [...(status?.vt_tray ?? [])].sort((a, b) => (a.id ?? 254) - (b.id ?? 254)), [status?.vt_tray]);
  const isDualNozzle = printer?.nozzle_count === 2 || status?.temperatures?.nozzle_2 !== undefined;

  const cachedAmsExtruderMap = useRef<Record<string, number>>({});
  useEffect(() => {
    if (status?.ams_extruder_map && Object.keys(status.ams_extruder_map).length > 0) {
      cachedAmsExtruderMap.current = status.ams_extruder_map;
    }
  }, [status?.ams_extruder_map]);
  const amsExtruderMap = (status?.ams_extruder_map && Object.keys(status.ams_extruder_map).length > 0)
    ? status.ams_extruder_map
    : cachedAmsExtruderMap.current;

  const getNozzleSide = useCallback((amsId: number): 'L' | 'R' | null => {
    if (!isDualNozzle) return null;
    const mappedExtruderId = amsExtruderMap[String(amsId)];
    const normalizedId = amsId >= 128 ? amsId - 128 : amsId;
    const extruderId = mappedExtruderId !== undefined ? mappedExtruderId : normalizedId;
    return extruderId === 1 ? 'L' : 'R';
  }, [isDualNozzle, amsExtruderMap]);

  // Assign spool to AMS slot — single API call, backend handles both
  // DB record AND MQTT auto-configuration (same as SpoolStation).
  const configureMutation = useMutation({
    mutationFn: async ({ amsId, trayId }: { amsId: number; trayId: number }) => {
      if (!printerId) throw new Error('No printer selected');

      await api.assignSpool({
        spool_id: spool.id,
        printer_id: printerId,
        ams_id: amsId,
        tray_id: trayId,
      });

      // Save slot preset mapping so ConfigureAmsSlotModal can show the preset
      // (same as ConfigureAmsSlotModal does after configuring a slot)
      if (spool.slicer_filament) {
        const base = spool.slicer_filament.includes('_')
          ? spool.slicer_filament.split('_')[0]
          : spool.slicer_filament;
        // Convert filament_id (GFL05) → setting_id (GFSL05); user presets (P*) pass through
        const presetId = base.startsWith('GF') && !base.startsWith('GFS')
          ? 'GFS' + base.slice(2)
          : base;
        const presetName = spool.subtype
          ? `${spool.material} ${spool.subtype}`
          : spool.material;
        try {
          await api.saveSlotPreset(printerId, amsId, trayId, presetId, presetName, 'cloud');
        } catch (e) {
          console.warn('Failed to save slot preset mapping:', e);
        }
      }
    },
    onSuccess: () => {
      setStatusType('success');
      setStatusMessage(t('spoolbuddy.modal.assignSuccess', 'Assigned!'));
      queryClient.invalidateQueries({ queryKey: ['slotPresets'] });
      setTimeout(() => onClose(), 1500);
    },
    onError: (err) => {
      setStatusType('error');
      setStatusMessage(err instanceof Error ? err.message : t('spoolbuddy.modal.assignError', 'Failed to assign spool.'));
    },
  });

  const isWaiting = configureMutation.isPending;

  const handleSlotClick = useCallback((amsId: number, trayId: number) => {
    if (isWaiting) return;
    setStatusType('info');
    setStatusMessage(t('spoolbuddy.modal.assigning', 'Configuring slot...'));
    configureMutation.mutate({ amsId, trayId });
  }, [isWaiting, configureMutation, t]);

  // Build single-slot items (HT + External)
  const singleSlots = useMemo(() => {
    const items: {
      key: string; label: string; amsId: number; trayId: number;
      tray: AMSTray; isEmpty: boolean; nozzleSide: 'L' | 'R' | null;
    }[] = [];

    for (const unit of htAms) {
      const tray = unit.tray?.[0] || {
        id: 0, tray_color: null, tray_type: '', tray_sub_brands: null,
        tray_id_name: null, tray_info_idx: null, remain: -1, k: null,
        cali_idx: null, tag_uid: null, tray_uuid: null, nozzle_temp_min: null, nozzle_temp_max: null,
      };
      items.push({
        key: `ht-${unit.id}`, label: getAmsName(unit.id),
        amsId: unit.id, trayId: 0, tray, isEmpty: isTrayEmpty(tray),
        nozzleSide: getNozzleSide(unit.id),
      });
    }

    for (const extTray of vtTrays) {
      const extTrayId = extTray.id ?? 254;
      items.push({
        key: `ext-${extTrayId}`,
        label: isDualNozzle
          ? (extTrayId === 254 ? t('printers.extL', 'Ext-L') : t('printers.extR', 'Ext-R'))
          : t('printers.ext', 'Ext'),
        amsId: 255, trayId: extTrayId - 254, tray: extTray,
        isEmpty: isTrayEmpty(extTray),
        nozzleSide: isDualNozzle ? (extTrayId === 254 ? 'L' : 'R') : null,
      });
    }

    return items;
  }, [htAms, vtTrays, isDualNozzle, t, getNozzleSide]);

  if (!isOpen) return null;

  const colorHex = spool.rgba ? `#${spool.rgba.slice(0, 6)}` : '#808080';

  return (
    <div className="fixed inset-0 z-[60] bg-bambu-dark flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-zinc-800 shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-7 h-7 rounded-full shrink-0" style={{ backgroundColor: colorHex }} />
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-zinc-100 truncate">
              {t('spoolbuddy.modal.assignToAmsTitle', 'Assign to AMS')}
              <span className="font-normal text-zinc-500 ml-2">
                {spool.color_name || 'Unknown'} &bull; {spool.brand} {spool.material}{spool.subtype && ` ${spool.subtype}`}
              </span>
            </h2>
          </div>
        </div>
        <button
          onClick={onClose}
          disabled={isWaiting}
          className="p-2 rounded-lg text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 transition-colors shrink-0 disabled:opacity-50"
        >
          <X className="w-5 h-5" />
        </button>
      </div>

      {/* Status message */}
      {statusMessage && (
        <div className={`mx-5 mt-3 p-3 rounded-lg flex items-center gap-3 border shrink-0 ${
          statusType === 'info'
            ? 'bg-blue-500/10 border-blue-500/40'
            : statusType === 'success'
              ? 'bg-green-500/10 border-green-500/40'
              : 'bg-red-500/10 border-red-500/40'
        }`}>
          {statusType === 'info' && <Loader2 className="w-4 h-4 text-blue-400 animate-spin shrink-0" />}
          {statusType === 'success' && <CheckCircle className="w-4 h-4 text-green-400 shrink-0" />}
          {statusType === 'error' && <XCircle className="w-4 h-4 text-red-400 shrink-0" />}
          <span className={`text-sm ${
            statusType === 'info' ? 'text-blue-300' : statusType === 'success' ? 'text-green-300' : 'text-red-300'
          }`}>{statusMessage}</span>
        </div>
      )}

      {/* AMS slots */}
      <div className="flex-1 flex flex-col gap-3 p-4 min-h-0">
        {!isConnected && printerId ? (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center text-white/50">
              <p className="text-lg mb-2">{t('spoolbuddy.ams.printerDisconnected', 'Printer disconnected')}</p>
            </div>
          </div>
        ) : amsUnits.length === 0 && vtTrays.length === 0 ? (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center text-white/50">
              <Layers className="w-12 h-12 mx-auto mb-3 opacity-50" />
              <p className="text-lg mb-2">{t('spoolbuddy.ams.noData', 'No AMS detected')}</p>
              <p className="text-sm">{t('spoolbuddy.ams.connectAms', 'Connect an AMS to see filament slots')}</p>
            </div>
          </div>
        ) : (
          <>
            {/* Regular AMS — 2-col grid */}
            {regularAms.length > 0 && (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3 flex-1 min-h-0">
                {regularAms.map((unit) => (
                  <AmsUnitCard
                    key={unit.id}
                    unit={unit}
                    activeSlot={null}
                    onConfigureSlot={(_amsId, trayId) => handleSlotClick(unit.id, trayId)}
                    isDualNozzle={isDualNozzle}
                    nozzleSide={getNozzleSide(unit.id)}
                  />
                ))}
              </div>
            )}

            {/* Single-slot items (HT + External) */}
            {singleSlots.length > 0 && (
              <div className="flex gap-2 shrink-0">
                {singleSlots.map(({ key, label, amsId, trayId, tray, isEmpty, nozzleSide }) => {
                  const color = trayColorToCSS(tray.tray_color);
                  return (
                    <div
                      key={key}
                      onClick={() => handleSlotClick(amsId, trayId)}
                      className={`bg-bambu-dark-secondary rounded-lg px-3 py-2 cursor-pointer hover:bg-bambu-dark-secondary/80 transition-all flex items-center gap-2 ${
                        isWaiting ? 'opacity-50 pointer-events-none' : ''
                      }`}
                    >
                      <div className="relative w-10 h-10 shrink-0">
                        {isEmpty ? (
                          <div className="w-full h-full rounded-full border-2 border-dashed border-gray-500 flex items-center justify-center">
                            <div className="w-1.5 h-1.5 rounded-full bg-gray-600" />
                          </div>
                        ) : (
                          <svg viewBox="0 0 56 56" className="w-full h-full">
                            <circle cx="28" cy="28" r="26" fill={color} />
                            <circle cx="28" cy="28" r="20" fill={color} style={{ filter: 'brightness(0.85)' }} />
                            <ellipse cx="20" cy="20" rx="6" ry="4" fill="white" opacity="0.3" />
                            <circle cx="28" cy="28" r="8" fill="#2d2d2d" />
                            <circle cx="28" cy="28" r="5" fill="#1a1a1a" />
                          </svg>
                        )}
                      </div>
                      <div className="min-w-0">
                        <div className="flex items-center gap-1">
                          <span className="text-xs text-white/50 font-medium">{label}</span>
                          {nozzleSide && <NozzleBadge side={nozzleSide} />}
                        </div>
                        <div className="text-sm text-white/80 truncate">
                          {isEmpty ? 'Empty' : tray.tray_type || '?'}
                        </div>
                      </div>
                      {!isEmpty && tray.remain != null && tray.remain >= 0 && (
                        <div className="w-1.5 h-8 bg-bambu-dark-tertiary rounded-full overflow-hidden shrink-0 flex flex-col-reverse">
                          <div
                            className="w-full rounded-full"
                            style={{
                              height: `${tray.remain}%`,
                              backgroundColor: tray.remain > 50 ? '#22c55e' : tray.remain > 20 ? '#f59e0b' : '#ef4444',
                            }}
                          />
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </>
        )}
      </div>

      {/* Footer */}
      <div className="flex justify-end gap-3 px-5 py-3 border-t border-zinc-800 shrink-0">
        <button
          onClick={onClose}
          disabled={isWaiting}
          className="px-5 py-2.5 rounded-lg text-sm font-medium bg-zinc-800 text-zinc-300 hover:bg-zinc-700 transition-colors min-h-[44px] disabled:opacity-50"
        >
          {statusType === 'success' ? t('spoolbuddy.dashboard.close', 'Close') : t('spoolbuddy.modal.cancel', 'Cancel')}
        </button>
      </div>
    </div>
  );
}
