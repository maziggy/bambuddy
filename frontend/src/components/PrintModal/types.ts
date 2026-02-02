import type { PrintQueueItem, Printer } from '../../api/client';

/**
 * Mode of operation for the PrintModal.
 * - 'reprint': Immediate print from archive (no schedule options)
 * - 'add-to-queue': Schedule print to queue (includes schedule options)
 * - 'edit-queue-item': Edit existing queue item (all options + existing values)
 */
export type PrintModalMode = 'reprint' | 'add-to-queue' | 'edit-queue-item';

/**
 * Props for the unified PrintModal component.
 *
 * Either archiveId or libraryFileId must be provided.
 * - archiveId: For reprinting/queueing archives
 * - libraryFileId: For printing library files directly
 */
export interface PrintModalProps {
  /** Modal operation mode */
  mode: PrintModalMode;
  /** Archive ID to print (mutually exclusive with libraryFileId) */
  archiveId?: number;
  /** Library file ID to print (mutually exclusive with archiveId) */
  libraryFileId?: number;
  /** Display name for the print */
  archiveName: string;
  /** Existing queue item (only for edit-queue-item mode) */
  queueItem?: PrintQueueItem;
  /** Handler for closing the modal */
  onClose: () => void;
  /** Handler for successful operation */
  onSuccess?: () => void;
}

/**
 * Print options that can be configured for a print job.
 */
export interface PrintOptions {
  bed_levelling: boolean;
  flow_cali: boolean;
  vibration_cali: boolean;
  layer_inspect: boolean;
  timelapse: boolean;
}

/**
 * Default print options values.
 */
export const DEFAULT_PRINT_OPTIONS: PrintOptions = {
  bed_levelling: true,
  flow_cali: false,
  vibration_cali: true,
  layer_inspect: false,
  timelapse: false,
};

/**
 * Schedule type for queue items.
 */
export type ScheduleType = 'asap' | 'scheduled' | 'manual';

/**
 * Schedule options for queue items.
 */
export interface ScheduleOptions {
  scheduleType: ScheduleType;
  scheduledTime: string;
  requirePreviousSuccess: boolean;
  autoOffAfter: boolean;
}

/**
 * Default schedule options values.
 */
export const DEFAULT_SCHEDULE_OPTIONS: ScheduleOptions = {
  scheduleType: 'asap',
  scheduledTime: '',
  requirePreviousSuccess: false,
  autoOffAfter: false,
};

/**
 * Plate information from a multi-plate 3MF file.
 */
export interface PlateInfo {
  index: number;
  name: string | null;
  has_thumbnail: boolean;
  thumbnail_url: string | null;
  objects: string[];
  filaments: Array<{
    type: string;
    color: string;
  }>;
  print_time_seconds: number | null;
  filament_used_grams: number | null;
}

/**
 * Response from the archive plates API.
 */
export interface PlatesResponse {
  is_multi_plate: boolean;
  plates: PlateInfo[];
}

/**
 * Assignment mode for queue items.
 * - 'printer': Assign to specific printer(s)
 * - 'model': Assign to any printer of a specific model (load balancing)
 */
export type AssignmentMode = 'printer' | 'model';

/**
 * Props for the PrinterSelector component.
 */
export interface PrinterSelectorProps {
  printers: Printer[];
  selectedPrinterIds: number[];
  onMultiSelect: (printerIds: number[]) => void;
  isLoading?: boolean;
  allowMultiple?: boolean;
  /** Show inactive printers (for edit mode where original assignment may be inactive) */
  showInactive?: boolean;
  /** Current assignment mode */
  assignmentMode?: AssignmentMode;
  /** Handler for assignment mode change */
  onAssignmentModeChange?: (mode: AssignmentMode) => void;
  /** Selected target model (when assignmentMode is 'model') */
  targetModel?: string | null;
  /** Handler for target model change */
  onTargetModelChange?: (model: string | null) => void;
  /** Selected target location (when assignmentMode is 'model') */
  targetLocation?: string | null;
  /** Handler for target location change */
  onTargetLocationChange?: (location: string | null) => void;
  /** Suggested model from sliced file (for pre-selection) */
  slicedForModel?: string | null;
}

/**
 * Props for the PlateSelector component.
 */
export interface PlateSelectorProps {
  plates: PlateInfo[];
  isMultiPlate: boolean;
  selectedPlate: number | null;
  onSelect: (plateIndex: number) => void;
}

/**
 * Filament requirement data structure.
 */
export interface FilamentReqsData {
  filaments: Array<{
    slot_id: number;
    type: string;
    color: string;
    used_grams: number;
    used_meters: number;
  }>;
}

/**
 * Props for the FilamentMapping component.
 */
export interface FilamentMappingProps {
  printerId: number;
  /** Pre-fetched filament requirements data */
  filamentReqs: FilamentReqsData | undefined;
  manualMappings: Record<number, number>;
  onManualMappingChange: (mappings: Record<number, number>) => void;
}

/**
 * Props for the PrintOptions component.
 */
export interface PrintOptionsProps {
  options: PrintOptions;
  onChange: (options: PrintOptions) => void;
  defaultExpanded?: boolean;
}

/**
 * Props for the ScheduleOptions component.
 */
export interface ScheduleOptionsProps {
  options: ScheduleOptions;
  onChange: (options: ScheduleOptions) => void;
}
