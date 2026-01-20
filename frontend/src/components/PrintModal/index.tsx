import { useState, useEffect, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { X, Printer, Loader2, Calendar, Pencil, AlertCircle } from 'lucide-react';
import { api } from '../../api/client';
import type { PrintQueueItemCreate, PrintQueueItemUpdate } from '../../api/client';
import { Card, CardContent } from '../Card';
import { Button } from '../Button';
import { useToast } from '../../contexts/ToastContext';
import { useFilamentMapping } from '../../hooks/useFilamentMapping';
import { isPlaceholderDate } from '../../utils/amsHelpers';
import { PrinterSelector } from './PrinterSelector';
import { PlateSelector } from './PlateSelector';
import { FilamentMapping } from './FilamentMapping';
import { PrintOptionsPanel } from './PrintOptions';
import { ScheduleOptionsPanel } from './ScheduleOptions';
import type {
  PrintModalProps,
  PrintOptions,
  ScheduleOptions,
  ScheduleType,
} from './types';
import { DEFAULT_PRINT_OPTIONS, DEFAULT_SCHEDULE_OPTIONS } from './types';

/**
 * Unified PrintModal component that handles three modes:
 * - 'reprint': Immediate print from archive (supports multi-printer)
 * - 'add-to-queue': Schedule print to queue (supports multi-printer)
 * - 'edit-queue-item': Edit existing queue item (single printer only)
 */
export function PrintModal({
  mode,
  archiveId,
  archiveName,
  queueItem,
  onClose,
  onSuccess,
}: PrintModalProps) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  // Single printer selection (for edit mode and backward compatibility)
  const [selectedPrinter, setSelectedPrinter] = useState<number | null>(() => {
    if (mode === 'edit-queue-item' && queueItem) {
      return queueItem.printer_id;
    }
    return null;
  });

  // Multiple printer selection (for reprint and add-to-queue modes)
  const [selectedPrinters, setSelectedPrinters] = useState<number[]>([]);

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
        scheduledTime = date.toISOString().slice(0, 16);
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

  // Manual slot overrides: slot_id (1-indexed) -> globalTrayId
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

  // Track initial values for clearing mappings on change (edit mode only)
  const [initialPrinterId] = useState(() => (mode === 'edit-queue-item' && queueItem ? queueItem.printer_id : null));
  const [initialPlateId] = useState(() => (mode === 'edit-queue-item' && queueItem ? queueItem.plate_id : null));

  // Submission state for multi-printer
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitProgress, setSubmitProgress] = useState({ current: 0, total: 0 });

  // Determine if we're in multi-printer mode
  const isMultiPrinterMode = mode !== 'edit-queue-item';
  const effectivePrinterCount = isMultiPrinterMode ? selectedPrinters.length : (selectedPrinter ? 1 : 0);
  // For filament mapping, use first selected printer (mapping applies to all)
  const effectivePrinterId = isMultiPrinterMode
    ? (selectedPrinters.length > 0 ? selectedPrinters[0] : null)
    : selectedPrinter;

  // Queries
  const { data: printers, isLoading: loadingPrinters } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  const { data: platesData } = useQuery({
    queryKey: ['archive-plates', archiveId],
    queryFn: () => api.getArchivePlates(archiveId),
  });

  const { data: filamentReqs } = useQuery({
    queryKey: ['archive-filaments', archiveId, selectedPlate],
    queryFn: () => api.getArchiveFilamentRequirements(archiveId, selectedPlate ?? undefined),
    enabled: selectedPlate !== null || !platesData?.is_multi_plate,
  });

  // Only fetch printer status when single printer selected (for filament mapping)
  const { data: printerStatus } = useQuery({
    queryKey: ['printer-status', effectivePrinterId],
    queryFn: () => api.getPrinterStatus(effectivePrinterId!),
    enabled: !!effectivePrinterId,
  });

  // Get AMS mapping from hook (only when single printer selected)
  const { amsMapping } = useFilamentMapping(filamentReqs, printerStatus, manualMappings);

  // Auto-select first plate for single-plate files
  useEffect(() => {
    if (platesData?.plates?.length === 1 && !selectedPlate) {
      setSelectedPlate(platesData.plates[0].index);
    }
  }, [platesData, selectedPlate]);

  // Auto-select first printer when only one available (non-multi mode)
  useEffect(() => {
    if (mode === 'edit-queue-item') return;
    const activePrinters = printers?.filter(p => p.is_active) || [];
    if (activePrinters.length === 1 && selectedPrinters.length === 0) {
      setSelectedPrinters([activePrinters[0].id]);
    }
  }, [mode, printers, selectedPrinters.length]);

  // Clear manual mappings when printer or plate changes
  useEffect(() => {
    if (mode === 'edit-queue-item') {
      if (selectedPrinter !== initialPrinterId || selectedPlate !== initialPlateId) {
        setManualMappings({});
      }
    } else {
      setManualMappings({});
    }
  }, [mode, selectedPrinter, selectedPrinters, selectedPlate, initialPrinterId, initialPlateId]);

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

    if (mode === 'edit-queue-item') {
      // Edit mode - single printer update
      const data: PrintQueueItemUpdate = {
        printer_id: selectedPrinter,
        require_previous_success: scheduleOptions.requirePreviousSuccess,
        auto_off_after: scheduleOptions.autoOffAfter,
        manual_start: scheduleOptions.scheduleType === 'manual',
        ams_mapping: amsMapping,
        plate_id: selectedPlate,
        ...printOptions,
      };

      if (scheduleOptions.scheduleType === 'scheduled' && scheduleOptions.scheduledTime) {
        data.scheduled_time = new Date(scheduleOptions.scheduledTime).toISOString();
      } else {
        data.scheduled_time = null;
      }

      updateQueueMutation.mutate(data);
      return;
    }

    // Multi-printer modes (reprint or add-to-queue)
    if (selectedPrinters.length === 0) {
      showToast('Please select at least one printer', 'error');
      return;
    }

    setIsSubmitting(true);
    setSubmitProgress({ current: 0, total: selectedPrinters.length });

    const results: { success: number; failed: number; errors: string[] } = {
      success: 0,
      failed: 0,
      errors: [],
    };

    for (let i = 0; i < selectedPrinters.length; i++) {
      const printerId = selectedPrinters[i];
      setSubmitProgress({ current: i + 1, total: selectedPrinters.length });

      try {
        // Use the same AMS mapping for all printers (configured via UI based on first printer)
        // This assumes all printers have the same filament configuration
        if (mode === 'reprint') {
          await api.reprintArchive(archiveId, printerId, {
            plate_id: selectedPlate ?? undefined,
            ams_mapping: amsMapping,
            ...printOptions,
          });
        } else {
          // add-to-queue mode
          const data: PrintQueueItemCreate = {
            printer_id: printerId,
            archive_id: archiveId,
            require_previous_success: scheduleOptions.requirePreviousSuccess,
            auto_off_after: scheduleOptions.autoOffAfter,
            manual_start: scheduleOptions.scheduleType === 'manual',
            ams_mapping: amsMapping,
            plate_id: selectedPlate,
            ...printOptions,
          };

          if (scheduleOptions.scheduleType === 'scheduled' && scheduleOptions.scheduledTime) {
            data.scheduled_time = new Date(scheduleOptions.scheduledTime).toISOString();
          }

          await addToQueueMutation.mutateAsync(data);
        }
        results.success++;
      } catch (error) {
        results.failed++;
        const printerName = printers?.find(p => p.id === printerId)?.name || `Printer ${printerId}`;
        results.errors.push(`${printerName}: ${(error as Error).message}`);
      }
    }

    setIsSubmitting(false);

    // Show result toast
    if (results.failed === 0) {
      const action = mode === 'reprint' ? 'sent to' : 'queued for';
      if (results.success === 1) {
        showToast(`Print ${action} printer`);
      } else {
        showToast(`Print ${action} ${results.success} printers`);
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

    // For edit mode, printer can be null (unassigned)
    if (mode === 'edit-queue-item') {
      return (printers?.length ?? 0) > 0;
    }

    // For reprint and add-to-queue, need at least one selected printer
    if (selectedPrinters.length === 0) return false;

    // For multi-plate files, need a selected plate
    if (isMultiPlate && !selectedPlate) return false;

    return true;
  }, [mode, selectedPrinters.length, isMultiPlate, selectedPlate, isPending, printers]);

  // Modal title and action button text based on mode
  const getModalConfig = () => {
    const printerCount = isMultiPrinterMode ? selectedPrinters.length : 1;

    if (mode === 'reprint') {
      return {
        title: 'Re-print',
        icon: Printer,
        submitText: printerCount > 1 ? `Print to ${printerCount} Printers` : 'Print',
        submitIcon: Printer,
        loadingText: submitProgress.total > 1
          ? `Sending ${submitProgress.current}/${submitProgress.total}...`
          : 'Sending...',
      };
    }
    if (mode === 'add-to-queue') {
      return {
        title: 'Schedule Print',
        icon: Calendar,
        submitText: printerCount > 1 ? `Queue to ${printerCount} Printers` : 'Add to Queue',
        submitIcon: Calendar,
        loadingText: submitProgress.total > 1
          ? `Adding ${submitProgress.current}/${submitProgress.total}...`
          : 'Adding...',
      };
    }
    return {
      title: 'Edit Queue Item',
      icon: Pencil,
      submitText: 'Save Changes',
      submitIcon: Pencil,
      loadingText: 'Saving...',
    };
  };

  const modalConfig = getModalConfig();
  const TitleIcon = modalConfig.icon;
  const SubmitIcon = modalConfig.submitIcon;

  // Show filament mapping only when single printer selected
  const showFilamentMapping = effectivePrinterId && (isMultiPlate ? selectedPlate !== null : true);

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
                  Send <span className="text-white">{archiveName}</span> to{' '}
                  {isMultiPrinterMode ? 'printer(s)' : 'a printer'}
                </>
              ) : (
                <>
                  <span className="block text-bambu-gray mb-1">Print Job</span>
                  <span className="text-white font-medium truncate block">{archiveName}</span>
                </>
              )}
            </p>

            {/* Printer selection */}
            <PrinterSelector
              printers={printers || []}
              selectedPrinterId={selectedPrinter}
              selectedPrinterIds={selectedPrinters}
              onSelect={setSelectedPrinter}
              onMultiSelect={setSelectedPrinters}
              isLoading={loadingPrinters}
              allowUnassigned={mode === 'edit-queue-item'}
              allowMultiple={isMultiPrinterMode}
            />

            {/* Multi-printer filament mapping note */}
            {isMultiPrinterMode && selectedPrinters.length > 1 && (
              <div className="flex items-start gap-2 p-3 mb-2 bg-blue-500/10 border border-blue-500/30 rounded-lg text-sm">
                <AlertCircle className="w-4 h-4 text-blue-400 mt-0.5 flex-shrink-0" />
                <p className="text-blue-400">
                  Slot mapping below applies to all {selectedPrinters.length} printers. Ensure they have matching filament configurations.
                </p>
              </div>
            )}

            {/* Plate selection */}
            <PlateSelector
              plates={plates}
              isMultiPlate={isMultiPlate}
              selectedPlate={selectedPlate}
              onSelect={setSelectedPlate}
            />

            {/* Filament mapping - show when single printer selected and plate ready */}
            {showFilamentMapping && (
              <FilamentMapping
                printerId={effectivePrinterId!}
                archiveId={archiveId}
                selectedPlate={selectedPlate}
                isMultiPlate={isMultiPlate}
                manualMappings={manualMappings}
                onManualMappingChange={setManualMappings}
              />
            )}

            {/* Print options */}
            {(mode === 'reprint' || effectivePrinterCount > 0) && (
              <PrintOptionsPanel options={printOptions} onChange={setPrintOptions} />
            )}

            {/* Schedule options - only for queue modes */}
            {mode !== 'reprint' && (
              <ScheduleOptionsPanel options={scheduleOptions} onChange={setScheduleOptions} />
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
