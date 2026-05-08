import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { X, Loader2, CheckCircle, XCircle, Layers } from 'lucide-react';
import { api, type InventorySpool, type PrinterStatus, type AMSTray } from '../../api/client';
import { ConfirmModal } from '../ConfirmModal';
import { AmsUnitCard, NozzleBadge } from './AmsUnitCard';
import type { AmsThresholds } from './AmsUnitCard';
import { getFillBarColor } from '../../utils/amsHelpers';

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

// --- Material/profile mismatch helpers (pure functions, no component state) ---
const normalizeValue = (value: string | undefined | null) =>
  (value ?? '').trim().toUpperCase();

function checkMaterialMatch(
  spoolMaterial: string | undefined | null,
  trayMaterial: string | undefined | null
): 'exact' | 'partial' | 'none' {
  const normalizedSpool = normalizeValue(spoolMaterial);
  const normalizedTray = normalizeValue(trayMaterial);
  if (!normalizedSpool || !normalizedTray) return 'none';
  if (normalizedSpool === normalizedTray) return 'exact';
  if (normalizedTray.includes(normalizedSpool) || normalizedSpool.includes(normalizedTray)) {
    return 'partial';
  }
  return 'none';
}

function checkProfileMatch(
  spoolProfile: string | undefined | null,
  trayProfile: string | undefined | null
): boolean {
  const normalizedSpoolProfile = normalizeValue(spoolProfile);
  const normalizedTrayProfile = normalizeValue(trayProfile);
  if (!normalizedSpoolProfile || !normalizedTrayProfile) return false;
  return normalizedSpoolProfile === normalizedTrayProfile;
}

interface AssignToAmsModalProps {
  isOpen: boolean;
  onClose: () => void;
  spool: InventorySpool;
  printerId: number | null;
  spoolmanMode?: boolean;
}

export function AssignToAmsModal({ isOpen, onClose, spool, printerId, spoolmanMode = false }: AssignToAmsModalProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [statusType, setStatusType] = useState<'info' | 'success' | 'error' | null>(null);
  const [showMismatchConfirm, setShowMismatchConfirm] = useState(false);
  const [mismatchDetails, setMismatchDetails] = useState<{
    type: 'material' | 'partial' | 'profile' | 'material_profile' | 'partial_profile';
    spoolMaterial: string;
    trayMaterial: string;
    spoolProfile?: string;
    trayProfile?: string;
    location: string;
  } | null>(null);
  const [pendingSlot, setPendingSlot] = useState<{ amsId: number; trayId: number } | null>(null);

  useEffect(() => {
    if (isOpen) {
      setStatusMessage(null);
      setStatusType(null);
      setShowMismatchConfirm(false);
      setMismatchDetails(null);
      setPendingSlot(null);
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

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.getSettings(),
    enabled: isOpen,
    staleTime: 5 * 60 * 1000,
  });

  const { data: assignments } = useQuery({
    queryKey: ['spool-assignments', printerId],
    queryFn: () => api.getAssignments(printerId!),
    enabled: isOpen && printerId !== null,
    staleTime: 30 * 1000,
  });

  const { data: spoolmanAssignments = [] } = useQuery({
    queryKey: ['spoolman-slot-assignments', printerId],
    queryFn: () => api.getSpoolmanSlotAssignments(printerId ?? undefined),
    enabled: isOpen && !!spoolmanMode && printerId !== null,
    staleTime: 30 * 1000,
  });

  const currentAssignment = spoolmanMode
    ? spoolmanAssignments.find(a => a.spoolman_spool_id === spool.id)
    : undefined;

  // Build fill-level override map from inventory assignments
  const fillOverrides = useMemo(() => {
    const map: Record<string, number> = {};
    if (!assignments) return map;
    for (const a of assignments) {
      const sp = a.spool;
      if (sp && sp.label_weight > 0 && sp.weight_used != null) {
        const fill = Math.round(Math.max(0, sp.label_weight - sp.weight_used) / sp.label_weight * 100);
        map[`${a.ams_id}-${a.tray_id}`] = fill;
      }
    }
    return map;
  }, [assignments]);

  const amsThresholds: AmsThresholds | undefined = settings ? {
    humidityGood: Number(settings.ams_humidity_good) || 40,
    humidityFair: Number(settings.ams_humidity_fair) || 60,
    tempGood: Number(settings.ams_temp_good) || 28,
    tempFair: Number(settings.ams_temp_fair) || 35,
  } : undefined;

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

  // Assign spool to AMS slot — single API call, backend handles both DB record
  // AND MQTT auto-configuration. When the target slot is currently empty, the
  // backend persists the assignment and skips the MQTT publish (firmware drops
  // it anyway); on_ams_change re-fires the full configuration when filament is
  // later inserted. The response's `pending_config` flag distinguishes that
  // from the immediate-apply path so we can adjust the success toast.
  const configureMutation = useMutation({
    mutationFn: async ({ amsId, trayId }: { amsId: number; trayId: number }) => {
      if (!printerId) throw new Error('No printer selected');

      if (spoolmanMode) {
        return await api.assignSpoolmanSlot({
          spoolman_spool_id: spool.id,
          printer_id: printerId,
          ams_id: amsId,
          tray_id: trayId,
        });
      }
      return await api.assignSpool({
        spool_id: spool.id,
        printer_id: printerId,
        ams_id: amsId,
        tray_id: trayId,
      });
    },
    onSuccess: (assignment) => {
      setStatusType('success');
      // pending_config only exists on SpoolAssignment (the local-inventory path);
      // the Spoolman path returns InventorySpool which always implies immediate apply.
      const pendingConfig = assignment && 'pending_config' in assignment && assignment.pending_config;
      if (pendingConfig) {
        setStatusMessage(
          t(
            'spoolbuddy.modal.assignPendingInsert',
            'Assigned. Slot will configure when you insert the spool.',
          ),
        );
      } else {
        setStatusMessage(t('spoolbuddy.modal.assignSuccess', 'Assigned!'));
      }
      queryClient.invalidateQueries({ queryKey: ['slotPresets'] });
      queryClient.invalidateQueries({ queryKey: ['spoolman-slot-assignments'] });
      queryClient.invalidateQueries({ queryKey: ['spoolman-slot-assignments-all'] });
      setTimeout(() => onClose(), pendingConfig ? 2500 : 1500);
    },
    onError: (err) => {
      setStatusType('error');
      setStatusMessage(err instanceof Error ? err.message : t('spoolbuddy.modal.assignError', 'Failed to assign spool.'));
    },
  });

  const isWaiting = configureMutation.isPending;

  const getTrayForSlot = useCallback((amsId: number, trayId: number): AMSTray | null => {
    if (amsId === 254 || amsId === 255) {
      const extTrayId = amsId === 254 ? 254 : 254 + trayId;
      return vtTrays.find(t => (t.id ?? 254) === extTrayId) || null;
    }
    const unit = amsUnits.find(u => u.id === amsId);
    return unit?.tray?.find(t => t.id === trayId) || null;
  }, [amsUnits, vtTrays]);

  const getSlotLocationLabel = useCallback((amsId: number, trayId: number): string => {
    if (amsId <= 3) return `${getAmsName(amsId)} ${t('ams.slot', 'Slot')} ${trayId + 1}`;
    if (amsId >= 128 && amsId <= 135) return getAmsName(amsId);
    if (amsId === 254) return t('printers.extL', 'Ext-L');
    return isDualNozzle ? t('printers.extR', 'Ext-R') : t('printers.ext', 'Ext');
  }, [t, isDualNozzle]);

  const doAssign = useCallback((amsId: number, trayId: number) => {
    setStatusType('info');
    setStatusMessage(t('spoolbuddy.modal.assigning', 'Configuring slot...'));
    configureMutation.mutate({ amsId, trayId });
  }, [configureMutation, t]);

  const handleSlotClick = useCallback((amsId: number, trayId: number) => {
    if (isWaiting) return;

    if (!settings?.disable_filament_warnings) {
      const tray = getTrayForSlot(amsId, trayId);
      if (tray && !isTrayEmpty(tray)) {
        const trayMaterial = tray.tray_sub_brands || tray.tray_type || '';
        const materialMatchResult = checkMaterialMatch(spool.material, trayMaterial);
        const spoolProfile = spool.slicer_filament_name || spool.slicer_filament;
        const trayProfile = tray.tray_type || '';
        const profileMatches = checkProfileMatch(spoolProfile, trayProfile);

        if (materialMatchResult !== 'exact' || !profileMatches) {
          let mismatchType: 'material' | 'partial' | 'profile' | 'material_profile' | 'partial_profile' = 'profile';
          if (materialMatchResult === 'none' && !profileMatches) {
            mismatchType = 'material_profile';
          } else if (materialMatchResult === 'partial' && !profileMatches) {
            mismatchType = 'partial_profile';
          } else if (materialMatchResult === 'none') {
            mismatchType = 'material';
          } else if (materialMatchResult === 'partial') {
            mismatchType = 'partial';
          }

          const location = getSlotLocationLabel(amsId, trayId);
          setPendingSlot({ amsId, trayId });
          setMismatchDetails({
            type: mismatchType,
            spoolMaterial: spool.material || '',
            trayMaterial: trayMaterial || '',
            spoolProfile: spoolProfile || undefined,
            trayProfile: trayProfile || undefined,
            location,
          });
          setShowMismatchConfirm(true);
          return;
        }
      }
    }

    doAssign(amsId, trayId);
  }, [isWaiting, settings?.disable_filament_warnings, spool, getTrayForSlot, getSlotLocationLabel, doAssign]);

  const handleConfirmMismatch = useCallback(() => {
    if (!pendingSlot) return;
    setShowMismatchConfirm(false);
    setMismatchDetails(null);
    doAssign(pendingSlot.amsId, pendingSlot.trayId);
    setPendingSlot(null);
  }, [pendingSlot, doAssign]);

  // Build single-slot items (HT + External)
  const singleSlots = useMemo(() => {
    const items: {
      key: string; label: string; amsId: number; trayId: number;
      tray: AMSTray; isEmpty: boolean; nozzleSide: 'L' | 'R' | null;
      effectiveFill: number | null;
    }[] = [];

    for (const unit of htAms) {
      const tray = unit.tray?.[0] || {
        id: 0, tray_color: null, tray_type: '', tray_sub_brands: null,
        tray_id_name: null, tray_info_idx: null, remain: -1, k: null,
        cali_idx: null, tag_uid: null, tray_uuid: null, nozzle_temp_min: null, nozzle_temp_max: null,
      };
      const invFill = fillOverrides[`${unit.id}-0`] ?? null;
      const amsFill = tray.remain != null && tray.remain >= 0 ? tray.remain : null;
      const resolvedInvFill = (invFill === 0 && amsFill !== null && amsFill > 0) ? null : invFill;
      items.push({
        key: `ht-${unit.id}`, label: getAmsName(unit.id),
        amsId: unit.id, trayId: 0, tray, isEmpty: isTrayEmpty(tray),
        nozzleSide: getNozzleSide(unit.id),
        effectiveFill: resolvedInvFill ?? amsFill,
      });
    }

    for (const extTray of vtTrays) {
      const extTrayId = extTray.id ?? 254;
      const extSlotTrayId = extTrayId - 254;
      const extInvFill = fillOverrides[`255-${extSlotTrayId}`] ?? null;
      const extAmsFill = extTray.remain != null && extTray.remain >= 0 ? extTray.remain : null;
      const extResolvedInvFill = (extInvFill === 0 && extAmsFill !== null && extAmsFill > 0) ? null : extInvFill;
      items.push({
        key: `ext-${extTrayId}`,
        label: isDualNozzle
          ? (extTrayId === 254 ? t('printers.extL', 'Ext-L') : t('printers.extR', 'Ext-R'))
          : t('printers.ext', 'Ext'),
        amsId: 255, trayId: extSlotTrayId, tray: extTray,
        isEmpty: isTrayEmpty(extTray),
        nozzleSide: isDualNozzle ? (extTrayId === 254 ? 'L' : 'R') : null,
        effectiveFill: extResolvedInvFill ?? extAmsFill,
      });
    }

    return items;
  }, [htAms, vtTrays, isDualNozzle, t, getNozzleSide, fillOverrides]);

  if (!isOpen) return null;

  const colorHex = spool.rgba ? `#${spool.rgba.slice(0, 6)}` : '#808080';

  return (
    <>
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
      <div className="flex-1 flex flex-col gap-3 p-4 min-h-0 overflow-y-auto">
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
                    activeSlot={currentAssignment?.ams_id === unit.id ? (currentAssignment.tray_id ?? null) : null}
                    onConfigureSlot={(_amsId, trayId) => handleSlotClick(unit.id, trayId)}
                    isDualNozzle={isDualNozzle}
                    nozzleSide={getNozzleSide(unit.id)}
                    thresholds={amsThresholds}
                    fillOverrides={fillOverrides}
                  />
                ))}
              </div>
            )}

            {/* Single-slot items (HT + External) */}
            {singleSlots.length > 0 && (
              <div className="flex gap-2 shrink-0">
                {singleSlots.map(({ key, label, amsId, trayId, tray, isEmpty, nozzleSide, effectiveFill }) => {
                  const color = trayColorToCSS(tray.tray_color);
                  const isActive = !!currentAssignment &&
                    currentAssignment.ams_id === amsId &&
                    currentAssignment.tray_id === trayId;
                  return (
                    <div
                      key={key}
                      onClick={() => handleSlotClick(amsId, trayId)}
                      className={`bg-bambu-dark-secondary rounded-lg px-3 py-2 cursor-pointer hover:bg-bambu-dark-secondary/80 transition-all flex items-center gap-2 ${
                        isActive ? 'ring-2 ring-bambu-green' : ''
                      } ${isWaiting ? 'opacity-50 pointer-events-none' : ''}`}
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
                      {!isEmpty && effectiveFill != null && effectiveFill >= 0 && (
                        <div className="w-1.5 h-8 bg-bambu-dark-tertiary rounded-full overflow-hidden shrink-0 flex flex-col-reverse">
                          <div
                            className="w-full rounded-full"
                            style={{
                              height: `${effectiveFill}%`,
                              backgroundColor: getFillBarColor(effectiveFill),
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

    {showMismatchConfirm && mismatchDetails && (() => {
      let message = '';

      if (mismatchDetails.type === 'material') {
        message = t('inventory.assignMismatchMessage', {
          spoolMaterial: mismatchDetails.spoolMaterial,
          trayMaterial: mismatchDetails.trayMaterial,
          location: mismatchDetails.location,
        });
      } else if (mismatchDetails.type === 'partial') {
        message = t('inventory.assignPartialMismatchMessage', {
          spoolMaterial: mismatchDetails.spoolMaterial,
          trayMaterial: mismatchDetails.trayMaterial,
          location: mismatchDetails.location,
        });
      } else if (mismatchDetails.type === 'material_profile') {
        message = `${t('inventory.assignMismatchMessage', {
          spoolMaterial: mismatchDetails.spoolMaterial,
          trayMaterial: mismatchDetails.trayMaterial,
          location: mismatchDetails.location,
        })}\n\n${t('inventory.assignProfileMismatchMessage', {
          spoolProfile: mismatchDetails.spoolProfile || t('common.unknown'),
          trayProfile: mismatchDetails.trayProfile || t('common.unknown'),
          location: mismatchDetails.location,
        })}`;
      } else if (mismatchDetails.type === 'partial_profile') {
        message = `${t('inventory.assignPartialMismatchMessage', {
          spoolMaterial: mismatchDetails.spoolMaterial,
          trayMaterial: mismatchDetails.trayMaterial,
          location: mismatchDetails.location,
        })}\n\n${t('inventory.assignProfileMismatchMessage', {
          spoolProfile: mismatchDetails.spoolProfile || t('common.unknown'),
          trayProfile: mismatchDetails.trayProfile || t('common.unknown'),
          location: mismatchDetails.location,
        })}`;
      } else if (mismatchDetails.type === 'profile') {
        message = t('inventory.assignProfileMismatchMessage', {
          spoolProfile: mismatchDetails.spoolProfile || t('common.unknown'),
          trayProfile: mismatchDetails.trayProfile || t('common.unknown'),
          location: mismatchDetails.location,
        });
      }

      return (
        <ConfirmModal
          title={t('inventory.assignMismatchTitle')}
          message={message}
          confirmText={t('inventory.assignMismatchConfirm')}
          variant="warning"
          isLoading={configureMutation.isPending}
          onConfirm={handleConfirmMismatch}
          onCancel={() => {
            if (!configureMutation.isPending) {
              setShowMismatchConfirm(false);
              setPendingSlot(null);
              setMismatchDetails(null);
            }
          }}
        />
      );
    })()}
    </>
  );
}
