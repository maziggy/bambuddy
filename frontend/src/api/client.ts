import type { ArchivePlatesResponse, LibraryFilePlatesResponse } from '../types/plates';

const API_BASE = '/api/v1';

// Auth token storage
let authToken: string | null = localStorage.getItem('auth_token');

export function setAuthToken(token: string | null) {
  authToken = token;
  if (token) {
    localStorage.setItem('auth_token', token);
  } else {
    localStorage.removeItem('auth_token');
  }
}

export function getAuthToken(): string | null {
  return authToken;
}

function parseContentDispositionFilename(header: string | null): string | null {
  if (!header) return null;
  // RFC 5987: filename*=utf-8''percent-encoded-name
  const rfc5987Match = header.match(/filename\*=(?:UTF-8|utf-8)''(.+?)(?:;|$)/);
  if (rfc5987Match) {
    try { return decodeURIComponent(rfc5987Match[1]); } catch { /* fall through */ }
  }
  // Standard: filename="name" or filename=name
  const standardMatch = header.match(/filename="?([^";\n]+)"?/);
  return standardMatch?.[1] || null;
}

async function request<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...options.headers as Record<string, string>,
  };

  // Add auth token if available
  if (authToken) {
    headers['Authorization'] = `Bearer ${authToken}`;
  }

  const response = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    cache: 'no-store', // Prevent browser caching of API responses
    headers,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    const detail = error.detail;
    const message = typeof detail === 'string'
      ? detail
      : (detail ? JSON.stringify(detail) : `HTTP ${response.status}`);

    // Handle 401 Unauthorized - only clear token if it's actually invalid
    // Don't clear on "Authentication required" which might be a timing issue
    if (response.status === 401) {
      const invalidTokenMessages = [
        'Could not validate credentials',
        'Token has expired',
        'User not found or inactive',
        'Invalid API key',
        'API key has expired',
      ];
      if (invalidTokenMessages.some(m => message.includes(m))) {
        setAuthToken(null);
      }
    }

    throw new Error(message);
  }

  // Handle empty responses (204 No Content, etc.)
  const contentLength = response.headers.get('content-length');
  if (response.status === 204 || contentLength === '0') {
    return undefined as T;
  }

  return await response.json();
}

// Printer types
export interface Printer {
  id: number;
  name: string;
  serial_number: string;
  ip_address: string;
  access_code: string;
  model: string | null;
  location: string | null;  // Group/location name
  nozzle_count: number;  // 1 or 2, auto-detected from MQTT
  is_active: boolean;
  auto_archive: boolean;
  external_camera_url: string | null;
  external_camera_type: string | null;  // "mjpeg", "rtsp", "snapshot"
  external_camera_enabled: boolean;
  plate_detection_enabled: boolean;  // Check plate before print
  plate_detection_roi?: PlateDetectionROI;  // ROI for plate detection
  created_at: string;
  updated_at: string;
}

export interface HMSError {
  code: string;
  attr: number;  // Attribute value for constructing wiki URL
  module: number;
  severity: number;  // 1=fatal, 2=serious, 3=common, 4=info
}

export interface AMSTray {
  id: number;
  tray_color: string | null;
  tray_type: string | null;
  tray_sub_brands: string | null;  // Full name like "PLA Basic", "PETG HF"
  tray_id_name: string | null;  // Bambu filament ID like "A00-Y2" (can decode to color)
  tray_info_idx: string | null;  // Filament preset ID like "GFA00" - maps to cloud setting_id
  remain: number;
  k: number | null;  // Pressure advance value (from tray or K-profile lookup)
  cali_idx: number | null;  // Calibration index for K-profile lookup
  tag_uid: string | null;  // RFID tag UID (any tag)
  tray_uuid: string | null;  // Bambu Lab spool UUID (32-char hex, only valid for Bambu Lab spools)
  nozzle_temp_min: number | null;  // Min nozzle temperature
  nozzle_temp_max: number | null;  // Max nozzle temperature
}

export interface AMSUnit {
  id: number;
  humidity: number | null;
  temp: number | null;
  is_ams_ht: boolean;  // True for AMS-HT (single spool), False for regular AMS (4 spools)
  tray: AMSTray[];
}

export interface NozzleInfo {
  nozzle_type: string;  // "stainless_steel" or "hardened_steel"
  nozzle_diameter: string;  // e.g., "0.4"
}

export interface NozzleRackSlot {
  id: number;
  nozzle_type: string;
  nozzle_diameter: string;
  wear: number | null;
  stat: number | null;  // Nozzle status (e.g. mounted/docked)
  max_temp: number;
  serial_number: string;
  filament_color: string;  // RGBA hex ("00000000" = no filament)
  filament_id: string;
  filament_type: string;  // Material type (e.g. "PLA", "PETG")
}

export interface PrintOptions {
  // Core AI detectors
  spaghetti_detector: boolean;
  print_halt: boolean;
  halt_print_sensitivity: string;  // "low", "medium", "high" - spaghetti sensitivity
  first_layer_inspector: boolean;
  printing_monitor: boolean;
  buildplate_marker_detector: boolean;
  allow_skip_parts: boolean;
  // Additional AI detectors (decoded from cfg bitmask)
  nozzle_clumping_detector: boolean;
  nozzle_clumping_sensitivity: string;  // "low", "medium", "high"
  pileup_detector: boolean;
  pileup_sensitivity: string;  // "low", "medium", "high"
  airprint_detector: boolean;
  airprint_sensitivity: string;  // "low", "medium", "high"
  auto_recovery_step_loss: boolean;
  filament_tangle_detect: boolean;
}

export interface PrinterStatus {
  id: number;
  name: string;
  connected: boolean;
  state: string | null;
  current_print: string | null;
  subtask_name: string | null;
  gcode_file: string | null;
  progress: number | null;
  remaining_time: number | null;
  layer_num: number | null;
  total_layers: number | null;
  temperatures: {
    bed?: number;
    bed_target?: number;
    bed_heating?: boolean;  // Actual heater state from MQTT
    nozzle?: number;
    nozzle_target?: number;
    nozzle_heating?: boolean;  // Actual heater state from MQTT
    nozzle_2?: number;  // Second nozzle for H2 series (dual nozzle)
    nozzle_2_target?: number;
    nozzle_2_heating?: boolean;  // Actual heater state from MQTT
    chamber?: number;
    chamber_target?: number;
    chamber_heating?: boolean;  // Actual heater state from MQTT
  } | null;
  cover_url: string | null;
  hms_errors: HMSError[];
  ams: AMSUnit[];
  ams_exists: boolean;
  vt_tray: AMSTray[];  // Virtual tray / external spool(s)
  sdcard: boolean;  // SD card inserted
  store_to_sdcard: boolean;  // Store sent files on SD card
  timelapse: boolean;  // Timelapse recording active
  ipcam: boolean;  // Live view enabled
  wifi_signal: number | null;  // WiFi signal strength in dBm
  nozzles: NozzleInfo[];  // Nozzle hardware info (index 0=left/primary, 1=right)
  nozzle_rack: NozzleRackSlot[];  // H2C 6-nozzle tool-changer rack
  print_options: PrintOptions | null;  // AI detection and print options
  // Calibration stage tracking
  stg_cur: number;  // Current stage number (-1 = not calibrating)
  stg_cur_name: string | null;  // Human-readable current stage name
  stg: number[];  // List of stage numbers in calibration sequence
  // Air conditioning mode (0=cooling, 1=heating)
  airduct_mode: number;
  // Print speed level (1=silent, 2=standard, 3=sport, 4=ludicrous)
  speed_level: number;
  // Chamber light on/off
  chamber_light: boolean;
  // Active extruder for dual nozzle (0=right, 1=left)
  active_extruder: number;
  // AMS mapping - which AMS is connected to which nozzle
  // Format: [ams_id_for_nozzle0, ams_id_for_nozzle1, ...] where -1 means no AMS
  ams_mapping: number[];
  // Per-AMS extruder mapping - extracted from each AMS unit's info field
  // Format: {ams_id: extruder_id} where extruder 0=right, 1=left
  // Note: JSON keys are always strings
  ams_extruder_map: Record<string, number>;
  // Currently loaded tray (global tray ID, 255 = no filament loaded, 254 = external spool)
  tray_now: number;
  // AMS status for filament change tracking (0=idle, 1=filament_change, 2=rfid_identifying, 3=assist, 4=calibration)
  ams_status_main: number;
  // AMS sub-status for filament change step (when main=1): 4=retraction, 6=load verification, 7=purge
  ams_status_sub: number;
  // mc_print_sub_stage - filament change step indicator used by OrcaSlicer/BambuStudio
  mc_print_sub_stage: number;
  // Timestamp of last AMS data update (for RFID refresh detection)
  last_ams_update: number;
  // Number of printable objects in current print (for skip objects feature)
  printable_objects_count: number;
  // Fan speeds (0-100 percentage, null if not available for this model)
  cooling_fan_speed: number | null;  // Part cooling fan
  big_fan1_speed: number | null;     // Auxiliary fan
  big_fan2_speed: number | null;     // Chamber/exhaust fan
  heatbreak_fan_speed: number | null; // Hotend heatbreak fan
}

export interface PrinterCreate {
  name: string;
  serial_number: string;
  ip_address: string;
  access_code: string;
  model?: string;
  location?: string;
  auto_archive?: boolean;
  external_camera_url?: string | null;
  external_camera_type?: string | null;
  external_camera_enabled?: boolean;
  plate_detection_enabled?: boolean;
  plate_detection_roi?: PlateDetectionROI;
}

// Plate Detection
export interface PlateDetectionROI {
  x: number;  // X start % (0.0-1.0)
  y: number;  // Y start % (0.0-1.0)
  w: number;  // Width % (0.0-1.0)
  h: number;  // Height % (0.0-1.0)
}

export interface PlateDetectionResult {
  is_empty: boolean;
  confidence: number;
  difference_percent: number;
  message: string;
  has_debug_image: boolean;
  debug_image_url?: string;
  needs_calibration: boolean;
  light_warning?: boolean;
  reference_count?: number;
  max_references?: number;
  roi?: PlateDetectionROI;
}

export interface PlateDetectionStatus {
  available: boolean;
  calibrated: boolean;
  reference_count: number;
  max_references: number;
  message: string;
}

export interface CalibrationResult {
  success: boolean;
  message: string;
}

export interface PlateReference {
  index: number;
  label: string;
  timestamp: string;
  has_image: boolean;
  thumbnail_url: string;
}

// Archive types
export interface ArchiveDuplicate {
  id: number;
  print_name: string | null;
  created_at: string;
  match_type: 'exact' | 'similar';  // 'exact' = hash match, 'similar' = name match
}

export interface Archive {
  id: number;
  printer_id: number | null;
  project_id: number | null;
  project_name: string | null;
  filename: string;
  file_path: string;
  file_size: number;
  content_hash: string | null;
  thumbnail_path: string | null;
  timelapse_path: string | null;
  source_3mf_path: string | null;
  f3d_path: string | null;
  duplicates: ArchiveDuplicate[] | null;
  duplicate_count: number;
  object_count: number | null;
  print_name: string | null;
  print_time_seconds: number | null;
  actual_time_seconds: number | null;  // Computed from started_at/completed_at
  time_accuracy: number | null;  // Percentage: 100 = perfect, >100 = faster than estimated
  filament_used_grams: number | null;
  filament_type: string | null;
  filament_color: string | null;
  layer_height: number | null;
  total_layers: number | null;
  nozzle_diameter: number | null;
  bed_temperature: number | null;
  nozzle_temperature: number | null;
  sliced_for_model: string | null;  // Printer model this file was sliced for
  status: string;
  started_at: string | null;
  completed_at: string | null;
  extra_data: Record<string, unknown> | null;
  makerworld_url: string | null;
  designer: string | null;
  external_url: string | null;
  is_favorite: boolean;
  tags: string | null;
  notes: string | null;
  cost: number | null;
  photos: string[] | null;
  failure_reason: string | null;
  quantity: number;
  energy_kwh: number | null;
  energy_cost: number | null;
  created_at: string;
  // User tracking (Issue #206)
  created_by_id: number | null;
  created_by_username: string | null;
}

export interface ArchiveStats {
  total_prints: number;
  successful_prints: number;
  failed_prints: number;
  total_print_time_hours: number;
  total_filament_grams: number;
  total_cost: number;
  prints_by_filament_type: Record<string, number>;
  prints_by_printer: Record<string, number>;
  average_time_accuracy: number | null;
  time_accuracy_by_printer: Record<string, number> | null;
  total_energy_kwh: number;
  total_energy_cost: number;
}

export interface TagInfo {
  name: string;
  count: number;
}

export interface FailureAnalysis {
  period_days: number;
  total_prints: number;
  failed_prints: number;
  failure_rate: number;
  failures_by_reason: Record<string, number>;
  failures_by_filament: Record<string, number>;
  failures_by_printer: Record<string, number>;
  failures_by_hour: Record<number, number>;
  recent_failures: Array<{
    id: number;
    print_name: string;
    failure_reason: string | null;
    filament_type: string | null;
    printer_id: number | null;
    created_at: string | null;
  }>;
  trend: Array<{
    week_start: string;
    total_prints: number;
    failed_prints: number;
    failure_rate: number;
  }>;
}

export interface BulkUploadResult {
  uploaded: number;
  failed: number;
  results: Array<{ filename: string; id: number; status: string }>;
  errors: Array<{ filename: string; error: string }>;
}

// Archive Comparison types
export interface ComparisonArchiveInfo {
  id: number;
  print_name: string;
  status: string;
  created_at: string | null;
  printer_id: number | null;
  project_name: string | null;
}

export interface ComparisonField {
  field: string;
  label: string;
  unit: string | null;
  values: (string | number | null)[];
  raw_values: (string | number | null)[];
  has_difference: boolean;
}

export interface SuccessCorrelationInsight {
  field: string;
  label: string;
  insight: string;
  success_avg?: number;
  failed_avg?: number;
  success_values?: string[];
  failed_values?: string[];
}

export interface SuccessCorrelation {
  has_both_outcomes: boolean;
  message?: string;
  successful_count?: number;
  failed_count?: number;
  insights?: SuccessCorrelationInsight[];
}

export interface ArchiveComparison {
  archives: ComparisonArchiveInfo[];
  comparison: ComparisonField[];
  differences: ComparisonField[];
  success_correlation: SuccessCorrelation;
}

export interface SimilarArchive {
  archive: {
    id: number;
    print_name: string;
    status: string;
    created_at: string | null;
  };
  match_reason: string;
  match_score: number;
}

// Project types
export interface ProjectStats {
  total_archives: number;
  total_items: number;  // Sum of quantities (total items printed)
  completed_prints: number;  // Sum of quantities for completed prints (parts)
  failed_prints: number;
  queued_prints: number;
  in_progress_prints: number;
  total_print_time_hours: number;
  total_filament_grams: number;
  progress_percent: number | null;  // Plates progress (total_archives / target_count)
  parts_progress_percent: number | null;  // Parts progress (completed_prints / target_parts_count)
  estimated_cost: number;
  total_energy_kwh: number;
  total_energy_cost: number;
  remaining_prints: number | null;  // Remaining plates
  remaining_parts: number | null;  // Remaining parts
  bom_total_items: number;
  bom_completed_items: number;
}

export interface ProjectChildPreview {
  id: number;
  name: string;
  color: string | null;
  status: string;
  progress_percent: number | null;
}

export interface Project {
  id: number;
  name: string;
  description: string | null;
  color: string | null;
  status: string;  // active, completed, archived
  target_count: number | null;  // Target number of plates/print jobs
  target_parts_count: number | null;  // Target number of parts/objects
  notes: string | null;
  attachments: ProjectAttachment[] | null;
  tags: string | null;
  due_date: string | null;
  priority: string;  // low, normal, high, urgent
  budget: number | null;
  is_template: boolean;
  template_source_id: number | null;
  parent_id: number | null;
  parent_name: string | null;
  children: ProjectChildPreview[];
  created_at: string;
  updated_at: string;
  stats?: ProjectStats;
}

export interface ProjectAttachment {
  filename: string;
  original_name: string;
  size: number;
  uploaded_at: string;
}

export interface ArchivePreview {
  id: number;
  print_name: string | null;
  thumbnail_path: string | null;
  status: string;
  filament_type: string | null;
  filament_color: string | null;
}

export interface ProjectListItem {
  id: number;
  name: string;
  description: string | null;
  color: string | null;
  status: string;
  target_count: number | null;  // Target number of plates/print jobs
  target_parts_count: number | null;  // Target number of parts/objects
  created_at: string;
  archive_count: number;  // Number of print jobs (plates)
  total_items: number;  // Sum of quantities (total items printed, including failed)
  completed_count: number;  // Sum of quantities for completed prints only (parts)
  failed_count: number;  // Sum of quantities for failed prints
  queue_count: number;
  progress_percent: number | null;  // Plates progress
  archives: ArchivePreview[];
}

export interface ProjectCreate {
  name: string;
  description?: string;
  color?: string;
  target_count?: number;
  target_parts_count?: number;
  notes?: string;
  tags?: string;
  due_date?: string;
  priority?: string;
  budget?: number;
  parent_id?: number;
}

export interface ProjectUpdate {
  name?: string;
  description?: string;
  color?: string;
  status?: string;
  target_count?: number;
  target_parts_count?: number;
  notes?: string;
  tags?: string;
  due_date?: string;
  priority?: string;
  budget?: number;
  parent_id?: number;
}

// BOM Types - Tracks sourced/purchased parts (hardware, electronics, etc.)
export interface BOMItem {
  id: number;
  project_id: number;
  name: string;
  quantity_needed: number;
  quantity_acquired: number;
  unit_price: number | null;
  sourcing_url: string | null;
  archive_id: number | null;
  archive_name: string | null;
  stl_filename: string | null;
  remarks: string | null;
  sort_order: number;
  is_complete: boolean;
  created_at: string;
  updated_at: string;
}

export interface BOMItemCreate {
  name: string;
  quantity_needed?: number;
  unit_price?: number;
  sourcing_url?: string;
  archive_id?: number;
  stl_filename?: string;
  remarks?: string;
}

export interface BOMItemUpdate {
  name?: string;
  quantity_needed?: number;
  quantity_acquired?: number;
  unit_price?: number;
  sourcing_url?: string;
  archive_id?: number;
  stl_filename?: string;
  remarks?: string;
}

// Project Export/Import Types
export interface BOMItemExport {
  name: string;
  quantity_needed: number;
  quantity_acquired: number;
  unit_price: number | null;
  sourcing_url: string | null;
  stl_filename: string | null;
  remarks: string | null;
}

export interface LinkedFolderExport {
  name: string;
}

export interface ProjectExport {
  name: string;
  description: string | null;
  color: string | null;
  status: string;
  target_count: number | null;
  target_parts_count: number | null;
  notes: string | null;
  tags: string | null;
  due_date: string | null;
  priority: string;
  budget: number | null;
  bom_items: BOMItemExport[];
  linked_folders: LinkedFolderExport[];
}

export interface ProjectImport {
  name: string;
  description?: string;
  color?: string;
  status?: string;
  target_count?: number;
  target_parts_count?: number;
  notes?: string;
  tags?: string;
  due_date?: string;
  priority?: string;
  budget?: number;
  bom_items?: BOMItemExport[];
  linked_folders?: LinkedFolderExport[];
}

// Timeline Types
export interface TimelineEvent {
  event_type: string;
  timestamp: string;
  title: string;
  description: string | null;
  metadata: Record<string, unknown> | null;
}

// API Key types
export interface APIKey {
  id: number;
  name: string;
  key_prefix: string;
  can_queue: boolean;
  can_control_printer: boolean;
  can_read_status: boolean;
  printer_ids: number[] | null;
  enabled: boolean;
  last_used: string | null;
  created_at: string;
  expires_at: string | null;
}

export interface APIKeyCreate {
  name: string;
  can_queue?: boolean;
  can_control_printer?: boolean;
  can_read_status?: boolean;
  printer_ids?: number[] | null;
  expires_at?: string | null;
}

export interface APIKeyCreateResponse extends APIKey {
  key: string;  // Full key, only shown on creation
}

export interface APIKeyUpdate {
  name?: string;
  can_queue?: boolean;
  can_control_printer?: boolean;
  can_read_status?: boolean;
  printer_ids?: number[] | null;
  enabled?: boolean;
  expires_at?: string | null;
}

// Settings types
export interface AppSettings {
  auto_archive: boolean;
  save_thumbnails: boolean;
  capture_finish_photo: boolean;
  default_filament_cost: number;
  currency: string;
  energy_cost_per_kwh: number;
  energy_tracking_mode: 'print' | 'total';
  check_updates: boolean;
  check_printer_firmware: boolean;
  notification_language: string;
  // AMS threshold settings
  ams_humidity_good: number;  // <= this is green
  ams_humidity_fair: number;  // <= this is orange, > is red
  ams_temp_good: number;      // <= this is green/blue
  ams_temp_fair: number;      // <= this is orange, > is red
  ams_history_retention_days: number;  // days to keep AMS sensor history
  // Print modal settings
  per_printer_mapping_expanded: boolean;  // Whether custom mapping is expanded by default in print modal
  // Date/time format settings
  date_format: 'system' | 'us' | 'eu' | 'iso';
  time_format: 'system' | '12h' | '24h';
  // Default printer
  default_printer_id: number | null;
  // Dark mode theme settings
  dark_style: 'classic' | 'glow' | 'vibrant';
  dark_background: 'neutral' | 'warm' | 'cool' | 'oled' | 'slate' | 'forest';
  dark_accent: 'green' | 'teal' | 'blue' | 'orange' | 'purple' | 'red';
  // Light mode theme settings
  light_style: 'classic' | 'glow' | 'vibrant';
  light_background: 'neutral' | 'warm' | 'cool';
  light_accent: 'green' | 'teal' | 'blue' | 'orange' | 'purple' | 'red';
  // FTP retry settings
  ftp_retry_enabled: boolean;
  ftp_retry_count: number;
  ftp_retry_delay: number;
  ftp_timeout: number;
  // MQTT relay settings
  mqtt_enabled: boolean;
  mqtt_broker: string;
  mqtt_port: number;
  mqtt_username: string;
  mqtt_password: string;
  mqtt_topic_prefix: string;
  mqtt_use_tls: boolean;
  // External URL for notifications
  external_url: string;
  // Home Assistant integration
  ha_enabled: boolean;
  ha_url: string;
  ha_token: string;
  ha_url_from_env: boolean;
  ha_token_from_env: boolean;
  ha_env_managed: boolean;
  // File Manager / Library settings
  library_archive_mode: 'always' | 'never' | 'ask';
  library_disk_warning_gb: number;
  // Camera view settings
  camera_view_mode: 'window' | 'embedded';
  // Preferred slicer
  preferred_slicer: 'bambu_studio' | 'orcaslicer';
  // Prometheus metrics
  prometheus_enabled: boolean;
  prometheus_token: string;
}

export type AppSettingsUpdate = Partial<AppSettings>;

// MQTT relay status
export interface MQTTStatus {
  enabled: boolean;
  connected: boolean;
  broker: string;
  port: number;
  topic_prefix: string;
}

// Cloud types
export interface CloudAuthStatus {
  is_authenticated: boolean;
  email: string | null;
}

export interface CloudLoginResponse {
  success: boolean;
  needs_verification: boolean;
  message: string;
  verification_type?: 'email' | 'totp' | null;
  tfa_key?: string | null;
}

export interface SlicerSetting {
  setting_id: string;
  name: string;
  type: string;
  version: string | null;
  user_id: string | null;
  updated_time: string | null;
  is_custom: boolean;
}

export interface SpoolCatalogEntry {
  id: number;
  name: string;
  weight: number;
  is_default: boolean;
}

export interface ColorCatalogEntry {
  id: number;
  manufacturer: string;
  color_name: string;
  hex_color: string;
  material: string | null;
  is_default: boolean;
}

export interface ColorLookupResult {
  found: boolean;
  hex_color: string | null;
  material: string | null;
}

export interface SlicerSettingsResponse {
  filament: SlicerSetting[];
  printer: SlicerSetting[];
  process: SlicerSetting[];
}

export interface SlicerSettingDetail {
  message?: string | null;
  code?: string | null;
  error?: string | null;
  public: boolean;
  version?: string | null;
  type: string;
  name: string;
  update_time?: string | null;
  nickname?: string | null;
  base_id?: string | null;
  setting: Record<string, unknown>;
  filament_id?: string | null;
  setting_id?: string | null;
}

export interface SlicerSettingCreate {
  type: string;  // 'filament', 'print', or 'printer'
  name: string;
  base_id: string;
  setting: Record<string, unknown>;
}

export interface SlicerSettingUpdate {
  name?: string;
  setting?: Record<string, unknown>;
}

export interface SlicerSettingDeleteResponse {
  success: boolean;
  message: string;
}

// Built-in filament fallback (static table from backend)
export interface BuiltinFilament {
  filament_id: string;
  name: string;
}

// Local preset types (OrcaSlicer imports)
export interface LocalPreset {
  id: number;
  name: string;
  preset_type: string;
  source: string;
  filament_type: string | null;
  filament_vendor: string | null;
  nozzle_temp_min: number | null;
  nozzle_temp_max: number | null;
  pressure_advance: string | null;
  default_filament_colour: string | null;
  filament_cost: string | null;
  filament_density: string | null;
  compatible_printers: string | null;
  inherits: string | null;
  version: string | null;
  created_at: string;
  updated_at: string;
}

export interface LocalPresetDetail extends LocalPreset {
  setting: Record<string, unknown>;
}

export interface LocalPresetsResponse {
  filament: LocalPreset[];
  printer: LocalPreset[];
  process: LocalPreset[];
}

export interface ImportResponse {
  success: boolean;
  imported: number;
  skipped: number;
  errors: string[];
}

export interface FieldOption {
  value: string;
  label: string;
}

export interface FieldDefinition {
  key: string;
  label: string;
  type: 'text' | 'number' | 'boolean' | 'select';
  category: string;
  description?: string;
  options?: FieldOption[];
  unit?: string;
  min?: number;
  max?: number;
  step?: number;
}

export interface FieldDefinitionsResponse {
  version: string;
  description: string;
  fields: FieldDefinition[];
}

export interface CloudDevice {
  dev_id: string;
  name: string;
  dev_model_name: string | null;
  dev_product_name: string | null;
  online: boolean;
}

// Smart Plug types
export interface SmartPlug {
  id: number;
  name: string;
  plug_type: 'tasmota' | 'homeassistant' | 'mqtt';
  ip_address: string | null;  // Required for Tasmota
  ha_entity_id: string | null;  // Required for Home Assistant (e.g., "switch.printer_plug", "script.turn_on_printer")
  // Home Assistant energy sensor entities (optional)
  ha_power_entity: string | null;
  ha_energy_today_entity: string | null;
  ha_energy_total_entity: string | null;
  // MQTT fields (required when plug_type="mqtt")
  // Legacy field - kept for backward compatibility
  mqtt_topic: string | null;  // Deprecated, use mqtt_power_topic
  mqtt_multiplier: number;  // Deprecated, use mqtt_power_multiplier
  // Power monitoring
  mqtt_power_topic: string | null;  // Topic for power data
  mqtt_power_path: string | null;  // e.g., "power_l1" or "data.power"
  mqtt_power_multiplier: number;  // Unit conversion for power
  // Energy monitoring
  mqtt_energy_topic: string | null;  // Topic for energy data
  mqtt_energy_path: string | null;  // e.g., "energy_l1"
  mqtt_energy_multiplier: number;  // Unit conversion for energy
  // State monitoring
  mqtt_state_topic: string | null;  // Topic for state data
  mqtt_state_path: string | null;  // e.g., "state_l1" for ON/OFF
  mqtt_state_on_value: string | null;  // What value means "ON" (e.g., "ON", "true", "1")
  printer_id: number | null;
  enabled: boolean;
  auto_on: boolean;
  auto_off: boolean;
  off_delay_mode: 'time' | 'temperature';
  off_delay_minutes: number;
  off_temp_threshold: number;
  username: string | null;
  password: string | null;
  // Power alerts
  power_alert_enabled: boolean;
  power_alert_high: number | null;
  power_alert_low: number | null;
  power_alert_last_triggered: string | null;
  // Schedule
  schedule_enabled: boolean;
  schedule_on_time: string | null;
  schedule_off_time: string | null;
  // Visibility options
  show_in_switchbar: boolean;
  show_on_printer_card: boolean;  // For scripts: show on printer card
  // Status
  last_state: string | null;
  last_checked: string | null;
  auto_off_executed: boolean;  // True when auto-off was triggered after print
  created_at: string;
  updated_at: string;
}

export interface SmartPlugCreate {
  name: string;
  plug_type?: 'tasmota' | 'homeassistant' | 'mqtt';
  ip_address?: string | null;  // Required for Tasmota
  ha_entity_id?: string | null;  // Required for Home Assistant
  // Home Assistant energy sensor entities (optional)
  ha_power_entity?: string | null;
  ha_energy_today_entity?: string | null;
  ha_energy_total_entity?: string | null;
  // MQTT fields (required when plug_type="mqtt")
  // Legacy fields - kept for backward compatibility
  mqtt_topic?: string | null;
  mqtt_multiplier?: number;
  // Power monitoring
  mqtt_power_topic?: string | null;
  mqtt_power_path?: string | null;
  mqtt_power_multiplier?: number;
  // Energy monitoring
  mqtt_energy_topic?: string | null;
  mqtt_energy_path?: string | null;
  mqtt_energy_multiplier?: number;
  // State monitoring
  mqtt_state_topic?: string | null;
  mqtt_state_path?: string | null;
  mqtt_state_on_value?: string | null;
  printer_id?: number | null;
  enabled?: boolean;
  auto_on?: boolean;
  auto_off?: boolean;
  off_delay_mode?: 'time' | 'temperature';
  off_delay_minutes?: number;
  off_temp_threshold?: number;
  username?: string | null;
  password?: string | null;
  // Power alerts
  power_alert_enabled?: boolean;
  power_alert_high?: number | null;
  power_alert_low?: number | null;
  // Schedule
  schedule_enabled?: boolean;
  schedule_on_time?: string | null;
  schedule_off_time?: string | null;
  // Visibility options
  show_in_switchbar?: boolean;
  show_on_printer_card?: boolean;
}

export interface SmartPlugUpdate {
  name?: string;
  plug_type?: 'tasmota' | 'homeassistant' | 'mqtt';
  ip_address?: string | null;
  ha_entity_id?: string | null;
  // Home Assistant energy sensor entities (optional)
  ha_power_entity?: string | null;
  ha_energy_today_entity?: string | null;
  ha_energy_total_entity?: string | null;
  // MQTT fields (legacy)
  mqtt_topic?: string | null;
  mqtt_multiplier?: number;
  // MQTT power fields
  mqtt_power_topic?: string | null;
  mqtt_power_path?: string | null;
  mqtt_power_multiplier?: number;
  // MQTT energy fields
  mqtt_energy_topic?: string | null;
  mqtt_energy_path?: string | null;
  mqtt_energy_multiplier?: number;
  // MQTT state fields
  mqtt_state_topic?: string | null;
  mqtt_state_path?: string | null;
  mqtt_state_on_value?: string | null;
  printer_id?: number | null;
  enabled?: boolean;
  auto_on?: boolean;
  auto_off?: boolean;
  off_delay_mode?: 'time' | 'temperature';
  off_delay_minutes?: number;
  off_temp_threshold?: number;
  username?: string | null;
  password?: string | null;
  // Power alerts
  power_alert_enabled?: boolean;
  power_alert_high?: number | null;
  power_alert_low?: number | null;
  // Schedule
  schedule_enabled?: boolean;
  schedule_on_time?: string | null;
  schedule_off_time?: string | null;
  // Visibility options
  show_in_switchbar?: boolean;
  show_on_printer_card?: boolean;
}

// Home Assistant entity for smart plug selection
export interface HAEntity {
  entity_id: string;
  friendly_name: string;
  state: string | null;
  domain: string;  // "switch", "light", "input_boolean", "script"
}

// Home Assistant sensor entity for energy monitoring
export interface HASensorEntity {
  entity_id: string;
  friendly_name: string;
  state: string | null;
  unit_of_measurement: string | null;  // "W", "kW", "kWh", "Wh"
}

export interface HATestConnectionResult {
  success: boolean;
  message: string | null;
  error: string | null;
}

export interface SmartPlugEnergy {
  power: number | null;  // Current watts
  voltage: number | null;  // Volts
  current: number | null;  // Amps
  today: number | null;  // kWh used today
  yesterday: number | null;  // kWh used yesterday
  total: number | null;  // Total kWh
  factor: number | null;  // Power factor (0-1)
  apparent_power: number | null;  // VA
  reactive_power: number | null;  // VAr
}

export interface SmartPlugStatus {
  state: string | null;
  reachable: boolean;
  device_name: string | null;
  energy: SmartPlugEnergy | null;
}

export interface SmartPlugTestResult {
  success: boolean;
  state: string | null;
  device_name: string | null;
}

// Tasmota Discovery types
export interface TasmotaScanStatus {
  running: boolean;
  scanned: number;
  total: number;
}

export interface DiscoveredTasmotaDevice {
  ip_address: string;
  name: string;
  module: number | null;
  state: string | null;
  discovered_at: string | null;
}

// Print Queue types
export interface PrintQueueItem {
  id: number;
  printer_id: number | null;  // null = unassigned
  target_model: string | null;  // Target printer model for model-based assignment
  target_location: string | null;  // Target location filter for model-based assignment
  required_filament_types: string[] | null;  // Required filament types for model-based assignment
  waiting_reason: string | null;  // Why a model-based job hasn't started yet
  // Either archive_id OR library_file_id must be set (archive created at print start)
  archive_id: number | null;
  library_file_id: number | null;
  position: number;
  scheduled_time: string | null;
  require_previous_success: boolean;
  auto_off_after: boolean;
  manual_start: boolean;  // Requires manual trigger to start (staged)
  ams_mapping: number[] | null;  // AMS slot mapping for multi-color prints
  plate_id: number | null;  // Plate ID for multi-plate 3MF files
  // Print options
  bed_levelling: boolean;
  flow_cali: boolean;
  vibration_cali: boolean;
  layer_inspect: boolean;
  timelapse: boolean;
  use_ams: boolean;
  status: 'pending' | 'printing' | 'completed' | 'failed' | 'skipped' | 'cancelled';
  started_at: string | null;
  completed_at: string | null;
  error_message: string | null;
  created_at: string;
  archive_name?: string | null;
  archive_thumbnail?: string | null;
  library_file_name?: string | null;
  library_file_thumbnail?: string | null;
  printer_name?: string | null;
  print_time_seconds?: number | null;  // Estimated print time from archive or library file
  // User tracking (Issue #206)
  created_by_id?: number | null;
  created_by_username?: string | null;
}

export interface PrintQueueItemCreate {
  printer_id?: number | null;  // null = unassigned
  target_model?: string | null;  // Target printer model (mutually exclusive with printer_id)
  target_location?: string | null;  // Target location filter (only used with target_model)
  // Either archive_id OR library_file_id must be provided
  archive_id?: number | null;
  library_file_id?: number | null;
  scheduled_time?: string | null;
  require_previous_success?: boolean;
  auto_off_after?: boolean;
  manual_start?: boolean;  // Requires manual trigger to start (staged)
  ams_mapping?: number[] | null;  // AMS slot mapping for multi-color prints
  plate_id?: number | null;  // Plate ID for multi-plate 3MF files
  // Print options
  bed_levelling?: boolean;
  flow_cali?: boolean;
  vibration_cali?: boolean;
  layer_inspect?: boolean;
  timelapse?: boolean;
  use_ams?: boolean;
}

export interface PrintQueueItemUpdate {
  printer_id?: number | null;  // null = unassign
  target_model?: string | null;  // Target printer model (mutually exclusive with printer_id)
  target_location?: string | null;  // Target location filter (only used with target_model)
  position?: number;
  scheduled_time?: string | null;
  require_previous_success?: boolean;
  auto_off_after?: boolean;
  manual_start?: boolean;
  ams_mapping?: number[];
  plate_id?: number | null;  // Plate ID for multi-plate 3MF files
  // Print options
  bed_levelling?: boolean;
  flow_cali?: boolean;
  vibration_cali?: boolean;
  layer_inspect?: boolean;
  timelapse?: boolean;
  use_ams?: boolean;
}

export interface PrintQueueBulkUpdate {
  item_ids: number[];
  printer_id?: number | null;
  scheduled_time?: string | null;
  require_previous_success?: boolean;
  auto_off_after?: boolean;
  manual_start?: boolean;
  // Print options
  bed_levelling?: boolean;
  flow_cali?: boolean;
  vibration_cali?: boolean;
  layer_inspect?: boolean;
  timelapse?: boolean;
  use_ams?: boolean;
}

export interface PrintQueueBulkUpdateResponse {
  updated_count: number;
  skipped_count: number;
  message: string;
}

// MQTT Logging types
export interface MQTTLogEntry {
  timestamp: string;
  topic: string;
  direction: 'in' | 'out';
  payload: Record<string, unknown>;
}

export interface MQTTLogsResponse {
  logging_enabled: boolean;
  logs: MQTTLogEntry[];
}

// K-Profile types
export interface KProfile {
  slot_id: number;
  extruder_id: number;
  nozzle_id: string;
  nozzle_diameter: string;
  filament_id: string;
  name: string;
  k_value: string;
  n_coef: string;
  ams_id: number;
  tray_id: number;
  setting_id: string | null;
}

export interface KProfileCreate {
  slot_id?: number;  // Storage slot, 0 for new profiles
  extruder_id?: number;
  nozzle_id: string;
  nozzle_diameter: string;
  filament_id: string;
  name: string;
  k_value: string;
  n_coef?: string;
  ams_id?: number;
  tray_id?: number;
  setting_id?: string | null;
}

export interface KProfileDelete {
  slot_id: number;  // cali_idx - calibration index to delete
  extruder_id: number;
  nozzle_id: string;  // e.g., "HH00-0.4"
  nozzle_diameter: string;  // e.g., "0.4"
  filament_id: string;  // Bambu filament identifier
  setting_id?: string | null;  // Setting ID (for X1C series)
}

export interface KProfilesResponse {
  profiles: KProfile[];
  nozzle_diameter: string;
}

export interface KProfileNote {
  setting_id: string;
  note: string;
}

export interface KProfileNotesResponse {
  notes: Record<string, string>;  // setting_id -> note
}

// Slot Preset Mapping
export interface SlotPresetMapping {
  ams_id: number;
  tray_id: number;
  preset_id: string;
  preset_name: string;
}

// Filament types
export interface Filament {
  id: number;
  name: string;
  type: string;  // PLA, PETG, ABS, etc.
  brand: string | null;
  color: string | null;
  color_hex: string | null;
  cost_per_kg: number;
  spool_weight_g: number;
  currency: string;
  density: number | null;
  print_temp_min: number | null;
  print_temp_max: number | null;
  bed_temp_min: number | null;
  bed_temp_max: number | null;
  created_at: string;
  updated_at: string;
}

// Notification Provider types
export type ProviderType = 'callmebot' | 'ntfy' | 'pushover' | 'telegram' | 'email' | 'discord' | 'webhook';

export interface NotificationProvider {
  id: number;
  name: string;
  provider_type: ProviderType;
  enabled: boolean;
  config: Record<string, unknown>;
  // Print lifecycle events
  on_print_start: boolean;
  on_print_complete: boolean;
  on_print_failed: boolean;
  on_print_stopped: boolean;
  on_print_progress: boolean;
  // Printer status events
  on_printer_offline: boolean;
  on_printer_error: boolean;
  on_filament_low: boolean;
  on_maintenance_due: boolean;
  // AMS environmental alarms (regular AMS)
  on_ams_humidity_high: boolean;
  on_ams_temperature_high: boolean;
  // AMS-HT environmental alarms
  on_ams_ht_humidity_high: boolean;
  on_ams_ht_temperature_high: boolean;
  // Build plate detection
  on_plate_not_empty: boolean;
  // Print queue events
  on_queue_job_added: boolean;
  on_queue_job_assigned: boolean;
  on_queue_job_started: boolean;
  on_queue_job_waiting: boolean;
  on_queue_job_skipped: boolean;
  on_queue_job_failed: boolean;
  on_queue_completed: boolean;
  // Quiet hours
  quiet_hours_enabled: boolean;
  quiet_hours_start: string | null;
  quiet_hours_end: string | null;
  // Daily digest
  daily_digest_enabled: boolean;
  daily_digest_time: string | null;
  // Printer filter
  printer_id: number | null;
  // Status tracking
  last_success: string | null;
  last_error: string | null;
  last_error_at: string | null;
  // Timestamps
  created_at: string;
  updated_at: string;
}

export interface NotificationProviderCreate {
  name: string;
  provider_type: ProviderType;
  enabled?: boolean;
  config: Record<string, unknown>;
  // Print lifecycle events
  on_print_start?: boolean;
  on_print_complete?: boolean;
  on_print_failed?: boolean;
  on_print_stopped?: boolean;
  on_print_progress?: boolean;
  // Printer status events
  on_printer_offline?: boolean;
  on_printer_error?: boolean;
  on_filament_low?: boolean;
  on_maintenance_due?: boolean;
  // AMS environmental alarms (regular AMS)
  on_ams_humidity_high?: boolean;
  on_ams_temperature_high?: boolean;
  // AMS-HT environmental alarms
  on_ams_ht_humidity_high?: boolean;
  on_ams_ht_temperature_high?: boolean;
  // Build plate detection
  on_plate_not_empty?: boolean;
  // Print queue events
  on_queue_job_added?: boolean;
  on_queue_job_assigned?: boolean;
  on_queue_job_started?: boolean;
  on_queue_job_waiting?: boolean;
  on_queue_job_skipped?: boolean;
  on_queue_job_failed?: boolean;
  on_queue_completed?: boolean;
  // Quiet hours
  quiet_hours_enabled?: boolean;
  quiet_hours_start?: string | null;
  quiet_hours_end?: string | null;
  // Daily digest
  daily_digest_enabled?: boolean;
  daily_digest_time?: string | null;
  // Printer filter
  printer_id?: number | null;
}

export interface NotificationProviderUpdate {
  name?: string;
  provider_type?: ProviderType;
  enabled?: boolean;
  config?: Record<string, unknown>;
  // Print lifecycle events
  on_print_start?: boolean;
  on_print_complete?: boolean;
  on_print_failed?: boolean;
  on_print_stopped?: boolean;
  on_print_progress?: boolean;
  // Printer status events
  on_printer_offline?: boolean;
  on_printer_error?: boolean;
  on_filament_low?: boolean;
  on_maintenance_due?: boolean;
  // AMS environmental alarms (regular AMS)
  on_ams_humidity_high?: boolean;
  on_ams_temperature_high?: boolean;
  // AMS-HT environmental alarms
  on_ams_ht_humidity_high?: boolean;
  on_ams_ht_temperature_high?: boolean;
  // Build plate detection
  on_plate_not_empty?: boolean;
  // Print queue events
  on_queue_job_added?: boolean;
  on_queue_job_assigned?: boolean;
  on_queue_job_started?: boolean;
  on_queue_job_waiting?: boolean;
  on_queue_job_skipped?: boolean;
  on_queue_job_failed?: boolean;
  on_queue_completed?: boolean;
  // Quiet hours
  quiet_hours_enabled?: boolean;
  quiet_hours_start?: string | null;
  quiet_hours_end?: string | null;
  // Daily digest
  daily_digest_enabled?: boolean;
  daily_digest_time?: string | null;
  // Printer filter
  printer_id?: number | null;
}

// GitHub Backup types
export type ScheduleType = 'hourly' | 'daily' | 'weekly';

export interface GitHubBackupConfig {
  id: number;
  repository_url: string;
  has_token: boolean;
  branch: string;
  schedule_enabled: boolean;
  schedule_type: ScheduleType;
  backup_kprofiles: boolean;
  backup_cloud_profiles: boolean;
  backup_settings: boolean;
  enabled: boolean;
  last_backup_at: string | null;
  last_backup_status: string | null;
  last_backup_message: string | null;
  last_backup_commit_sha: string | null;
  next_scheduled_run: string | null;
  created_at: string;
  updated_at: string;
}

export interface GitHubBackupConfigCreate {
  repository_url: string;
  access_token: string;
  branch?: string;
  schedule_enabled?: boolean;
  schedule_type?: ScheduleType;
  backup_kprofiles?: boolean;
  backup_cloud_profiles?: boolean;
  backup_settings?: boolean;
  enabled?: boolean;
}

export interface GitHubBackupLog {
  id: number;
  config_id: number;
  started_at: string;
  completed_at: string | null;
  status: string;
  trigger: string;
  commit_sha: string | null;
  files_changed: number;
  error_message: string | null;
}

export interface GitHubBackupStatus {
  configured: boolean;
  enabled: boolean;
  is_running: boolean;
  progress: string | null;
  last_backup_at: string | null;
  last_backup_status: string | null;
  next_scheduled_run: string | null;
}

export interface GitHubTestConnectionResponse {
  success: boolean;
  message: string;
  repo_name: string | null;
  permissions: Record<string, boolean> | null;
}

export interface GitHubBackupTriggerResponse {
  success: boolean;
  message: string;
  log_id: number | null;
  commit_sha: string | null;
  files_changed: number;
}

export interface NotificationTestRequest {
  provider_type: ProviderType;
  config: Record<string, unknown>;
}

export interface NotificationTestResponse {
  success: boolean;
  message: string;
}

// Provider-specific config types for reference
export interface CallMeBotConfig {
  phone: string;
  apikey: string;
}

export interface NtfyConfig {
  server?: string;
  topic: string;
  auth_token?: string | null;
}

export interface PushoverConfig {
  user_key: string;
  app_token: string;
  priority?: number;
}

export interface TelegramConfig {
  bot_token: string;
  chat_id: string;
}

export interface EmailConfig {
  smtp_server: string;
  smtp_port?: number;
  username: string;
  password: string;
  from_email: string;
  to_email: string;
  use_tls?: boolean;
}

// Notification Template types
export interface NotificationTemplate {
  id: number;
  event_type: string;
  name: string;
  title_template: string;
  body_template: string;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface NotificationTemplateUpdate {
  title_template?: string;
  body_template?: string;
}

export interface EventVariablesResponse {
  event_type: string;
  event_name: string;
  variables: string[];
}

export interface TemplatePreviewRequest {
  event_type: string;
  title_template: string;
  body_template: string;
}

export interface TemplatePreviewResponse {
  title: string;
  body: string;
}

// Notification Log types
export interface NotificationLogEntry {
  id: number;
  provider_id: number;
  provider_name: string | null;
  provider_type: string | null;
  event_type: string;
  title: string;
  message: string;
  success: boolean;
  error_message: string | null;
  printer_id: number | null;
  printer_name: string | null;
  created_at: string;
}

export interface NotificationLogStats {
  total: number;
  success_count: number;
  failure_count: number;
  by_event_type: Record<string, number>;
  by_provider: Record<string, number>;
}

// Spoolman types
export interface SpoolmanStatus {
  enabled: boolean;
  connected: boolean;
  url: string | null;
}

export interface SkippedSpool {
  location: string;
  reason: string;
  filament_type: string | null;
  color: string | null;
}

export interface SpoolmanSyncResult {
  success: boolean;
  synced_count: number;
  skipped_count: number;
  skipped: SkippedSpool[];
  errors: string[];
}

export interface UnlinkedSpool {
  id: number;
  filament_name: string | null;
  filament_material: string | null;
  filament_color_hex: string | null;
  remaining_weight: number | null;
  location: string | null;
}

export interface LinkedSpoolInfo {
  id: number;
  remaining_weight: number | null;
  filament_weight: number | null;
}

export interface LinkedSpoolsMap {
  linked: Record<string, LinkedSpoolInfo>; // tag (uppercase) -> spool info
}

// Inventory types
export interface InventorySpool {
  id: number;
  material: string;
  subtype: string | null;
  color_name: string | null;
  rgba: string | null;
  brand: string | null;
  label_weight: number;
  core_weight: number;
  weight_used: number;
  slicer_filament: string | null;
  slicer_filament_name: string | null;
  nozzle_temp_min: number | null;
  nozzle_temp_max: number | null;
  note: string | null;
  added_full: boolean | null;
  last_used: string | null;
  encode_time: string | null;
  tag_uid: string | null;
  tray_uuid: string | null;
  data_origin: string | null;
  tag_type: string | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
  k_profiles?: SpoolKProfile[];
}

export interface SpoolUsageRecord {
  id: number;
  spool_id: number;
  printer_id: number | null;
  print_name: string | null;
  weight_used: number;
  percent_used: number;
  status: string;
  created_at: string;
}

export interface SpoolKProfile {
  id: number;
  spool_id: number;
  printer_id: number;
  extruder: number;
  nozzle_diameter: string;
  nozzle_type: string | null;
  k_value: number;
  name: string | null;
  cali_idx: number | null;
  setting_id: string | null;
  created_at: string;
}

export interface SpoolKProfileInput {
  printer_id: number;
  extruder?: number;
  nozzle_diameter?: string;
  nozzle_type?: string | null;
  k_value: number;
  name?: string | null;
  cali_idx?: number | null;
  setting_id?: string | null;
}

export interface SpoolAssignment {
  id: number;
  spool_id: number;
  printer_id: number;
  printer_name: string | null;
  ams_id: number;
  tray_id: number;
  fingerprint_color: string | null;
  fingerprint_type: string | null;
  spool?: InventorySpool | null;
  configured: boolean;
  created_at: string;
}

// Update types
export interface VersionInfo {
  version: string;
  repo: string;
}

export interface UpdateCheckResult {
  update_available: boolean;
  current_version: string;
  latest_version: string | null;
  release_name?: string;
  release_notes?: string;
  release_url?: string;
  published_at?: string;
  error?: string;
  message?: string;
  is_docker?: boolean;
  update_method?: 'docker' | 'git';
}

export interface UpdateStatus {
  status: 'idle' | 'checking' | 'downloading' | 'installing' | 'complete' | 'error';
  progress: number;
  message: string;
  error: string | null;
}

// Maintenance types
export interface MaintenanceType {
  id: number;
  name: string;
  description: string | null;
  default_interval_hours: number;
  interval_type: 'hours' | 'days';  // "hours" = print hours, "days" = calendar days
  icon: string | null;
  wiki_url: string | null;  // Documentation link
  is_system: boolean;
  created_at: string;
}

export interface MaintenanceTypeCreate {
  name: string;
  description?: string | null;
  default_interval_hours?: number;
  interval_type?: 'hours' | 'days';
  icon?: string | null;
  wiki_url?: string | null;
}

export interface MaintenanceStatus {
  id: number;
  printer_id: number;
  printer_name: string;
  printer_model: string | null;
  maintenance_type_id: number;
  maintenance_type_name: string;
  maintenance_type_icon: string | null;
  maintenance_type_wiki_url: string | null;  // Custom wiki URL from type
  enabled: boolean;
  interval_hours: number;  // For hours type: print hours; for days type: number of days
  interval_type: 'hours' | 'days';
  current_hours: number;
  hours_since_maintenance: number;
  hours_until_due: number;
  days_since_maintenance: number | null;  // For days type
  days_until_due: number | null;  // For days type
  is_due: boolean;
  is_warning: boolean;
  last_performed_at: string | null;
}

export interface PrinterMaintenanceOverview {
  printer_id: number;
  printer_name: string;
  printer_model: string | null;
  total_print_hours: number;
  maintenance_items: MaintenanceStatus[];
  due_count: number;
  warning_count: number;
}

export interface MaintenanceHistory {
  id: number;
  printer_maintenance_id: number;
  performed_at: string;
  hours_at_maintenance: number;
  notes: string | null;
}

export interface MaintenanceSummary {
  total_due: number;
  total_warning: number;
  printers_with_issues: Array<{
    printer_id: number;
    printer_name: string;
    due_count: number;
    warning_count: number;
  }>;
}

// External Links (sidebar)
export interface ExternalLink {
  id: number;
  name: string;
  url: string;
  icon: string;
  open_in_new_tab: boolean;
  custom_icon: string | null;
  sort_order: number;
  created_at: string;
  updated_at: string;
}

export interface ExternalLinkCreate {
  name: string;
  url: string;
  icon: string;
  open_in_new_tab?: boolean;
}

export interface ExternalLinkUpdate {
  name?: string;
  url?: string;
  icon?: string;
  open_in_new_tab?: boolean;
}

// Permission type - all available permissions
export type Permission =
  | 'printers:read' | 'printers:create' | 'printers:update' | 'printers:delete' | 'printers:control' | 'printers:files' | 'printers:ams_rfid'
  | 'archives:read' | 'archives:create'
  | 'archives:update_own' | 'archives:update_all' | 'archives:delete_own' | 'archives:delete_all'
  | 'archives:reprint_own' | 'archives:reprint_all'
  | 'queue:read' | 'queue:create'
  | 'queue:update_own' | 'queue:update_all' | 'queue:delete_own' | 'queue:delete_all'
  | 'queue:reorder'
  | 'library:read' | 'library:upload'
  | 'library:update_own' | 'library:update_all' | 'library:delete_own' | 'library:delete_all'
  | 'projects:read' | 'projects:create' | 'projects:update' | 'projects:delete'
  | 'filaments:read' | 'filaments:create' | 'filaments:update' | 'filaments:delete'
  | 'smart_plugs:read' | 'smart_plugs:create' | 'smart_plugs:update' | 'smart_plugs:delete' | 'smart_plugs:control'
  | 'camera:view'
  | 'maintenance:read' | 'maintenance:create' | 'maintenance:update' | 'maintenance:delete'
  | 'kprofiles:read' | 'kprofiles:create' | 'kprofiles:update' | 'kprofiles:delete'
  | 'notifications:read' | 'notifications:create' | 'notifications:update' | 'notifications:delete'
  | 'notification_templates:read' | 'notification_templates:update'
  | 'external_links:read' | 'external_links:create' | 'external_links:update' | 'external_links:delete'
  | 'discovery:scan'
  | 'firmware:read' | 'firmware:update'
  | 'ams_history:read'
  | 'stats:read'
  | 'system:read'
  | 'settings:read' | 'settings:update' | 'settings:backup' | 'settings:restore'
  | 'github:backup' | 'github:restore'
  | 'cloud:auth'
  | 'api_keys:read' | 'api_keys:create' | 'api_keys:update' | 'api_keys:delete'
  | 'users:read' | 'users:create' | 'users:update' | 'users:delete'
  | 'groups:read' | 'groups:create' | 'groups:update' | 'groups:delete'
  | 'websocket:connect';

// Group types
export interface GroupBrief {
  id: number;
  name: string;
}

export interface Group {
  id: number;
  name: string;
  description: string | null;
  permissions: Permission[];
  is_system: boolean;
  user_count: number;
  created_at: string;
  updated_at: string;
}

export interface GroupDetail extends Group {
  users: Array<{ id: number; username: string; is_active: boolean }>;
}

export interface GroupCreate {
  name: string;
  description?: string;
  permissions: Permission[];
}

export interface GroupUpdate {
  name?: string;
  description?: string;
  permissions?: Permission[];
}

export interface PermissionInfo {
  value: Permission;
  label: string;
}

export interface PermissionCategory {
  name: string;
  permissions: PermissionInfo[];
}

export interface PermissionsListResponse {
  categories: PermissionCategory[];
  all_permissions: Permission[];
}

// Auth types
export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  user: UserResponse;
}

export interface UserResponse {
  id: number;
  username: string;
  email?: string;
  role: string;  // Deprecated, kept for backward compatibility
  is_active: boolean;
  is_admin: boolean;  // Computed from role and group membership
  groups: GroupBrief[];
  permissions: Permission[];  // All permissions from groups
  created_at: string;
}

export interface UserCreate {
  username: string;
  password?: string;  // Optional when advanced auth is enabled
  email?: string;
  role: string;
  group_ids?: number[];
}

export interface UserUpdate {
  username?: string;
  password?: string;
  email?: string;
  role?: string;
  is_active?: boolean;
  group_ids?: number[];
}

export interface SetupRequest {
  auth_enabled: boolean;
  admin_username?: string;
  admin_password?: string;
}

export interface ForgotPasswordRequest {
  email: string;
}

export interface ForgotPasswordResponse {
  message: string;
}

export interface ResetPasswordRequest {
  user_id: number;
}

export interface ResetPasswordResponse {
  message: string;
}

export interface SMTPSettings {
  smtp_host: string;
  smtp_port: number;
  smtp_username?: string;
  smtp_password?: string;
  smtp_security: 'starttls' | 'ssl' | 'none';
  smtp_auth_enabled: boolean;
  smtp_from_email: string;
  smtp_from_name: string;
}

export interface TestSMTPRequest {
  smtp_host: string;
  smtp_port: number;
  smtp_username?: string;
  smtp_password?: string;
  smtp_security: 'starttls' | 'ssl' | 'none';
  smtp_auth_enabled: boolean;
  smtp_from_email: string;
  test_recipient: string;
}

export interface TestSMTPResponse {
  success: boolean;
  message: string;
}

export interface AdvancedAuthStatus {
  advanced_auth_enabled: boolean;
  smtp_configured: boolean;
}

export interface SetupResponse {
  auth_enabled: boolean;
  admin_created?: boolean;
}

export interface AuthStatus {
  auth_enabled: boolean;
  requires_setup: boolean;
}

// API functions
export const api = {
  // Authentication
  getAuthStatus: () => request<AuthStatus>('/auth/status'),
  setupAuth: (data: SetupRequest) =>
    request<SetupResponse>('/auth/setup', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  login: (data: LoginRequest) =>
    request<LoginResponse>('/auth/login', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  logout: () =>
    request<{ message: string }>('/auth/logout', {
      method: 'POST',
    }),
  getCurrentUser: () => request<UserResponse>('/auth/me'),
  disableAuth: () =>
    request<{ message: string; auth_enabled: boolean }>('/auth/disable', {
      method: 'POST',
    }),

  // Advanced Authentication
  testSMTP: (data: TestSMTPRequest) =>
    request<TestSMTPResponse>('/auth/smtp/test', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  getSMTPSettings: () => request<SMTPSettings | null>('/auth/smtp'),
  saveSMTPSettings: (data: SMTPSettings) =>
    request<{ message: string }>('/auth/smtp', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  enableAdvancedAuth: () =>
    request<{ message: string; advanced_auth_enabled: boolean }>('/auth/advanced-auth/enable', {
      method: 'POST',
    }),
  disableAdvancedAuth: () =>
    request<{ message: string; advanced_auth_enabled: boolean }>('/auth/advanced-auth/disable', {
      method: 'POST',
    }),
  getAdvancedAuthStatus: () => request<AdvancedAuthStatus>('/auth/advanced-auth/status'),
  forgotPassword: (data: ForgotPasswordRequest) =>
    request<ForgotPasswordResponse>('/auth/forgot-password', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  resetUserPassword: (data: ResetPasswordRequest) =>
    request<ResetPasswordResponse>('/auth/reset-password', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  // Users
  getUsers: () => request<UserResponse[]>('/users/'),
  getUser: (id: number) => request<UserResponse>(`/users/${id}`),
  createUser: (data: UserCreate) =>
    request<UserResponse>('/users/', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateUser: (id: number, data: UserUpdate) =>
    request<UserResponse>(`/users/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  deleteUser: (id: number, deleteItems: boolean = false) =>
    request<void>(`/users/${id}?delete_items=${deleteItems}`, {
      method: 'DELETE',
    }),
  getUserItemsCount: (id: number) =>
    request<{ archives: number; queue_items: number; library_files: number }>(`/users/${id}/items-count`),
  changePassword: (currentPassword: string, newPassword: string) =>
    request<{ message: string }>('/users/me/change-password', {
      method: 'POST',
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    }),

  // Groups
  getPermissions: () => request<PermissionsListResponse>('/groups/permissions'),
  getGroups: () => request<Group[]>('/groups/'),
  getGroup: (id: number) => request<GroupDetail>(`/groups/${id}`),
  createGroup: (data: GroupCreate) =>
    request<Group>('/groups/', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateGroup: (id: number, data: GroupUpdate) =>
    request<Group>(`/groups/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  deleteGroup: (id: number) =>
    request<void>(`/groups/${id}`, {
      method: 'DELETE',
    }),
  addUserToGroup: (groupId: number, userId: number) =>
    request<void>(`/groups/${groupId}/users/${userId}`, {
      method: 'POST',
    }),
  removeUserFromGroup: (groupId: number, userId: number) =>
    request<void>(`/groups/${groupId}/users/${userId}`, {
      method: 'DELETE',
    }),

  // Printers
  getPrinters: () => request<Printer[]>('/printers/'),
  getPrinter: (id: number) => request<Printer>(`/printers/${id}`),
  createPrinter: (data: PrinterCreate) =>
    request<Printer>('/printers/', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updatePrinter: (id: number, data: Partial<PrinterCreate>) =>
    request<Printer>(`/printers/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  deletePrinter: (id: number, deleteArchives: boolean = true) =>
    request<{ status: string; archives_deleted: boolean }>(
      `/printers/${id}?delete_archives=${deleteArchives}`,
      { method: 'DELETE' }
    ),
  getPrinterStatus: (id: number) =>
    request<PrinterStatus>(`/printers/${id}/status`),
  refreshPrinterStatus: (id: number) =>
    request<{ status: string }>(`/printers/${id}/refresh-status`, {
      method: 'POST',
    }),
  connectPrinter: (id: number) =>
    request<{ connected: boolean }>(`/printers/${id}/connect`, {
      method: 'POST',
    }),
  disconnectPrinter: (id: number) =>
    request<{ connected: boolean }>(`/printers/${id}/disconnect`, {
      method: 'POST',
    }),
  testExternalCamera: (printerId: number, url: string, cameraType: string) =>
    request<{ success: boolean; error?: string; resolution?: string }>(
      `/printers/${printerId}/camera/external/test?url=${encodeURIComponent(url)}&camera_type=${encodeURIComponent(cameraType)}`,
      { method: 'POST' }
    ),

  // Print Control
  stopPrint: (printerId: number) =>
    request<{ success: boolean; message: string }>(`/printers/${printerId}/print/stop`, {
      method: 'POST',
    }),
  pausePrint: (printerId: number) =>
    request<{ success: boolean; message: string }>(`/printers/${printerId}/print/pause`, {
      method: 'POST',
    }),
  resumePrint: (printerId: number) =>
    request<{ success: boolean; message: string }>(`/printers/${printerId}/print/resume`, {
      method: 'POST',
    }),
  clearPlate: (printerId: number) =>
    request<{ success: boolean; message: string }>(`/printers/${printerId}/clear-plate`, {
      method: 'POST',
    }),

  // Get current print user (for reprint tracking - Issue #206)
  getCurrentPrintUser: (printerId: number) =>
    request<{ user_id?: number; username?: string }>(`/printers/${printerId}/current-print-user`),

  // Chamber Light Control
  setChamberLight: (printerId: number, on: boolean) =>
    request<{ success: boolean; message: string }>(`/printers/${printerId}/chamber-light?on=${on}`, {
      method: 'POST',
    }),

  // Skip Objects
  getPrintableObjects: (printerId: number) =>
    request<{
      objects: Array<{ id: number; name: string; x: number | null; y: number | null; skipped: boolean }>;
      total: number;
      skipped_count: number;
      is_printing: boolean;
      bbox_all: [number, number, number, number] | null;
    }>(`/printers/${printerId}/print/objects`),

  skipObjects: (printerId: number, objectIds: number[]) =>
    request<{ success: boolean; message: string; skipped_objects: number[] }>(
      `/printers/${printerId}/print/skip-objects`,
      {
        method: 'POST',
        body: JSON.stringify(objectIds),
      }
    ),

  // AMS Control
  refreshAmsSlot: (printerId: number, amsId: number, slotId: number) =>
    request<{ success: boolean; message: string }>(
      `/printers/${printerId}/ams/${amsId}/slot/${slotId}/refresh`,
      { method: 'POST' }
    ),

  // MQTT Debug Logging
  enableMQTTLogging: (printerId: number) =>
    request<{ logging_enabled: boolean }>(`/printers/${printerId}/logging/enable`, {
      method: 'POST',
    }),
  disableMQTTLogging: (printerId: number) =>
    request<{ logging_enabled: boolean }>(`/printers/${printerId}/logging/disable`, {
      method: 'POST',
    }),
  getMQTTLogs: (printerId: number) =>
    request<MQTTLogsResponse>(`/printers/${printerId}/logging`),
  clearMQTTLogs: (printerId: number) =>
    request<{ status: string }>(`/printers/${printerId}/logging`, {
      method: 'DELETE',
    }),

  // Printer File Manager
  getPrinterFiles: (printerId: number, path = '/') =>
    request<{
      path: string;
      files: Array<{
        name: string;
        is_directory: boolean;
        size: number;
        path: string;
        mtime?: string;
      }>;
    }>(`/printers/${printerId}/files?path=${encodeURIComponent(path)}`),
  getPrinterFileDownloadUrl: (printerId: number, path: string) =>
    `${API_BASE}/printers/${printerId}/files/download?path=${encodeURIComponent(path)}`,
  getPrinterFileGcodeUrl: (printerId: number, path: string) =>
    `${API_BASE}/printers/${printerId}/files/gcode?path=${encodeURIComponent(path)}`,
  getPrinterFilePlates: (printerId: number, path: string) =>
    request<{
      printer_id: number;
      path: string;
      filename: string;
      plates: Array<{
        index: number;
        name: string | null;
        objects: string[];
        has_thumbnail: boolean;
        thumbnail_url: string | null;
        print_time_seconds: number | null;
        filament_used_grams: number | null;
        filaments: Array<{
          slot_id: number;
          type: string;
          color: string;
          used_grams: number;
          used_meters: number;
        }>;
      }>;
      is_multi_plate: boolean;
    }>(`/printers/${printerId}/files/plates?path=${encodeURIComponent(path)}`),
  getPrinterFilePlateThumbnail: (printerId: number, plateIndex: number, path: string) =>
    `${API_BASE}/printers/${printerId}/files/plate-thumbnail/${plateIndex}?path=${encodeURIComponent(path)}`,
  downloadPrinterFile: async (printerId: number, path: string): Promise<void> => {
    const headers: Record<string, string> = {};
    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }
    const response = await fetch(
      `${API_BASE}/printers/${printerId}/files/download?path=${encodeURIComponent(path)}`,
      { headers }
    );
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    const disposition = response.headers.get('Content-Disposition');
    const filename = parseContentDispositionFilename(disposition) || path.split('/').pop() || 'download';
    const blob = await response.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.URL.revokeObjectURL(url);
  },
  downloadPrinterFilesAsZip: async (printerId: number, paths: string[]): Promise<Blob> => {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }
    const response = await fetch(`${API_BASE}/printers/${printerId}/files/download-zip`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ paths }),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    return response.blob();
  },
  deletePrinterFile: (printerId: number, path: string) =>
    request<{ status: string; path: string }>(`/printers/${printerId}/files?path=${encodeURIComponent(path)}`, {
      method: 'DELETE',
    }),
  getPrinterStorage: (printerId: number) =>
    request<{ used_bytes: number | null; free_bytes: number | null }>(`/printers/${printerId}/storage`),

  // Archives
  getArchives: (printerId?: number, projectId?: number, limit = 50, offset = 0) => {
    const params = new URLSearchParams();
    if (printerId) params.set('printer_id', String(printerId));
    if (projectId) params.set('project_id', String(projectId));
    params.set('limit', String(limit));
    params.set('offset', String(offset));
    return request<Archive[]>(`/archives/?${params}`);
  },
  getArchive: (id: number) => request<Archive>(`/archives/${id}`),
  searchArchives: (query: string, options?: {
    printerId?: number;
    projectId?: number;
    status?: string;
    limit?: number;
    offset?: number;
  }) => {
    const params = new URLSearchParams();
    params.set('q', query);
    if (options?.printerId) params.set('printer_id', String(options.printerId));
    if (options?.projectId) params.set('project_id', String(options.projectId));
    if (options?.status) params.set('status', options.status);
    if (options?.limit) params.set('limit', String(options.limit));
    if (options?.offset) params.set('offset', String(options.offset));
    return request<Archive[]>(`/archives/search?${params}`);
  },
  rebuildSearchIndex: () => request<{ message: string }>('/archives/search/rebuild-index', { method: 'POST' }),
  updateArchive: (id: number, data: {
    printer_id?: number | null;
    project_id?: number | null;
    print_name?: string;
    is_favorite?: boolean;
    tags?: string;
    notes?: string;
    cost?: number;
    failure_reason?: string | null;
    status?: string;
    quantity?: number;
    external_url?: string | null;
  }) =>
    request<Archive>(`/archives/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  toggleFavorite: (id: number) =>
    request<Archive>(`/archives/${id}/favorite`, { method: 'POST' }),
  deleteArchive: (id: number) =>
    request<void>(`/archives/${id}`, { method: 'DELETE' }),
  getArchiveStats: () => request<ArchiveStats>('/archives/stats'),
  // Tag management
  getTags: () => request<TagInfo[]>('/archives/tags'),
  renameTag: (oldName: string, newName: string) =>
    request<{ affected: number }>(`/archives/tags/${encodeURIComponent(oldName)}`, {
      method: 'PUT',
      body: JSON.stringify({ new_name: newName }),
    }),
  deleteTag: (name: string) =>
    request<{ affected: number }>(`/archives/tags/${encodeURIComponent(name)}`, {
      method: 'DELETE',
    }),
  recalculateCosts: () =>
    request<{ message: string; updated: number }>('/archives/recalculate-costs', { method: 'POST' }),
  getFailureAnalysis: (options?: { days?: number; printerId?: number; projectId?: number }) => {
    const params = new URLSearchParams();
    if (options?.days) params.set('days', String(options.days));
    if (options?.printerId) params.set('printer_id', String(options.printerId));
    if (options?.projectId) params.set('project_id', String(options.projectId));
    return request<FailureAnalysis>(`/archives/analysis/failures?${params}`);
  },
  compareArchives: (archiveIds: number[]) =>
    request<ArchiveComparison>(`/archives/compare?archive_ids=${archiveIds.join(',')}`),
  findSimilarArchives: (archiveId: number, limit = 10) =>
    request<SimilarArchive[]>(`/archives/${archiveId}/similar?limit=${limit}`),
  exportArchives: async (options?: {
    format?: 'csv' | 'xlsx';
    fields?: string[];
    printerId?: number;
    projectId?: number;
    status?: string;
    dateFrom?: string;
    dateTo?: string;
    search?: string;
  }): Promise<{ blob: Blob; filename: string }> => {
    const params = new URLSearchParams();
    if (options?.format) params.set('format', options.format);
    if (options?.fields) params.set('fields', options.fields.join(','));
    if (options?.printerId) params.set('printer_id', String(options.printerId));
    if (options?.projectId) params.set('project_id', String(options.projectId));
    if (options?.status) params.set('status', options.status);
    if (options?.dateFrom) params.set('date_from', options.dateFrom);
    if (options?.dateTo) params.set('date_to', options.dateTo);
    if (options?.search) params.set('search', options.search);

    const headers: Record<string, string> = {};
    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }
    const response = await fetch(`${API_BASE}/archives/export?${params}`, { headers });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }

    const contentDisposition = response.headers.get('Content-Disposition');
    let filename = options?.format === 'xlsx' ? 'archives_export.xlsx' : 'archives_export.csv';
    if (contentDisposition) {
      const match = contentDisposition.match(/filename="?([^"]+)"?/);
      if (match) filename = match[1];
    }

    const blob = await response.blob();
    return { blob, filename };
  },
  exportStats: async (options?: {
    format?: 'csv' | 'xlsx';
    days?: number;
    printerId?: number;
    projectId?: number;
  }): Promise<{ blob: Blob; filename: string }> => {
    const params = new URLSearchParams();
    if (options?.format) params.set('format', options.format);
    if (options?.days) params.set('days', String(options.days));
    if (options?.printerId) params.set('printer_id', String(options.printerId));
    if (options?.projectId) params.set('project_id', String(options.projectId));

    const headers: Record<string, string> = {};
    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }
    const response = await fetch(`${API_BASE}/archives/stats/export?${params}`, { headers });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }

    const contentDisposition = response.headers.get('Content-Disposition');
    let filename = options?.format === 'xlsx' ? 'stats_export.xlsx' : 'stats_export.csv';
    if (contentDisposition) {
      const match = contentDisposition.match(/filename="?([^"]+)"?/);
      if (match) filename = match[1];
    }

    const blob = await response.blob();
    return { blob, filename };
  },
  getArchiveDuplicates: (id: number) =>
    request<{ duplicates: ArchiveDuplicate[]; count: number }>(`/archives/${id}/duplicates`),
  backfillContentHashes: () =>
    request<{ updated: number; errors: Array<{ id: number; error: string }> }>('/archives/backfill-hashes', {
      method: 'POST',
    }),
  getArchiveThumbnail: (id: number) => `${API_BASE}/archives/${id}/thumbnail?v=${Date.now()}`,
  getArchivePlateThumbnail: (id: number, plateIndex: number) =>
    `${API_BASE}/archives/${id}/plate-thumbnail/${plateIndex}`,
  getArchiveDownload: (id: number) => `${API_BASE}/archives/${id}/download`,
  downloadArchive: async (id: number, filename?: string): Promise<void> => {
    const headers: Record<string, string> = {};
    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }
    const response = await fetch(`${API_BASE}/archives/${id}/download`, { headers });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    const disposition = response.headers.get('Content-Disposition');
    const downloadFilename = parseContentDispositionFilename(disposition) || filename || `archive_${id}.3mf`;
    const blob = await response.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = downloadFilename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.URL.revokeObjectURL(url);
  },
  getArchiveGcode: (id: number) => `${API_BASE}/archives/${id}/gcode`,
  getArchivePlatePreview: (id: number) => `${API_BASE}/archives/${id}/plate-preview`,
  getArchiveTimelapse: (id: number) => `${API_BASE}/archives/${id}/timelapse?v=${Date.now()}`,
  scanArchiveTimelapse: (id: number) =>
    request<{
      status: string;
      message: string;
      filename?: string;
      available_files?: Array<{ name: string; path: string; size: number; mtime: string | null }>;
    }>(`/archives/${id}/timelapse/scan`, {
      method: 'POST',
    }),
  selectArchiveTimelapse: (id: number, filename: string) =>
    request<{ status: string; message: string; filename: string }>(
      `/archives/${id}/timelapse/select?filename=${encodeURIComponent(filename)}`,
      { method: 'POST' }
    ),
  uploadArchiveTimelapse: async (archiveId: number, file: File): Promise<{ status: string; filename: string }> => {
    const formData = new FormData();
    formData.append('file', file);
    const headers: Record<string, string> = {};
    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }
    const response = await fetch(`${API_BASE}/archives/${archiveId}/timelapse/upload`, {
      method: 'POST',
      headers,
      body: formData,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    return response.json();
  },
  // Timelapse Editor
  getTimelapseInfo: (archiveId: number) =>
    request<{
      duration: number;
      width: number;
      height: number;
      fps: number;
      codec: string;
      file_size: number;
      has_audio: boolean;
    }>(`/archives/${archiveId}/timelapse/info`),
  getTimelapseThumbnails: (archiveId: number, count: number = 10) =>
    request<{
      thumbnails: string[];
      timestamps: number[];
    }>(`/archives/${archiveId}/timelapse/thumbnails?count=${count}`),
  processTimelapse: async (
    archiveId: number,
    params: {
      trimStart?: number;
      trimEnd?: number;
      speed?: number;
      saveMode: 'replace' | 'new';
      outputFilename?: string;
    },
    audioFile?: File
  ): Promise<{ status: string; output_path: string | null; message: string }> => {
    const formData = new FormData();
    formData.append('trim_start', String(params.trimStart ?? 0));
    if (params.trimEnd !== undefined) {
      formData.append('trim_end', String(params.trimEnd));
    }
    formData.append('speed', String(params.speed ?? 1));
    formData.append('save_mode', params.saveMode);
    if (params.outputFilename) {
      formData.append('output_filename', params.outputFilename);
    }
    if (audioFile) {
      formData.append('audio', audioFile);
    }
    const headers: Record<string, string> = {};
    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }
    const response = await fetch(`${API_BASE}/archives/${archiveId}/timelapse/process`, {
      method: 'POST',
      headers,
      body: formData,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    return response.json();
  },
  // Photos
  getArchivePhotoUrl: (archiveId: number, filename: string) =>
    `${API_BASE}/archives/${archiveId}/photos/${encodeURIComponent(filename)}`,
  uploadArchivePhoto: async (archiveId: number, file: File): Promise<{ status: string; filename: string; photos: string[] }> => {
    const formData = new FormData();
    formData.append('file', file);
    const headers: Record<string, string> = {};
    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }
    const response = await fetch(`${API_BASE}/archives/${archiveId}/photos`, {
      headers,
      method: 'POST',
      body: formData,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    return response.json();
  },
  deleteArchivePhoto: (archiveId: number, filename: string) =>
    request<{ status: string; photos: string[] | null }>(`/archives/${archiveId}/photos/${encodeURIComponent(filename)}`, {
      method: 'DELETE',
    }),
  // Source 3MF (original slicer project file)
  getSource3mfDownloadUrl: (archiveId: number) =>
    `${API_BASE}/archives/${archiveId}/source`,
  downloadSource3mf: async (archiveId: number): Promise<void> => {
    const headers: Record<string, string> = {};
    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }
    const response = await fetch(`${API_BASE}/archives/${archiveId}/source`, { headers });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    const disposition = response.headers.get('Content-Disposition');
    const filename = parseContentDispositionFilename(disposition) || `source_${archiveId}.3mf`;
    const blob = await response.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.URL.revokeObjectURL(url);
  },
  getSource3mfForSlicer: (archiveId: number, filename: string) =>
    `${API_BASE}/archives/${archiveId}/source/${encodeURIComponent(filename.endsWith('.3mf') ? filename : filename + '.3mf')}`,
  uploadSource3mf: async (archiveId: number, file: File): Promise<{ status: string; filename: string }> => {
    const formData = new FormData();
    formData.append('file', file);
    const headers: Record<string, string> = {};
    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }
    const response = await fetch(`${API_BASE}/archives/${archiveId}/source`, {
      method: 'POST',
      headers,
      body: formData,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    return response.json();
  },
  deleteSource3mf: (archiveId: number) =>
    request<{ status: string }>(`/archives/${archiveId}/source`, {
      method: 'DELETE',
    }),
  // F3D (Fusion 360 design file)
  getF3dDownloadUrl: (archiveId: number) =>
    `${API_BASE}/archives/${archiveId}/f3d`,
  downloadF3d: async (archiveId: number): Promise<void> => {
    const headers: Record<string, string> = {};
    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }
    const response = await fetch(`${API_BASE}/archives/${archiveId}/f3d`, { headers });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    const disposition = response.headers.get('Content-Disposition');
    const filename = parseContentDispositionFilename(disposition) || `archive_${archiveId}.f3d`;
    const blob = await response.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.URL.revokeObjectURL(url);
  },
  uploadF3d: async (archiveId: number, file: File): Promise<{ status: string; filename: string }> => {
    const formData = new FormData();
    formData.append('file', file);
    const headers: Record<string, string> = {};
    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }
    const response = await fetch(`${API_BASE}/archives/${archiveId}/f3d`, {
      method: 'POST',
      headers,
      body: formData,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    return response.json();
  },
  deleteF3d: (archiveId: number) =>
    request<{ status: string }>(`/archives/${archiveId}/f3d`, {
      method: 'DELETE',
    }),

  // QR Code
  getArchiveQRCodeUrl: (archiveId: number, size = 200) =>
    `${API_BASE}/archives/${archiveId}/qrcode?size=${size}`,
  getArchiveCapabilities: (id: number) =>
    request<{
      has_model: boolean;
      has_gcode: boolean;
      has_source: boolean;
      build_volume: { x: number; y: number; z: number };
      filament_colors: string[];
    }>(`/archives/${id}/capabilities`),
  // Project Page
  getArchiveProjectPage: (id: number) =>
    request<{
      title: string | null;
      description: string | null;
      designer: string | null;
      designer_user_id: string | null;
      license: string | null;
      copyright: string | null;
      creation_date: string | null;
      modification_date: string | null;
      origin: string | null;
      profile_title: string | null;
      profile_description: string | null;
      profile_cover: string | null;
      profile_user_id: string | null;
      profile_user_name: string | null;
      design_model_id: string | null;
      design_profile_id: string | null;
      design_region: string | null;
      model_pictures: Array<{ name: string; path: string; url: string }>;
      profile_pictures: Array<{ name: string; path: string; url: string }>;
      thumbnails: Array<{ name: string; path: string; url: string }>;
    }>(`/archives/${id}/project-page`),
  updateArchiveProjectPage: (id: number, data: {
    title?: string;
    description?: string;
    designer?: string;
    license?: string;
    copyright?: string;
    profile_title?: string;
    profile_description?: string;
  }) =>
    request(`/archives/${id}/project-page`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  getArchiveProjectImageUrl: (archiveId: number, imagePath: string) =>
    `${API_BASE}/archives/${archiveId}/project-image/${encodeURIComponent(imagePath)}`,
  getArchiveForSlicer: (id: number, filename: string) =>
    `${API_BASE}/archives/${id}/file/${encodeURIComponent(filename.endsWith('.3mf') ? filename : filename + '.3mf')}`,
  getArchivePlates: (archiveId: number) =>
    request<ArchivePlatesResponse>(`/archives/${archiveId}/plates`),
  getArchiveFilamentRequirements: (archiveId: number, plateId?: number) =>
    request<{
      archive_id: number;
      filename: string;
      plate_id: number | null;
      filaments: Array<{
        slot_id: number;
        type: string;
        color: string;
        used_grams: number;
        used_meters: number;
      }>;
    }>(`/archives/${archiveId}/filament-requirements${plateId !== undefined ? `?plate_id=${plateId}` : ''}`),
  reprintArchive: (
    archiveId: number,
    printerId: number,
    options?: {
      plate_id?: number;
      ams_mapping?: number[];
      timelapse?: boolean;
      bed_levelling?: boolean;
      flow_cali?: boolean;
      vibration_cali?: boolean;
      layer_inspect?: boolean;
      use_ams?: boolean;
    }
  ) =>
    request<{ status: string; printer_id: number; archive_id: number; filename: string }>(
      `/archives/${archiveId}/reprint?printer_id=${printerId}`,
      {
        method: 'POST',
        headers: options ? { 'Content-Type': 'application/json' } : undefined,
        body: options ? JSON.stringify(options) : undefined,
      }
    ),
  uploadArchive: async (file: File, printerId?: number): Promise<Archive> => {
    const formData = new FormData();
    formData.append('file', file);
    const url = printerId
      ? `${API_BASE}/archives/upload?printer_id=${printerId}`
      : `${API_BASE}/archives/upload`;
    const headers: Record<string, string> = {};
    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }
    const response = await fetch(url, {
      method: 'POST',
      headers,
      body: formData,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    return response.json();
  },
  uploadArchivesBulk: async (files: File[], printerId?: number): Promise<BulkUploadResult> => {
    const formData = new FormData();
    files.forEach((file) => formData.append('files', file));
    const url = printerId
      ? `${API_BASE}/archives/upload-bulk?printer_id=${printerId}`
      : `${API_BASE}/archives/upload-bulk`;
    const headers: Record<string, string> = {};
    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }
    const response = await fetch(url, {
      method: 'POST',
      headers,
      body: formData,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    return response.json();
  },

  // Settings
  getSettings: () => request<AppSettings>('/settings/'),
  updateSettings: (data: AppSettingsUpdate) =>
    request<AppSettings>('/settings/', {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  getMQTTStatus: () => request<MQTTStatus>('/settings/mqtt/status'),
  resetSettings: () =>
    request<AppSettings>('/settings/reset', { method: 'POST' }),
  exportBackup: async (): Promise<{ blob: Blob; filename: string }> => {
    // New simplified backup - complete database + all files
    const url = `${API_BASE}/settings/backup`;
    const headers: Record<string, string> = {};
    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }
    const response = await fetch(url, { headers });

    // Check for errors
    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(errorText || `Backup failed with status ${response.status}`);
    }

    // Get filename from Content-Disposition header
    const contentDisposition = response.headers.get('Content-Disposition');
    let filename = 'bambuddy-backup.zip';
    if (contentDisposition) {
      const match = contentDisposition.match(/filename=([^;]+)/);
      if (match) filename = match[1].trim();
    }

    const blob = await response.blob();
    return { blob, filename };
  },
  importBackup: async (file: File) => {
    // New simplified restore - replaces database + all directories
    const formData = new FormData();
    formData.append('file', file);
    const url = `${API_BASE}/settings/restore`;
    const headers: Record<string, string> = {};
    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }
    const response = await fetch(url, {
      method: 'POST',
      headers,
      body: formData,
    });
    return response.json() as Promise<{
      success: boolean;
      message: string;
    }>;
  },
  checkFfmpeg: () =>
    request<{ installed: boolean; path: string | null }>('/settings/check-ffmpeg'),
  getNetworkInterfaces: () =>
    request<{ interfaces: NetworkInterface[] }>('/settings/network-interfaces'),

  // Cloud
  getCloudStatus: () => request<CloudAuthStatus>('/cloud/status'),
  cloudLogin: (email: string, password: string, region = 'global') =>
    request<CloudLoginResponse>('/cloud/login', {
      method: 'POST',
      body: JSON.stringify({ email, password, region }),
    }),
  cloudVerify: (email: string, code: string, tfaKey?: string) =>
    request<CloudLoginResponse>('/cloud/verify', {
      method: 'POST',
      body: JSON.stringify({ email, code, tfa_key: tfaKey }),
    }),
  cloudSetToken: (access_token: string) =>
    request<CloudAuthStatus>('/cloud/token', {
      method: 'POST',
      body: JSON.stringify({ access_token }),
    }),
  cloudLogout: () =>
    request<{ success: boolean }>('/cloud/logout', { method: 'POST' }),
  getCloudSettings: (version = '02.04.00.70') =>
    request<SlicerSettingsResponse>(`/cloud/settings?version=${version}`),
  getBuiltinFilaments: () =>
    request<BuiltinFilament[]>('/cloud/builtin-filaments'),
  getFilamentIdMap: () =>
    request<Record<string, string>>('/cloud/filament-id-map'),
  getCloudSettingDetail: (settingId: string) =>
    request<SlicerSettingDetail>(`/cloud/settings/${settingId}`),
  createCloudSetting: (data: SlicerSettingCreate) =>
    request<SlicerSettingDetail>('/cloud/settings', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateCloudSetting: (settingId: string, data: SlicerSettingUpdate) =>
    request<SlicerSettingDetail>(`/cloud/settings/${settingId}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  deleteCloudSetting: (settingId: string) =>
    request<SlicerSettingDeleteResponse>(`/cloud/settings/${settingId}`, {
      method: 'DELETE',
    }),
  getCloudDevices: () => request<CloudDevice[]>('/cloud/devices'),
  getCloudFields: (presetType: 'filament' | 'print' | 'process' | 'printer') =>
    request<FieldDefinitionsResponse>(`/cloud/fields/${presetType}`),
  getAllCloudFields: () =>
    request<Record<string, FieldDefinitionsResponse>>('/cloud/fields'),
  getFilamentInfo: (settingIds: string[]) =>
    request<Record<string, { name: string; k: number | null }>>('/cloud/filament-info', {
      method: 'POST',
      body: JSON.stringify(settingIds),
    }),

  // Smart Plugs
  getSmartPlugs: () => request<SmartPlug[]>('/smart-plugs/'),
  getSmartPlug: (id: number) => request<SmartPlug>(`/smart-plugs/${id}`),
  getSmartPlugByPrinter: (printerId: number) => request<SmartPlug | null>(`/smart-plugs/by-printer/${printerId}`),
  getScriptPlugsByPrinter: (printerId: number) => request<SmartPlug[]>(`/smart-plugs/by-printer/${printerId}/scripts`),
  createSmartPlug: (data: SmartPlugCreate) =>
    request<SmartPlug>('/smart-plugs/', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateSmartPlug: (id: number, data: SmartPlugUpdate) =>
    request<SmartPlug>(`/smart-plugs/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  deleteSmartPlug: (id: number) =>
    request<void>(`/smart-plugs/${id}`, { method: 'DELETE' }),
  controlSmartPlug: (id: number, action: 'on' | 'off' | 'toggle') =>
    request<{ success: boolean; action: string }>(`/smart-plugs/${id}/control`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    }),
  getSmartPlugStatus: (id: number) =>
    request<SmartPlugStatus>(`/smart-plugs/${id}/status`),
  testSmartPlugConnection: (ip_address: string, username?: string | null, password?: string | null) =>
    request<SmartPlugTestResult>('/smart-plugs/test-connection', {
      method: 'POST',
      body: JSON.stringify({ ip_address, username, password }),
    }),

  // Tasmota Discovery (auto-detects network)
  startTasmotaScan: () =>
    request<TasmotaScanStatus>('/smart-plugs/discover/scan', { method: 'POST' }),
  getTasmotaScanStatus: () =>
    request<TasmotaScanStatus>('/smart-plugs/discover/status'),
  stopTasmotaScan: () =>
    request<TasmotaScanStatus>('/smart-plugs/discover/stop', { method: 'POST' }),
  getDiscoveredTasmotaDevices: () =>
    request<DiscoveredTasmotaDevice[]>('/smart-plugs/discover/devices'),

  // Home Assistant Integration
  testHAConnection: (url: string, token: string) =>
    request<HATestConnectionResult>('/smart-plugs/ha/test-connection', {
      method: 'POST',
      body: JSON.stringify({ url, token }),
    }),
  getHAEntities: (search?: string) => {
    const params = search ? `?search=${encodeURIComponent(search)}` : '';
    return request<HAEntity[]>(`/smart-plugs/ha/entities${params}`);
  },
  getHASensorEntities: () =>
    request<HASensorEntity[]>('/smart-plugs/ha/sensors'),

  // Print Queue
  getQueue: (printerId?: number, status?: string) => {
    const params = new URLSearchParams();
    if (printerId) params.set('printer_id', String(printerId));
    if (status) params.set('status', status);
    return request<PrintQueueItem[]>(`/queue/?${params}`);
  },
  getQueueItem: (id: number) => request<PrintQueueItem>(`/queue/${id}`),
  addToQueue: (data: PrintQueueItemCreate) =>
    request<PrintQueueItem>('/queue/', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateQueueItem: (id: number, data: PrintQueueItemUpdate) =>
    request<PrintQueueItem>(`/queue/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  removeFromQueue: (id: number) =>
    request<{ message: string }>(`/queue/${id}`, { method: 'DELETE' }),
  reorderQueue: (items: { id: number; position: number }[]) =>
    request<{ message: string }>('/queue/reorder', {
      method: 'POST',
      body: JSON.stringify({ items }),
    }),
  cancelQueueItem: (id: number) =>
    request<{ message: string }>(`/queue/${id}/cancel`, { method: 'POST' }),
  stopQueueItem: (id: number) =>
    request<{ message: string }>(`/queue/${id}/stop`, { method: 'POST' }),
  startQueueItem: (id: number) =>
    request<PrintQueueItem>(`/queue/${id}/start`, { method: 'POST' }),
  bulkUpdateQueue: (data: PrintQueueBulkUpdate) =>
    request<PrintQueueBulkUpdateResponse>('/queue/bulk', {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),

  // K-Profiles
  getKProfiles: (printerId: number, nozzleDiameter = '0.4') =>
    request<KProfilesResponse>(`/printers/${printerId}/kprofiles/?nozzle_diameter=${nozzleDiameter}`),
  setKProfile: (printerId: number, profile: KProfileCreate) =>
    request<{ success: boolean; message: string }>(`/printers/${printerId}/kprofiles/`, {
      method: 'POST',
      body: JSON.stringify(profile),
    }),
  deleteKProfile: (printerId: number, profile: KProfileDelete) =>
    request<{ success: boolean; message: string }>(`/printers/${printerId}/kprofiles/`, {
      method: 'DELETE',
      body: JSON.stringify(profile),
    }),
  setKProfilesBatch: (printerId: number, profiles: KProfileCreate[]) =>
    request<{ success: boolean; message: string }>(`/printers/${printerId}/kprofiles/batch`, {
      method: 'POST',
      body: JSON.stringify(profiles),
    }),

  // K-Profile Notes (stored locally, not on printer)
  getKProfileNotes: (printerId: number) =>
    request<KProfileNotesResponse>(`/printers/${printerId}/kprofiles/notes`),
  setKProfileNote: (printerId: number, settingId: string, note: string) =>
    request<{ success: boolean; message: string }>(`/printers/${printerId}/kprofiles/notes`, {
      method: 'PUT',
      body: JSON.stringify({ setting_id: settingId, note }),
    }),
  deleteKProfileNote: (printerId: number, settingId: string) =>
    request<{ success: boolean; message: string }>(`/printers/${printerId}/kprofiles/notes/${encodeURIComponent(settingId)}`, {
      method: 'DELETE',
    }),

  // Slot Preset Mappings
  getSlotPresets: (printerId: number) =>
    request<Record<number, SlotPresetMapping>>(`/printers/${printerId}/slot-presets`),
  getSlotPreset: (printerId: number, amsId: number, trayId: number) =>
    request<SlotPresetMapping | null>(`/printers/${printerId}/slot-presets/${amsId}/${trayId}`),
  saveSlotPreset: (printerId: number, amsId: number, trayId: number, presetId: string, presetName: string, presetSource = 'cloud') =>
    request<SlotPresetMapping>(`/printers/${printerId}/slot-presets/${amsId}/${trayId}?preset_id=${encodeURIComponent(presetId)}&preset_name=${encodeURIComponent(presetName)}&preset_source=${encodeURIComponent(presetSource)}`, {
      method: 'PUT',
    }),
  deleteSlotPreset: (printerId: number, amsId: number, trayId: number) =>
    request<{ success: boolean }>(`/printers/${printerId}/slot-presets/${amsId}/${trayId}`, {
      method: 'DELETE',
    }),
  configureAmsSlot: (
    printerId: number,
    amsId: number,
    trayId: number,
    config: {
      tray_info_idx: string;
      tray_type: string;
      tray_sub_brands: string;
      tray_color: string;
      nozzle_temp_min: number;
      nozzle_temp_max: number;
      cali_idx: number;
      nozzle_diameter: string;
      setting_id?: string;
      kprofile_filament_id?: string;
      kprofile_setting_id?: string;
      k_value?: number;
    }
  ) => {
    const params = new URLSearchParams({
      tray_info_idx: config.tray_info_idx,
      tray_type: config.tray_type,
      tray_sub_brands: config.tray_sub_brands,
      tray_color: config.tray_color,
      nozzle_temp_min: config.nozzle_temp_min.toString(),
      nozzle_temp_max: config.nozzle_temp_max.toString(),
      cali_idx: config.cali_idx.toString(),
      nozzle_diameter: config.nozzle_diameter,
    });
    if (config.setting_id) {
      params.set('setting_id', config.setting_id);
    }
    if (config.kprofile_filament_id) {
      params.set('kprofile_filament_id', config.kprofile_filament_id);
    }
    if (config.kprofile_setting_id) {
      params.set('kprofile_setting_id', config.kprofile_setting_id);
    }
    if (config.k_value !== undefined && config.k_value > 0) {
      params.set('k_value', config.k_value.toString());
    }
    return request<{ success: boolean; message: string }>(
      `/printers/${printerId}/slots/${amsId}/${trayId}/configure?${params}`,
      { method: 'POST' }
    );
  },
  resetAmsSlot: (printerId: number, amsId: number, trayId: number) =>
    request<{ success: boolean; message: string }>(
      `/printers/${printerId}/ams/${amsId}/tray/${trayId}/reset`,
      { method: 'POST' }
    ),

  // Filaments
  listFilaments: () => request<Filament[]>('/filaments/'),
  getFilament: (id: number) => request<Filament>(`/filaments/${id}`),
  getFilamentsByType: (type: string) => request<Filament[]>(`/filaments/by-type/${type}`),

  // Notification Providers
  getNotificationProviders: () => request<NotificationProvider[]>('/notifications/'),
  getNotificationProvider: (id: number) => request<NotificationProvider>(`/notifications/${id}`),
  createNotificationProvider: (data: NotificationProviderCreate) =>
    request<NotificationProvider>('/notifications/', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateNotificationProvider: (id: number, data: NotificationProviderUpdate) =>
    request<NotificationProvider>(`/notifications/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  deleteNotificationProvider: (id: number) =>
    request<{ message: string }>(`/notifications/${id}`, { method: 'DELETE' }),
  testNotificationProvider: (id: number) =>
    request<NotificationTestResponse>(`/notifications/${id}/test`, { method: 'POST' }),
  testNotificationConfig: (data: NotificationTestRequest) =>
    request<NotificationTestResponse>('/notifications/test-config', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  testAllNotificationProviders: () =>
    request<{
      tested: number;
      success: number;
      failed: number;
      results: Array<{
        provider_id: number;
        provider_name: string;
        provider_type: string;
        success: boolean;
        message: string;
      }>;
    }>('/notifications/test-all', { method: 'POST' }),

  // Notification Templates
  getNotificationTemplates: () => request<NotificationTemplate[]>('/notification-templates'),
  getNotificationTemplate: (id: number) => request<NotificationTemplate>(`/notification-templates/${id}`),
  updateNotificationTemplate: (id: number, data: NotificationTemplateUpdate) =>
    request<NotificationTemplate>(`/notification-templates/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  resetNotificationTemplate: (id: number) =>
    request<NotificationTemplate>(`/notification-templates/${id}/reset`, {
      method: 'POST',
    }),
  getTemplateVariables: () => request<EventVariablesResponse[]>('/notification-templates/variables'),
  previewTemplate: (data: TemplatePreviewRequest) =>
    request<TemplatePreviewResponse>('/notification-templates/preview', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  // Notification Logs
  getNotificationLogs: (params?: {
    limit?: number;
    offset?: number;
    provider_id?: number;
    event_type?: string;
    success?: boolean;
    days?: number;
  }) => {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.offset) searchParams.set('offset', String(params.offset));
    if (params?.provider_id) searchParams.set('provider_id', String(params.provider_id));
    if (params?.event_type) searchParams.set('event_type', params.event_type);
    if (params?.success !== undefined) searchParams.set('success', String(params.success));
    if (params?.days) searchParams.set('days', String(params.days));
    return request<NotificationLogEntry[]>(`/notifications/logs?${searchParams}`);
  },
  getNotificationLogStats: (days = 7) =>
    request<NotificationLogStats>(`/notifications/logs/stats?days=${days}`),
  clearNotificationLogs: (olderThanDays = 30) =>
    request<{ deleted: number; message: string }>(
      `/notifications/logs?older_than_days=${olderThanDays}`,
      { method: 'DELETE' }
    ),

  // Spoolman Integration
  getSpoolmanStatus: () => request<SpoolmanStatus>('/spoolman/status'),
  connectSpoolman: () =>
    request<{ success: boolean; message: string }>('/spoolman/connect', {
      method: 'POST',
    }),
  disconnectSpoolman: () =>
    request<{ success: boolean; message: string }>('/spoolman/disconnect', {
      method: 'POST',
    }),
  syncPrinterAms: (printerId: number) =>
    request<SpoolmanSyncResult>(`/spoolman/sync/${printerId}`, {
      method: 'POST',
    }),
  syncAllPrintersAms: () =>
    request<SpoolmanSyncResult>('/spoolman/sync-all', {
      method: 'POST',
    }),
  getSpoolmanSpools: () =>
    request<{ spools: unknown[] }>('/spoolman/spools'),
  getSpoolmanFilaments: () =>
    request<{ filaments: unknown[] }>('/spoolman/filaments'),
  getUnlinkedSpools: () =>
    request<UnlinkedSpool[]>('/spoolman/spools/unlinked'),
  getLinkedSpools: () =>
    request<LinkedSpoolsMap>('/spoolman/spools/linked'),
  linkSpool: (spoolId: number, trayUuid: string) =>
    request<{ success: boolean; message: string }>(`/spoolman/spools/${spoolId}/link`, {
      method: 'POST',
      body: JSON.stringify({ tray_uuid: trayUuid }),
    }),
  getSpoolmanSettings: () =>
    request<{ spoolman_enabled: string; spoolman_url: string; spoolman_sync_mode: string; spoolman_disable_weight_sync: string; spoolman_report_partial_usage: string; }>('/settings/spoolman'),
  updateSpoolmanSettings: (data: { spoolman_enabled?: string; spoolman_url?: string; spoolman_sync_mode?: string; spoolman_disable_weight_sync?: string; spoolman_report_partial_usage?: string; }) =>
    request<{ spoolman_enabled: string; spoolman_url: string; spoolman_sync_mode: string; spoolman_disable_weight_sync: string; spoolman_report_partial_usage: string; }>('/settings/spoolman', {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  // Inventory
  getSpools: (includeArchived = false) =>
    request<InventorySpool[]>(`/inventory/spools?include_archived=${includeArchived}`),
  getSpool: (id: number) => request<InventorySpool>(`/inventory/spools/${id}`),
  createSpool: (data: Omit<InventorySpool, 'id' | 'archived_at' | 'created_at' | 'updated_at' | 'k_profiles'>) =>
    request<InventorySpool>('/inventory/spools', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateSpool: (id: number, data: Partial<Omit<InventorySpool, 'id' | 'archived_at' | 'created_at' | 'updated_at' | 'k_profiles'>>) =>
    request<InventorySpool>(`/inventory/spools/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  deleteSpool: (id: number) =>
    request<{ status: string }>(`/inventory/spools/${id}`, { method: 'DELETE' }),
  archiveSpool: (id: number) =>
    request<InventorySpool>(`/inventory/spools/${id}/archive`, { method: 'POST' }),
  restoreSpool: (id: number) =>
    request<InventorySpool>(`/inventory/spools/${id}/restore`, { method: 'POST' }),
  getSpoolKProfiles: (spoolId: number) =>
    request<SpoolKProfile[]>(`/inventory/spools/${spoolId}/k-profiles`),
  saveSpoolKProfiles: (spoolId: number, profiles: SpoolKProfileInput[]) =>
    request<SpoolKProfile[]>(`/inventory/spools/${spoolId}/k-profiles`, {
      method: 'PUT',
      body: JSON.stringify(profiles),
    }),
  getAssignments: (printerId?: number) =>
    request<SpoolAssignment[]>(`/inventory/assignments${printerId ? `?printer_id=${printerId}` : ''}`),
  assignSpool: (data: { spool_id: number; printer_id: number; ams_id: number; tray_id: number }) =>
    request<SpoolAssignment>('/inventory/assignments', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  unassignSpool: (printerId: number, amsId: number, trayId: number) =>
    request<{ status: string }>(`/inventory/assignments/${printerId}/${amsId}/${trayId}`, { method: 'DELETE' }),
  getSpoolCatalog: () =>
    request<SpoolCatalogEntry[]>('/inventory/catalog'),
  addCatalogEntry: (data: { name: string; weight: number }) =>
    request<SpoolCatalogEntry>('/inventory/catalog', { method: 'POST', body: JSON.stringify(data) }),
  updateCatalogEntry: (id: number, data: { name: string; weight: number }) =>
    request<SpoolCatalogEntry>(`/inventory/catalog/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteCatalogEntry: (id: number) =>
    request<{ status: string }>(`/inventory/catalog/${id}`, { method: 'DELETE' }),
  resetSpoolCatalog: () =>
    request<{ status: string }>('/inventory/catalog/reset', { method: 'POST' }),
  getColorCatalog: () =>
    request<ColorCatalogEntry[]>('/inventory/colors'),
  addColorEntry: (data: { manufacturer: string; color_name: string; hex_color: string; material: string | null }) =>
    request<ColorCatalogEntry>('/inventory/colors', { method: 'POST', body: JSON.stringify(data) }),
  updateColorEntry: (id: number, data: { manufacturer: string; color_name: string; hex_color: string; material: string | null }) =>
    request<ColorCatalogEntry>(`/inventory/colors/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteColorEntry: (id: number) =>
    request<{ status: string }>(`/inventory/colors/${id}`, { method: 'DELETE' }),
  resetColorCatalog: () =>
    request<{ status: string }>('/inventory/colors/reset', { method: 'POST' }),
  lookupColor: (manufacturer: string, colorName: string, material?: string) =>
    request<ColorLookupResult>(`/inventory/colors/lookup?manufacturer=${encodeURIComponent(manufacturer)}&color_name=${encodeURIComponent(colorName)}${material ? `&material=${encodeURIComponent(material)}` : ''}`),
  searchColors: (manufacturer?: string, material?: string) =>
    request<ColorCatalogEntry[]>(`/inventory/colors/search?${manufacturer ? `manufacturer=${encodeURIComponent(manufacturer)}` : ''}${manufacturer && material ? '&' : ''}${material ? `material=${encodeURIComponent(material)}` : ''}`),
  linkTagToSpool: (spoolId: number, data: { tag_uid?: string; tray_uuid?: string; tag_type?: string; data_origin?: string }) =>
    request<InventorySpool>(`/inventory/spools/${spoolId}/link-tag`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  getSpoolUsageHistory: (spoolId: number, limit = 50) =>
    request<SpoolUsageRecord[]>(`/inventory/spools/${spoolId}/usage?limit=${limit}`),
  getAllUsageHistory: (limit = 100, printerId?: number) =>
    request<SpoolUsageRecord[]>(`/inventory/usage?limit=${limit}${printerId ? `&printer_id=${printerId}` : ''}`),
  clearSpoolUsageHistory: (spoolId: number) =>
    request<{ status: string }>(`/inventory/spools/${spoolId}/usage`, { method: 'DELETE' }),
  getFilamentPresets: () =>
    request<SlicerSetting[]>('/cloud/filaments'),

  // Updates
  getVersion: () => request<VersionInfo>('/updates/version'),
  checkForUpdates: () => request<UpdateCheckResult>('/updates/check'),
  applyUpdate: () =>
    request<{ success: boolean; message: string; status?: UpdateStatus; is_docker?: boolean }>('/updates/apply', {
      method: 'POST',
    }),
  getUpdateStatus: () => request<UpdateStatus>('/updates/status'),

  // Maintenance
  getMaintenanceTypes: () => request<MaintenanceType[]>('/maintenance/types'),
  createMaintenanceType: (data: MaintenanceTypeCreate) =>
    request<MaintenanceType>('/maintenance/types', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateMaintenanceType: (id: number, data: Partial<MaintenanceTypeCreate>) =>
    request<MaintenanceType>(`/maintenance/types/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  deleteMaintenanceType: (id: number) =>
    request<{ status: string }>(`/maintenance/types/${id}`, { method: 'DELETE' }),
  restoreDefaultMaintenanceTypes: () =>
    request<{ restored: number }>(`/maintenance/types/restore-defaults`, { method: 'POST' }),
  getMaintenanceOverview: () => request<PrinterMaintenanceOverview[]>('/maintenance/overview'),
  getPrinterMaintenance: (printerId: number) =>
    request<PrinterMaintenanceOverview>(`/maintenance/printers/${printerId}`),
  updateMaintenanceItem: (itemId: number, data: { custom_interval_hours?: number | null; custom_interval_type?: 'hours' | 'days' | null; enabled?: boolean }) =>
    request<MaintenanceStatus>(`/maintenance/items/${itemId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  performMaintenance: (itemId: number, notes?: string) =>
    request<MaintenanceStatus>(`/maintenance/items/${itemId}/perform`, {
      method: 'POST',
      body: JSON.stringify({ notes }),
    }),
  getMaintenanceHistory: (itemId: number) =>
    request<MaintenanceHistory[]>(`/maintenance/items/${itemId}/history`),
  getMaintenanceSummary: () => request<MaintenanceSummary>('/maintenance/summary'),
  setPrinterHours: (printerId: number, totalHours: number) =>
    request<{ printer_id: number; total_hours: number; archive_hours: number; offset_hours: number }>(
      `/maintenance/printers/${printerId}/hours?total_hours=${totalHours}`,
      { method: 'PATCH' }
    ),
  assignMaintenanceType: (printerId: number, typeId: number) =>
    request<MaintenanceStatus>(`/maintenance/printers/${printerId}/assign/${typeId}`, {
      method: 'POST',
    }),
  removeMaintenanceItem: (itemId: number) =>
    request<{ status: string }>(`/maintenance/items/${itemId}`, {
      method: 'DELETE',
    }),

  // Camera
  getCameraStreamUrl: (printerId: number, fps = 10) =>
    `${API_BASE}/printers/${printerId}/camera/stream?fps=${fps}`,
  getCameraSnapshotUrl: (printerId: number) =>
    `${API_BASE}/printers/${printerId}/camera/snapshot`,
  testCameraConnection: (printerId: number) =>
    request<{ success: boolean; message?: string; error?: string }>(`/printers/${printerId}/camera/test`),
  getCameraStatus: (printerId: number) =>
    request<{ active: boolean; stalled: boolean }>(`/printers/${printerId}/camera/status`),

  // Plate Detection - Multi-reference calibration (stores up to 5 references per printer)
  checkPlateEmpty: (printerId: number, options?: { useExternal?: boolean; includeDebugImage?: boolean }) => {
    const params = new URLSearchParams();
    params.set('use_external', String(options?.useExternal ?? false));
    params.set('include_debug_image', String(options?.includeDebugImage ?? false));
    return request<PlateDetectionResult>(
      `/printers/${printerId}/camera/check-plate?${params.toString()}`
    );
  },
  getPlateDetectionStatus: (printerId: number) => {
    return request<PlateDetectionStatus & { chamber_light?: boolean }>(
      `/printers/${printerId}/camera/plate-detection/status`
    );
  },
  calibratePlateDetection: (printerId: number, options?: { label?: string; useExternal?: boolean }) => {
    const params = new URLSearchParams();
    if (options?.label) params.set('label', options.label);
    params.set('use_external', String(options?.useExternal ?? false));
    return request<CalibrationResult & { index: number }>(
      `/printers/${printerId}/camera/plate-detection/calibrate?${params.toString()}`,
      { method: 'POST' }
    );
  },
  deletePlateCalibration: (printerId: number) => {
    return request<CalibrationResult>(
      `/printers/${printerId}/camera/plate-detection/calibrate`,
      { method: 'DELETE' }
    );
  },
  getPlateReferences: (printerId: number) => {
    return request<{
      references: PlateReference[];
      max_references: number;
    }>(`/printers/${printerId}/camera/plate-detection/references`);
  },
  getPlateReferenceThumbnailUrl: (printerId: number, index: number) => {
    return `${API_BASE}/printers/${printerId}/camera/plate-detection/references/${index}/thumbnail`;
  },
  updatePlateReferenceLabel: (printerId: number, index: number, label: string) => {
    const params = new URLSearchParams();
    params.set('label', label);
    return request<{ success: boolean; index: number; label: string }>(
      `/printers/${printerId}/camera/plate-detection/references/${index}?${params.toString()}`,
      { method: 'PUT' }
    );
  },
  deletePlateReference: (printerId: number, index: number) => {
    return request<{ success: boolean; message: string }>(
      `/printers/${printerId}/camera/plate-detection/references/${index}`,
      { method: 'DELETE' }
    );
  },

  // External Links
  getExternalLinks: () => request<ExternalLink[]>('/external-links/'),
  getExternalLink: (id: number) => request<ExternalLink>(`/external-links/${id}`),
  createExternalLink: (data: ExternalLinkCreate) =>
    request<ExternalLink>('/external-links/', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateExternalLink: (id: number, data: ExternalLinkUpdate) =>
    request<ExternalLink>(`/external-links/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  deleteExternalLink: (id: number) =>
    request<{ message: string }>(`/external-links/${id}`, { method: 'DELETE' }),
  reorderExternalLinks: (ids: number[]) =>
    request<ExternalLink[]>('/external-links/reorder', {
      method: 'PUT',
      body: JSON.stringify({ ids }),
    }),
  uploadExternalLinkIcon: async (id: number, file: File): Promise<ExternalLink> => {
    const formData = new FormData();
    formData.append('file', file);
    const headers: Record<string, string> = {};
    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }
    const response = await fetch(`${API_BASE}/external-links/${id}/icon`, {
      method: 'POST',
      headers,
      body: formData,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    return response.json();
  },
  deleteExternalLinkIcon: (id: number) =>
    request<ExternalLink>(`/external-links/${id}/icon`, { method: 'DELETE' }),
  getExternalLinkIconUrl: (id: number) => `${API_BASE}/external-links/${id}/icon`,

  // Projects
  getProjects: (status?: string) => {
    const params = new URLSearchParams();
    if (status) params.set('status', status);
    return request<ProjectListItem[]>(`/projects/?${params}`);
  },
  getProject: (id: number) => request<Project>(`/projects/${id}`),
  createProject: (data: ProjectCreate) =>
    request<Project>('/projects/', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateProject: (id: number, data: ProjectUpdate) =>
    request<Project>(`/projects/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  deleteProject: (id: number) =>
    request<{ message: string }>(`/projects/${id}`, { method: 'DELETE' }),
  getProjectArchives: (id: number, limit = 100, offset = 0) =>
    request<Archive[]>(`/projects/${id}/archives?limit=${limit}&offset=${offset}`),
  addArchivesToProject: (projectId: number, archiveIds: number[]) =>
    request<{ message: string }>(`/projects/${projectId}/add-archives`, {
      method: 'POST',
      body: JSON.stringify({ archive_ids: archiveIds }),
    }),
  removeArchivesFromProject: (projectId: number, archiveIds: number[]) =>
    request<{ message: string }>(`/projects/${projectId}/remove-archives`, {
      method: 'POST',
      body: JSON.stringify({ archive_ids: archiveIds }),
    }),
  addQueueItemsToProject: (projectId: number, queueItemIds: number[]) =>
    request<{ message: string }>(`/projects/${projectId}/add-queue`, {
      method: 'POST',
      body: JSON.stringify({ queue_item_ids: queueItemIds }),
    }),

  // Project Attachments
  uploadProjectAttachment: async (projectId: number, file: File): Promise<{
    status: string;
    filename: string;
    original_name: string;
    attachments: ProjectAttachment[];
  }> => {
    const formData = new FormData();
    formData.append('file', file);
    const headers: Record<string, string> = {};
    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }
    const response = await fetch(`${API_BASE}/projects/${projectId}/attachments`, {
      method: 'POST',
      headers,
      body: formData,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    return response.json();
  },
  getProjectAttachmentUrl: (projectId: number, filename: string) =>
    `${API_BASE}/projects/${projectId}/attachments/${encodeURIComponent(filename)}`,
  deleteProjectAttachment: (projectId: number, filename: string) =>
    request<{ status: string; message: string; attachments: ProjectAttachment[] | null }>(
      `/projects/${projectId}/attachments/${encodeURIComponent(filename)}`,
      { method: 'DELETE' }
    ),

  // BOM (Bill of Materials)
  getProjectBOM: (projectId: number) =>
    request<BOMItem[]>(`/projects/${projectId}/bom`),
  createBOMItem: (projectId: number, data: BOMItemCreate) =>
    request<BOMItem>(`/projects/${projectId}/bom`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateBOMItem: (projectId: number, itemId: number, data: BOMItemUpdate) =>
    request<BOMItem>(`/projects/${projectId}/bom/${itemId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  deleteBOMItem: (projectId: number, itemId: number) =>
    request<{ status: string; message: string }>(`/projects/${projectId}/bom/${itemId}`, {
      method: 'DELETE',
    }),

  // Templates
  getTemplates: () => request<ProjectListItem[]>('/projects/templates/'),
  createTemplateFromProject: (projectId: number) =>
    request<Project>(`/projects/${projectId}/create-template`, { method: 'POST' }),
  createProjectFromTemplate: (templateId: number, name?: string) =>
    request<Project>(`/projects/from-template/${templateId}${name ? `?name=${encodeURIComponent(name)}` : ''}`, {
      method: 'POST',
    }),

  // Timeline
  getProjectTimeline: (projectId: number, limit = 50) =>
    request<TimelineEvent[]>(`/projects/${projectId}/timeline?limit=${limit}`),

  // Project Export/Import
  exportProjectJson: (projectId: number) =>
    request<ProjectExport>(`/projects/${projectId}/export?format=json`),
  importProject: (data: ProjectImport) =>
    request<Project>('/projects/import', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  importProjectFile: async (file: File): Promise<Project> => {
    const formData = new FormData();
    formData.append('file', file);
    const headers: Record<string, string> = {};
    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }
    const response = await fetch(`${API_BASE}/projects/import/file`, {
      method: 'POST',
      headers,
      body: formData,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    return response.json();
  },
  exportProjectZip: async (projectId: number): Promise<{ blob: Blob; filename: string }> => {
    const headers: Record<string, string> = {};
    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }
    const response = await fetch(`${API_BASE}/projects/${projectId}/export`, {
      headers,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    const contentDisposition = response.headers.get('Content-Disposition');
    const filename = parseContentDispositionFilename(contentDisposition) || `project_${projectId}.zip`;
    const blob = await response.blob();
    return { blob, filename };
  },

  // API Keys
  getAPIKeys: () => request<APIKey[]>('/api-keys/'),
  createAPIKey: (data: APIKeyCreate) =>
    request<APIKeyCreateResponse>('/api-keys/', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateAPIKey: (id: number, data: APIKeyUpdate) =>
    request<APIKey>(`/api-keys/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  deleteAPIKey: (id: number) =>
    request<{ message: string }>(`/api-keys/${id}`, { method: 'DELETE' }),

  // AMS History
  getAMSHistory: (printerId: number, amsId: number, hours = 24) =>
    request<AMSHistoryResponse>(`/ams-history/${printerId}/${amsId}?hours=${hours}`),

  // System Info
  getSystemInfo: () => request<SystemInfo>('/system/info'),

  // Library (File Manager)
  getLibraryFolders: () => request<LibraryFolderTree[]>('/library/folders'),
  createLibraryFolder: (data: LibraryFolderCreate) =>
    request<LibraryFolder>('/library/folders', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateLibraryFolder: (id: number, data: LibraryFolderUpdate) =>
    request<LibraryFolder>(`/library/folders/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  deleteLibraryFolder: (id: number) =>
    request<{ status: string; message: string }>(`/library/folders/${id}`, { method: 'DELETE' }),
  getLibraryFoldersByProject: (projectId: number) =>
    request<LibraryFolder[]>(`/library/folders/by-project/${projectId}`),
  getLibraryFoldersByArchive: (archiveId: number) =>
    request<LibraryFolder[]>(`/library/folders/by-archive/${archiveId}`),

  getLibraryFiles: (folderId?: number | null, includeRoot = true) => {
    const params = new URLSearchParams();
    if (folderId !== undefined && folderId !== null) {
      params.set('folder_id', String(folderId));
    }
    params.set('include_root', String(includeRoot));
    return request<LibraryFileListItem[]>(`/library/files?${params}`);
  },
  getLibraryFile: (id: number) => request<LibraryFile>(`/library/files/${id}`),
  uploadLibraryFile: async (
    file: File,
    folderId?: number | null,
    generateStlThumbnails: boolean = true
  ): Promise<LibraryFileUploadResponse> => {
    const formData = new FormData();
    formData.append('file', file);
    const params = new URLSearchParams();
    if (folderId) params.set('folder_id', String(folderId));
    params.set('generate_stl_thumbnails', String(generateStlThumbnails));
    const headers: Record<string, string> = {};
    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }
    const response = await fetch(`${API_BASE}/library/files?${params}`, {
      method: 'POST',
      headers,
      body: formData,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    return response.json();
  },
  extractZipFile: async (
    file: File,
    folderId?: number | null,
    preserveStructure: boolean = true,
    createFolderFromZip: boolean = false,
    generateStlThumbnails: boolean = true
  ): Promise<ZipExtractResponse> => {
    const formData = new FormData();
    formData.append('file', file);
    const params = new URLSearchParams();
    if (folderId) params.set('folder_id', String(folderId));
    params.set('preserve_structure', String(preserveStructure));
    params.set('create_folder_from_zip', String(createFolderFromZip));
    params.set('generate_stl_thumbnails', String(generateStlThumbnails));
    const headers: Record<string, string> = {};
    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }
    const response = await fetch(`${API_BASE}/library/files/extract-zip?${params}`, {
      method: 'POST',
      headers,
      body: formData,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    return response.json();
  },
  updateLibraryFile: (id: number, data: LibraryFileUpdate) =>
    request<LibraryFile>(`/library/files/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  deleteLibraryFile: (id: number) =>
    request<{ status: string; message: string }>(`/library/files/${id}`, { method: 'DELETE' }),
  getLibraryFileDownloadUrl: (id: number) => `${API_BASE}/library/files/${id}/download`,
  downloadLibraryFile: async (id: number, filename?: string): Promise<void> => {
    const headers: Record<string, string> = {};
    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }
    const response = await fetch(`${API_BASE}/library/files/${id}/download`, { headers });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    const disposition = response.headers.get('Content-Disposition');
    const downloadFilename = parseContentDispositionFilename(disposition) || filename || `file_${id}`;
    const blob = await response.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = downloadFilename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.URL.revokeObjectURL(url);
  },
  getLibraryFileThumbnailUrl: (id: number) => `${API_BASE}/library/files/${id}/thumbnail`,
  getLibraryFilePlateThumbnail: (id: number, plateIndex: number) =>
    `${API_BASE}/library/files/${id}/plate-thumbnail/${plateIndex}`,
  getLibraryFileGcodeUrl: (id: number) => `${API_BASE}/library/files/${id}/gcode`,
  moveLibraryFiles: (fileIds: number[], folderId: number | null) =>
    request<{ status: string; moved: number }>('/library/files/move', {
      method: 'POST',
      body: JSON.stringify({ file_ids: fileIds, folder_id: folderId }),
    }),
  bulkDeleteLibrary: (fileIds: number[], folderIds: number[]) =>
    request<{ deleted_files: number; deleted_folders: number }>('/library/bulk-delete', {
      method: 'POST',
      body: JSON.stringify({ file_ids: fileIds, folder_ids: folderIds }),
    }),
  getLibraryStats: () => request<LibraryStats>('/library/stats'),
  batchGenerateStlThumbnails: (options: {
    file_ids?: number[];
    folder_id?: number;
    all_missing?: boolean;
  }) =>
    request<BatchThumbnailResponse>('/library/generate-stl-thumbnails', {
      method: 'POST',
      body: JSON.stringify(options),
    }),
  addLibraryFilesToQueue: (fileIds: number[]) =>
    request<AddToQueueResponse>('/library/files/add-to-queue', {
      method: 'POST',
      body: JSON.stringify({ file_ids: fileIds }),
    }),
  printLibraryFile: (
    fileId: number,
    printerId: number,
    options?: {
      plate_id?: number;
      ams_mapping?: number[];
      bed_levelling?: boolean;
      flow_cali?: boolean;
      vibration_cali?: boolean;
      layer_inspect?: boolean;
      timelapse?: boolean;
      use_ams?: boolean;
    }
  ) =>
    request<{ status: string; printer_id: number; archive_id: number; filename: string }>(
      `/library/files/${fileId}/print?printer_id=${printerId}`,
      {
        method: 'POST',
        body: options ? JSON.stringify(options) : undefined,
      }
    ),
  getLibraryFilePlates: (fileId: number) =>
    request<LibraryFilePlatesResponse>(`/library/files/${fileId}/plates`),
  getLibraryFileFilamentRequirements: (fileId: number, plateId?: number) =>
    request<{
      file_id: number;
      filename: string;
      filaments: Array<{
        slot_id: number;
        type: string;
        color: string;
        used_grams: number;
        used_meters: number;
      }>;
    }>(`/library/files/${fileId}/filament-requirements${plateId !== undefined ? `?plate_id=${plateId}` : ''}`),

  // GitHub Backup
  getGitHubBackupConfig: () =>
    request<GitHubBackupConfig | null>('/github-backup/config'),

  saveGitHubBackupConfig: (config: GitHubBackupConfigCreate) =>
    request<GitHubBackupConfig>('/github-backup/config', {
      method: 'POST',
      body: JSON.stringify(config),
    }),

  updateGitHubBackupConfig: (config: Partial<GitHubBackupConfigCreate>) =>
    request<GitHubBackupConfig>('/github-backup/config', {
      method: 'PATCH',
      body: JSON.stringify(config),
    }),

  deleteGitHubBackupConfig: () =>
    request<{ message: string }>('/github-backup/config', { method: 'DELETE' }),

  testGitHubConnection: (repoUrl: string, token: string) =>
    request<GitHubTestConnectionResponse>(
      `/github-backup/test?repo_url=${encodeURIComponent(repoUrl)}&token=${encodeURIComponent(token)}`,
      { method: 'POST' }
    ),

  testGitHubStoredConnection: () =>
    request<GitHubTestConnectionResponse>('/github-backup/test-stored', { method: 'POST' }),

  triggerGitHubBackup: () =>
    request<GitHubBackupTriggerResponse>('/github-backup/run', { method: 'POST' }),

  getGitHubBackupStatus: () =>
    request<GitHubBackupStatus>('/github-backup/status'),

  getGitHubBackupLogs: (limit: number = 50) =>
    request<GitHubBackupLog[]>(`/github-backup/logs?limit=${limit}`),

  clearGitHubBackupLogs: (keepLast: number = 10) =>
    request<{ deleted: number; message: string }>(`/github-backup/logs?keep_last=${keepLast}`, { method: 'DELETE' }),

  // Local Presets (OrcaSlicer imports)
  getLocalPresets: () =>
    request<LocalPresetsResponse>('/local-presets/'),
  getLocalPresetDetail: (id: number) =>
    request<LocalPresetDetail>(`/local-presets/${id}`),
  importLocalPresets: (formData: FormData) =>
    fetch(`${API_BASE}/local-presets/import`, {
      method: 'POST',
      headers: authToken ? { 'Authorization': `Bearer ${authToken}` } : {},
      body: formData,
    }).then(async (res) => {
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      return res.json() as Promise<ImportResponse>;
    }),
  createLocalPreset: (data: { name: string; preset_type: string; setting: Record<string, unknown> }) =>
    request<LocalPreset>('/local-presets/', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateLocalPreset: (id: number, data: { name?: string; setting?: Record<string, unknown> }) =>
    request<LocalPreset>(`/local-presets/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  deleteLocalPreset: (id: number) =>
    request<{ success: boolean }>(`/local-presets/${id}`, { method: 'DELETE' }),
  refreshBaseProfileCache: () =>
    request<{ refreshed: number; failed: number; total: number }>('/local-presets/base-cache/refresh', { method: 'POST' }),
};

// AMS History types
export interface AMSHistoryPoint {
  recorded_at: string;
  humidity: number | null;
  humidity_raw: number | null;
  temperature: number | null;
}

export interface AMSHistoryResponse {
  printer_id: number;
  ams_id: number;
  data: AMSHistoryPoint[];
  min_humidity: number | null;
  max_humidity: number | null;
  avg_humidity: number | null;
  min_temperature: number | null;
  max_temperature: number | null;
  avg_temperature: number | null;
}

// System Info types
export interface SystemInfo {
  app: {
    version: string;
    base_dir: string;
    archive_dir: string;
  };
  database: {
    archives: number;
    archives_completed: number;
    archives_failed: number;
    archives_printing: number;
    printers: number;
    filaments: number;
    projects: number;
    smart_plugs: number;
    total_print_time_seconds: number;
    total_print_time_formatted: string;
    total_filament_grams: number;
    total_filament_kg: number;
  };
  printers: {
    total: number;
    connected: number;
    connected_list: Array<{
      id: number;
      name: string;
      state: string;
      model: string;
    }>;
  };
  storage: {
    archive_size_bytes: number;
    archive_size_formatted: string;
    database_size_bytes: number;
    database_size_formatted: string;
    disk_total_bytes: number;
    disk_total_formatted: string;
    disk_used_bytes: number;
    disk_used_formatted: string;
    disk_free_bytes: number;
    disk_free_formatted: string;
    disk_percent_used: number;
  };
  system: {
    platform: string;
    platform_release: string;
    platform_version: string;
    architecture: string;
    hostname: string;
    python_version: string;
    uptime_seconds: number;
    uptime_formatted: string;
    boot_time: string;
  };
  memory: {
    total_bytes: number;
    total_formatted: string;
    available_bytes: number;
    available_formatted: string;
    used_bytes: number;
    used_formatted: string;
    percent_used: number;
  };
  cpu: {
    count: number;
    count_logical: number;
    percent: number;
  };
}

// Library (File Manager) types
export interface LibraryFolderTree {
  id: number;
  name: string;
  parent_id: number | null;
  project_id: number | null;
  archive_id: number | null;
  project_name: string | null;
  archive_name: string | null;
  file_count: number;
  children: LibraryFolderTree[];
}

export interface LibraryFolder {
  id: number;
  name: string;
  parent_id: number | null;
  project_id: number | null;
  archive_id: number | null;
  project_name: string | null;
  archive_name: string | null;
  file_count: number;
  created_at: string;
  updated_at: string;
}

export interface LibraryFolderCreate {
  name: string;
  parent_id?: number | null;
  project_id?: number | null;
  archive_id?: number | null;
}

export interface LibraryFolderUpdate {
  name?: string;
  parent_id?: number | null;
  project_id?: number | null;  // 0 to unlink
  archive_id?: number | null;  // 0 to unlink
}

export interface LibraryFileDuplicate {
  id: number;
  filename: string;
  folder_id: number | null;
  folder_name: string | null;
  created_at: string;
}

export interface LibraryFile {
  id: number;
  folder_id: number | null;
  folder_name: string | null;
  project_id: number | null;
  project_name: string | null;
  filename: string;
  file_path: string;
  file_type: string;
  file_size: number;
  file_hash: string | null;
  thumbnail_path: string | null;
  metadata: Record<string, unknown> | null;
  print_count: number;
  last_printed_at: string | null;
  notes: string | null;
  duplicates: LibraryFileDuplicate[] | null;
  duplicate_count: number;
  // User tracking (Issue #206)
  created_by_id: number | null;
  created_by_username: string | null;
  created_at: string;
  updated_at: string;
  // Metadata fields
  print_name: string | null;
  print_time_seconds: number | null;
  filament_used_grams: number | null;
  sliced_for_model: string | null;
}

export interface LibraryFileListItem {
  id: number;
  folder_id: number | null;
  filename: string;
  file_type: string;
  file_size: number;
  thumbnail_path: string | null;
  print_count: number;
  duplicate_count: number;
  // User tracking (Issue #206)
  created_by_id: number | null;
  created_by_username: string | null;
  created_at: string;
  print_name: string | null;
  print_time_seconds: number | null;
  filament_used_grams: number | null;
  sliced_for_model: string | null;
}

export interface LibraryFileUpdate {
  filename?: string;
  folder_id?: number | null;
  project_id?: number | null;
  notes?: string | null;
}

export interface LibraryFileUploadResponse {
  id: number;
  filename: string;
  file_type: string;
  file_size: number;
  thumbnail_path: string | null;
  duplicate_of: number | null;
  metadata: Record<string, unknown> | null;
}

export interface LibraryStats {
  total_files: number;
  total_folders: number;
  total_size_bytes: number;
  files_by_type: Record<string, number>;
  total_prints: number;
  disk_free_bytes: number;
  disk_total_bytes: number;
  disk_used_bytes: number;
}

export interface ZipExtractResult {
  filename: string;
  file_id: number;
  folder_id: number | null;
}

export interface ZipExtractError {
  filename: string;
  error: string;
}

export interface ZipExtractResponse {
  extracted: number;
  folders_created: number;
  files: ZipExtractResult[];
  errors: ZipExtractError[];
}

// STL Thumbnail Generation types
export interface BatchThumbnailResult {
  file_id: number;
  filename: string;
  success: boolean;
  error?: string | null;
}

export interface BatchThumbnailResponse {
  processed: number;
  succeeded: number;
  failed: number;
  results: BatchThumbnailResult[];
}

// Library Queue types
export interface AddToQueueResult {
  file_id: number;
  filename: string;
  queue_item_id: number;
  archive_id: number;
}

export interface AddToQueueError {
  file_id: number;
  filename: string;
  error: string;
}

export interface AddToQueueResponse {
  added: AddToQueueResult[];
  errors: AddToQueueError[];
}

// Discovery types
export interface DiscoveredPrinter {
  serial: string;
  name: string;
  ip_address: string;
  model: string | null;
  discovered_at: string | null;
}

export interface DiscoveryStatus {
  running: boolean;
}

export interface DiscoveryInfo {
  is_docker: boolean;
  ssdp_running: boolean;
  scan_running: boolean;
  subnets: string[];
}

export interface SubnetScanStatus {
  running: boolean;
  scanned: number;
  total: number;
}

// Discovery API
export const discoveryApi = {
  getInfo: () => request<DiscoveryInfo>('/discovery/info'),

  getStatus: () => request<DiscoveryStatus>('/discovery/status'),

  startDiscovery: (duration: number = 10) =>
    request<DiscoveryStatus>(`/discovery/start?duration=${duration}`, { method: 'POST' }),

  stopDiscovery: () =>
    request<DiscoveryStatus>('/discovery/stop', { method: 'POST' }),

  getDiscoveredPrinters: () =>
    request<DiscoveredPrinter[]>('/discovery/printers'),

  // Subnet scanning (for Docker environments)
  startSubnetScan: (subnet: string, timeout: number = 1.0) =>
    request<SubnetScanStatus>('/discovery/scan', {
      method: 'POST',
      body: JSON.stringify({ subnet, timeout }),
    }),

  getScanStatus: () => request<SubnetScanStatus>('/discovery/scan/status'),

  stopSubnetScan: () =>
    request<SubnetScanStatus>('/discovery/scan/stop', { method: 'POST' }),
};

// Virtual Printer types
export type VirtualPrinterMode = 'immediate' | 'queue' | 'review' | 'print_queue' | 'proxy';  // 'queue' is legacy, normalized to 'review'

export interface VirtualPrinterProxyStatus {
  running: boolean;
  target_host: string;
  ftp_port: number;
  mqtt_port: number;
  ftp_connections: number;
  mqtt_connections: number;
}

export interface VirtualPrinterStatus {
  enabled: boolean;
  running: boolean;
  mode: VirtualPrinterMode;
  name: string;
  serial: string;
  model: string;
  model_name: string;
  pending_files: number;
  target_printer_ip?: string;  // For proxy mode
  proxy?: VirtualPrinterProxyStatus;  // For proxy mode
}

export interface VirtualPrinterSettings {
  enabled: boolean;
  access_code_set: boolean;
  mode: VirtualPrinterMode;
  model: string;
  target_printer_id: number | null;  // For proxy mode
  remote_interface_ip: string | null;  // For SSDP proxy across networks
  status: VirtualPrinterStatus;
}

export interface NetworkInterface {
  name: string;
  ip: string;
  netmask: string;
  subnet: string;
}

export interface VirtualPrinterModels {
  models: Record<string, string>;  // SSDP code -> display name
  default: string;
}

export interface PendingUpload {
  id: number;
  filename: string;
  file_size: number;
  source_ip: string | null;
  status: string;
  tags: string | null;
  notes: string | null;
  project_id: number | null;
  uploaded_at: string;
}

// Virtual Printer API
export const virtualPrinterApi = {
  getSettings: () => request<VirtualPrinterSettings>('/settings/virtual-printer'),

  getModels: () => request<VirtualPrinterModels>('/settings/virtual-printer/models'),

  updateSettings: (data: {
    enabled?: boolean;
    access_code?: string;
    mode?: 'immediate' | 'review' | 'print_queue' | 'proxy';
    model?: string;
    target_printer_id?: number;
    remote_interface_ip?: string;
  }) => {
    const params = new URLSearchParams();
    if (data.enabled !== undefined) params.set('enabled', String(data.enabled));
    if (data.access_code !== undefined) params.set('access_code', data.access_code);
    if (data.mode !== undefined) params.set('mode', data.mode);
    if (data.model !== undefined) params.set('model', data.model);
    if (data.target_printer_id !== undefined) params.set('target_printer_id', String(data.target_printer_id));
    if (data.remote_interface_ip !== undefined) params.set('remote_interface_ip', data.remote_interface_ip);

    return request<VirtualPrinterSettings>(`/settings/virtual-printer?${params.toString()}`, {
      method: 'PUT',
    });
  },
};

// Pending Uploads API
export const pendingUploadsApi = {
  list: () => request<PendingUpload[]>('/pending-uploads/'),

  getCount: () => request<{ count: number }>('/pending-uploads/count'),

  get: (id: number) => request<PendingUpload>(`/pending-uploads/${id}`),

  archive: (id: number, data?: { tags?: string; notes?: string; project_id?: number }) =>
    request<{ id: number; print_name: string; filename: string }>(`/pending-uploads/${id}/archive`, {
      method: 'POST',
      body: JSON.stringify(data || {}),
    }),

  discard: (id: number) =>
    request<{ success: boolean }>(`/pending-uploads/${id}`, { method: 'DELETE' }),

  archiveAll: () =>
    request<{ archived: number; failed: number }>('/pending-uploads/archive-all', { method: 'POST' }),

  discardAll: () =>
    request<{ discarded: number }>('/pending-uploads/discard-all', { method: 'DELETE' }),
};

// Firmware API Types
export interface FirmwareUpdateInfo {
  printer_id: number;
  printer_name: string;
  model: string | null;
  current_version: string | null;
  latest_version: string | null;
  update_available: boolean;
  download_url: string | null;
  release_notes: string | null;
}

export interface FirmwareUploadPrepare {
  can_proceed: boolean;
  sd_card_present: boolean;
  sd_card_free_space: number;
  firmware_size: number;
  space_sufficient: boolean;
  update_available: boolean;
  current_version: string | null;
  latest_version: string | null;
  firmware_filename: string | null;
  errors: string[];
}

export interface FirmwareUploadStatus {
  status: 'idle' | 'preparing' | 'downloading' | 'uploading' | 'complete' | 'error';
  progress: number;
  message: string;
  error: string | null;
  firmware_filename: string | null;
  firmware_version: string | null;
}

// Firmware API
export const firmwareApi = {
  checkUpdates: () =>
    request<{ updates: FirmwareUpdateInfo[]; updates_available: number }>('/firmware/updates'),

  checkPrinterUpdate: (printerId: number) =>
    request<FirmwareUpdateInfo>(`/firmware/updates/${printerId}`),

  prepareUpload: (printerId: number) =>
    request<FirmwareUploadPrepare>(`/firmware/updates/${printerId}/prepare`),

  startUpload: (printerId: number) =>
    request<{ started: boolean; message: string }>(`/firmware/updates/${printerId}/upload`, {
      method: 'POST',
    }),

  getUploadStatus: (printerId: number) =>
    request<FirmwareUploadStatus>(`/firmware/updates/${printerId}/upload/status`),
};

// Support types
export interface DebugLoggingState {
  enabled: boolean;
  enabled_at: string | null;
  duration_seconds: number | null;
}

export interface LogEntry {
  timestamp: string;
  level: string;
  logger_name: string;
  message: string;
}

export interface LogsResponse {
  entries: LogEntry[];
  total_in_file: number;
  filtered_count: number;
}

// Support API
export const supportApi = {
  getDebugLoggingState: () =>
    request<DebugLoggingState>('/support/debug-logging'),

  setDebugLogging: (enabled: boolean) =>
    request<DebugLoggingState>('/support/debug-logging', {
      method: 'POST',
      body: JSON.stringify({ enabled }),
    }),

  downloadSupportBundle: async () => {
    const headers: Record<string, string> = {};
    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }
    const response = await fetch(`${API_BASE}/support/bundle`, { headers });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    // Get filename from Content-Disposition header or use default
    const disposition = response.headers.get('Content-Disposition');
    const filename = parseContentDispositionFilename(disposition) || 'bambuddy-support.zip';

    // Download the blob
    const blob = await response.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.URL.revokeObjectURL(url);
  },

  getLogs: (params?: { limit?: number; level?: string; search?: string }) => {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set('limit', params.limit.toString());
    if (params?.level) searchParams.set('level', params.level);
    if (params?.search) searchParams.set('search', params.search);
    const query = searchParams.toString();
    return request<LogsResponse>(`/support/logs${query ? `?${query}` : ''}`);
  },

  clearLogs: () =>
    request<{ message: string }>('/support/logs', { method: 'DELETE' }),
};
