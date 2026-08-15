import type { PrintQueueItem, Printer, CalibrationMode } from '../../api/client';
import type { VariantCandidate } from './VariantCandidates';

export type { CalibrationMode };
export type { VariantCandidate };

/**
 * Mode of operation for the PrintModal.
 * - 'create': Create a print queue item from an archive or library file
 * - 'edit-queue-item': Edit existing queue item (all options + existing values)
 */
export type PrintModalMode = 'create' | 'edit-queue-item';

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
  /** Pre-select specific printers when opening the modal */
  initialSelectedPrinterIds?: number[];
  /** Handler for closing the modal */
  onClose: () => void;
  /** Handler for successful operation */
  onSuccess?: () => void;
  /** Project ID to associate the resulting archive with (only when triggered from project view) */
  projectId?: number;
  /** Delete the LibraryFile after dispatch — used by the Printers-page Direct-Print flow
   *  so transient uploads don't linger in File Manager. Only applies to library-file prints. */
  cleanupLibraryAfterDispatch?: boolean;
  /**
   * Cross-model alternatives (#671): the same job sliced for several printers,
   * to be queued as ONE item that runs on whichever frees up first.
   *
   * Supplied by the File Manager when the user multi-selects sliced files, or
   * when the clicked file belongs to a variant group. Two or more entries put
   * the modal in cross-model mode: the printer picker is replaced by the ordered
   * candidate list, and submit posts `variants` instead of a single file.
   * `libraryFileId` must still be the first candidate — the shared filament and
   * plate preview reads from it.
   */
  variantFiles?: VariantCandidate[];
}

/**
 * Print options that can be configured for a print job.
 */
export type PreheatOverride = 'inherit' | 'on' | 'off';

export interface PrintOptions {
  bed_levelling: CalibrationMode;
  flow_cali: CalibrationMode;
  vibration_cali: boolean;
  layer_inspect: boolean;
  timelapse: boolean;
  nozzle_offset_cali: CalibrationMode;
  // Per-item preheat / heat-soak override (#1468). 'inherit' uses the global
  // Settings → Workflow toggle; 'on' / 'off' force the per-print decision.
  // chamber_target_override is non-null to bypass the per-filament-type
  // derivation with an explicit °C target.
  preheat_override: PreheatOverride;
  preheat_chamber_target_override: number | null;
}

/**
 * Default print options values.
 */
export const DEFAULT_PRINT_OPTIONS: PrintOptions = {
  bed_levelling: 'auto',
  flow_cali: 'auto',
  vibration_cali: true,
  layer_inspect: false,
  timelapse: false,
  nozzle_offset_cali: 'auto',
  preheat_override: 'inherit',
  preheat_chamber_target_override: null,
};

/**
 * Schedule type for queue items.
 */
export type ScheduleType = 'asap' | 'queue' | 'scheduled';

/**
 * Schedule options for queue items.
 */
export interface ScheduleOptions {
  scheduleType: ScheduleType;
  scheduledTime: string;
  requireManualStart: boolean;
  requirePreviousSuccess: boolean;
  autoOffAfter: boolean;
  gcodeInjection: boolean;
  staggerEnabled: boolean;
  staggerGroupSize: number;
  staggerIntervalMinutes: number;
}

/**
 * Default schedule options values.
 */
export const DEFAULT_SCHEDULE_OPTIONS: ScheduleOptions = {
  scheduleType: 'asap',
  scheduledTime: '',
  requireManualStart: false,
  requirePreviousSuccess: false,
  autoOffAfter: false,
  gcodeInjection: false,
  staggerEnabled: false,
  staggerGroupSize: 2,
  staggerIntervalMinutes: 5,
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
  bed_type?: string | null;  // Build plate type for this plate, e.g. "Textured PEI Plate" (#1281)
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
  /** Disable selection of busy printers */
  disableBusy?: boolean;
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
  selectedPlates: Set<number>;
  onToggle: (plateIndex: number) => void;
  onSelectAll?: () => void;
  onDeselectAll?: () => void;
  /** Whether multi-select (checkboxes) is enabled */
  multiSelect?: boolean;
  /**
   * How many runs of each plate to queue, keyed by plate index (#342). When
   * provided, each selected plate gets its own quantity control and the
   * modal's single global Quantity field is hidden — one number per plate is
   * the whole point, and two controls for the same value would be ambiguous.
   */
  quantities?: Record<number, number>;
  onQuantityChange?: (plateIndex: number, quantity: number) => void;
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
    nozzle_id?: number;
    /** Bambu SKU code from the 3MF (e.g. `GFA01` = Bambu PLA Matte, `P4d64437`
     *  = user custom). Used to resolve the "original" filament label in
     *  FilamentOverride against the builtin + cloud user-preset maps. #1718. */
    tray_info_idx?: string;
    /** Which filament group this slot prints in, on a nozzle-rack machine
     *  (#1784). The group is the slicer's logical nozzle, so it — not the slot
     *  — is what a rack position is chosen for. Absent on every other model. */
    group_id?: number;
    /** What that group needs of a hotend. Only groups with `on_rack` get a
     *  position picker; the rest are on the fixed carriage and have no choice
     *  to make. */
    group?: RackGroupInfo;
  }>;
}

/** A filament group's hotend requirements, from the 3MF (#1784). */
export interface RackGroupInfo {
  on_rack: boolean;
  nozzle_diameter: string;
  volume_type: string;
  filament_color: string;
}

/** One position on the H2C's six-slot nozzle rack, as offered to the user. */
export interface RackPositionOption {
  /** 1-based, the way the printer card, BambuStudio and the operator count. */
  position: number;
  diameter: string;
  nozzleType: string;
  filamentColor: string;
  /** False when the position is empty or holds the wrong nozzle for the group. */
  eligible: boolean;
  /** Why not, when `eligible` is false — shown as the option's title. */
  reason?: string;
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
  onEstimatedCostChange?: (estimatedCost: number | null) => void;
  budgetAvailable?: number | null;
  quantity?: number;
  currencySymbol: string;
  defaultCostPerKg: number;
  /** Per-slot force-color-match flags. The scheduler honors this flag in both
   *  model-mode and printer-mode dispatch, but the checkbox was previously only
   *  surfaced in FilamentOverride (model mode). #1717. */
  forceColorMatch?: Record<number, boolean>;
  /** Called when a slot's force-color-match checkbox is toggled. */
  onForceColorMatchChange?: (slotId: number, value: boolean) => void;
  /** Names the plate this panel maps, when one panel is rendered per selected
   *  plate. Each plate prints its own subset of the file's slots and gets its
   *  own AMS mapping, so the panels have to be told apart. */
  plateLabel?: string;
  /** The archive's own saved AMS-slot pick from the slicer
   *  (`extra_data.slicer_ams_mapping`, written when the source virtual
   *  printer has "Save AMS mapping" enabled) — position = slot_id-1, value =
   *  global tray ID. When present, a "Mapping" toggle next to "Re-read" lets
   *  the user select every slot from this array instead of the type/color
   *  auto-match. Undefined/omitted when the archive has no saved mapping —
   *  the toggle is hidden and behaviour is unchanged. */
  archiveAmsMapping?: number[];
  /** The operator's rack-position pick per filament group (#1784), keyed by
   *  group id. Only meaningful on a nozzle-rack model; omit elsewhere and no
   *  picker is rendered. A group absent from the object is assigned a position
   *  by the dispatcher against the rack as it stands at dispatch. */
  nozzleRackChoice?: Record<number, number>;
  /** Called when a rack position is picked for a group. */
  onNozzleRackChoiceChange?: (choice: Record<number, number>) => void;
}

/**
 * Props for the PrintOptions component.
 */
export interface PrintOptionsProps {
  options: PrintOptions;
  onChange: (options: PrintOptions) => void;
  defaultExpanded?: boolean;
  /** Show the dual-nozzle-only options (nozzle offset calibration). Default false.
   *  Pass true when at least one selected printer is dual-nozzle. */
  showDualNozzleOptions?: boolean;
}

/**
 * Props for the ScheduleOptions component.
 */
export interface ScheduleOptionsProps {
  options: ScheduleOptions;
  onChange: (options: ScheduleOptions) => void;
  /** Date format setting from user preferences */
  dateFormat?: 'system' | 'us' | 'eu' | 'iso';
  /** Time format setting from user preferences */
  timeFormat?: 'system' | '12h' | '24h';
  /** Whether the user has permission to control printers (for auto power off) */
  canControlPrinter?: boolean;
  /** Show stagger options (only when multiple printers selected in queue mode) */
  showStagger?: boolean;
  /** Number of selected printers (for stagger preview) */
  printerCount?: number;
  /** Whether G-code snippets are configured in settings */
  hasGcodeSnippets?: boolean;
}
