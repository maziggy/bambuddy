const API_BASE = '/api/v1';

async function request<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const response = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
}

// Printer types
export interface Printer {
  id: number;
  name: string;
  serial_number: string;
  ip_address: string;
  access_code: string;
  model: string | null;
  nozzle_count: number;  // 1 or 2, auto-detected from MQTT
  is_active: boolean;
  auto_archive: boolean;
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
  k: number | null;  // Pressure advance value
  tag_uid: string | null;  // RFID tag UID (any tag)
  tray_uuid: string | null;  // Bambu Lab spool UUID (32-char hex, only valid for Bambu Lab spools)
  nozzle_temp_min: number | null;  // Min nozzle temperature
  nozzle_temp_max: number | null;  // Max nozzle temperature
}

export interface AMSUnit {
  id: number;
  humidity: number | null;
  temp: number | null;
  tray: AMSTray[];
}

export interface NozzleInfo {
  nozzle_type: string;  // "stainless_steel" or "hardened_steel"
  nozzle_diameter: string;  // e.g., "0.4"
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
    nozzle?: number;
    nozzle_target?: number;
    chamber?: number;
  } | null;
  cover_url: string | null;
  hms_errors: HMSError[];
  ams: AMSUnit[];
  ams_exists: boolean;
  vt_tray: AMSTray | null;  // Virtual tray / external spool
  sdcard: boolean;  // SD card inserted
  store_to_sdcard: boolean;  // Store sent files on SD card
  timelapse: boolean;  // Timelapse recording active
  ipcam: boolean;  // Live view enabled
  wifi_signal: number | null;  // WiFi signal strength in dBm
  nozzles: NozzleInfo[];  // Nozzle hardware info (index 0=left/primary, 1=right)
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
}

export interface PrinterCreate {
  name: string;
  serial_number: string;
  ip_address: string;
  access_code: string;
  model?: string;
  auto_archive?: boolean;
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
  filename: string;
  file_path: string;
  file_size: number;
  content_hash: string | null;
  thumbnail_path: string | null;
  timelapse_path: string | null;
  source_3mf_path: string | null;
  duplicates: ArchiveDuplicate[] | null;
  duplicate_count: number;
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
  status: string;
  started_at: string | null;
  completed_at: string | null;
  extra_data: Record<string, unknown> | null;
  makerworld_url: string | null;
  designer: string | null;
  is_favorite: boolean;
  tags: string | null;
  notes: string | null;
  cost: number | null;
  photos: string[] | null;
  failure_reason: string | null;
  energy_kwh: number | null;
  energy_cost: number | null;
  created_at: string;
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

export interface BulkUploadResult {
  uploaded: number;
  failed: number;
  results: Array<{ filename: string; id: number; status: string }>;
  errors: Array<{ filename: string; error: string }>;
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
  notification_language: string;
}

export type AppSettingsUpdate = Partial<AppSettings>;

// Cloud types
export interface CloudAuthStatus {
  is_authenticated: boolean;
  email: string | null;
}

export interface CloudLoginResponse {
  success: boolean;
  needs_verification: boolean;
  message: string;
}

export interface SlicerSetting {
  setting_id: string;
  name: string;
  type: string;
  version: string | null;
  user_id: string | null;
  updated_time: string | null;
}

export interface SlicerSettingsResponse {
  filament: SlicerSetting[];
  printer: SlicerSetting[];
  process: SlicerSetting[];
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
  ip_address: string;
  printer_id: number | null;
  enabled: boolean;
  auto_on: boolean;
  auto_off: boolean;
  off_delay_mode: 'time' | 'temperature';
  off_delay_minutes: number;
  off_temp_threshold: number;
  username: string | null;
  password: string | null;
  last_state: string | null;
  last_checked: string | null;
  auto_off_executed: boolean;  // True when auto-off was triggered after print
  created_at: string;
  updated_at: string;
}

export interface SmartPlugCreate {
  name: string;
  ip_address: string;
  printer_id?: number | null;
  enabled?: boolean;
  auto_on?: boolean;
  auto_off?: boolean;
  off_delay_mode?: 'time' | 'temperature';
  off_delay_minutes?: number;
  off_temp_threshold?: number;
  username?: string | null;
  password?: string | null;
}

export interface SmartPlugUpdate {
  name?: string;
  ip_address?: string;
  printer_id?: number | null;
  enabled?: boolean;
  auto_on?: boolean;
  auto_off?: boolean;
  off_delay_mode?: 'time' | 'temperature';
  off_delay_minutes?: number;
  off_temp_threshold?: number;
  username?: string | null;
  password?: string | null;
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

// Print Queue types
export interface PrintQueueItem {
  id: number;
  printer_id: number;
  archive_id: number;
  position: number;
  scheduled_time: string | null;
  require_previous_success: boolean;
  auto_off_after: boolean;
  status: 'pending' | 'printing' | 'completed' | 'failed' | 'skipped' | 'cancelled';
  started_at: string | null;
  completed_at: string | null;
  error_message: string | null;
  created_at: string;
  archive_name?: string | null;
  archive_thumbnail?: string | null;
  printer_name?: string | null;
}

export interface PrintQueueItemCreate {
  printer_id: number;
  archive_id: number;
  scheduled_time?: string | null;
  require_previous_success?: boolean;
  auto_off_after?: boolean;
}

export interface PrintQueueItemUpdate {
  printer_id?: number;
  position?: number;
  scheduled_time?: string | null;
  require_previous_success?: boolean;
  auto_off_after?: boolean;
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
export type ProviderType = 'callmebot' | 'ntfy' | 'pushover' | 'telegram' | 'email';

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
  // Quiet hours
  quiet_hours_enabled: boolean;
  quiet_hours_start: string | null;
  quiet_hours_end: string | null;
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
  // Quiet hours
  quiet_hours_enabled?: boolean;
  quiet_hours_start?: string | null;
  quiet_hours_end?: string | null;
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
  // Quiet hours
  quiet_hours_enabled?: boolean;
  quiet_hours_start?: string | null;
  quiet_hours_end?: string | null;
  // Printer filter
  printer_id?: number | null;
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

// Spoolman types
export interface SpoolmanStatus {
  enabled: boolean;
  connected: boolean;
  url: string | null;
}

export interface SpoolmanSyncResult {
  success: boolean;
  synced_count: number;
  errors: string[];
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
}

export interface UpdateStatus {
  status: 'idle' | 'checking' | 'downloading' | 'installing' | 'complete' | 'error';
  progress: number;
  message: string;
  error: string | null;
}

// Printer Control types
export interface ControlResponse {
  success: boolean;
  message: string;
}

export interface ConfirmationRequired {
  requires_confirmation: boolean;
  token: string;
  warning: string;
  expires_in: number;
}

export type ControlResult = ControlResponse | ConfirmationRequired;

export function isConfirmationRequired(result: ControlResult): result is ConfirmationRequired {
  return 'requires_confirmation' in result && result.requires_confirmation === true;
}

// Maintenance types
export interface MaintenanceType {
  id: number;
  name: string;
  description: string | null;
  default_interval_hours: number;
  icon: string | null;
  is_system: boolean;
  created_at: string;
}

export interface MaintenanceTypeCreate {
  name: string;
  description?: string | null;
  default_interval_hours?: number;
  icon?: string | null;
}

export interface MaintenanceStatus {
  id: number;
  printer_id: number;
  printer_name: string;
  maintenance_type_id: number;
  maintenance_type_name: string;
  maintenance_type_icon: string | null;
  enabled: boolean;
  interval_hours: number;
  current_hours: number;
  hours_since_maintenance: number;
  hours_until_due: number;
  is_due: boolean;
  is_warning: boolean;
  last_performed_at: string | null;
}

export interface PrinterMaintenanceOverview {
  printer_id: number;
  printer_name: string;
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

// API functions
export const api = {
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
  deletePrinter: (id: number) =>
    request<void>(`/printers/${id}`, { method: 'DELETE' }),
  getPrinterStatus: (id: number) =>
    request<PrinterStatus>(`/printers/${id}/status`),
  connectPrinter: (id: number) =>
    request<{ connected: boolean }>(`/printers/${id}/connect`, {
      method: 'POST',
    }),
  disconnectPrinter: (id: number) =>
    request<{ connected: boolean }>(`/printers/${id}/disconnect`, {
      method: 'POST',
    }),

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
      }>;
    }>(`/printers/${printerId}/files?path=${encodeURIComponent(path)}`),
  getPrinterFileDownloadUrl: (printerId: number, path: string) =>
    `${API_BASE}/printers/${printerId}/files/download?path=${encodeURIComponent(path)}`,
  deletePrinterFile: (printerId: number, path: string) =>
    request<{ status: string; path: string }>(`/printers/${printerId}/files?path=${encodeURIComponent(path)}`, {
      method: 'DELETE',
    }),
  getPrinterStorage: (printerId: number) =>
    request<{ used_bytes: number | null; free_bytes: number | null }>(`/printers/${printerId}/storage`),

  // Archives
  getArchives: (printerId?: number, limit = 50, offset = 0) => {
    const params = new URLSearchParams();
    if (printerId) params.set('printer_id', String(printerId));
    params.set('limit', String(limit));
    params.set('offset', String(offset));
    return request<Archive[]>(`/archives/?${params}`);
  },
  getArchive: (id: number) => request<Archive>(`/archives/${id}`),
  updateArchive: (id: number, data: {
    printer_id?: number | null;
    print_name?: string;
    is_favorite?: boolean;
    tags?: string;
    notes?: string;
    cost?: number;
    failure_reason?: string | null;
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
  getArchiveDuplicates: (id: number) =>
    request<{ duplicates: ArchiveDuplicate[]; count: number }>(`/archives/${id}/duplicates`),
  backfillContentHashes: () =>
    request<{ updated: number; errors: Array<{ id: number; error: string }> }>('/archives/backfill-hashes', {
      method: 'POST',
    }),
  getArchiveThumbnail: (id: number) => `${API_BASE}/archives/${id}/thumbnail`,
  getArchiveDownload: (id: number) => `${API_BASE}/archives/${id}/download`,
  getArchiveGcode: (id: number) => `${API_BASE}/archives/${id}/gcode`,
  getArchiveTimelapse: (id: number) => `${API_BASE}/archives/${id}/timelapse`,
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
    const response = await fetch(`${API_BASE}/archives/${archiveId}/timelapse/upload`, {
      method: 'POST',
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
    const response = await fetch(`${API_BASE}/archives/${archiveId}/photos`, {
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
  getSource3mfForSlicer: (archiveId: number, filename: string) =>
    `${API_BASE}/archives/${archiveId}/source/${encodeURIComponent(filename.endsWith('.3mf') ? filename : filename + '.3mf')}`,
  uploadSource3mf: async (archiveId: number, file: File): Promise<{ status: string; filename: string }> => {
    const formData = new FormData();
    formData.append('file', file);
    const response = await fetch(`${API_BASE}/archives/${archiveId}/source`, {
      method: 'POST',
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

  // QR Code
  getArchiveQRCodeUrl: (archiveId: number, size = 200) =>
    `${API_BASE}/archives/${archiveId}/qrcode?size=${size}`,
  getArchiveCapabilities: (id: number) =>
    request<{
      has_model: boolean;
      has_gcode: boolean;
      build_volume: { x: number; y: number; z: number };
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
  reprintArchive: (archiveId: number, printerId: number) =>
    request<{ status: string; printer_id: number; archive_id: number; filename: string }>(
      `/archives/${archiveId}/reprint?printer_id=${printerId}`,
      { method: 'POST' }
    ),
  uploadArchive: async (file: File, printerId?: number): Promise<Archive> => {
    const formData = new FormData();
    formData.append('file', file);
    const url = printerId
      ? `${API_BASE}/archives/upload?printer_id=${printerId}`
      : `${API_BASE}/archives/upload`;
    const response = await fetch(url, {
      method: 'POST',
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
    const response = await fetch(url, {
      method: 'POST',
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
  resetSettings: () =>
    request<AppSettings>('/settings/reset', { method: 'POST' }),
  checkFfmpeg: () =>
    request<{ installed: boolean; path: string | null }>('/settings/check-ffmpeg'),

  // Cloud
  getCloudStatus: () => request<CloudAuthStatus>('/cloud/status'),
  cloudLogin: (email: string, password: string, region = 'global') =>
    request<CloudLoginResponse>('/cloud/login', {
      method: 'POST',
      body: JSON.stringify({ email, password, region }),
    }),
  cloudVerify: (email: string, code: string) =>
    request<CloudLoginResponse>('/cloud/verify', {
      method: 'POST',
      body: JSON.stringify({ email, code }),
    }),
  cloudSetToken: (access_token: string) =>
    request<CloudAuthStatus>('/cloud/token', {
      method: 'POST',
      body: JSON.stringify({ access_token }),
    }),
  cloudLogout: () =>
    request<{ success: boolean }>('/cloud/logout', { method: 'POST' }),
  getCloudSettings: (version = '01.09.00.00') =>
    request<SlicerSettingsResponse>(`/cloud/settings?version=${version}`),
  getCloudSettingDetail: (settingId: string) =>
    request<Record<string, unknown>>(`/cloud/settings/${settingId}`),
  getCloudDevices: () => request<CloudDevice[]>('/cloud/devices'),

  // Smart Plugs
  getSmartPlugs: () => request<SmartPlug[]>('/smart-plugs/'),
  getSmartPlug: (id: number) => request<SmartPlug>(`/smart-plugs/${id}`),
  getSmartPlugByPrinter: (printerId: number) => request<SmartPlug | null>(`/smart-plugs/by-printer/${printerId}`),
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

  // Slot Preset Mappings
  getSlotPresets: (printerId: number) =>
    request<Record<number, SlotPresetMapping>>(`/printers/${printerId}/slot-presets`),
  getSlotPreset: (printerId: number, amsId: number, trayId: number) =>
    request<SlotPresetMapping | null>(`/printers/${printerId}/slot-presets/${amsId}/${trayId}`),
  saveSlotPreset: (printerId: number, amsId: number, trayId: number, presetId: string, presetName: string) =>
    request<SlotPresetMapping>(`/printers/${printerId}/slot-presets/${amsId}/${trayId}?preset_id=${encodeURIComponent(presetId)}&preset_name=${encodeURIComponent(presetName)}`, {
      method: 'PUT',
    }),
  deleteSlotPreset: (printerId: number, amsId: number, trayId: number) =>
    request<{ success: boolean }>(`/printers/${printerId}/slot-presets/${amsId}/${trayId}`, {
      method: 'DELETE',
    }),

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

  // Updates
  getVersion: () => request<VersionInfo>('/updates/version'),
  checkForUpdates: () => request<UpdateCheckResult>('/updates/check'),
  applyUpdate: () =>
    request<{ success: boolean; message: string; status: UpdateStatus }>('/updates/apply', {
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
  getMaintenanceOverview: () => request<PrinterMaintenanceOverview[]>('/maintenance/overview'),
  getPrinterMaintenance: (printerId: number) =>
    request<PrinterMaintenanceOverview>(`/maintenance/printers/${printerId}`),
  updateMaintenanceItem: (itemId: number, data: { custom_interval_hours?: number | null; enabled?: boolean }) =>
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

  // Printer Control
  pausePrint: (printerId: number) =>
    request<ControlResponse>(`/printers/${printerId}/control/pause`, { method: 'POST' }),
  resumePrint: (printerId: number) =>
    request<ControlResponse>(`/printers/${printerId}/control/resume`, { method: 'POST' }),
  stopPrint: (printerId: number, confirmToken?: string) =>
    request<ControlResult>(`/printers/${printerId}/control/stop`, {
      method: 'POST',
      body: JSON.stringify({ confirm_token: confirmToken }),
    }),
  setBedTemperature: (printerId: number, target: number, confirmToken?: string) =>
    request<ControlResult>(`/printers/${printerId}/control/temperature/bed`, {
      method: 'POST',
      body: JSON.stringify({ target, confirm_token: confirmToken }),
    }),
  setNozzleTemperature: (printerId: number, target: number, nozzle = 0, confirmToken?: string) =>
    request<ControlResult>(`/printers/${printerId}/control/temperature/nozzle`, {
      method: 'POST',
      body: JSON.stringify({ target, nozzle, confirm_token: confirmToken }),
    }),
  setChamberTemperature: (printerId: number, target: number, confirmToken?: string) =>
    request<ControlResult>(`/printers/${printerId}/control/temperature/chamber`, {
      method: 'POST',
      body: JSON.stringify({ target, confirm_token: confirmToken }),
    }),
  setPrintSpeed: (printerId: number, mode: number) =>
    request<ControlResponse>(`/printers/${printerId}/control/speed`, {
      method: 'POST',
      body: JSON.stringify({ mode }),
    }),
  setPartFan: (printerId: number, speed: number) =>
    request<ControlResponse>(`/printers/${printerId}/control/fan/part`, {
      method: 'POST',
      body: JSON.stringify({ speed }),
    }),
  setAuxFan: (printerId: number, speed: number) =>
    request<ControlResponse>(`/printers/${printerId}/control/fan/aux`, {
      method: 'POST',
      body: JSON.stringify({ speed }),
    }),
  setChamberFan: (printerId: number, speed: number) =>
    request<ControlResponse>(`/printers/${printerId}/control/fan/chamber`, {
      method: 'POST',
      body: JSON.stringify({ speed }),
    }),
  setAirductMode: (printerId: number, mode: 'cooling' | 'heating') =>
    request<ControlResponse>(`/printers/${printerId}/control/airduct`, {
      method: 'POST',
      body: JSON.stringify({ mode }),
    }),
  setChamberLight: (printerId: number, on: boolean) =>
    request<ControlResponse>(`/printers/${printerId}/control/light`, {
      method: 'POST',
      body: JSON.stringify({ on }),
    }),
  selectExtruder: (printerId: number, extruder: number) =>
    request<ControlResponse>(`/printers/${printerId}/control/extruder`, {
      method: 'POST',
      body: JSON.stringify({ extruder }),
    }),
  homeAxes: (printerId: number, axes = 'XYZ', confirmToken?: string) =>
    request<ControlResult>(`/printers/${printerId}/control/home`, {
      method: 'POST',
      body: JSON.stringify({ axes, confirm_token: confirmToken }),
    }),
  moveAxis: (printerId: number, axis: string, distance: number, speed = 3000, confirmToken?: string) =>
    request<ControlResult>(`/printers/${printerId}/control/move`, {
      method: 'POST',
      body: JSON.stringify({ axis, distance, speed, confirm_token: confirmToken }),
    }),
  disableMotors: (printerId: number, confirmToken?: string) =>
    request<ControlResult>(`/printers/${printerId}/control/motors/disable`, {
      method: 'POST',
      body: JSON.stringify({ confirm_token: confirmToken }),
    }),
  enableMotors: (printerId: number) =>
    request<ControlResponse>(`/printers/${printerId}/control/motors/enable`, { method: 'POST' }),
  amsLoadFilament: (printerId: number, trayId: number, extruderId?: number) =>
    request<ControlResponse>(`/printers/${printerId}/control/ams/load`, {
      method: 'POST',
      body: JSON.stringify({ tray_id: trayId, extruder_id: extruderId }),
    }),
  amsUnloadFilament: (printerId: number) =>
    request<ControlResponse>(`/printers/${printerId}/control/ams/unload`, { method: 'POST' }),
  amsSetFilamentSetting: (printerId: number, data: {
    ams_id: number;
    tray_id: number;
    tray_info_idx: string;
    tray_type: string;
    tray_sub_brands: string;
    tray_color: string;
    nozzle_temp_min: number;
    nozzle_temp_max: number;
    k: number;
  }) =>
    request<ControlResponse>(`/printers/${printerId}/control/ams/filament-setting`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  sendGcode: (printerId: number, command: string, confirmToken?: string) =>
    request<ControlResult>(`/printers/${printerId}/control/gcode`, {
      method: 'POST',
      body: JSON.stringify({ command, confirm_token: confirmToken }),
    }),
  getCameraStreamUrl: (printerId: number, fps = 10) =>
    `${API_BASE}/printers/${printerId}/camera/stream?fps=${fps}`,
  getCameraSnapshotUrl: (printerId: number) =>
    `${API_BASE}/printers/${printerId}/camera/snapshot`,
  testCameraConnection: (printerId: number) =>
    request<{ success: boolean; message?: string; error?: string }>(`/printers/${printerId}/camera/test`),
  setTimelapse: (printerId: number, enable: boolean) =>
    request<ControlResponse>(`/printers/${printerId}/control/camera/timelapse`, {
      method: 'POST',
      body: JSON.stringify({ enable }),
    }),
  setLiveview: (printerId: number, enable: boolean) =>
    request<ControlResponse>(`/printers/${printerId}/control/camera/liveview`, {
      method: 'POST',
      body: JSON.stringify({ enable }),
    }),

  // Request full status update from printer (pushall)
  refreshStatus: (printerId: number) =>
    request<ControlResponse>(`/printers/${printerId}/control/refresh`, {
      method: 'POST',
    }),

  // Refresh a specific AMS tray (trigger RFID re-read)
  refreshAmsTray: (printerId: number, amsId: number, trayId: number) =>
    request<ControlResponse>(`/printers/${printerId}/control/ams/refresh-tray`, {
      method: 'POST',
      body: JSON.stringify({ ams_id: amsId, tray_id: trayId }),
    }),

  // Print Options (AI Detection)
  setPrintOption: (
    printerId: number,
    moduleName: string,
    enabled: boolean,
    printHalt = true,
    sensitivity = 'medium'
  ) => {
    const params = new URLSearchParams({
      module_name: moduleName,
      enabled: String(enabled),
      print_halt: String(printHalt),
      sensitivity,
    });
    return request<{
      success: boolean;
      module_name: string;
      enabled: boolean;
      print_halt: boolean;
      sensitivity: string;
    }>(`/printers/${printerId}/print-options?${params}`, {
      method: 'POST',
    });
  },

  // Calibration
  startCalibration: (
    printerId: number,
    options: {
      bed_leveling?: boolean;
      vibration?: boolean;
      motor_noise?: boolean;
      nozzle_offset?: boolean;
      high_temp_heatbed?: boolean;
    }
  ) => {
    const params = new URLSearchParams();
    if (options.bed_leveling) params.append('bed_leveling', 'true');
    if (options.vibration) params.append('vibration', 'true');
    if (options.motor_noise) params.append('motor_noise', 'true');
    if (options.nozzle_offset) params.append('nozzle_offset', 'true');
    if (options.high_temp_heatbed) params.append('high_temp_heatbed', 'true');
    return request<{
      success: boolean;
      bed_leveling: boolean;
      vibration: boolean;
      motor_noise: boolean;
      nozzle_offset: boolean;
      high_temp_heatbed: boolean;
    }>(`/printers/${printerId}/calibration?${params}`, {
      method: 'POST',
    });
  },
};
