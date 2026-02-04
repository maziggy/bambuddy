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
}

export interface LibraryFilePlatesResponse {
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
