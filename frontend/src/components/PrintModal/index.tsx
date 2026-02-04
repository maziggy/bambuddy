import { useState, useEffect, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { X, Printer, Loader2, Calendar, Pencil, AlertCircle, AlertTriangle } from 'lucide-react';
import { api } from '../../api/client';
import type { PrintQueueItemCreate, PrintQueueItemUpdate } from '../../api/client';
import { Card, CardContent } from '../Card';
import { Button } from '../Button';
import { useToast } from '../../contexts/ToastContext';
import { useFilamentMapping } from '../../hooks/useFilamentMapping';
import { useMultiPrinterFilamentMapping, type PerPrinterConfig } from '../../hooks/useMultiPrinterFilamentMapping';
import { isPlaceholderDate } from '../../utils/amsHelpers';
import { toDateTimeLocalValue } from '../../utils/date';
import { PrinterSelector } from './PrinterSelector';
import { PlateSelector } from './PlateSelector';
import { PlateDetails } from './PlateDetails';
import { FilamentMapping } from './FilamentMapping';
import { PrintOptionsPanel } from './PrintOptions';
import { ScheduleOptionsPanel } from './ScheduleOptions';
import type {
  PrintModalProps,
  PrintOptions,
  ScheduleOptions,
  ScheduleType,
  AssignmentMode,
} from './types';
import { DEFAULT_PRINT_OPTIONS, DEFAULT_SCHEDULE_OPTIONS } from './types';

/**
 * Unified PrintModal component that handles three modes:
 * - 'reprint': Immediate print from archive or library file (supports multi-printer)
 * - 'add-to-queue': Schedule print to queue from archive or library file (supports multi-printer)
 * - 'edit-queue-item': Edit existing queue item (supports multi-printer)
 *
 * Both archiveId and libraryFileId are supported. Library files can be printed immediately
 * or added to queue (archive is created at print start time, not when queued).
 */
export function PrintModal({
  mode,
  archiveId,
  libraryFileId,
  archiveName,
  queueItem,
  onClose,
  onSuccess,
}: PrintModalProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  // Determine if we're printing a library file
  const isLibraryFile = !!libraryFileId && !archiveId;

  // Multiple printer selection (used for all modes now)
  const [selectedPrinters, setSelectedPrinters] = useState<number[]>(() => {
    // Initialize with the queue item's printer if editing
    if (mode === 'edit-queue-item' && queueItem?.printer_id) {
      return [queueItem.printer_id];
    }
    return [];
  });

  const [selectedPlate, setSelectedPlate] = useState<number | null>(() => {
    if (mode === 'edit-queue-item' && queueItem) {
      return queueItem.plate_id;
    }
    return null;
  });

  const [printOptions, setPrintOptions] = useState<PrintOptions>(() => {
    if (mode === 'edit-queue-item' && queueItem) {
      return {
        bed_levelling: queueItem.bed_levelling ?? DEFAULT_PRINT_OPTIONS.bed_levelling,
        flow_cali: queueItem.flow_cali ?? DEFAULT_PRINT_OPTIONS.flow_cali,
        vibration_cali: queueItem.vibration_cali ?? DEFAULT_PRINT_OPTIONS.vibration_cali,
        layer_inspect: queueItem.layer_inspect ?? DEFAULT_PRINT_OPTIONS.layer_inspect,
        timelapse: queueItem.timelapse ?? DEFAULT_PRINT_OPTIONS.timelapse,
      };
    }
    return DEFAULT_PRINT_OPTIONS;
  });

  const [scheduleOptions, setScheduleOptions] = useState<ScheduleOptions>(() => {
    if (mode === 'edit-queue-item' && queueItem) {
      let scheduleType: ScheduleType = 'asap';
      if (queueItem.manual_start) {
        scheduleType = 'manual';
      } else if (queueItem.scheduled_time && !isPlaceholderDate(queueItem.scheduled_time)) {
        scheduleType = 'scheduled';
      }

      let scheduledTime = '';
      if (queueItem.scheduled_time && !isPlaceholderDate(queueItem.scheduled_time)) {
        const date = new Date(queueItem.scheduled_time);
        // Use toDateTimeLocalValue to convert UTC to local time for datetime-local input
        scheduledTime = toDateTimeLocalValue(date);
      }

      return {
        scheduleType,
        scheduledTime,
        requirePreviousSuccess: queueItem.require_previous_success,
        autoOffAfter: queueItem.auto_off_after,
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

  // Track initial values for clearing mappings on change (edit mode only)
  const [initialPrinterIds] = useState(() => (mode === 'edit-queue-item' && queueItem?.printer_id ? [queueItem.printer_id] : []));
  const [initialPlateId] = useState(() => (mode === 'edit-queue-item' && queueItem ? queueItem.plate_id : null));

  // Submission state for multi-printer
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitProgress, setSubmitProgress] = useState({ current: 0, total: 0 });

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

  const { data: printers, isLoading: loadingPrinters } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

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

  // Only fetch printer status when single printer selected (for filament mapping)
  const { data: printerStatus } = useQuery({
    queryKey: ['printer-status', effectivePrinterId],
    queryFn: () => api.getPrinterStatus(effectivePrinterId!),
    enabled: !!effectivePrinterId,
  });

  // Get AMS mapping from hook (only when single printer selected)
  const { amsMapping } = useFilamentMapping(effectiveFilamentReqs, printerStatus, manualMappings);

  // Multi-printer filament mapping (for per-printer configuration)
  const multiPrinterMapping = useMultiPrinterFilamentMapping(
    selectedPrinters,
    printers,
    effectiveFilamentReqs,
    manualMappings,
    perPrinterConfigs,
    setPerPrinterConfigs
  );

  // Auto-select first plate for single-plate files
  useEffect(() => {
    if (platesData?.plates?.length === 1 && !selectedPlate) {
      setSelectedPlate(platesData.plates[0].index);
    }
  }, [platesData, selectedPlate]);

  // Auto-select first printer when only one available
  useEffect(() => {
    // Skip auto-select for edit mode (already initialized from queueItem)
    if (mode === 'edit-queue-item') return;
    const activePrinters = printers?.filter(p => p.is_active) || [];
    if (activePrinters.length === 1 && selectedPrinters.length === 0) {
      setSelectedPrinters([activePrinters[0].id]);
    }
  }, [mode, printers, selectedPrinters.length]);

  // Clear manual mappings and per-printer configs when printer or plate changes
  useEffect(() => {
    if (mode === 'edit-queue-item') {
      // For edit mode, clear mappings if printer selection or plate changed from initial
      const printersChanged = JSON.stringify(selectedPrinters.sort()) !== JSON.stringify(initialPrinterIds.sort());
      if (printersChanged || selectedPlate !== initialPlateId) {
        setManualMappings({});
        setPerPrinterConfigs({});
        setInitialExpandApplied(new Set());
      }
    } else {
      setManualMappings({});
      setPerPrinterConfigs({});
      setInitialExpandApplied(new Set());
    }
  }, [mode, selectedPrinters, selectedPlate, initialPrinterIds, initialPlateId]);

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
  const selectedPlateInfo = selectedPlate != null
    ? plates.find((plate) => plate.index === selectedPlate) ?? null
    : null;

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

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault();

    // Validate printer/model selection
    if (assignmentMode === 'printer' && selectedPrinters.length === 0) {
      showToast('Please select at least one printer', 'error');
      return;
    }
    if (assignmentMode === 'model' && !targetModel) {
      showToast('Please select a target printer model', 'error');
      return;
    }

    setIsSubmitting(true);
    // For model-based assignment, we just make one API call
    const totalCount = assignmentMode === 'model' ? 1 : selectedPrinters.length;
    setSubmitProgress({ current: 0, total: totalCount });

    const results: { success: number; failed: number; errors: string[] } = {
      success: 0,
      failed: 0,
      errors: [],
    };

    // Get mapping for a specific printer (per-printer override or default)
    const getMappingForPrinter = (printerId: number): number[] | undefined => {
      // For multi-printer selection, check if this printer has an override
      if (selectedPrinters.length > 1) {
        const printerConfig = perPrinterConfigs[printerId];
        if (printerConfig && !printerConfig.useDefault) {
          return multiPrinterMapping.getFinalMapping(printerId);
        }
      }
      return amsMapping;
    };

    // Common queue data for add-to-queue and edit modes
    const getQueueData = (printerId: number | null): PrintQueueItemCreate => ({
      printer_id: assignmentMode === 'printer' ? printerId : null,
      target_model: assignmentMode === 'model' ? targetModel : null,
      target_location: assignmentMode === 'model' ? targetLocation : null,
      // Use library_file_id for library files, archive_id for archives
      archive_id: isLibraryFile ? undefined : archiveId,
      library_file_id: isLibraryFile ? libraryFileId : undefined,
      require_previous_success: scheduleOptions.requirePreviousSuccess,
      auto_off_after: scheduleOptions.autoOffAfter,
      manual_start: scheduleOptions.scheduleType === 'manual',
      ams_mapping: printerId ? getMappingForPrinter(printerId) : undefined,
      plate_id: selectedPlate,
      scheduled_time: scheduleOptions.scheduleType === 'scheduled' && scheduleOptions.scheduledTime
        ? new Date(scheduleOptions.scheduledTime).toISOString()
        : undefined,
      ...printOptions,
    });

    // Model-based assignment: single API call
    if (assignmentMode === 'model') {
      setSubmitProgress({ current: 1, total: 1 });
      try {
        if (mode === 'reprint') {
          // Model-based reprint not supported (need specific printer for immediate print)
          showToast('Model-based assignment only works with queue mode', 'error');
          setIsSubmitting(false);
          return;
        } else if (mode === 'edit-queue-item') {
          // Edit mode - update with target_model
          const updateData: PrintQueueItemUpdate = {
            printer_id: null,
            target_model: targetModel,
            target_location: targetLocation,
            require_previous_success: scheduleOptions.requirePreviousSuccess,
            auto_off_after: scheduleOptions.autoOffAfter,
            manual_start: scheduleOptions.scheduleType === 'manual',
            ams_mapping: undefined,
            plate_id: selectedPlate,
            scheduled_time: scheduleOptions.scheduleType === 'scheduled' && scheduleOptions.scheduledTime
              ? new Date(scheduleOptions.scheduledTime).toISOString()
              : null,
            ...printOptions,
          };
          await updateQueueMutation.mutateAsync(updateData);
        } else {
          // Add-to-queue mode with model-based assignment
          await addToQueueMutation.mutateAsync(getQueueData(null));
        }
        results.success++;
      } catch (error) {
        results.failed++;
        results.errors.push((error as Error).message);
      }
    } else {
      // Printer-based assignment: loop through selected printers
      for (let i = 0; i < selectedPrinters.length; i++) {
        const printerId = selectedPrinters[i];
        setSubmitProgress({ current: i + 1, total: selectedPrinters.length });

        try {
          if (mode === 'reprint') {
            // Reprint mode - start print immediately
            const printerMapping = getMappingForPrinter(printerId);
            if (isLibraryFile) {
              await api.printLibraryFile(libraryFileId!, printerId, {
                ams_mapping: printerMapping,
                ...printOptions,
              });
            } else {
              await api.reprintArchive(archiveId!, printerId, {
                plate_id: selectedPlate ?? undefined,
                ams_mapping: printerMapping,
                ...printOptions,
              });
            }
          } else if (mode === 'edit-queue-item' && i === 0) {
            // Edit mode - update the original queue item for the first printer
            const printerMapping = getMappingForPrinter(printerId);
            const updateData: PrintQueueItemUpdate = {
              printer_id: printerId,
              target_model: null,
              target_location: null,
              require_previous_success: scheduleOptions.requirePreviousSuccess,
              auto_off_after: scheduleOptions.autoOffAfter,
              manual_start: scheduleOptions.scheduleType === 'manual',
              ams_mapping: printerMapping,
              plate_id: selectedPlate,
              scheduled_time: scheduleOptions.scheduleType === 'scheduled' && scheduleOptions.scheduledTime
                ? new Date(scheduleOptions.scheduledTime).toISOString()
                : null,
              ...printOptions,
            };
            await updateQueueMutation.mutateAsync(updateData);
          } else {
            // Add-to-queue mode OR edit mode with additional printers
            await addToQueueMutation.mutateAsync(getQueueData(printerId));
          }
          results.success++;
        } catch (error) {
          results.failed++;
          const printerName = printers?.find(p => p.id === printerId)?.name || `Printer ${printerId}`;
          results.errors.push(`${printerName}: ${(error as Error).message}`);
        }
      }
    }

    setIsSubmitting(false);

    // Show result toast
    if (results.failed === 0) {
      if (assignmentMode === 'model') {
        showToast(mode === 'edit-queue-item' ? 'Queue item updated' : `Queued for any ${targetModel}`);
      } else {
        const action = mode === 'reprint' ? 'sent to' : (mode === 'edit-queue-item' ? 'updated/queued for' : 'queued for');
        if (results.success === 1) {
          showToast(mode === 'edit-queue-item' ? 'Queue item updated' : `Print ${action} printer`);
        } else {
          showToast(`Print ${action} ${results.success} printers`);
        }
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

    // Model-based assignment only works in queue modes (not immediate reprint)
    if (assignmentMode === 'model' && mode === 'reprint') return false;

    // For multi-plate archive files, need a selected plate (library files skip this)
    if (!isLibraryFile && isMultiPlate && !selectedPlate) return false;

    return true;
  }, [selectedPrinters.length, assignmentMode, targetModel, mode, isMultiPlate, selectedPlate, isPending, isLibraryFile]);

  // Modal title and action button text based on mode
  const getModalConfig = () => {
    const printerCount = selectedPrinters.length;

    if (mode === 'reprint') {
      return {
        title: isLibraryFile ? t('queue.print') : t('queue.reprint'),
        icon: Printer,
        submitText: printerCount > 1 ? t('queue.printToPrinters', { count: printerCount }) : t('queue.print'),
        submitIcon: Printer,
        loadingText: submitProgress.total > 1
          ? t('queue.sendingProgress', { current: submitProgress.current, total: submitProgress.total })
          : t('queue.sending'),
      };
    }
    if (mode === 'add-to-queue') {
      return {
        title: t('queue.schedulePrint'),
        icon: Calendar,
        submitText: printerCount > 1 ? t('queue.queueToPrinters', { count: printerCount }) : t('queue.addToQueue'),
        submitIcon: Calendar,
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
  const showFilamentMapping = effectivePrinterId && (
    isLibraryFile || (isMultiPlate ? selectedPlate !== null : true)
  );

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
      onClick={isSubmitting ? undefined : onClose}
    >
      <Card
        className="w-full max-w-lg max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <CardContent className={mode === 'reprint' ? '' : 'p-0'}>
          {/* Header */}
          <div
            className={`flex items-center justify-between ${
              mode === 'reprint' ? 'mb-4' : 'p-4 border-b border-bambu-dark-tertiary'
            }`}
          >
            <div className="flex items-center gap-2">
              <TitleIcon className="w-5 h-5 text-bambu-green" />
              <h2 className="text-lg font-semibold text-white">{modalConfig.title}</h2>
            </div>
            <Button variant="ghost" size="sm" onClick={onClose} disabled={isSubmitting}>
              <X className="w-5 h-5" />
            </Button>
          </div>

          <form onSubmit={handleSubmit} className={mode === 'reprint' ? '' : 'p-4 space-y-4'}>
            {/* Archive name */}
            <p className={`text-sm text-bambu-gray ${mode === 'reprint' ? 'mb-4' : ''}`}>
              {mode === 'reprint' ? (
                <>
                  Send <span className="text-white">{archiveName}</span> to printer(s)
                </>
              ) : (
                <>
                  <span className="block text-bambu-gray mb-1">Print Job</span>
                  <span className="text-white font-medium truncate block">{archiveName}</span>
                </>
              )}
            </p>

            {/* Plate selection - first so users know filament requirements before selecting printers */}
            <PlateSelector
              plates={plates}
              isMultiPlate={isMultiPlate}
              selectedPlate={selectedPlate}
              onSelect={setSelectedPlate}
            />

            <PlateDetails plate={selectedPlateInfo} />

            {/* Printer selection with per-printer mapping */}
            <PrinterSelector
              printers={printers || []}
              selectedPrinterIds={selectedPrinters}
              onMultiSelect={setSelectedPrinters}
              isLoading={loadingPrinters}
              allowMultiple={true}
              showInactive={mode === 'edit-queue-item'}
              printerMappingResults={multiPrinterMapping.printerResults}
              filamentReqs={effectiveFilamentReqs}
              onAutoConfigurePrinter={multiPrinterMapping.autoConfigurePrinter}
              onUpdatePrinterConfig={multiPrinterMapping.updatePrinterConfig}
              assignmentMode={mode === 'reprint' ? 'printer' : assignmentMode}
              onAssignmentModeChange={mode !== 'reprint' ? setAssignmentMode : undefined}
              targetModel={targetModel}
              onTargetModelChange={mode !== 'reprint' ? setTargetModel : undefined}
              targetLocation={targetLocation}
              onTargetLocationChange={mode !== 'reprint' ? setTargetLocation : undefined}
              slicedForModel={slicedForModel}
            />

            {/* Compatibility warning when sliced model doesn't match selected printer */}
            {slicedForModel && assignmentMode === 'printer' && selectedPrinters.length === 1 && (() => {
              const selectedPrinter = printers?.find(p => p.id === selectedPrinters[0]);
              if (selectedPrinter && selectedPrinter.model && slicedForModel !== selectedPrinter.model) {
                return (
                  <div className="p-3 mb-2 bg-yellow-500/10 border border-yellow-500/30 rounded-lg flex items-center gap-2">
                    <AlertTriangle className="w-4 h-4 text-yellow-400 flex-shrink-0" />
                    <span className="text-sm text-yellow-400">
                      File was sliced for {slicedForModel}, but printing on {selectedPrinter.model}
                    </span>
                  </div>
                );
              }
              return null;
            })()}

            {/* Warning when archive data couldn't be loaded */}
            {archiveDataMissing && (
              <div className="flex items-start gap-2 p-3 mb-2 bg-orange-500/10 border border-orange-500/30 rounded-lg text-sm">
                <AlertCircle className="w-4 h-4 text-orange-400 mt-0.5 flex-shrink-0" />
                <p className="text-orange-400">
                  Archive data unavailable. The source file may have been deleted. Filament mapping is disabled.
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
                defaultExpanded={settings?.per_printer_mapping_expanded ?? false}
              />
            )}

            {/* Print options */}
            {(mode === 'reprint' || effectivePrinterCount > 0 || (assignmentMode === 'model' && targetModel)) && (
              <PrintOptionsPanel options={printOptions} onChange={setPrintOptions} />
            )}

            {/* Schedule options - only for queue modes */}
            {mode !== 'reprint' && (
              <ScheduleOptionsPanel
                options={scheduleOptions}
                onChange={setScheduleOptions}
                dateFormat={settings?.date_format || 'system'}
                timeFormat={settings?.time_format || 'system'}
              />
            )}

            {/* Error message */}
            {updateQueueMutation.isError && (
              <div className="mb-4 p-3 bg-red-500/20 border border-red-500/50 rounded-lg text-sm text-red-400">
                {(updateQueueMutation.error as Error)?.message || 'Failed to complete operation'}
              </div>
            )}

            {/* Actions */}
            <div className={`flex gap-3 ${mode === 'reprint' ? '' : 'pt-2'}`}>
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
    </div>
  );
}

// Re-export types for convenience
export type { PrintModalProps, PrintModalMode } from './types';
