export interface PlateFilament {
  slot_id: number;
  type: string;
  color: string;
  used_grams: number;
  used_meters: number;
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

export interface ArchivePlatesResponse {
  archive_id: number;
  filename: string;
  plates: PlateMetadata[];
  is_multi_plate: boolean;
  has_gcode?: boolean;
  // Bound printer model from the source 3MF's project_settings.config (e.g.
  // "Bambu Lab A1"). Used by the SliceModal to warn before slicing if the
  // user picks a profile for a different printer — the slicer CLI can't
  // convert a 3MF across printer models.
  source_printer_model?: string | null;
}

export interface LibraryFilePlatesResponse {
  file_id: number;
  filename: string;
  plates: PlateMetadata[];
  is_multi_plate: boolean;
  source_printer_model?: string | null;
}

export interface ViewerPlateSelectionState {
  selected_plate_id: number | null;
}

export interface PlateAssignment {
  object_id: string;
  plate_id: number | null;
}
