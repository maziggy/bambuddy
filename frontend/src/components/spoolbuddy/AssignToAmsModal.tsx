import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { X, Loader2, Check, CheckCircle, XCircle, Layers, Palette } from 'lucide-react';
import { api, type InventorySpool, type PrinterStatus, type AMSTray } from '../../api/client';
import { ConfirmModal } from '../ConfirmModal';
import { AmsUnitCard, NozzleBadge } from './AmsUnitCard';
import type { AmsThresholds } from './AmsUnitCard';
import { QUICK_COLORS } from '../spool-form/constants';
import { getFillBarColor } from '../../utils/amsHelpers';
import { getSwatchStyle } from '../../utils/colors';

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

function normalizeRgba(color: string | null | undefined): string {
  const clean = (color ?? '').replace(/^#/, '').toUpperCase();
  if (/^[0-9A-F]{8}$/.test(clean)) return clean;
  if (/^[0-9A-F]{6}$/.test(clean)) return `${clean}FF`;
  return '808080FF';
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
  const [selectedPrinterId, setSelectedPrinterId] = useState<number | null>(printerId);
  const [selectedSlot, setSelectedSlot] = useState<{ amsId: number; trayId: number } | null>(null);
  const [selectedRgba, setSelectedRgba] = useState(() => normalizeRgba(spool.rgba));
  const [selectedColorName, setSelectedColorName] = useState(spool.color_name ?? '');
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [statusType, setStatusType] = useState<'info' | 'success' | 'error' | null>(null);
  const [showMismatchConfirm, setShowMismatchConfirm] = useState(false);
  // Profile-only mismatches no longer trigger the popup — the backend
  // pushes the spool's slicer profile to the AMS slot on every assign
  // anyway, so the warning was friction without benefit (#1552). Material
  // mismatch still warns because firmware can refuse a print when type
  // doesn't match.
  const [mismatchDetails, setMismatchDetails] = useState<{
    type: 'material' | 'partial' | 'material_profile' | 'partial_profile';
    spoolMaterial: string;
    trayMaterial: string;
    spoolProfile?: string;
    trayProfile?: string;
    location: string;
  } | null>(null);
  const [pendingSlot, setPendingSlot] = useState<{ amsId: number; trayId: number } | null>(null);

  useEffect(() => {
    if (isOpen) {
      setSelectedPrinterId(printerId);
      setSelectedSlot(null);
      setSelectedRgba(normalizeRgba(spool.rgba));
      setSelectedColorName(spool.color_name ?? '');
      setStatusMessage(null);
      setStatusType(null);
      setShowMismatchConfirm(false);
      setMismatchDetails(null);
      setPendingSlot(null);
    }
  }, [isOpen, printerId, spool.id, spool.rgba, spool.color_name]);

  // SpoolBuddy opens this modal with a printer selected in its top bar. The
  // regular inventory/QR workflow has no such context, so a null printerId
  // enables an in-modal printer picker before the user chooses an AMS slot.
  const allowsPrinterSelection = printerId === null;
  const targetPrinterId = printerId ?? selectedPrinterId;
  const { data: selectablePrinters = [], isLoading: printersLoading } = useQuery({
    queryKey: ['printers'],
    queryFn: () => api.getPrinters(),
    enabled: isOpen && allowsPrinterSelection,
  });
  const activePrinters = useMemo(
    () => selectablePrinters.filter((candidate) => candidate.is_active !== false),
    [selectablePrinters],
  );

  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Escape') onClose();
  }, [onClose]);

  useEffect(() => {
    if (!isOpen) return;

    const previousOverflow = document.body.style.overflow;
    document.addEventListener('keydown', handleKeyDown);
    document.body.style.overflow = 'hidden';

    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [isOpen, handleKeyDown]);

  const { data: status, isLoading: statusLoading } = useQuery<PrinterStatus>({
    queryKey: ['printerStatus', targetPrinterId],
    queryFn: () => api.getPrinterStatus(targetPrinterId!),
    enabled: isOpen && targetPrinterId !== null,
    refetchInterval: 5000,
  });

  const { data: printer } = useQuery({
    queryKey: ['printer', targetPrinterId],
    queryFn: () => api.getPrinter(targetPrinterId!),
    enabled: isOpen && targetPrinterId !== null,
  });

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.getSettings(),
    enabled: isOpen,
    staleTime: 5 * 60 * 1000,
  });

  const { data: assignments } = useQuery({
    queryKey: ['spool-assignments', targetPrinterId],
    queryFn: () => api.getAssignments(targetPrinterId!),
    enabled: isOpen && targetPrinterId !== null,
    staleTime: 30 * 1000,
  });

  const { data: spoolmanAssignments = [] } = useQuery({
    queryKey: ['spoolman-slot-assignments', targetPrinterId],
    queryFn: () => api.getSpoolmanSlotAssignments(targetPrinterId ?? undefined),
    enabled: isOpen && !!spoolmanMode && targetPrinterId !== null,
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
    cachedAmsExtruderMap.current = {};
  }, [targetPrinterId]);
  useEffect(() => {
    if (status?.ams_extruder_map && Object.keys(status.ams_extruder_map).length > 0) {
      cachedAmsExtruderMap.current = status.ams_extruder_map;
    }
  }, [status?.ams_extruder_map]);
  const amsExtruderMap = (status?.ams_extruder_map && Object.keys(status.ams_extruder_map).length > 0)
    ? status.ams_extruder_map
    : cachedAmsExtruderMap.current;

  const ftsInstalled = status?.fila_switch?.installed === true;

  const getNozzleSide = useCallback((amsId: number): 'L' | 'R' | null => {
    if (!isDualNozzle) return null;
    const mappedExtruderId = amsExtruderMap[String(amsId)];
    if (mappedExtruderId !== undefined) return mappedExtruderId === 1 ? 'L' : 'R';
    // With a Filament Track Switch every AMS reports extruder 0xE and reaches
    // both nozzles through the switch, so there is no side to show. The unit-id
    // guess below would label them all "R" — it exists only for dual-nozzle
    // printers that never sent a map at all. See PrintersPage.amsSideBadge,
    // which shows the switch inlet in place of L/R on the printer card.
    if (ftsInstalled) return null;
    const normalizedId = amsId >= 128 ? amsId - 128 : amsId;
    return normalizedId === 1 ? 'L' : 'R';
  }, [isDualNozzle, amsExtruderMap, ftsInstalled]);

  // Assign spool to AMS slot — single API call, backend handles both DB record
  // AND MQTT auto-configuration. When the target slot is currently empty, the
  // backend persists the assignment and skips the MQTT publish (firmware drops
  // it anyway); on_ams_change re-fires the full configuration when filament is
  // later inserted. The response's `pending_config` flag distinguishes that
  // from the immediate-apply path so we can adjust the success toast.
  const configureMutation = useMutation({
    mutationFn: async ({
      amsId,
      trayId,
      rgba,
      colorName,
    }: {
      amsId: number;
      trayId: number;
      rgba: string;
      colorName: string;
    }) => {
      if (!targetPrinterId) throw new Error('No printer selected');

      const normalizedOriginalRgba = normalizeRgba(spool.rgba);
      const normalizedColorName = colorName.trim();
      if (
        allowsPrinterSelection &&
        (rgba !== normalizedOriginalRgba || normalizedColorName !== (spool.color_name ?? ''))
      ) {
        const colorUpdate = {
          rgba,
          color_name: normalizedColorName || null,
        };
        if (spoolmanMode) {
          await api.updateSpoolmanInventorySpool(spool.id, colorUpdate);
        } else {
          await api.updateSpool(spool.id, colorUpdate);
        }
      }

      if (spoolmanMode) {
        return await api.assignSpoolmanSlot({
          spoolman_spool_id: spool.id,
          printer_id: targetPrinterId,
          ams_id: amsId,
          tray_id: trayId,
        });
      }
      return await api.assignSpool({
        spool_id: spool.id,
        printer_id: targetPrinterId,
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
      queryClient.invalidateQueries({ queryKey: ['spool-assignments'] });
      queryClient.invalidateQueries({ queryKey: ['spoolman-slot-assignments'] });
      queryClient.invalidateQueries({ queryKey: ['spoolman-slot-assignments-all'] });
      queryClient.invalidateQueries({ queryKey: ['inventory-spools'] });
      queryClient.invalidateQueries({ queryKey: ['spoolman-inventory-spools'] });
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
    if (!isDualNozzle) return t('printers.ext', 'Ext');
    // Current assignment records use AMS 255 with tray 0/1 for Ext-L/Ext-R;
    // AMS 254 is retained for compatibility with older assignment records.
    if (amsId === 254 || (amsId === 255 && trayId === 0)) return t('printers.extL', 'Ext-L');
    return t('printers.extR', 'Ext-R');
  }, [t, isDualNozzle]);

  const doAssign = useCallback((amsId: number, trayId: number) => {
    setStatusType('info');
    setStatusMessage(t('spoolbuddy.modal.assigning', 'Configuring slot...'));
    configureMutation.mutate({
      amsId,
      trayId,
      rgba: selectedRgba,
      colorName: selectedColorName,
    });
  }, [configureMutation, selectedRgba, selectedColorName, t]);

  const prepareAssignment = useCallback((amsId: number, trayId: number) => {
    if (isWaiting) return;
    if (!settings?.disable_filament_warnings) {
      const tray = getTrayForSlot(amsId, trayId);
      if (tray && !isTrayEmpty(tray)) {
        const trayMaterial = tray.tray_sub_brands || tray.tray_type || '';
        const materialMatchResult = checkMaterialMatch(spool.material, trayMaterial);
        const spoolProfile = spool.slicer_filament_name || spool.slicer_filament;
        const trayProfile = tray.tray_type || '';
        const profileMatches = checkProfileMatch(spoolProfile, trayProfile);

        if (materialMatchResult !== 'exact') {
          let mismatchType: 'material' | 'partial' | 'material_profile' | 'partial_profile';
          if (materialMatchResult === 'none' && !profileMatches) {
            mismatchType = 'material_profile';
          } else if (materialMatchResult === 'partial' && !profileMatches) {
            mismatchType = 'partial_profile';
          } else if (materialMatchResult === 'none') {
            mismatchType = 'material';
          } else {
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

  const handleSlotClick = useCallback((amsId: number, trayId: number) => {
    if (isWaiting) return;

    if (allowsPrinterSelection) {
      setSelectedSlot({ amsId, trayId });
      setStatusMessage(null);
      setStatusType(null);
      return;
    }

    prepareAssignment(amsId, trayId);
  }, [allowsPrinterSelection, isWaiting, prepareAssignment]);

  const handleAssignSelectedSlot = useCallback(() => {
    if (!selectedSlot) return;
    prepareAssignment(selectedSlot.amsId, selectedSlot.trayId);
  }, [prepareAssignment, selectedSlot]);

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

  const colorStyle = getSwatchStyle(selectedRgba);
  const selectedHex = selectedRgba.slice(0, 6);
  const selectedSlotLabel = selectedSlot
    ? getSlotLocationLabel(selectedSlot.amsId, selectedSlot.trayId)
    : null;
  const overlayClasses = allowsPrinterSelection
    ? 'fixed inset-0 z-[60] flex items-center justify-center bg-black/45 dark:bg-black/70 p-2 sm:p-4 backdrop-blur-sm animate-fade-in'
    : 'fixed inset-0 z-[60] bg-bambu-dark';
  const dialogClasses = allowsPrinterSelection
    ? 'bg-bambu-dark-secondary text-white font-sans w-full max-w-5xl h-[calc(100vh-1rem)] sm:h-[calc(100vh-2rem)] max-h-[820px] border border-[var(--border-color)] rounded-xl sm:rounded-2xl shadow-2xl flex flex-col overflow-hidden animate-slide-up'
    : 'w-full h-full bg-bambu-dark flex flex-col';

  return (
    <>
    <div
      className={overlayClasses}
      onClick={allowsPrinterSelection && !isWaiting ? onClose : undefined}
    >
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="assign-to-ams-title"
      className={dialogClasses}
      onClick={(event) => event.stopPropagation()}
    >
      {/* Header */}
      <div className="flex items-center justify-between bg-bambu-dark-secondary px-5 py-3 border-b border-[var(--border-color)] shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-7 h-7 rounded-full shrink-0" style={colorStyle} />
          <div className="min-w-0">
            <h2 id="assign-to-ams-title" className="text-sm font-semibold text-white truncate">
              {t('spoolbuddy.modal.assignToAmsTitle', 'Assign to AMS')}
              <span className="font-normal text-bambu-gray-light ml-2">
                {selectedColorName || t('common.unknown')} &bull; {spool.brand} {spool.material}{spool.subtype && ` ${spool.subtype}`}
              </span>
              <span className="text-[10px] font-mono text-bambu-gray ml-2 shrink-0">#{spool.id}</span>
            </h2>
          </div>
        </div>
        <button
          onClick={onClose}
          disabled={isWaiting}
          aria-label={t('common.close', 'Close')}
          className="p-2 rounded-lg text-bambu-gray hover:text-white hover:bg-bambu-dark-tertiary transition-colors shrink-0 disabled:opacity-50"
        >
          <X className="w-5 h-5" />
        </button>
      </div>

      {allowsPrinterSelection && (
        <div className="bg-bambu-dark-secondary px-5 py-3 border-b border-[var(--border-color)] shrink-0">
          <label htmlFor="assign-spool-printer" className="flex items-center gap-2 text-sm font-medium text-white mb-2">
            <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-bambu-green text-[11px] font-bold text-[#ffffff]">1</span>
            {t('printModal.selectPrinter')}
          </label>
          <select
            id="assign-spool-printer"
            aria-label={t('printModal.selectPrinter')}
            value={selectedPrinterId ?? ''}
            onChange={(event) => {
              setSelectedPrinterId(event.target.value ? Number(event.target.value) : null);
              setSelectedSlot(null);
              setStatusMessage(null);
              setStatusType(null);
            }}
            disabled={isWaiting || printersLoading}
            className="w-full bg-bambu-dark border border-[var(--border-color)] rounded-lg px-3 py-2.5 text-sm text-white focus:outline-none focus:border-bambu-green disabled:opacity-50"
          >
            <option value="">
              {printersLoading
                ? t('common.loading')
                : activePrinters.length === 0
                  ? t('common.noPrinters')
                  : t('printModal.selectPrinter')}
            </option>
            {activePrinters.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.name}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Status message */}
      {statusMessage && (
        <div className={`mx-5 mt-3 p-3 rounded-lg flex items-center gap-3 border shrink-0 ${
          statusType === 'info'
            ? 'bg-blue-50 border-blue-300 dark:bg-blue-500/10 dark:border-blue-500/40'
            : statusType === 'success'
              ? 'bg-green-50 border-green-300 dark:bg-green-500/10 dark:border-green-500/40'
              : 'bg-red-50 border-red-300 dark:bg-red-500/10 dark:border-red-500/40'
        }`}>
          {statusType === 'info' && <Loader2 className="w-4 h-4 text-blue-600 dark:text-blue-400 animate-spin shrink-0" />}
          {statusType === 'success' && <CheckCircle className="w-4 h-4 text-green-600 dark:text-green-400 shrink-0" />}
          {statusType === 'error' && <XCircle className="w-4 h-4 text-red-600 dark:text-red-400 shrink-0" />}
          <span className={`text-sm ${
            statusType === 'info'
              ? 'text-blue-700 dark:text-blue-300'
              : statusType === 'success'
                ? 'text-green-700 dark:text-green-300'
                : 'text-red-700 dark:text-red-300'
          }`}>{statusMessage}</span>
        </div>
      )}

      <div className={`flex-1 min-h-0 ${allowsPrinterSelection ? 'grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_19rem] overflow-y-auto lg:overflow-hidden' : 'flex flex-col'}`}>
        {/* AMS slots */}
        <section className="flex min-h-[18rem] flex-col gap-3 bg-bambu-dark p-4 lg:min-h-0 lg:overflow-y-auto">
          {allowsPrinterSelection && (
            <div className="flex items-center gap-2 text-sm font-medium text-white shrink-0">
              <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-bambu-green text-[11px] font-bold text-[#ffffff]">2</span>
              {t('printModal.selectSlot')}
            </div>
          )}

          {!targetPrinterId ? (
            <div className="flex flex-1 items-center justify-center rounded-xl border border-dashed border-[var(--border-color)] bg-bambu-dark-secondary p-8">
              <div className="text-center text-bambu-gray">
                <Layers className="w-12 h-12 mx-auto mb-3 opacity-60" />
                <p className="text-base text-bambu-gray-light">{t('printModal.selectPrinter')}</p>
              </div>
            </div>
          ) : statusLoading ? (
            <div className="flex flex-1 items-center justify-center text-bambu-gray-light">
              <Loader2 className="mr-2 h-5 w-5 animate-spin text-bambu-green" />
              {t('common.loading')}
            </div>
          ) : !isConnected ? (
            <div className="flex flex-1 items-center justify-center rounded-xl border border-red-300 bg-red-50 p-8 dark:border-red-500/30 dark:bg-red-500/5">
              <p className="text-base text-red-700 dark:text-red-300">{t('spoolbuddy.ams.printerDisconnected', 'Printer disconnected')}</p>
            </div>
          ) : amsUnits.length === 0 && vtTrays.length === 0 ? (
            <div className="flex flex-1 items-center justify-center rounded-xl border border-dashed border-[var(--border-color)] bg-bambu-dark-secondary p-8">
              <div className="text-center text-bambu-gray">
                <Layers className="w-12 h-12 mx-auto mb-3 opacity-60" />
                <p className="text-base text-bambu-gray-light mb-1">{t('spoolbuddy.ams.noData', 'No AMS detected')}</p>
                <p className="text-sm">{t('spoolbuddy.ams.connectAms', 'Connect an AMS to see filament slots')}</p>
              </div>
            </div>
          ) : (
            <>
              {/* Regular AMS — 2-col grid */}
              {regularAms.length > 0 && (
                <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
                  {regularAms.map((unit) => (
                    <AmsUnitCard
                      key={unit.id}
                      unit={unit}
                      activeSlot={
                        selectedSlot?.amsId === unit.id
                          ? selectedSlot.trayId
                          : currentAssignment?.ams_id === unit.id
                            ? (currentAssignment.tray_id ?? null)
                            : null
                      }
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
                <div className="flex flex-wrap gap-2 shrink-0">
                  {singleSlots.map(({ key, label, amsId, trayId, tray, isEmpty, nozzleSide, effectiveFill }) => {
                    const color = trayColorToCSS(tray.tray_color);
                    const isActive = selectedSlot
                      ? selectedSlot.amsId === amsId && selectedSlot.trayId === trayId
                      : !!currentAssignment && currentAssignment.ams_id === amsId && currentAssignment.tray_id === trayId;
                    return (
                      <button
                        key={key}
                        type="button"
                        aria-pressed={isActive}
                        title={getSlotLocationLabel(amsId, trayId)}
                        onClick={() => handleSlotClick(amsId, trayId)}
                        className={`bg-bambu-dark-secondary border rounded-lg px-3 py-2 hover:bg-bambu-dark-tertiary transition-all flex items-center gap-2 ${
                          isActive ? 'ring-2 ring-bambu-green border-bambu-green bg-bambu-green/10' : 'border-[var(--border-color)]'
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
                        <div className="min-w-0 text-left">
                          <div className="flex items-center gap-1">
                            <span className="text-xs text-bambu-gray font-medium">{label}</span>
                            {nozzleSide && <NozzleBadge side={nozzleSide} />}
                          </div>
                          <div className="text-sm text-white truncate">
                            {isEmpty ? t('ams.empty') : tray.tray_type || '?'}
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
                      </button>
                    );
                  })}
                </div>
              )}
            </>
          )}
        </section>

        {/* The inventory flow may update the spool colour before assigning it. */}
        {allowsPrinterSelection && (
          <aside className="border-t border-[var(--border-color)] bg-bambu-dark-tertiary p-4 lg:overflow-y-auto lg:border-l lg:border-t-0">
            <div className="flex items-center gap-2 text-sm font-medium text-white mb-3">
              <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-bambu-green text-[11px] font-bold text-[#ffffff]">3</span>
              {t('inventory.color')}
            </div>

            <div className="flex items-center gap-3 rounded-xl border border-[var(--border-color)] bg-bambu-dark-secondary p-3 mb-4">
              <div className="h-14 w-14 shrink-0 rounded-xl border border-black/15 dark:border-white/20 shadow-inner" style={colorStyle} />
              <div className="min-w-0">
                <div className="truncate font-medium text-white">{selectedColorName || t('common.unknown')}</div>
                <div className="font-mono text-xs text-bambu-gray">#{selectedHex}</div>
              </div>
            </div>

            <div className="grid grid-cols-7 gap-2 mb-4">
              {QUICK_COLORS.map((color) => {
                const rgba = normalizeRgba(color.hex);
                const isSelected = selectedRgba === rgba;
                return (
                  <button
                    key={`${color.name}-${color.hex}`}
                    type="button"
                    title={color.name}
                    aria-label={color.name}
                    aria-pressed={isSelected}
                    onClick={() => {
                      setSelectedRgba(rgba);
                      setSelectedColorName(color.name);
                    }}
                    className={`relative aspect-square min-h-7 rounded-full border transition-transform hover:scale-110 ${
                      isSelected
                        ? 'ring-2 ring-bambu-green ring-offset-2 ring-offset-[var(--bg-tertiary)] border-black/30 dark:border-white/70'
                        : 'border-black/20 dark:border-white/20'
                    }`}
                    style={getSwatchStyle(color.hex)}
                  >
                    {isSelected && <Check className="absolute inset-0 m-auto h-3.5 w-3.5 text-[#ffffff] drop-shadow-[0_1px_2px_rgba(0,0,0,0.9)]" />}
                  </button>
                );
              })}
            </div>

            <label htmlFor="assign-spool-color-name" className="block text-xs font-medium text-white mb-1.5">
              {t('inventory.colorName')}
            </label>
            <input
              id="assign-spool-color-name"
              type="text"
              value={selectedColorName}
              onChange={(event) => setSelectedColorName(event.target.value)}
              placeholder={t('inventory.colorNamePlaceholder')}
              className="w-full rounded-lg border border-[var(--border-color)] bg-bambu-dark-secondary px-3 py-2 text-sm text-white placeholder:text-bambu-gray focus:border-bambu-green focus:outline-none"
            />

            <div className="flex items-center gap-2 mt-3">
              <label className="relative flex min-h-[40px] flex-1 items-center justify-center gap-2 rounded-lg border border-[var(--border-color)] bg-bambu-dark-secondary px-3 py-2 text-sm text-white hover:bg-bambu-dark">
                <Palette className="h-4 w-4 text-bambu-green" />
                {t('inventory.pickColor')}
                <input
                  type="color"
                  aria-label={t('inventory.pickColor')}
                  value={`#${selectedHex}`}
                  onChange={(event) => {
                    const customHex = event.target.value.replace('#', '').toUpperCase();
                    setSelectedRgba(`${customHex}FF`);
                    setSelectedColorName(`#${customHex}`);
                  }}
                  className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
                />
              </label>
              <span className="rounded-lg border border-[var(--border-color)] bg-bambu-dark-secondary px-2.5 py-2 font-mono text-xs text-bambu-gray-light">
                #{selectedHex}
              </span>
            </div>
          </aside>
        )}
      </div>

      {/* Footer */}
      <div className="flex items-center gap-3 bg-bambu-dark-secondary px-5 py-3 border-t border-[var(--border-color)] shrink-0">
        {allowsPrinterSelection && (
          <div className="min-w-0 flex-1 text-sm">
            <span className="text-bambu-gray">{t('printModal.selectSlot')}: </span>
            <span className={selectedSlotLabel ? 'font-medium text-white' : 'text-bambu-gray'}>
              {selectedSlotLabel || '—'}
            </span>
          </div>
        )}
        <button
          onClick={onClose}
          disabled={isWaiting}
          className="px-5 py-2.5 rounded-lg border border-[var(--border-color)] text-sm font-medium bg-bambu-dark-tertiary text-white hover:bg-bambu-dark transition-colors min-h-[44px] disabled:opacity-50"
        >
          {statusType === 'success' ? t('common.close') : t('common.cancel')}
        </button>
        {allowsPrinterSelection && (
          <button
            type="button"
            onClick={handleAssignSelectedSlot}
            disabled={!targetPrinterId || !selectedSlot || isWaiting || statusType === 'success'}
            className="inline-flex min-h-[44px] items-center gap-2 rounded-lg bg-bambu-green px-5 py-2.5 text-sm font-semibold text-[#ffffff] transition-colors hover:bg-bambu-green-dark disabled:cursor-not-allowed disabled:opacity-40"
          >
            {isWaiting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
            {t('inventory.assignSpool')}
          </button>
        )}
      </div>
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
      }

      // Always tell the user the AMS slot will be reconfigured — without
      // this, "Assign Anyway" reads as a no-op confirmation when the
      // backend in fact pushes the spool profile on every assign (#1552).
      message = `${message}\n\n${t('inventory.assignReconfigureNote')}`;

      return (
        <ConfirmModal
          title={t('inventory.assignMismatchTitle')}
          message={message}
          confirmText={t('inventory.assignMismatchConfirm')}
          variant="warning"
          overlayZIndex="z-[70]"
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
