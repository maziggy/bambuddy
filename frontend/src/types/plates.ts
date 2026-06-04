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
}

// Printer / process preset names the source 3MF was prepared with, read from
// its project_settings.config. Used by the SliceModal to default its printer
// and process dropdowns (#1325). Null / absent when the file carries no
// embedded slicer config (STL, plain model 3MF, parse failure).
interface EmbeddedPresets {
  embedded_printer?: string | null;
  embedded_process?: string | null;
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
