import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, AlertTriangle, Loader2, Pencil, Printer, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { PrinterStatus, PrintQueueItemCreate, PrintQueueItemUpdate, SpoolAssignment } from '../../api/client';
import { api } from '../../api/client';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent } from '../Card';
import { Button } from '../Button';
import { ConfirmModal } from '../ConfirmModal';
import { useToast } from '../../contexts/ToastContext';
import {
  buildAmsMapping,
  buildFilamentComparison,
  buildLoadedFilaments,
  useFilamentMapping,
} from '../../hooks/useFilamentMapping';
import { useMultiPrinterFilamentMapping, type PerPrinterConfig } from '../../hooks/useMultiPrinterFilamentMapping';
import { getColorName } from '../../utils/colors';
import { isGcodeCompatible } from '../../utils/printer';
import { getCurrencySymbol } from '../../utils/currency';
import { getBedTypeInfo } from '../../utils/bedType';
import { toDateTimeLocalValue, parseUTCDate } from '../../utils/date';
import { getGlobalTrayId, isPlaceholderDate, effectivePreferLowest } from '../../utils/amsHelpers';
import { FilamentMapping } from './FilamentMapping';
import { FilamentOverride } from './FilamentOverride';
import { PlateSelector } from './PlateSelector';
import { PrinterSelector } from './PrinterSelector';
import { PrintOptionsPanel } from './PrintOptions';
import { ScheduleOptionsPanel } from './ScheduleOptions';
import type {
  AssignmentMode,
  FilamentReqsData,
  PrintModalProps,
  PrintOptions,
  ScheduleOptions,
  ScheduleType,
} from './types';
import { DEFAULT_PRINT_OPTIONS, DEFAULT_SCHEDULE_OPTIONS } from './types';

/**
 * Unified PrintModal component that handles queue item creation and editing.
 * - 'create': Create a print queue item from an archive or library file
 * - 'edit-queue-item': Edit existing queue item
 *
 * Both archiveId and libraryFileId are supported. Library files are archived at
 * print start time by the scheduler, not when queued.
 */
export function PrintModal({
  mode,
  archiveId,
  libraryFileId,
  archiveName,
  queueItem,
  initialSelectedPrinterIds,
  onClose,
  onSuccess,
  projectId,
  cleanupLibraryAfterDispatch,
}: PrintModalProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { hasPermission } = useAuth();

  // Determine if we're printing a library file
  const isLibraryFile = !!libraryFileId && !archiveId;
  const isEditing = mode === 'edit-queue-item';

  type FilamentWarningItem = {
    printerName: string;
    slotLabel: string;
    requiredGrams: number;
    remainingGrams: number;
  };

  // Multiple printer selection (used for all modes now)
  const [selectedPrinters, setSelectedPrinters] = useState<number[]>(() => {
    // Initialize with the queue item's printer if editing
    if (mode === 'edit-queue-item' && queueItem?.printer_id) {
      return [queueItem.printer_id];
    }
    if (initialSelectedPrinterIds?.length) {
      return initialSelectedPrinterIds;
    }
    return [];
  });

  // Multi-select plates: create mode users can pick a subset of plates
  const [selectedPlates, setSelectedPlates] = useState<Set<number>>(() => {
    if (mode === 'edit-queue-item' && queueItem?.plate_id != null) {
      return new Set([queueItem.plate_id]);
    }
    return new Set();
  });

  // Derived single-plate value for filament queries and single-select contexts
  const selectedPlate = selectedPlates.size === 1 ? [...selectedPlates][0] : null;

  // Quantity — number of copies (creates a batch if > 1)
  const [quantity, setQuantity] = useState(1);

  const [printOptions, setPrintOptions] = useState<PrintOptions>(() => {
    if (mode === 'edit-queue-item' && queueItem) {
      return {
        bed_levelling: queueItem.bed_levelling ?? DEFAULT_PRINT_OPTIONS.bed_levelling,
        flow_cali: queueItem.flow_cali ?? DEFAULT_PRINT_OPTIONS.flow_cali,
        vibration_cali: queueItem.vibration_cali ?? DEFAULT_PRINT_OPTIONS.vibration_cali,
        layer_inspect: queueItem.layer_inspect ?? DEFAULT_PRINT_OPTIONS.layer_inspect,
        timelapse: queueItem.timelapse ?? DEFAULT_PRINT_OPTIONS.timelapse,
        nozzle_offset_cali: queueItem.nozzle_offset_cali ?? DEFAULT_PRINT_OPTIONS.nozzle_offset_cali,
        preheat_override: queueItem.preheat_override ?? DEFAULT_PRINT_OPTIONS.preheat_override,
        preheat_chamber_target_override: queueItem.preheat_chamber_target_override ?? DEFAULT_PRINT_OPTIONS.preheat_chamber_target_override,
      };
    }
    return DEFAULT_PRINT_OPTIONS;
  });

  const [scheduleOptions, setScheduleOptions] = useState<ScheduleOptions>(() => {
    if (mode === 'edit-queue-item' && queueItem) {
      let scheduleType: ScheduleType = 'queue';
      if (queueItem.scheduled_time && !isPlaceholderDate(queueItem.scheduled_time)) {
        scheduleType = 'scheduled';
      }

      let scheduledTime = '';
      if (queueItem.scheduled_time && !isPlaceholderDate(queueItem.scheduled_time)) {
        const date = parseUTCDate(queueItem.scheduled_time) ?? new Date();
        // Use toDateTimeLocalValue to convert UTC to local time for datetime-local input
        scheduledTime = toDateTimeLocalValue(date);
      }

      return {
        scheduleType,
        scheduledTime,
        requireManualStart: queueItem.manual_start,
        requirePreviousSuccess: queueItem.require_previous_success,
        autoOffAfter: queueItem.auto_off_after,
        gcodeInjection: queueItem.gcode_injection ?? false,
        staggerEnabled: false,
        staggerGroupSize: DEFAULT_SCHEDULE_OPTIONS.staggerGroupSize,
        staggerIntervalMinutes: DEFAULT_SCHEDULE_OPTIONS.staggerIntervalMinutes,
      };
    }
    return DEFAULT_SCHEDULE_OPTIONS;
  });

  // Manual slot overrides: slot_id (1-indexed) -> globalTrayId (default mapping for single printer or all printers)
  const [manualMappings, setManualMappings] = useState<Record<number, number>>(() => {
    if (mode === 'edit-queue-item' && queueItem?.ams_mapping && Array.isArray(queueItem.ams_mapping)) {
      const mappings: Record<number, number> = {};
      queueItem.ams_mapping.forEach((globalTrayId, idx) => {
        if (globalTrayId !== -1) {
          mappings[idx + 1] = globalTrayId;
        }
      });
      return mappings;
    }
    return {};
  });

  // Per-printer override configs (for multi-printer selection)
  const [perPrinterConfigs, setPerPrinterConfigs] = useState<Record<number, PerPrinterConfig>>({});

  // Assignment mode: 'printer' (specific) or 'model' (any of model)
  const [assignmentMode, setAssignmentMode] = useState<AssignmentMode>(() => {
    // Initialize from queue item if editing with target_model
    if (mode === 'edit-queue-item' && queueItem?.target_model) {
      return 'model';
    }
    return 'printer';
  });

  // Target model for model-based assignment
  const [targetModel, setTargetModel] = useState<string | null>(() => {
    if (mode === 'edit-queue-item' && queueItem?.target_model) {
      return queueItem.target_model;
    }
    return null;
  });

  // Target location for model-based assignment (optional filter)
  const [targetLocation, setTargetLocation] = useState<string | null>(() => {
    if (mode === 'edit-queue-item' && queueItem?.target_location) {
      return queueItem.target_location;
    }
    return null;
  });

  // Filament overrides for model-based assignment: slot_id -> {type, color}
  const [filamentOverrides, setFilamentOverrides] = useState<Record<number, { type: string; color: string }>>(() => {
    if (mode === 'edit-queue-item' && queueItem?.filament_overrides) {
      const overrides: Record<number, { type: string; color: string }> = {};
      for (const o of queueItem.filament_overrides) {
        overrides[o.slot_id] = { type: o.type, color: o.color };
      }
      return overrides;
    }
    return {};
  });

  // Per-slot force color match flags. Default is false (opt-in).
  const [forceColorMatch, setForceColorMatch] = useState<Record<number, boolean>>(() => {
    if (mode === 'edit-queue-item' && queueItem?.filament_overrides) {
      const flags: Record<number, boolean> = {};
      for (const o of queueItem.filament_overrides) {
        flags[o.slot_id] = o.force_color_match === true;
      }
      return flags;
    }
    return {};
  });

  // Track initial values for clearing mappings on change (edit mode only)
  const [initialPrinterIds] = useState(() => (mode === 'edit-queue-item' && queueItem?.printer_id ? [queueItem.printer_id] : []));
  const [initialPlateId] = useState(() => (mode === 'edit-queue-item' && queueItem ? queueItem.plate_id : null));

  // Submission state for multi-printer
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitProgress, setSubmitProgress] = useState({ current: 0, total: 0 });

  const [filamentWarningItems, setFilamentWarningItems] = useState<FilamentWarningItem[] | null>(null);

  // Track which printers have had the "Expand custom mapping by default" setting applied
  // This ensures the setting only affects initial state, not preventing unchecking
  const [initialExpandApplied, setInitialExpandApplied] = useState<Set<number>>(new Set());

  // Printer counts and effective printer for filament mapping
  const effectivePrinterCount = selectedPrinters.length;
  // For filament mapping, use first selected printer (mapping applies to all)
  const effectivePrinterId = selectedPrinters.length > 0 ? selectedPrinters[0] : null;

  // Queries
  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
  });

  // Sync print option defaults from settings once available
  const printDefaultsApplied = useRef(false);
  useEffect(() => {
    if (!settings || printDefaultsApplied.current || mode === 'edit-queue-item') return;
    printDefaultsApplied.current = true;
    setPrintOptions({
      bed_levelling: settings.default_bed_levelling ?? DEFAULT_PRINT_OPTIONS.bed_levelling,
      flow_cali: settings.default_flow_cali ?? DEFAULT_PRINT_OPTIONS.flow_cali,
      vibration_cali: settings.default_vibration_cali ?? DEFAULT_PRINT_OPTIONS.vibration_cali,
      layer_inspect: settings.default_layer_inspect ?? DEFAULT_PRINT_OPTIONS.layer_inspect,
      timelapse: settings.default_timelapse ?? DEFAULT_PRINT_OPTIONS.timelapse,
      nozzle_offset_cali: settings.default_nozzle_offset_cali ?? DEFAULT_PRINT_OPTIONS.nozzle_offset_cali,
      preheat_override: DEFAULT_PRINT_OPTIONS.preheat_override,
      preheat_chamber_target_override: DEFAULT_PRINT_OPTIONS.preheat_chamber_target_override,
    });
  }, [settings, mode]);

  // Sync stagger defaults from settings once available
  const staggerDefaultsApplied = useRef(false);
  useEffect(() => {
    if (!settings || staggerDefaultsApplied.current || mode === 'edit-queue-item') return;
    staggerDefaultsApplied.current = true;
    setScheduleOptions((prev) => ({
      ...prev,
      staggerGroupSize: settings.stagger_group_size ?? prev.staggerGroupSize,
      staggerIntervalMinutes: settings.stagger_interval_minutes ?? prev.staggerIntervalMinutes,
    }));
  }, [settings, mode]);

  const currencySymbol = getCurrencySymbol(settings?.currency || 'USD');
  const defaultCostPerKg = settings?.default_filament_cost ?? 0;

  const { data: printers, isLoading: loadingPrinters } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  const { data: spoolAssignments } = useQuery({
    queryKey: ['spool-assignments'],
    queryFn: () => api.getAssignments(),
    staleTime: 30 * 1000,
    enabled: !isEditing && assignmentMode === 'printer',
  });

  // Fetch per-printer Map<globalTrayId, gramsRemaining> via the dedicated
  // backend endpoint (#1766). Server-side mirrors `_build_inventory_remain_overrides`
  // so internal and Spoolman modes both work uniformly, VT/external slots are
  // excluded, and negative grams are clamped — single source of truth between
  // the client-side preview and dispatch-time picks.
  const inventoryRemainQueries = useQueries({
    queries: selectedPrinters.map((printerId) => ({
      queryKey: ['printer-inventory-remain', printerId],
      queryFn: () => api.getInventoryRemain(printerId),
      staleTime: 30 * 1000,
      enabled: selectedPrinters.length > 0,
    })),
  });
  const inventoryByTrayIdPerPrinter = useMemo(() => {
    const result = new Map<number, Map<number, number>>();
    selectedPrinters.forEach((printerId, idx) => {
      const data = inventoryRemainQueries[idx]?.data?.inventory_remain_g;
      if (!data) return;
      const printerMap = new Map<number, number>();
      Object.entries(data).forEach(([key, grams]) => {
        const gtid = Number(key);
        if (!Number.isNaN(gtid)) printerMap.set(gtid, grams);
      });
      result.set(printerId, printerMap);
    });
    return result;
  }, [selectedPrinters, inventoryRemainQueries]);

  // Fetch archive details to get sliced_for_model
  const { data: archiveDetails } = useQuery({
    queryKey: ['archive', archiveId],
    queryFn: () => api.getArchive(archiveId!),
    enabled: !!archiveId && !isLibraryFile,
  });

  // Fetch library file details to get sliced_for_model
  const { data: libraryFileDetails } = useQuery({
    queryKey: ['library-file', libraryFileId],
    queryFn: () => api.getLibraryFile(libraryFileId!),
    enabled: isLibraryFile && !!libraryFileId,
  });

  // Get sliced_for_model from archive or library file
  const slicedForModel = archiveDetails?.sliced_for_model || libraryFileDetails?.sliced_for_model || null;

  // Fetch plates for archives
  const { data: archivePlatesData, isError: archivePlatesError } = useQuery({
    queryKey: ['archive-plates', archiveId],
    queryFn: () => api.getArchivePlates(archiveId!),
    enabled: !!archiveId && !isLibraryFile,
    retry: false,
  });

  // Fetch plates for library files
  const { data: libraryPlatesData } = useQuery({
    queryKey: ['library-file-plates', libraryFileId],
    queryFn: () => api.getLibraryFilePlates(libraryFileId!),
    enabled: isLibraryFile && !!libraryFileId,
  });

  // Combine plates data from either source
  const platesData = isLibraryFile ? libraryPlatesData : archivePlatesData;

  // Fetch filament requirements for archives
  const { data: archiveFilamentReqs, isError: archiveFilamentReqsError } = useQuery({
    queryKey: ['archive-filaments', archiveId, selectedPlate],
    queryFn: () => api.getArchiveFilamentRequirements(archiveId!, selectedPlate ?? undefined),
    enabled: !!archiveId && !isLibraryFile && (selectedPlate !== null || !platesData?.is_multi_plate),
    retry: false,
  });

  // Fetch filament requirements for library files (with plate support)
  const { data: libraryFilamentReqs } = useQuery({
    queryKey: ['library-file-filaments', libraryFileId, selectedPlate],
    queryFn: () => api.getLibraryFileFilamentRequirements(libraryFileId!, selectedPlate ?? undefined),
    enabled: isLibraryFile && !!libraryFileId && (selectedPlate !== null || !platesData?.is_multi_plate),
  });

  // Track if archive data couldn't be loaded (archive deleted or file missing)
  const archiveDataMissing = !isLibraryFile && (archivePlatesError || archiveFilamentReqsError);

  // Combine filament requirements from either source
  const effectiveFilamentReqs = isLibraryFile ? libraryFilamentReqs : archiveFilamentReqs;

  // Fetch available filaments for model-based assignment (for filament override UI)
  const { data: availableFilaments } = useQuery({
    queryKey: ['available-filaments', targetModel, targetLocation],
    queryFn: () => api.getAvailableFilaments(targetModel!, targetLocation ?? undefined),
    enabled: assignmentMode === 'model' && !!targetModel,
  });

  // Only fetch printer status when single printer selected (for filament mapping)
  const { data: printerStatus } = useQuery({
    queryKey: ['printer-status', effectivePrinterId],
    queryFn: () => api.getPrinterStatus(effectivePrinterId!),
    enabled: !!effectivePrinterId,
  });

  // Single-printer flow: gate prefer_lowest on this printer's backup state.
  // Multi-printer flow gates per-printer inside the hook (different printers
  // may have different backup states), so we pass the raw setting down.
  const singlePrinterPreferLowest = effectivePreferLowest(
    settings?.prefer_lowest_filament,
    printerStatus?.ams_filament_backup,
  );

  const isPrinterCurrentlyDispatchable = (status: PrinterStatus | undefined): boolean => {
    if (!status?.connected) return false;
    if (status.awaiting_plate_clear) return false;
    if (status.ams?.some((ams) => ams.dry_time > 0)) return false;
    return ['IDLE', 'FINISH', 'FAILED'].includes(status.state ?? '');
  };

  const asapToastShouldPromiseLaterStart = async (): Promise<boolean> => {
    if (scheduleOptions.scheduleType !== 'asap' || assignmentMode !== 'printer') return false;
    if (selectedPrinters.length === 0) return false;

    try {
      const statuses = await Promise.all(
        selectedPrinters.map((printerId) =>
          queryClient.fetchQuery({
            queryKey: ['printer-status', printerId],
            queryFn: () => api.getPrinterStatus(printerId),
            staleTime: 0,
          }),
        ),
      );
      return statuses.some((status) => !isPrinterCurrentlyDispatchable(status));
    } catch {
      return true;
    }
  };

  // Get AMS mapping from hook (only when single printer selected)
  const { amsMapping } = useFilamentMapping(
    effectiveFilamentReqs,
    printerStatus,
    manualMappings,
    singlePrinterPreferLowest,
    effectivePrinterId ? inventoryByTrayIdPerPrinter.get(effectivePrinterId) : undefined,
  );

  // --- Per-plate filament mapping (multi-plate submissions) ---------------
  // Each plate prints its own subset of the file's slots and needs its own AMS
  // mapping. `effectiveFilamentReqs` above is keyed on `selectedPlate`, which is
  // null the moment two plates are picked, so it holds the union of every plate's
  // filaments — matching against that union lets two plates that share a colour
  // on different slots compete for the same tray, and sends the loser to a worse
  // tray or to none. So when several plates are selected we fetch each plate's
  // requirements and map them separately (#2551 follow-up).
  const selectedPlateIds = useMemo(() => [...selectedPlates].sort((a, b) => a - b), [selectedPlates]);
  const isMultiPlateSelection = selectedPlates.size > 1;

  const perPlateReqQueries = useQueries({
    queries: (isMultiPlateSelection ? selectedPlateIds : []).map((plateId) => ({
      queryKey: isLibraryFile
        ? ['library-file-filaments', libraryFileId, plateId]
        : ['archive-filaments', archiveId, plateId],
      queryFn: () =>
        isLibraryFile
          ? api.getLibraryFileFilamentRequirements(libraryFileId!, plateId)
          : api.getArchiveFilamentRequirements(archiveId!, plateId),
      enabled: isLibraryFile ? !!libraryFileId : !!archiveId,
      // Same policy as the single-plate query above: these keys are shared, and a
      // retrying observer would leave the plate looking merely slow for seconds.
      retry: false,
    })),
  });

  // A plate that has not answered yet and a plate whose 3MF cannot be read look
  // identical from here — both are simply absent from `perPlateReqs`. Neither may
  // be treated as "this plate needs no filament": that would queue it with no
  // mapping and no force-colour overrides, and it would print in whatever happens
  // to be loaded. Both states gate submission instead (see `canSubmit`).
  // `isPending` is "no data yet", not "a request is in flight" — a background
  // refetch of a plate we already have must not disable the button under the user.
  const perPlateReqsPending = perPlateReqQueries.some((q) => q.isPending);
  const perPlateReqsFailed = perPlateReqQueries.some((q) => q.isError);

  const perPlateReqs = useMemo(() => {
    const byPlate = new Map<number, FilamentReqsData>();
    selectedPlateIds.forEach((plateId, i) => {
      const data = perPlateReqQueries[i]?.data;
      if (data) byPlate.set(plateId, data);
    });
    return byPlate;
    // Keyed on each query's last update stamp, not on the query objects (fresh every
    // render) and not on a spread of their data (a dep array whose *length* changes
    // with the plate count, which React treats as always-changed and warns about).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedPlateIds, perPlateReqQueries.map((q) => q.dataUpdatedAt).join('|')]);

  // Manual slot overrides are per plate: slot 3 of plate 1 and slot 3 of plate 2
  // are different prints and may want different trays.
  const [manualMappingsByPlate, setManualMappingsByPlate] = useState<Record<number, Record<number, number>>>({});

  // Only ever computed for a single target printer: a tray id means nothing on a
  // different printer, so a fan-out across printers must not reuse these.
  const perPlateAmsMappings = useMemo(() => {
    const byPlate = new Map<number, number[] | undefined>();
    if (!isMultiPlateSelection || !effectivePrinterId || selectedPrinters.length !== 1) return byPlate;

    const loaded = buildLoadedFilaments(printerStatus);
    const ftsActive = printerStatus?.fila_switch?.installed === true;
    const inventoryByTrayId = inventoryByTrayIdPerPrinter.get(effectivePrinterId);

    for (const plateId of selectedPlateIds) {
      const reqs = perPlateReqs.get(plateId);
      if (!reqs) continue;
      const comparison = buildFilamentComparison(
        reqs,
        loaded,
        manualMappingsByPlate[plateId] ?? {},
        singlePrinterPreferLowest,
        inventoryByTrayId,
        ftsActive,
      );
      byPlate.set(plateId, buildAmsMapping(comparison));
    }
    return byPlate;
  }, [
    isMultiPlateSelection,
    effectivePrinterId,
    printerStatus,
    inventoryByTrayIdPerPrinter,
    selectedPlateIds,
    perPlateReqs,
    manualMappingsByPlate,
    singlePrinterPreferLowest,
    selectedPrinters.length,
  ]);

  // Multi-printer filament mapping (for per-printer configuration)
  const multiPrinterMapping = useMultiPrinterFilamentMapping(
    selectedPrinters,
    printers,
    effectiveFilamentReqs,
    manualMappings,
    perPrinterConfigs,
    setPerPrinterConfigs,
    settings?.prefer_lowest_filament,
    inventoryByTrayIdPerPrinter,
  );

  // Auto-select first plate when plates load (single or multi-plate)
  useEffect(() => {
    if (platesData?.plates && platesData.plates.length >= 1 && selectedPlates.size === 0) {
      setSelectedPlates(new Set([platesData.plates[0].index]));
    }
  }, [platesData, selectedPlates.size]);

  // Auto-select first printer when only one available
  useEffect(() => {
    // Skip auto-select for edit mode (already initialized from queueItem)
    if (mode === 'edit-queue-item') return;
    const activePrinters = printers?.filter(p => p.is_active) || [];
    if (activePrinters.length === 1 && selectedPrinters.length === 0) {
      setSelectedPrinters([activePrinters[0].id]);
    }
  }, [mode, printers, selectedPrinters.length]);

  // Clear manual mappings and per-printer configs when printer or plate changes.
  // The per-plate mappings go with them: a manual override holds a global tray id,
  // which names a different spool on a different printer.
  useEffect(() => {
    if (mode === 'edit-queue-item') {
      // For edit mode, clear mappings if printer selection or plate changed from initial
      const printersChanged = JSON.stringify(selectedPrinters.sort()) !== JSON.stringify(initialPrinterIds.sort());
      if (printersChanged || selectedPlate !== initialPlateId) {
        setManualMappings({});
        setManualMappingsByPlate({});
        setPerPrinterConfigs({});
        setInitialExpandApplied(new Set());
      }
    } else {
      setManualMappings({});
      setManualMappingsByPlate({});
      setPerPrinterConfigs({});
      setInitialExpandApplied(new Set());
    }
  }, [mode, selectedPrinters, selectedPlate, initialPrinterIds, initialPlateId]);

  // Clear filament overrides when target model or plate changes (but not on initial mount for edit mode)
  const [prevTargetModel, setPrevTargetModel] = useState(targetModel);
  const [prevPlateForOverrides, setPrevPlateForOverrides] = useState(selectedPlate);
  useEffect(() => {
    if (targetModel !== prevTargetModel || selectedPlate !== prevPlateForOverrides) {
      setPrevTargetModel(targetModel);
      setPrevPlateForOverrides(selectedPlate);
      // Don't clear on initial render in edit mode (values are initialized from queueItem)
      if (mode !== 'edit-queue-item' || prevTargetModel !== null) {
        setFilamentOverrides({});
        setForceColorMatch({});
      }
    }
  }, [targetModel, selectedPlate, prevTargetModel, prevPlateForOverrides, mode]);

  // The sliced-for metadata loads async. If the user switched to model mode
  // before it arrived, the target is still empty (we never silently default
  // to another model, #2578) — fill it with the sliced-for model once known,
  // provided an active printer of that model exists.
  useEffect(() => {
    if (assignmentMode !== 'model' || targetModel || !slicedForModel) return;
    if (printers?.some((p) => p.is_active && p.model === slicedForModel)) {
      setTargetModel(slicedForModel);
    }
  }, [assignmentMode, targetModel, slicedForModel, printers]);

  // Auto-expand per-printer mapping when setting is enabled and multiple printers selected
  // Only applies once per printer on initial selection, not when user unchecks
  useEffect(() => {
    if (!settings?.per_printer_mapping_expanded) return;
    if (selectedPrinters.length <= 1) return;

    // Only auto-configure printers that:
    // 1. Haven't had initial expand applied yet
    // 2. Have their status loaded (so auto-configure will actually work)
    const printersReadyForExpand = selectedPrinters.filter(printerId => {
      if (initialExpandApplied.has(printerId)) return false;

      // Check if this printer has status loaded
      const result = multiPrinterMapping.printerResults.find(r => r.printerId === printerId);
      return result && result.status && !result.isLoading;
    });

    if (printersReadyForExpand.length > 0) {
      // Mark these printers as having been initially expanded
      setInitialExpandApplied(prev => {
        const next = new Set(prev);
        printersReadyForExpand.forEach(id => next.add(id));
        return next;
      });

      // Auto-configure printers
      printersReadyForExpand.forEach(printerId => {
        multiPrinterMapping.autoConfigurePrinter(printerId);
      });
    }
  }, [settings?.per_printer_mapping_expanded, selectedPrinters, initialExpandApplied, multiPrinterMapping]);

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !isSubmitting) onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose, isSubmitting]);

  const isMultiPlate = platesData?.is_multi_plate ?? false;
  const plates = platesData?.plates ?? [];

  const spoolAssignmentsByPrinter = useMemo(() => {
    const map = new Map<number, Map<number, SpoolAssignment>>();
    if (!spoolAssignments) return map;
    spoolAssignments.forEach((assignment) => {
      const isExternal = assignment.ams_id === 255;
      const globalTrayId = getGlobalTrayId(
        assignment.ams_id,
        assignment.tray_id,
        isExternal
      );
      const printerMap = map.get(assignment.printer_id) ?? new Map();
      printerMap.set(globalTrayId, assignment);
      map.set(assignment.printer_id, printerMap);
    });
    return map;
  }, [spoolAssignments]);

  const filamentWarningMessage = useMemo(() => {
    if (!filamentWarningItems || filamentWarningItems.length === 0) return '';
    const lines = filamentWarningItems.map((item) =>
      t('printModal.insufficientFilamentLine', {
        printer: item.printerName,
        slot: item.slotLabel,
        required: Math.round(item.requiredGrams),
        remaining: Math.round(item.remainingGrams),
      })
    );
    return [t('printModal.insufficientFilamentMessage'), ...lines].join('\n');
  }, [filamentWarningItems, t]);

  // Add to queue mutation (single printer)
  const addToQueueMutation = useMutation({
    mutationFn: (data: PrintQueueItemCreate) => api.addToQueue(data),
  });

  // Update queue item mutation
  const updateQueueMutation = useMutation({
    mutationFn: (data: PrintQueueItemUpdate) => api.updateQueueItem(queueItem!.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue'] });
      showToast('Queue item updated');
      onSuccess?.();
      onClose();
    },
    onError: (error: Error) => {
      showToast(error.message || 'Failed to update queue item', 'error');
    },
  });

  // Get mapping for a specific printer (per-printer override or default).
  // A multi-plate submission maps each plate on its own — `amsMapping` and the
  // per-printer mappings are both derived from the union of every selected
  // plate's filaments, which is not this plate's print (#2551 follow-up).
  // Without a per-plate mapping we send none at all and let the scheduler
  // compute one at dispatch, which it already does per plate; a union mapping
  // would be used verbatim and could feed a slot from the wrong tray.
  const getMappingForPrinter = (printerId: number, plateId: number | null): number[] | undefined => {
    if (isMultiPlateSelection) {
      // Fanning several plates across several printers would be a mapping per
      // plate *per printer*; those items go out without one and the scheduler
      // maps each plate against the printer it actually picks.
      if (plateId === null || selectedPrinters.length !== 1) return undefined;
      return perPlateAmsMappings.get(plateId);
    }
    // For multi-printer selection, check if this printer has an override
    if (selectedPrinters.length > 1) {
      const printerConfig = perPrinterConfigs[printerId];
      if (printerConfig && !printerConfig.useDefault) {
        return multiPrinterMapping.getFinalMapping(printerId);
      }
    }
    return amsMapping;
  };

  const handleSubmit = async (e?: React.FormEvent, options?: { skipFilamentCheck?: boolean }) => {
    e?.preventDefault();

    if (
      !options?.skipFilamentCheck &&
      !settings?.disable_filament_warnings &&
      !isEditing &&
      assignmentMode === 'printer'
    ) {
      const warningItems: FilamentWarningItem[] = [];

      // The spool check follows what is actually dispatched: one job per selected
      // plate, each with the mapping that plate's queue item carries. Two plates
      // can also draw on the same spool, so the demand is summed per tray before
      // it is weighed against what is left on it — 60 g left does not cover two
      // plates of 40 g, even though it covers either one of them (#2551).
      const plateJobs = isMultiPlateSelection
        ? selectedPlateIds.map((plateId) => ({ plateId, reqs: perPlateReqs.get(plateId)?.filaments ?? [] }))
        : [{ plateId: selectedPlate, reqs: effectiveFilamentReqs?.filaments ?? [] }];

      if (plateJobs.some((job) => job.reqs.length > 0) && spoolAssignmentsByPrinter.size > 0) {
        const getRemainingWeight = (labelWeight: number, weightUsed: number) => {
          if (!Number.isFinite(labelWeight) || labelWeight <= 0) return null;
          if (!Number.isFinite(weightUsed) || weightUsed < 0) return null;
          return Math.max(0, labelWeight - weightUsed);
        };

        for (const printerId of selectedPrinters) {
          const printerStatusForWarning = selectedPrinters.length > 1
            ? multiPrinterMapping.printerResults.find((result) => result.printerId === printerId)?.status
            : printerStatus;

          const loadedFilaments = buildLoadedFilaments(printerStatusForWarning);
          const slotLabelByTray = new Map(loadedFilaments.map((f) => [f.globalTrayId, f.label]));
          const assignments = spoolAssignmentsByPrinter.get(printerId);
          const printerName = printers?.find((p) => p.id === printerId)?.name ?? `Printer ${printerId}`;

          if (!assignments) continue;

          const gramsByTray = new Map<number, number>();
          for (const job of plateJobs) {
            // No mapping means the scheduler picks the trays at dispatch, against
            // an AMS state we cannot see from here — nothing to weigh.
            const printerMapping = getMappingForPrinter(printerId, job.plateId);
            if (!printerMapping) continue;

            job.reqs.forEach((req) => {
              if (!req.slot_id || req.slot_id <= 0) return;
              const globalTrayId = printerMapping[req.slot_id - 1];
              if (!Number.isFinite(globalTrayId) || globalTrayId < 0) return;
              gramsByTray.set(globalTrayId, (gramsByTray.get(globalTrayId) ?? 0) + req.used_grams);
            });
          }

          for (const [globalTrayId, requiredGrams] of gramsByTray) {
            const spool = assignments.get(globalTrayId)?.spool;
            if (!spool) continue;

            const remainingGrams = getRemainingWeight(spool.label_weight, spool.weight_used);
            if (remainingGrams === null) continue;
            if (remainingGrams >= requiredGrams) continue;

            warningItems.push({
              printerName,
              slotLabel: slotLabelByTray.get(globalTrayId) ?? `Tray ${globalTrayId}`,
              requiredGrams,
              remainingGrams,
            });
          }
        }
      }

      if (warningItems.length > 0) {
        setFilamentWarningItems(warningItems);
        return;
      }
    }

    // Validate printer/model selection
    if (assignmentMode === 'printer' && selectedPrinters.length === 0) {
      showToast('Please select at least one printer', 'error');
      return;
    }
    if (assignmentMode === 'model' && !targetModel) {
      showToast('Please select a target printer model', 'error');
      return;
    }
    // Cross-model safety gate (#2578) — mirrors the backend's 400 so the user
    // gets inline feedback instead of a failed request.
    if (assignmentMode === 'model' && !isGcodeCompatible(slicedForModel, targetModel)) {
      showToast(`File was sliced for ${slicedForModel} and cannot be dispatched to ${targetModel} printers`, 'error');
      return;
    }

    setIsSubmitting(true);
    // Calculate total API calls: plates × printers (or 1 for model-based)
    const platesToQueue = selectedPlates.size > 1
      ? plates.filter(p => selectedPlates.has(p.index))
      : [null];
    const totalCount = assignmentMode === 'model'
      ? platesToQueue.length
      : selectedPrinters.length * platesToQueue.length;
    setSubmitProgress({ current: 0, total: totalCount });

    const results: { success: number; failed: number; errors: string[] } = {
      success: 0,
      failed: 0,
      errors: [],
    };

    // Convert filament overrides from Record to array format for API.
    // Include all slots that either have a user override or have force_color_match enabled
    // (which is the default for model-based assignment).
    const buildFilamentOverridesArray = (reqs: FilamentReqsData | undefined) => {
      const entries: Array<{ slot_id: number; type: string; color: string; color_name: string; force_color_match: boolean }> = [];

      // Process all slots from filament requirements (to capture force_color_match defaults)
      if (reqs?.filaments) {
        for (const req of reqs.filaments) {
          const userOverride = filamentOverrides[req.slot_id];
          const isForceColor = forceColorMatch[req.slot_id] ?? false;
          const effectiveType = userOverride?.type ?? req.type;
          const effectiveColor = userOverride?.color ?? req.color;

          // Include slot if user changed the filament OR force_color_match is enabled
          if (userOverride || isForceColor) {
            entries.push({ slot_id: req.slot_id, type: effectiveType, color: effectiveColor, color_name: getColorName(effectiveColor), force_color_match: isForceColor });
          }
        }
      } else {
        // Fallback: no filament requirements data — only include explicit user overrides
        for (const [slotId, { type, color }] of Object.entries(filamentOverrides)) {
          const id = parseInt(slotId, 10);
          const isForceColor = forceColorMatch[id] ?? false;
          entries.push({ slot_id: id, type, color, color_name: getColorName(color), force_color_match: isForceColor });
        }
      }

      return entries.length > 0 ? entries : undefined;
    };

    const filamentOverridesArray = buildFilamentOverridesArray(effectiveFilamentReqs);

    // A plate only carries the slots it prints (#2552). Slot ids are global to the
    // file, so an override on slot 3 means the same filament in every plate that
    // uses slot 3 — the per-plate list is a subset of the shared state, not a
    // rewrite of it. No fallback to the whole-file list: it holds slots this plate
    // never prints, and submission is gated on every selected plate having answered,
    // so a plate is never missing here.
    const overridesForPlate = (plateId: number | null) =>
      isMultiPlateSelection && plateId !== null
        ? buildFilamentOverridesArray(perPlateReqs.get(plateId))
        : filamentOverridesArray;

    // Multi-plate auto-batch: when the user adds 2+ plates from one source in
    // a single create submission, pre-create a PrintBatch and pass its
    // id to each subsequent addToQueue call so the queue UI groups them as a
    // collapsible batch. Only triggered for single-target submissions —
    // multi-printer fan-out keeps the old per-item shape.
    const shouldAutoBatch =
      mode === 'create'
      && platesToQueue.length > 1
      && (assignmentMode === 'model' || selectedPrinters.length === 1);
    let autoBatchId: number | null = null;
    if (shouldAutoBatch) {
      try {
        const baseName = (archiveName || '').replace(/\.gcode\.3mf$/i, '').replace(/\.3mf$/i, '');
        const batchName = `${baseName || 'Batch'} · ${platesToQueue.length} plates`;
        const batch = await api.createBatch({
          name: batchName,
          archive_id: isLibraryFile ? undefined : archiveId,
          library_file_id: isLibraryFile ? libraryFileId : undefined,
        });
        autoBatchId = batch.id;
      } catch {
        // Non-fatal: fall back to ungrouped items so the queue still works.
        autoBatchId = null;
      }
    }

    const asapInsertionCounts = new Map<string, number>();

    const applyAsapInsertion = (
      queueData: PrintQueueItemCreate,
      printerId: number | null,
      itemCount = 1,
    ) => {
      if (scheduleOptions.scheduleType !== 'asap') return;
      const scopeKey = printerId !== null ? `printer:${printerId}` : 'unassigned';
      const insertPosition = (asapInsertionCounts.get(scopeKey) ?? 0) + 1;
      queueData.insert_at_top = true;
      queueData.insert_position = insertPosition;
      asapInsertionCounts.set(scopeKey, insertPosition + itemCount - 1);
    };

    // Common queue data for create and edit modes
    const getQueueData = (printerId: number | null, plateOverride?: number | null): PrintQueueItemCreate => {
      const plateId = plateOverride !== undefined ? plateOverride : selectedPlate;
      return {
      printer_id: assignmentMode === 'printer' ? printerId : null,
      target_model: assignmentMode === 'model' ? targetModel : null,
      target_location: assignmentMode === 'model' ? targetLocation : null,
      filament_overrides: assignmentMode === 'model' ? overridesForPlate(plateId) : undefined,
      // Use library_file_id for library files, archive_id for archives
      archive_id: isLibraryFile ? undefined : archiveId,
      library_file_id: isLibraryFile ? libraryFileId : undefined,
      require_previous_success: scheduleOptions.requirePreviousSuccess,
      auto_off_after: scheduleOptions.autoOffAfter,
      gcode_injection: scheduleOptions.gcodeInjection,
      manual_start: scheduleOptions.scheduleType === 'queue' && scheduleOptions.requireManualStart,
      // When the user clicks "Print Anyway" on the frontend deficit warning,
      // persist that acknowledgement so the scheduler doesn't immediately
      // re-flag the item on its first dispatch tick (#1698-followup).
      skip_filament_check: options?.skipFilamentCheck === true ? true : undefined,
      ams_mapping: printerId ? getMappingForPrinter(printerId, plateId) : undefined,
      plate_id: plateId,
      scheduled_time: scheduleOptions.scheduleType === 'scheduled' && scheduleOptions.scheduledTime
        ? new Date(scheduleOptions.scheduledTime).toISOString()
        : undefined,
      ...printOptions,
      project_id: projectId ?? undefined,
      batch_id: autoBatchId ?? undefined,
      cleanup_library_after_dispatch: cleanupLibraryAfterDispatch,
      };
    };

    // Model-based assignment
    if (assignmentMode === 'model') {
      let progressCounter = 0;
      for (const plate of platesToQueue) {
        progressCounter++;
        setSubmitProgress({ current: progressCounter, total: totalCount });
        const plateId = plate ? plate.index : selectedPlate;

        try {
          if (mode === 'edit-queue-item' && !plate) {
            // Edit mode - update with target_model (only for single plate)
            const updateData: PrintQueueItemUpdate = {
              printer_id: null,
              target_model: targetModel,
              target_location: targetLocation,
              filament_overrides: filamentOverridesArray || null,
              require_previous_success: scheduleOptions.requirePreviousSuccess,
              auto_off_after: scheduleOptions.autoOffAfter,
              gcode_injection: scheduleOptions.gcodeInjection,
              manual_start: scheduleOptions.scheduleType === 'queue' && scheduleOptions.requireManualStart,
              ams_mapping: undefined,
              plate_id: plateId,
              scheduled_time: scheduleOptions.scheduleType === 'scheduled' && scheduleOptions.scheduledTime
                ? new Date(scheduleOptions.scheduledTime).toISOString()
                : null,
              ...printOptions,
            };
            await updateQueueMutation.mutateAsync(updateData);
          } else {
            // Add-to-queue mode with model-based assignment
            const queueData = getQueueData(null, plateId);
            if (effectiveQuantity > 1) queueData.quantity = effectiveQuantity;
            applyAsapInsertion(queueData, null, effectiveQuantity);
            await addToQueueMutation.mutateAsync(queueData);
          }
          results.success++;
        } catch (error) {
          results.failed++;
          const plateName = plate ? (plate.name || `Plate ${plate.index}`) : '';
          results.errors.push(plateName ? `${plateName}: ${(error as Error).message}` : (error as Error).message);
        }
      }
    } else {
      // Printer-based assignment: loop through plates × printers
      // Compute stagger base time once before the loop
      const useStagger = scheduleOptions.staggerEnabled
        && !isEditing
        && selectedPrinters.length > 1;
      const staggerBaseTime = useStagger
        ? (scheduleOptions.scheduleType === 'scheduled' && scheduleOptions.scheduledTime
          ? new Date(scheduleOptions.scheduledTime).getTime()
          : Date.now())
        : 0;

      let progressCounter = 0;
      for (const plate of platesToQueue) {
        const plateId = plate ? plate.index : selectedPlate;

        for (let i = 0; i < selectedPrinters.length; i++) {
          const printerId = selectedPrinters[i];
          progressCounter++;
          setSubmitProgress({ current: progressCounter, total: totalCount });

          try {
            if (isEditing && progressCounter === 1) {
              // Edit mode - update the original queue item for the first entry
              const printerMapping = getMappingForPrinter(printerId, plateId);
              const updateData: PrintQueueItemUpdate = {
                printer_id: printerId,
                target_model: null,
                target_location: null,
                require_previous_success: scheduleOptions.requirePreviousSuccess,
                auto_off_after: scheduleOptions.autoOffAfter,
                gcode_injection: scheduleOptions.gcodeInjection,
                manual_start: scheduleOptions.scheduleType === 'queue' && scheduleOptions.requireManualStart,
                ams_mapping: printerMapping,
                plate_id: plateId,
                scheduled_time: scheduleOptions.scheduleType === 'scheduled' && scheduleOptions.scheduledTime
                  ? new Date(scheduleOptions.scheduledTime).toISOString()
                  : null,
                ...printOptions,
              };
              await updateQueueMutation.mutateAsync(updateData);
            } else {
              // New print mode, staggered print, or edit mode with additional entries
              const queueData = getQueueData(printerId, plateId);
              if (effectiveQuantity > 1) queueData.quantity = effectiveQuantity;
              applyAsapInsertion(queueData, printerId, effectiveQuantity);
              // Apply stagger offset for groups after the first
              if (useStagger) {
                const groupIndex = Math.floor(i / scheduleOptions.staggerGroupSize);
                if (groupIndex > 0) {
                  const offsetMs = groupIndex * scheduleOptions.staggerIntervalMinutes * 60_000;
                  queueData.scheduled_time = new Date(staggerBaseTime + offsetMs).toISOString();
                }
                // Group 0 with ASAP: no scheduled_time (start immediately)
                // Group 0 with scheduled: keeps the scheduled_time from getQueueData
              }
              await addToQueueMutation.mutateAsync(queueData);
            }
            results.success++;
          } catch (error) {
            results.failed++;
            const printerName = printers?.find(p => p.id === printerId)?.name || `Printer ${printerId}`;
            const plateName = plate ? (plate.name || `Plate ${plate.index}`) : '';
            const label = plateName ? `${printerName} (${plateName})` : printerName;
            results.errors.push(`${label}: ${(error as Error).message}`);
          }
        }
      }
    }

    setIsSubmitting(false);

    // Show result toast
    if (results.failed === 0) {
      if (isEditing) {
        if (mode === 'edit-queue-item') {
          showToast('Queue item updated');
        }
      } else if (results.success === 1) {
        const waitForIdleToast = await asapToastShouldPromiseLaterStart();
        showToast(
          waitForIdleToast
            ? t('queue.printQueuedWillStartWhenIdle')
            : assignmentMode === 'model'
              ? `Queued for any ${targetModel}`
              : t('queue.printQueued'),
        );
      } else {
        const waitForIdleToast = await asapToastShouldPromiseLaterStart();
        showToast(
          waitForIdleToast
            ? t('queue.printQueuedWillStartWhenIdle')
            : t('queue.itemsQueued', { count: results.success }),
        );
      }
      queryClient.invalidateQueries({ queryKey: ['queue'] });
      onSuccess?.();
      onClose();
    } else if (results.success === 0) {
      showToast(`Failed: ${results.errors[0]}`, 'error');
    } else {
      showToast(`${results.success} succeeded, ${results.failed} failed`, 'error');
      queryClient.invalidateQueries({ queryKey: ['queue'] });
    }
  };

  const isPending = isSubmitting || updateQueueMutation.isPending;

  const canSubmit = useMemo(() => {
    if (isPending) return false;

    // Need valid printer/model selection
    if (assignmentMode === 'printer' && selectedPrinters.length === 0) return false;
    if (assignmentMode === 'model' && !targetModel) return false;
    // Cross-model mismatch cannot be queued (#2578)
    if (assignmentMode === 'model' && !isGcodeCompatible(slicedForModel, targetModel)) return false;

    // For multi-plate files, need at least one plate selected
    if (isMultiPlate && selectedPlates.size === 0) return false;

    // Every selected plate has to have answered before we can queue it: a plate
    // still in flight would be sent with no mapping and no overrides, and one that
    // failed to load cannot be mapped at all. Deselect the failing plate to queue
    // the rest — the banner above says which state we are in.
    if (perPlateReqsPending || perPlateReqsFailed) return false;

    return true;
  }, [
    selectedPrinters.length,
    assignmentMode,
    targetModel,
    slicedForModel,
    isMultiPlate,
    selectedPlates.size,
    isPending,
    perPlateReqsPending,
    perPlateReqsFailed,
  ]);

  // Quantity only applies for single-printer or model-based assignment (not multi-printer)
  const effectiveQuantity = (assignmentMode === 'printer' && selectedPrinters.length > 1) ? 1 : quantity;

  // Clear gcode_injection if the admin removes all snippets while the modal
  // is open — the checkbox itself hides via hasGcodeSnippets in
  // ScheduleOptions, but the boolean would otherwise stay true and ship to
  // the API. The previous gate also reset the flag whenever effectiveQuantity
  // dropped to <= 1, which silently un-ticked the checkbox on every single-
  // print create flow (#1852). The scheduler reads item.gcode_injection per
  // queue item regardless of batch size, so there's no underlying reason for
  // the quantity-1 case to be blocked.
  useEffect(() => {
    if (mode === 'create' && scheduleOptions.gcodeInjection && !settings?.gcode_snippets) {
      setScheduleOptions((opts) => ({ ...opts, gcodeInjection: false }));
    }
  }, [mode, settings?.gcode_snippets, scheduleOptions.gcodeInjection]);

  // Modal title and action button text based on mode
  const getModalConfig = () => {
    if (!isEditing) {
      return {
        title: t('common.print'),
        icon: Printer,
        submitText: t('common.print'),
        submitIcon: Printer,
        loadingText: submitProgress.total > 1
          ? t('queue.addingProgress', { current: submitProgress.current, total: submitProgress.total })
          : t('queue.adding'),
      };
    }
    // edit-queue-item mode
    return {
      title: t('queue.editQueueItem'),
      icon: Pencil,
      submitText: t('common.save'),
      submitIcon: Pencil,
      loadingText: submitProgress.total > 1
        ? t('queue.savingProgress', { current: submitProgress.current, total: submitProgress.total })
        : t('common.saving'),
    };
  };

  const modalConfig = getModalConfig();
  const TitleIcon = modalConfig.icon;
  const SubmitIcon = modalConfig.submitIcon;

  // Show filament mapping when:
  // - Single printer selected
  // - For archives: plate is selected (for multi-plate) or not required (single-plate)
  // - For library files: always show (no plate selection)
  const showFilamentMapping = effectivePrinterId && selectedPlates.size <= 1 && (
    isLibraryFile || (isMultiPlate ? selectedPlate !== null : true)
  );

  // Several plates on one printer: one mapping panel per plate, each mapping only
  // the slots its own plate prints. Multi-printer fan-out would be a panel per
  // plate *per printer*, so those items ship without a mapping and the scheduler
  // computes one per plate when it picks the printer.
  const showPerPlateFilamentMapping =
    !!effectivePrinterId && isMultiPlateSelection && selectedPrinters.length === 1;

  // Model mode has no printer and so no trays to map onto; what it offers instead
  // is the filament each slot must be printed in, which the scheduler matches
  // against whatever printer of the model it picks. Needs the model's loaded
  // filaments to offer as alternatives.
  const showFilamentOverride =
    assignmentMode === 'model' && !!targetModel && !!availableFilaments && availableFilaments.length > 0;

  // Dual-nozzle gate for the Nozzle Offset Calibration toggle (#1682).
  // Mirrors backend `DUAL_NOZZLE_MODELS` so model-based assignment can show
  // the toggle without a specific printer selected. For printer-mode we rely
  // on the canonical `nozzle_count` field auto-detected from MQTT.
  const DUAL_NOZZLE_MODELS = useMemo(
    () => new Set(['H2D', 'H2DPRO', 'H2C', 'X2D']),
    [],
  );
  const showDualNozzleOptions = useMemo(() => {
    if (assignmentMode === 'model') {
      if (!targetModel) return false;
      return DUAL_NOZZLE_MODELS.has(targetModel.toUpperCase().replace(/[\s-]/g, ''));
    }
    if (!printers || selectedPrinters.length === 0) return false;
    return selectedPrinters.some(id => printers.find(p => p.id === id)?.nozzle_count === 2);
  }, [assignmentMode, targetModel, printers, selectedPrinters, DUAL_NOZZLE_MODELS]);

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
      onClick={isSubmitting ? undefined : onClose}
    >
      <Card
        className="w-full max-w-2xl max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <CardContent className="p-0">
          {/* Header */}
          <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
            <div className="flex items-center gap-2">
              <TitleIcon className="w-5 h-5 text-bambu-green" />
              <h2 className="text-lg font-semibold text-white">{modalConfig.title}</h2>
            </div>
            <Button variant="ghost" size="sm" onClick={onClose} disabled={isSubmitting}>
              <X className="w-5 h-5" />
            </Button>
          </div>

          <form onSubmit={handleSubmit} className="p-4 space-y-4">
            {/* Archive name */}
            <p className="text-sm text-bambu-gray">
              <span className="block text-bambu-gray mb-1">Print Job</span>
              <span className="text-white font-medium truncate block">{archiveName}</span>
            </p>

            {/* Build-plate badge for the selected (or sole) plate — surfaced
                early so the user knows which plate to mount before scheduling
                (#1281). PlateSelector renders its own per-plate badges for
                multi-plate files; this badge covers the single-plate case and
                the multi-plate case where exactly one plate is selected. */}
            {(() => {
              if (!plates.length) return null;
              const target = selectedPlate != null
                ? plates.find(p => p.index === selectedPlate)
                : plates[0];
              const bed = getBedTypeInfo(target?.bed_type);
              if (!bed) return null;
              return (
                <p className="flex items-center gap-1.5 text-xs text-bambu-gray -mt-2" title={bed.label}>
                  <img src={bed.icon} alt="" className="w-4 h-4 object-contain flex-shrink-0" />
                  <span className="truncate">{bed.label}</span>
                </p>
              );
            })()}

            {/* Plate selection - first so users know filament requirements before selecting printers */}
            <PlateSelector
              plates={plates}
              isMultiPlate={isMultiPlate}
              selectedPlates={selectedPlates}
              onToggle={(plateIndex) => {
                setSelectedPlates(prev => {
                  const next = new Set(prev);
                  if (!isEditing) {
                    // Multi-select: toggle the plate
                    if (next.has(plateIndex)) {
                      next.delete(plateIndex);
                    } else {
                      next.add(plateIndex);
                    }
                  } else {
                    // Single-select: replace selection
                    next.clear();
                    next.add(plateIndex);
                  }
                  return next;
                });
              }}
              onSelectAll={!isEditing ? () => setSelectedPlates(new Set(plates.map(p => p.index))) : undefined}
              onDeselectAll={!isEditing ? () => setSelectedPlates(new Set()) : undefined}
              multiSelect={!isEditing}
            />

            {/* Printer selection with per-printer mapping — hidden when printer is pre-selected via props */}
            {!initialSelectedPrinterIds?.length && (
              <PrinterSelector
                printers={printers || []}
                selectedPrinterIds={selectedPrinters}
                onMultiSelect={setSelectedPrinters}
                isLoading={loadingPrinters}
                allowMultiple={true}
                showInactive={mode === 'edit-queue-item'}
                disableBusy={false}
                printerMappingResults={multiPrinterMapping.printerResults}
                // The per-printer tray editor inside the selector maps one filament
                // list onto each printer. Several plates have several lists, and a
                // fan-out across printers ships no mapping at all (the scheduler maps
                // each plate against the printer it picks), so the editor would be
                // collecting tray choices it then throws away. Withhold its input.
                filamentReqs={isMultiPlateSelection ? undefined : effectiveFilamentReqs}
                onAutoConfigurePrinter={multiPrinterMapping.autoConfigurePrinter}
                onUpdatePrinterConfig={multiPrinterMapping.updatePrinterConfig}
                assignmentMode={assignmentMode}
                onAssignmentModeChange={setAssignmentMode}
                targetModel={targetModel}
                onTargetModelChange={setTargetModel}
                targetLocation={targetLocation}
                onTargetLocationChange={setTargetLocation}
                slicedForModel={slicedForModel}
              />
            )}

            {/* Filament override - shown in model mode when filament requirements are available */}
            {showFilamentOverride && !isMultiPlateSelection && effectiveFilamentReqs && (
              <FilamentOverride
                filamentReqs={effectiveFilamentReqs}
                availableFilaments={availableFilaments!}
                overrides={filamentOverrides}
                onChange={setFilamentOverrides}
                forceColorMatch={forceColorMatch}
                onForceColorMatchChange={(slotId, value) =>
                  setForceColorMatch((prev) => ({ ...prev, [slotId]: value }))
                }
              />
            )}

            {/* Filament override, one panel per selected plate. `effectiveFilamentReqs`
                is keyed on `selectedPlate`, which is null as soon as two plates are
                picked, so a multi-plate selection used to render this panel from
                whatever the whole-file query had left in the cache — the union of every
                plate's filaments, or nothing at all once the plates query was warm and
                the whole-file query therefore never ran, which is why the section
                vanished on the second open of the dialog (#2552). */}
            {showFilamentOverride && isMultiPlateSelection && selectedPlateIds.map((plateId, idx) => {
              const plate = plates.find((p) => p.index === plateId);
              const plateReqs = perPlateReqs.get(plateId);
              if (!plateReqs) return null;
              return (
                <FilamentOverride
                  key={plateId}
                  plateLabel={plate?.name || t('printModal.plateN', 'Plate {{n}}', { n: plateId })}
                  showHint={idx === 0}
                  filamentReqs={plateReqs}
                  availableFilaments={availableFilaments!}
                  overrides={filamentOverrides}
                  onChange={setFilamentOverrides}
                  forceColorMatch={forceColorMatch}
                  onForceColorMatchChange={(slotId, value) =>
                    setForceColorMatch((prev) => ({ ...prev, [slotId]: value }))
                  }
                />
              );
            })}

            {/* Compatibility warning when sliced model doesn't match selected printer */}
            {slicedForModel && assignmentMode === 'printer' && selectedPrinters.length === 1 && (() => {
              const selectedPrinter = printers?.find(p => p.id === selectedPrinters[0]);
              if (selectedPrinter && selectedPrinter.model && slicedForModel !== selectedPrinter.model) {
                return (
                  <div className="p-3 mb-2 bg-yellow-50 dark:bg-yellow-500/10 border border-yellow-300 dark:border-yellow-500/30 rounded-lg flex items-center gap-2">
                    <AlertTriangle className="w-4 h-4 text-yellow-600 dark:text-yellow-400 flex-shrink-0" />
                    <span className="text-sm text-yellow-700 dark:text-yellow-400">
                      File was sliced for {slicedForModel}, but printing on {selectedPrinter.model}
                    </span>
                  </div>
                );
              }
              return null;
            })()}

            {/* Warning when archive data couldn't be loaded */}
            {archiveDataMissing && (
              <div className="flex items-start gap-2 p-3 mb-2 bg-orange-50 dark:bg-orange-500/10 border border-orange-300 dark:border-orange-500/30 rounded-lg text-sm">
                <AlertCircle className="w-4 h-4 text-orange-600 dark:text-orange-400 mt-0.5 flex-shrink-0" />
                <p className="text-orange-700 dark:text-orange-400">
                  Archive data unavailable. The source file may have been deleted. Filament mapping is disabled.
                </p>
              </div>
            )}

            {/* A selected plate whose filaments could not be read cannot be mapped and
                cannot carry its forced colours, so it is not queued silently — say so
                and hold the button until the plate is deselected. */}
            {perPlateReqsFailed && (
              <div className="flex items-start gap-2 p-3 mb-2 bg-orange-50 dark:bg-orange-500/10 border border-orange-300 dark:border-orange-500/30 rounded-lg text-sm">
                <AlertCircle className="w-4 h-4 text-orange-600 dark:text-orange-400 mt-0.5 flex-shrink-0" />
                <p className="text-orange-700 dark:text-orange-400">
                  {t(
                    'printModal.plateFilamentsUnreadable',
                    "The filaments of a selected plate could not be read, so it can't be mapped. Deselect it to queue the others.",
                  )}
                </p>
              </div>
            )}

            {/* Filament mapping - only show when single printer selected */}
            {showFilamentMapping && !archiveDataMissing && selectedPrinters.length === 1 && (
              <FilamentMapping
                printerId={effectivePrinterId!}
                filamentReqs={effectiveFilamentReqs}
                manualMappings={manualMappings}
                onManualMappingChange={setManualMappings}
                defaultExpanded={!!initialSelectedPrinterIds?.length || (settings?.per_printer_mapping_expanded ?? false)}
                currencySymbol={currencySymbol}
                defaultCostPerKg={defaultCostPerKg}
                forceColorMatch={forceColorMatch}
                onForceColorMatchChange={(slotId, value) =>
                  setForceColorMatch((prev) => ({ ...prev, [slotId]: value }))
                }
              />
            )}

            {/* Filament mapping, one panel per selected plate — each plate is its
                own print with its own slots, so it gets its own AMS mapping. */}
            {showPerPlateFilamentMapping && !archiveDataMissing && selectedPlateIds.map((plateId) => {
              const plate = plates.find((p) => p.index === plateId);
              const plateReqs = perPlateReqs.get(plateId);
              if (!plateReqs) return null;
              return (
                <FilamentMapping
                  key={plateId}
                  printerId={effectivePrinterId!}
                  plateLabel={plate?.name || t('printModal.plateN', 'Plate {{n}}', { n: plateId })}
                  filamentReqs={plateReqs}
                  manualMappings={manualMappingsByPlate[plateId] ?? {}}
                  onManualMappingChange={(mappings) =>
                    setManualMappingsByPlate((prev) => ({ ...prev, [plateId]: mappings }))
                  }
                  defaultExpanded={false}
                  currencySymbol={currencySymbol}
                  defaultCostPerKg={defaultCostPerKg}
                  forceColorMatch={forceColorMatch}
                  onForceColorMatchChange={(slotId, value) =>
                    setForceColorMatch((prev) => ({ ...prev, [slotId]: value }))
                  }
                />
              );
            })}

            {/* Print options */}
            {(mode === 'create' || effectivePrinterCount > 0 || (assignmentMode === 'model' && targetModel)) && (
              <PrintOptionsPanel
                options={printOptions}
                onChange={setPrintOptions}
                defaultExpanded={!!initialSelectedPrinterIds?.length}
                showDualNozzleOptions={showDualNozzleOptions}
              />
            )}

            {/* Quantity — create multiple copies (batch). Hidden for multi-printer selection. */}
            {mode !== 'edit-queue-item' && (assignmentMode === 'model' || selectedPrinters.length <= 1) && (
              <div className="flex items-center gap-3">
                <label htmlFor="printQuantity" className="text-sm text-bambu-gray whitespace-nowrap">
                  {t('queue.quantity', 'Quantity')}
                </label>
                <input
                  id="printQuantity"
                  type="number"
                  min={1}
                  max={999}
                  value={quantity}
                  onChange={(e) => setQuantity(Math.max(1, Math.min(999, parseInt(e.target.value) || 1)))}
                  className="w-20 px-2 py-1 text-sm bg-bambu-dark border border-bambu-dark-tertiary rounded text-white focus:outline-none focus:ring-1 focus:ring-bambu-green"
                />
                {quantity > 1 && (
                  <span className="text-xs text-bambu-gray">
                    {t('queue.quantityHint', 'Creates {{count}} queue items', { count: quantity })}
                  </span>
                )}
              </div>
            )}

            {/* Schedule options */}
            <ScheduleOptionsPanel
              options={scheduleOptions}
              onChange={setScheduleOptions}
              dateFormat={settings?.date_format || 'system'}
              timeFormat={settings?.time_format || 'system'}
              canControlPrinter={hasPermission('printers:control')}
              showStagger={!isEditing && assignmentMode === 'printer' && selectedPrinters.length > 1}
              printerCount={selectedPrinters.length}
              hasGcodeSnippets={!!settings?.gcode_snippets}
            />

            {/* Error message */}
            {updateQueueMutation.isError && (
              <div className="mb-4 p-3 bg-red-100 dark:bg-red-500/20 border border-red-500/50 rounded-lg text-sm text-red-700 dark:text-red-400">
                {(updateQueueMutation.error as Error)?.message || 'Failed to complete operation'}
              </div>
            )}

            {/* Actions */}
            <div className="flex gap-3 pt-2">
              <Button type="button" variant="secondary" onClick={onClose} className="flex-1" disabled={isSubmitting}>
                Cancel
              </Button>
              <Button
                type="submit"
                disabled={!canSubmit}
                className="flex-1"
              >
                {isPending ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    {modalConfig.loadingText}
                  </>
                ) : (
                  <>
                    <SubmitIcon className="w-4 h-4" />
                    {modalConfig.submitText}
                  </>
                )}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {filamentWarningItems && filamentWarningItems.length > 0 && (
        <ConfirmModal
          title={t('printModal.insufficientFilamentTitle')}
          message={filamentWarningMessage}
          confirmText={t('printModal.printAnyway')}
          cancelText={t('common.cancel')}
          variant="warning"
          onConfirm={() => {
            setFilamentWarningItems(null);
            void handleSubmit(undefined, { skipFilamentCheck: true });
          }}
          onCancel={() => setFilamentWarningItems(null)}
        />
      )}
    </div>
  );
}

// Re-export types for convenience
export type { PrintModalMode, PrintModalProps } from './types';
