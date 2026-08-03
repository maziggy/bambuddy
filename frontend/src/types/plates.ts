export interface PlateFilament {
  slot_id: number;
  type: string;
  color: string;
  used_grams: number;
  used_meters: number;
  // True when this AMS slot is consumed by the picked plate. False
  // means the slot is configured project-wide but the picked plate
  // doesn't paint with it. Sliced 3MFs (.gcode.3mf) report only used
  // filaments — the field is true for every entry. Unsliced project
  // files report ALL project slots; SliceModal disables the unused
  // rows so the user only interacts with the dropdowns that matter,
  // while the backend still passes the complete list to the slicer
  // CLI to prevent silent fallback to embedded defaults.
  used_in_plate?: boolean;
}

export interface PlateMetadata {
  index: number;
  name: string | null;
  objects: string[];
  object_count?: number;
  has_thumbnail: boolean;
  thumbnail_url: string | null;
  print_time_seconds: number | null;
  filament_used_grams: number | null;
  filaments: PlateFilament[];
  // Per-plate build plate type so multi-plate prints can show the right
  // plate at scheduling time (#1281). Falls back to null for older 3MFs
  // that don't carry curr_bed_type in slice_info.config.
  bed_type?: string | null;
}

// Printer / process preset names the source 3MF was prepared with, read from
// its project_settings.config. Used by the SliceModal to default its printer
// and process dropdowns (#1325). Null / absent when the file carries no
// embedded slicer config (STL, plain model 3MF, parse failure).
interface EmbeddedPresets {
  embedded_printer?: string | null;
  embedded_process?: string | null;
  // Process settings the designer changed away from the stock preset, read
  // from the 3MF's own `different_settings_to_system` (#2622). Offered in the
  // SliceModal so a re-slice for another printer can carry them instead of
  // losing them to the picked process profile. Empty for STL, OrcaSlicer
  // files, and older exports that predate the field.
  design_overrides?: DesignOverride[];
}

// One process setting the designer deviated on. `printer_coupled` marks the
// values that only make sense on the machine they were tuned for (speeds,
// accelerations, prime-tower geometry) — offered, but never pre-selected.
export interface DesignOverride {
  key: string;
  value: unknown;
  printer_coupled: boolean;
}

export interface ArchivePlatesResponse extends EmbeddedPresets {
  archive_id: number;
  filename: string;
  plates: PlateMetadata[];
  is_multi_plate: boolean;
  has_gcode?: boolean;
}

export interface LibraryFilePlatesResponse extends EmbeddedPresets {
  file_id: number;
  filename: string;
  plates: PlateMetadata[];
  is_multi_plate: boolean;
}

export interface ViewerPlateSelectionState {
  selected_plate_id: number | null;
}

export interface PlateAssignment {
  object_id: string;
  plate_id: number | null;
}
