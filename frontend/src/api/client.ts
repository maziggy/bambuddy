import type { ArchivePlatesResponse, LibraryFilePlatesResponse } from '../types/plates';

const API_BASE = '/api/v1';

export class ApiError extends Error {
  status: number;
  /** Stable error code from a structured backend detail (`{code, message}`).
   *  Frontend uses this to look up an i18n key instead of showing the raw
   *  English fallback. Null when the backend returned a plain-string detail. */
  code: string | null;
  /** Full structured detail object when the backend returned `{code, ...}`
   *  with additional fields (e.g. the deficit list for 409s on queue
   *  start, #1496). Null for plain-string or array-shaped details. */
  detail: Record<string, unknown> | null;
  constructor(
    message: string,
    status: number,
    code: string | null = null,
    detail: Record<string, unknown> | null = null,
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

// Auth token storage
// By default tokens are stored in sessionStorage (tab-scoped, cleared on close).
// When the token originates from the ?token= URL param (kiosk bootstrap), it is
// additionally persisted in localStorage so the kiosk survives page reloads.
// 'persistent' also writes to localStorage so the token survives tab close
// (used by Remember Me and the ?token= kiosk bootstrap).
let authToken: string | null =
  sessionStorage.getItem('auth_token') ?? localStorage.getItem('auth_token');

export type TokenPersistence = 'session' | 'persistent';

export function setAuthToken(token: string | null, persistence: TokenPersistence = 'session') {
  authToken = token;
  try {
    if (token) {
      sessionStorage.setItem('auth_token', token);
    } else {
      sessionStorage.removeItem('auth_token');
    }
  } catch (err) {
    // Storage unavailable (quota exceeded, private mode): in-memory token still works for this tab.
    console.warn('setAuthToken: sessionStorage unavailable, token kept in-memory only', err);
  }
  try {
    if (!token) {
      localStorage.removeItem('auth_token');
    } else if (persistence === 'persistent') {
      localStorage.setItem('auth_token', token);
    }
  } catch (err) {
    console.warn('setAuthToken: localStorage operation failed', err);
  }
}

export function getAuthToken(): string | null {
  return authToken;
}

// Stream token for image/video URLs loaded via <img>/<video> tags
// (these can't send Authorization headers, so a query param token is used)
let streamToken: string | null = null;

export function setStreamToken(token: string | null) {
  streamToken = token;
}

export function getStreamToken(): string | null {
  return streamToken;
}

/** Append the stream token to a URL if available (for <img>/<video> src). */
export function withStreamToken(url: string): string {
  if (!streamToken) return url;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}token=${encodeURIComponent(streamToken)}`;
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

function buildSlicerUrlFilename(filename: string): string {
  const safe = filename.replace(/[/\\?#]/g, '_');
  return safe.toLowerCase().endsWith('.3mf') ? safe : `${safe}.3mf`;
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
    credentials: 'include', // Required for HttpOnly cookies (e.g. 2fa_challenge)
    headers,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    const detail = error.detail;
    let message: string;
    let code: string | null = null;
    if (typeof detail === 'string') {
      message = detail;
    } else if (Array.isArray(detail)) {
      // FastAPI 422 shape: each entry has `msg` like "Value error, <real msg>".
      // Strip the prefix and join. Fall back to raw JSON if every entry has an
      // empty msg (defensive — shouldn't happen with stock Pydantic, but the
      // previous fallback masked the real cause as a bare "HTTP 422" toast).
      const joined = detail
        .map((e: { msg?: string }) => (e.msg ?? '').replace(/^Value error,\s*/i, ''))
        .filter(Boolean)
        .join('; ');
      message = joined || JSON.stringify(detail) || `HTTP ${response.status}`;
    } else if (detail && typeof detail === 'object') {
      // Structured detail `{code, message, ...}` — frontend uses the code
      // to pick an i18n key, message is the English fallback, any extra
      // fields land on ApiError.detail (e.g. `deficit` for #1496).
      code = typeof detail.code === 'string' ? detail.code : null;
      message = typeof detail.message === 'string' ? detail.message : `HTTP ${response.status}`;
    } else {
      message = `HTTP ${response.status}`;
    }
    const structuredDetail = detail && typeof detail === 'object' && !Array.isArray(detail)
      ? (detail as Record<string, unknown>)
      : null;

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
        // Notify AuthContext so the protected route guard re-evaluates and
        // redirects to /login on the same tab — without this, AuthContext.user
        // stays cached and the tab silently fails every request until a manual
        // refresh remounts AuthProvider (#1698, reported by @TCL987).
        if (typeof window !== 'undefined') {
          window.dispatchEvent(new CustomEvent('auth:expired'));
        }
      }
    }

    throw new ApiError(message, response.status, code, structuredDetail);
  }

  // Handle empty responses (204 No Content, etc.)
  const contentLength = response.headers.get('content-length');
  if (response.status === 204 || contentLength === '0') {
    return undefined as T;
  }

  return await response.json();
}

/** Upload a CSV to the spool import endpoint (#1576). Multipart, so it bypasses
 *  `request<T>()` (which sends JSON): the browser must set the form-data
 *  boundary itself. `dryRun` toggles preview-only vs. real import. */
async function uploadSpoolsCsv<T>(file: File, dryRun: boolean): Promise<T> {
  const form = new FormData();
  form.append('file', file);
  const headers: Record<string, string> = {};
  if (authToken) headers['Authorization'] = `Bearer ${authToken}`;
  const response = await fetch(`${API_BASE}/inventory/spools/import${dryRun ? '?dry_run=true' : ''}`, {
    method: 'POST',
    headers,
    body: form,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    // detail may be a plain string or a structured {code, message} object
    // (e.g. the 413 too-large response). Surface the human message either way.
    const detail = error?.detail;
    const message = typeof detail === 'string' ? detail : detail?.message;
    throw new Error(message || `HTTP ${response.status}`);
  }
  return response.json();
}

// Camera diagnostic result (#1395 follow-up). Returned by
// POST /printers/{id}/camera/diagnose; the frontend modal renders one
// row per stage and looks up the summary code in i18n for the user-
// facing remediation hint.
export interface CameraDiagnoseStage {
  name: 'tcp_reachable' | 'first_frame' | 'live_stream_active';
  status: 'ok' | 'failed' | 'skipped';
  duration_ms: number;
  code: string | null;
}

export interface CameraDiagnoseResult {
  printer_id: number;
  protocol: 'rtsp' | 'chamber_image';
  port: number;
  // 'default' = historical X1/H2 tuning. Anything else = this model has
  // an override entry in backend/app/services/camera_profiles.py.
  profile: string;
  overall_status: 'ok' | 'failed';
  stages: CameraDiagnoseStage[];
  // i18n key under `camera.diagnose.summary.*`.
  summary_code: string;
}

// Connection diagnostic (GET /printers/{id}/diagnostic and
// POST /printers/diagnostic). Each check's `id` + `status` resolve a
// localized title/fix under `diagnostic.check.*`; `params` interpolate it.
export type DiagnosticStatus = 'pass' | 'fail' | 'warn' | 'skip';

export interface DiagnosticCheck {
  id:
    | 'port_mqtt'
    | 'port_ftps'
    | 'port_rtsps'
    | 'network_mode'
    | 'subnet'
    | 'mqtt_auth'
    | 'developer_mode';
  status: DiagnosticStatus;
  params: Record<string, string | number>;
}

export interface PrinterDiagnosticResult {
  printer_id: number | null;
  ip_address: string;
  overall: 'ok' | 'warnings' | 'problems';
  checks: DiagnosticCheck[];
}

// --- Log-health scan: self-service triage on the System page + bug reporter.
// The backend matches recent logs against a curated known-issue catalog;
// human-readable cause/fix text is rendered from i18n keys keyed by signature_id.
export type LogFindingSeverity = 'error' | 'warning';
export type LogFindingCategory = 'layer8' | 'environment' | 'bug';

export interface LogFinding {
  signature_id: string;
  severity: LogFindingSeverity;
  category: LogFindingCategory;
  wiki_anchor: string;
  count: number;
  first_seen: string;
  last_seen: string;
  sample: string;
}

export interface SystemHealthResult {
  findings: LogFinding[];
  scanned_entries: number;
  log_available: boolean;
  summary: {
    total: number;
    layer8: number;
    environment: number;
    bug: number;
  };
}

// Long-lived camera tokens (#1108). The `token` field is populated only on the
// create response — listing endpoints set it to null because the plaintext
// value is shown to the user exactly once.
//
// 'camera_stream' reaches the video endpoints only. 'camwall' additionally
// reaches the read-only Cam Wall feed, which names the printers (#2531), so it
// is a separate scope rather than a widening of tokens already in the wild.
export type LongLivedTokenScope = 'camera_stream' | 'camwall';

export interface LongLivedCameraToken {
  id: number;
  user_id: number;
  name: string;
  scope: LongLivedTokenScope;
  lookup_prefix: string;
  created_at: string;
  expires_at: string;
  last_used_at: string | null;
  token: string | null;
}

// One row of the token-authenticated Cam Wall feed (#2531). Deliberately
// smaller than PrinterStatus: no serial, no IP, no print filename — a kiosk URL
// is not a secret, so the payload behind it must not be either.
export interface CamWallPrinter {
  id: number;
  name: string;
  camera_rotation: number;
  connected: boolean;
  state: string | null;
  progress: number | null;
  remaining_time: number | null;
  layer_num: number | null;
  total_layers: number | null;
  hms_errors: HMSError[];
}

// Printer types
export interface Printer {
  id: number;
  name: string;
  serial_number: string;
  ip_address: string;
  // Optional because the backend only returns access_code when the caller has
  // PRINTERS_UPDATE — Admin / Operator JWTs or auth-disabled mode. Viewers and
  // API keys receive a Printer without this field.
  access_code?: string;
  model: string | null;
  location: string | null;  // Group/location name
  nozzle_count: number;  // 1 or 2, auto-detected from MQTT
  is_active: boolean;
  auto_archive: boolean;
  external_camera_url: string | null;
  external_camera_type: string | null;  // "mjpeg", "rtsp", "snapshot"
  external_camera_enabled: boolean;
  external_camera_snapshot_url: string | null;  // optional single-frame override (#1177)
  camera_rotation: number;  // 0, 90, 180, 270 degrees
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
  actions?: string[];  // List of user-facing action keys (e.g. "CHECK_FILAMENT")
  job_id?: string;  // Optional job ID for actions that require it (e.g. "CHECK_ASSISTANT")
  // Canonical hex identifier the firmware matches against — 8 chars for
  // print_error-sourced faults, 16 chars for hms[]-array-sourced faults. Send
  // this back as HmsActionBody.print_error so we don't truncate the 64-bit
  // identifier into the silent-rejection short code (#1830).
  full_code?: string;
}

export interface HMSActionBody {
  print_error: string;  // HMS error code (e.g. "05000070")
  action: string;  // "HMS action to execute (e.g. 'resume_after_error')"
  job_id: string | null;  // Optional job ID for context (if applicable)
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
  drying_temp: number | null;      // RFID-recommended drying temp
  drying_time: number | null;      // RFID-recommended drying time (hours)
  state: number | null;            // AMS tray state: 9=empty, 10=spool present not loaded, 11=loaded
  exists?: boolean | null;         // Firmware tray_exist_bits: spool physically present (non-RFID → "?" not "Empty", #2527)
}

export interface AMSUnit {
  id: number;
  humidity: number | null;
  temp: number | null;
  is_ams_ht: boolean;  // True for AMS-HT (single spool), False for regular AMS (4 spools)
  tray: AMSTray[];
  serial_number: string;  // AMS unit serial number (from MQTT sn field)
  sw_ver: string;         // AMS firmware version (from get_version info.module ams/* entry)
  dry_time: number;       // Minutes remaining (0 = not drying, >0 = drying active)
  dry_status: number;     // 0=Off, 1=Checking, 2=Drying, 3=Cooling, 4=Stopping, 5=Error
  dry_sub_status: number; // 0=Off, 1=Heating, 2=Dehumidify
  dry_sf_reason: number[]; // Cannot-dry reasons (1=InsufficientPower, 8=NeedPluginPower)
  dry_target_temp: number | null; // Active-cycle target °C (Bambu does not echo)
  dry_filament: string | null;    // Active-cycle filament name we sent
  module_type: string;    // "ams", "n3f", "n3s"
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

export interface FilaSwitchState {
  installed: boolean;
  // in[track] = currently loaded slot for that track (-1 = empty)
  in_slots: number[];
  // out[track] = extruder this track terminates at (0 = right, 1 = left)
  out_extruders: number[];
  stat: number;
  info: number;
}

export interface PrinterStatus {
  id: number;
  name: string;
  connected: boolean;
  state: string | null;
  current_print: string | null;
  subtask_name: string | null;
  current_archive_id: number | null;
  current_plate_id: number | null;
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
  store_to_sdcard: boolean;  // Store sent files on SD card
  timelapse: boolean;  // Timelapse recording active
  ipcam: boolean;  // Live view enabled
  wifi_signal: number | null;  // WiFi signal strength in dBm
  wired_network: boolean;  // Ethernet connection detected
  door_open: boolean;  // Enclosure door open (models with a door sensor: X1/X1C/X1E/X2D/P2S/H2*)
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
  // Filament Track Switch accessory — null when not installed. When present,
  // AMS slots aren't tied to a specific extruder; the FTS routes any slot to
  // either extruder, so per-extruder slot filtering must be skipped.
  fila_switch: FilaSwitchState | null;
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
  firmware_version: string | null;   // Firmware version from MQTT
  // Developer LAN mode: true = enabled, false = disabled, null = unknown
  developer_mode: boolean | null;
  // AMS Filament Backup ("auto-switch" to a backup spool when one runs out).
  // true = ON, false = OFF, null = unknown / unsupported (A1 family).
  ams_filament_backup: boolean | null;
  // Queue: printer is awaiting user ack that the build plate was cleared after a
  // finished/failed print. Persisted across restarts (#961).
  awaiting_plate_clear: boolean;
  // AMS drying support
  supports_drying: boolean;
  // The AMS can dry, but only from the printer's own screen (P1 series, #2533).
  drying_screen_only?: boolean;
  // Active chamber heater (responds to M141). True only for H2C/H2D/H2DPro/H2S/X2D.
  supports_chamber_heater?: boolean;
}

export interface PrinterCreate {
  name: string;
  serial_number: string;
  ip_address: string;
  access_code: string;
  model?: string;
  location?: string;
  auto_archive?: boolean;
  // Maintenance Mode flag (#1476). Backend already gates MQTT, queue dispatch,
  // scheduler, metrics and the print picker on this; toggling via PATCH
  // /printers/{id} disconnects or reconnects MQTT accordingly.
  is_active?: boolean;
  external_camera_url?: string | null;
  external_camera_type?: string | null;
  external_camera_enabled?: boolean;
  external_camera_snapshot_url?: string | null;
  camera_rotation?: number;
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
  duplicate_sequence: number;  // 0 = original, 1+ = nth duplicate
  original_archive_id: number | null;  // ID of the first/original archive
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
  bed_type: string | null;  // Build plate type from 3MF (e.g. "Cool Plate", "Textured PEI Plate")
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
  // Per-archive run aggregates from PrintLogEntry (#1378)
  run_count: number;
  last_run_at: string | null;
  total_filament_actual_grams: number | null;
  successful_run_count: number;
  failed_run_count: number;
}

export interface ArchiveSlim {
  printer_id: number | null;
  print_name: string | null;
  print_time_seconds: number | null;
  actual_time_seconds: number | null;
  filament_used_grams: number | null;
  filament_type: string | null;
  filament_color: string | null;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  cost: number | null;
  quantity: number;
  created_at: string;
}

export interface PrintLogEntry {
  id: number;
  archive_id: number | null;
  print_name: string | null;
  printer_name: string | null;
  printer_id: number | null;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  filament_type: string | null;
  filament_color: string | null;
  filament_used_grams: number | null;
  cost: number | null;
  energy_kwh: number | null;
  energy_cost: number | null;
  failure_reason: string | null;
  thumbnail_path: string | null;
  created_by_id: number | null;
  created_by_username: string | null;
  created_at: string;
}

export interface PrintLogResponse {
  items: PrintLogEntry[];
  total: number;
}

export interface ArchiveStats {
  total_prints: number;
  successful_prints: number;
  failed_prints: number;
  cancelled_prints: number;
  total_print_time_hours: number;
  total_filament_grams: number;
  total_cost: number;
  prints_by_filament_type: Record<string, number>;
  prints_by_printer: Record<string, number>;
  average_time_accuracy: number | null;
  time_accuracy_by_printer: Record<string, number> | null;
  total_energy_kwh: number;
  total_energy_cost: number;
  // True when a date-filtered total-consumption query is running on incomplete
  // snapshot history (e.g. right after upgrade, before hourly snapshots have
  // a baseline). UI should explain why the number may undercount.
  energy_data_warming_up?: boolean;
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
  bom_cost: number;
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
  url: string | null;  // External link rendered next to project name on the card (#1155)
  cover_image_filename: string | null;  // Filename within project attachments dir (#1155)
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
  budget: number | null;
  tags: string | null;  // #2536 — the shared edit dialog seeds itself from this
  due_date: string | null;  // #2536
  priority: string;  // #2536
  created_at: string;
  archive_count: number;  // Number of print jobs (plates)
  total_items: number;  // Sum of quantities (total items printed, including failed)
  completed_count: number;  // Sum of quantities for completed prints only (parts)
  failed_count: number;  // Sum of quantities for failed prints
  queue_count: number;
  progress_percent: number | null;  // Plates progress
  archives: ArchivePreview[];
  url: string | null;  // #1155
  cover_image_filename: string | null;  // #1155
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
  budget?: number | null;
  parent_id?: number;
  url?: string | null;  // #1155
}

export interface ProjectUpdate {
  name?: string;
  description?: string;
  color?: string;
  status?: string;
  target_count?: number;
  target_parts_count?: number;
  notes?: string;
  tags?: string | null;  // #2536 — explicit null clears the tags
  due_date?: string | null;  // #2536 — explicit null clears the due date
  priority?: string;
  budget?: number | null;
  parent_id?: number;
  url?: string | null;  // #1155 — explicit null clears the URL
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
  budget?: number | null;
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
  user_id: number | null;  // Owner; null on legacy keys created before per-user ownership (#1182)
  can_queue: boolean;
  can_control_printer: boolean;
  can_read_status: boolean;
  can_manage_library: boolean;
  can_manage_inventory: boolean;
  can_manage_maintenance: boolean;
  can_manage_archives: boolean;
  can_manage_projects: boolean;
  can_access_cloud: boolean;
  can_update_energy_cost: boolean;
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
  can_manage_library?: boolean;
  can_manage_inventory?: boolean;
  can_manage_maintenance?: boolean;
  can_manage_archives?: boolean;
  can_manage_projects?: boolean;
  can_access_cloud?: boolean;
  can_update_energy_cost?: boolean;
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
  can_manage_library?: boolean;
  can_manage_inventory?: boolean;
  can_manage_maintenance?: boolean;
  can_manage_archives?: boolean;
  can_manage_projects?: boolean;
  can_access_cloud?: boolean;
  can_update_energy_cost?: boolean;
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
  include_beta_updates: boolean;
  // #1589: false hides the local username/password form on the login page;
  // BAMBUDDY_LOCAL_LOGIN=true on the server flips the reported value back to
  // true so the env-var recovery path is visible to the SPA.
  local_login_enabled: boolean;
  language: string;
  notification_language: string;
  // AMS threshold settings
  ams_humidity_good: number;  // <= this is green
  ams_humidity_fair: number;  // <= this is orange, > is red
  ams_temp_good: number;      // <= this is green/blue
  ams_temp_fair: number;      // <= this is orange, > is red
  ams_history_retention_days: number;  // days to keep AMS sensor history
  // Queue auto-drying settings
  queue_drying_enabled: boolean;  // Auto-dry AMS between queued prints
  queue_drying_block: boolean;  // Block queue until drying completes
  ambient_drying_enabled: boolean;  // Auto-dry idle printers based on humidity regardless of queue
  print_drying_enabled: boolean;  // Continue drying while a print is running on capable hardware
  drying_presets: string;  // JSON blob of drying presets per filament type
  ams_humidity_thresholds: string;  // JSON blob of per-filament humidity thresholds (#1605)
  gcode_snippets: string;  // JSON: per-model G-code injection snippets
  // Scheduled local backup
  local_backup_enabled: boolean;
  local_backup_schedule: string;
  local_backup_time: string;
  local_backup_retention: number;
  local_backup_path: string;
  // Print modal settings
  per_printer_mapping_expanded: boolean;  // Whether custom mapping is expanded by default in print modal
  // Date/time format settings
  date_format: 'system' | 'us' | 'eu' | 'iso';
  time_format: 'system' | '12h' | '24h';
  // Filament tracking
  disable_filament_warnings: boolean;  // Disable filament warnings (print insufficiency and assignment mismatch)
  prefer_lowest_filament: boolean;  // When multiple spools match, prefer lowest remaining filament
  spoolman_enabled: boolean;  // True when the user has switched filament tracking to Spoolman; backend includes this in the /settings/ response even though earlier consumers read it from the dedicated /settings/spoolman endpoint as a string
  auto_add_unknown_rfid: boolean;  // When false, the backend skips auto-creating inventory spools for unknown RFID tags and instead broadcasts an unknown_tag event for the confirmation modal
  spoolman_url: string;
  // Default printer
  default_printer_id: number | null;
  pipeline_max_copies: number;
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
  // Preferred slicer (server-side API / sidecar)
  preferred_slicer: 'bambu_studio' | 'orcaslicer';
  // Desktop "Open in Slicer" override (#1329). Null inherits from
  // preferred_slicer so existing installs behave identically.
  open_in_slicer: 'bambu_studio' | 'orcaslicer' | null;
  // Use the slicer-API sidecar for slicing (in-app modal) vs desktop URI scheme
  use_slicer_api: boolean;
  // Per-install sidecar URLs. Empty string falls back to the env defaults.
  orcaslicer_api_url: string;
  bambu_studio_api_url: string;
  // Prometheus metrics
  prometheus_enabled: boolean;
  prometheus_token: string;
  // Bed cooled threshold
  bed_cooled_threshold: number;
  // Inventory low stock threshold
  low_stock_threshold: number;
  // Session policy (#1706) — admin-set ceiling, hours, [1, 720]
  session_max_hours: number;
  // User email notifications toggle
  user_notifications_enabled: boolean;
  // Default print options
  default_bed_levelling: boolean;
  default_flow_cali: boolean;
  default_vibration_cali: boolean;
  default_layer_inspect: boolean;
  default_timelapse: boolean;
  default_nozzle_offset_cali: boolean;
  // Staggered batch start defaults
  stagger_group_size: number;
  stagger_interval_minutes: number;
  // Plate-clear confirmation
  require_plate_clear: boolean;
  // Shortest job first scheduling
  queue_shortest_first: boolean;
  // Preheat / heat-soak before queued prints (#1468). Master toggle is the
  // default for new queue items; per-item PrintQueueItem.preheat_override can
  // flip the decision per print. Chamber target derives from the loaded AMS
  // filament types via preheat_filament_targets (JSON map of type → °C, max
  // across loaded slots); the per-item override field bypasses derivation.
  preheat_enabled: boolean;
  preheat_filament_targets: string;
  preheat_max_wait_seconds: number;
  preheat_soak_seconds: number;
  // User-configurable presets for the printer-card popovers (JSON arrays of 3 ints).
  // Empty string = use built-in defaults.
  nozzle_temp_presets: string;
  bed_temp_presets: string;
  chamber_temp_presets: string;
  fan_speed_presets: string;
  // Default sidebar order (admin-set for all users)
  default_sidebar_order: string;
  // LDAP authentication
  ldap_enabled: boolean;
  ldap_server_url: string;
  ldap_bind_dn: string;
  ldap_bind_password: string;
  ldap_search_base: string;
  ldap_user_filter: string;
  ldap_security: string;
  ldap_group_mapping: string;
  ldap_auto_provision: boolean;
  ldap_default_group: string;
  obico_enabled: boolean;
  obico_ml_url: string;
  obico_sensitivity: 'low' | 'medium' | 'high';
  obico_action: 'notify' | 'pause' | 'pause_and_off';
  obico_poll_interval: number;
  obico_enabled_printers: string;
  // Inventory forecasting global lead time
  forecast_global_lead_time_days: number;
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
  region?: 'global' | 'china' | null;
}

export interface CloudLoginResponse {
  success: boolean;
  needs_verification: boolean;
  message: string;
  verification_type?: 'email' | 'totp' | null;
  tfa_key?: string | null;
}

// Orca Cloud types — paste-flow PKCE handshake against auth.orcaslicer.com.
// See backend/app/services/orca_cloud.py for the deep dive on why this
// flow is paste-based rather than callback-based.
export type OrcaOAuthProvider = 'google' | 'apple' | 'github';

export interface OrcaAuthStartResponse {
  auth_url: string;
}

export interface OrcaAuthStatusResponse {
  connected: boolean;
  email: string | null;
  user_id: string | null;
}

// Orca profiles are shaped to match Bambu Cloud's SlicerSetting on the wire
// so the frontend can use the same visual components for both surfaces (cards,
// grouped sections, filter bar). Backend handles the source-specific
// transformation in routes/orca_cloud.py::_orca_to_setting.
export interface OrcaProfileMeta {
  setting_id: string;
  name: string;
  type: string;
  version: string | null;
  user_id: string | null;
  updated_time: string | null;
  is_custom: boolean;
}

export interface OrcaProfileListResponse {
  filament: OrcaProfileMeta[];
  printer: OrcaProfileMeta[];
  process: OrcaProfileMeta[];
}

export interface OrcaProfileDetail {
  setting_id: string;
  name: string;
  type: string;
  version: string | null;
  base_id: string | null;
  update_time: string | null;
  setting: Record<string, unknown>;
}

// MakerWorld integration. Full metadata/instance shapes come back as
// Record<string, unknown> — MakerWorld's API adds fields over time, so we
// pass them through verbatim rather than maintaining a brittle mirror.
export interface MakerworldStatus {
  has_cloud_token: boolean;
  can_download: boolean;
}

export interface MakerworldResolvedModel {
  model_id: number;
  profile_id: number | null;
  design: Record<string, unknown>;
  instances: Array<Record<string, unknown>>;
  already_imported_library_ids: number[];
}

export interface MakerworldImportResponse {
  library_file_id: number;
  filename: string;
  folder_id: number | null;
  profile_id: number | null;
  was_existing: boolean;
}

export interface MakerworldRecentImport {
  library_file_id: number;
  filename: string;
  folder_id: number | null;
  thumbnail_path: string | null;
  source_url: string | null;
  created_at: string;
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

export interface StorageLocation {
  id: number;
  name: string;
  identifier: string | null;
  spool_count: number;
  created_at: string;
  updated_at: string;
}

export interface ColorCatalogEntry {
  id: number;
  manufacturer: string;
  color_name: string;
  hex_color: string;
  material: string | null;
  is_default: boolean;
  // #1154: optional multi-colour gradient stops + visual effect.
  extra_colors?: string | null;
  effect_type?: string | null;
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

// Slice request/response — POST /library/files/{id}/slice and /archives/{id}/slice
//
// Two preset shapes are accepted per slot:
//   - Legacy bare integer ids (`*_preset_id`) — pre-cloud-tier clients.
//   - Source-aware refs (`*_preset: PresetRef`) — new SliceModal that picks
//     across cloud / local / standard tiers. Source-aware refs win when both
//     are present in the same payload.
export type PresetSource = 'orca_cloud' | 'cloud' | 'local' | 'standard';
export interface PresetRef {
  source: PresetSource;
  id: string;
}
export interface SliceRequest {
  printer_preset_id?: number;
  process_preset_id?: number;
  filament_preset_id?: number;
  printer_preset?: PresetRef;
  process_preset?: PresetRef;
  filament_preset?: PresetRef;
  // Multi-color: one PresetRef per plate slot, in plate order. Always
  // preferred over the singular `filament_preset` when both are sent; the
  // backend validator promotes a singular into a one-element list when this
  // is omitted, so legacy single-color clients keep working unchanged.
  filament_presets?: PresetRef[];
  plate?: number;
  export_3mf?: boolean;
  // Build-plate override (#1337). When omitted, the slicer uses the process
  // preset's curr_bed_type as-is. Canonical values match BambuStudio /
  // OrcaSlicer's enum: "Cool Plate", "Engineering Plate", "High Temp Plate",
  // "Textured PEI Plate", "Smooth PEI Plate", "Cool Plate (SuperTack)",
  // "Supertack Plate".
  bed_type?: string | null;
}

// GET /api/v1/slicer/presets — unified listing across cloud / local / standard.
export type SlicerCloudStatus = 'ok' | 'not_authenticated' | 'expired' | 'unreachable';
export interface UnifiedPreset {
  id: string;
  name: string;
  source: PresetSource;
  // Populated for the filament slot only — used by the SliceModal multi-color
  // pre-pick to score presets against each plate slot's required (type,
  // colour). Optional because the bundled / standard tier rarely carries a
  // colour (colour is a runtime spool attribute on Bambu) and older API
  // responses pre-date these fields entirely.
  filament_type?: string | null;
  filament_colour?: string | null;
  // Printer-preset names a process / filament preset declares itself
  // compatible with. Populated for the local tier (the slicer's own
  // `compatible_printers`); null for cloud / standard. The SliceModal filters
  // the process / filament dropdowns by the selected printer using this when
  // present (#1325).
  compatible_printers?: string[] | null;
}
export interface UnifiedPresetsBySlot {
  printer: UnifiedPreset[];
  process: UnifiedPreset[];
  filament: UnifiedPreset[];
}
export interface UnifiedPresetsResponse {
  // Priority order: local > orca_cloud > cloud > standard. No cross-tier
  // dedup — every tier surfaces its full list so the user can pick from
  // any source. The order drives auto-pick + visual group rendering only.
  orca_cloud: UnifiedPresetsBySlot;
  cloud: UnifiedPresetsBySlot;
  local: UnifiedPresetsBySlot;
  standard: UnifiedPresetsBySlot;
  cloud_status: SlicerCloudStatus;
  orca_cloud_status: SlicerCloudStatus;
}

// Slicer Pipelines (#1425) — named bundles of preset slots the SliceModal
// can apply in one click. PR A surfaces only the bundle; target_* and
// fanout_strategy round-trip from the backend but the UI doesn't yet expose
// them (they come alive in PR B / PR C).
export interface SlicerPipeline {
  id: number;
  name: string;
  description: string | null;
  printer_preset: PresetRef;
  process_preset: PresetRef;
  filament_presets: PresetRef[];
  bed_type: string | null;
  target_kind: 'specific_printer' | 'printer_class';
  target_printer_id: number | null;
  target_model_class: string | null;
  fanout_strategy: 'max_parallel' | 'fill_one_first' | 'round_robin';
  created_by: number | null;
  created_at: string;
  updated_at: string;
}
export interface SlicerPipelineCreateRequest {
  name: string;
  description?: string | null;
  printer_preset: PresetRef;
  process_preset: PresetRef;
  filament_presets: PresetRef[];
  bed_type?: string | null;
}
export type SlicerPipelineUpdateRequest = Partial<SlicerPipelineCreateRequest> & {
  target_kind?: 'specific_printer' | 'printer_class';
  // ``target_printer_id: 0`` means "clear the target" — the backend maps that
  // to null. Use null in TypeScript for the same intent.
  target_printer_id?: number | null;
  target_model_class?: string | null;
  fanout_strategy?: 'max_parallel' | 'fill_one_first' | 'round_robin';
};
export interface SlicerPipelinesListResponse {
  pipelines: SlicerPipeline[];
}

// Slicer Pipeline runs (#1425 PR B + PR C)
export type PipelineEligibilityKind =
  | 'printer_not_set'
  | 'printer_not_found'
  | 'printer_disabled'
  | 'printer_offline'
  | 'filament_type_mismatch'
  | 'filament_color_mismatch'
  | 'ams_slot_missing'
  | 'filament_unverified'
  | 'no_class_matches'
  | 'class_not_set';
export interface PipelineEligibilityIssue {
  kind: PipelineEligibilityKind;
  slot_index: number | null;
  expected: string | null;
  actual: string | null;
}
export interface PipelinePerPrinterReport {
  printer_id: number;
  printer_name: string;
  ok: boolean;
  issues: PipelineEligibilityIssue[];
}
export interface PipelineEligibilityReport {
  ok: boolean;
  target_kind: 'specific_printer' | 'printer_class';
  target_printer_id: number | null;
  target_printer_name: string | null;
  target_model_class: string | null;
  issues: PipelineEligibilityIssue[];
  printer_reports: PipelinePerPrinterReport[];
}
export interface PipelineJob {
  id: number;
  pipeline_run_id: number;
  copy_index: number;
  assigned_printer_id: number | null;
  assigned_printer_name: string | null;
  queue_entry_id: number | null;
  status:
    | 'pending'
    | 'awaiting_printer'
    | 'queued'
    | 'printing'
    | 'completed'
    | 'failed'
    | 'cancelled';
  error_message: string | null;
  dispatched_at: string | null;
  completed_at: string | null;
}
export interface PipelineRun {
  id: number;
  pipeline_id: number | null;
  pipeline_name: string | null;
  source_library_file_id: number | null;
  source_archive_id: number | null;
  source_filename: string | null;
  parent_run_id: number | null;
  copies: number;
  copies_completed: number;
  copies_failed: number;
  copies_cancelled: number;
  copies_in_progress: number;
  status:
    | 'queued'
    | 'slicing'
    | 'dispatching'
    | 'in_progress'
    | 'completed'
    | 'failed'
    | 'partial_failure'
    | 'cancelled';
  slice_job_id: number | null;
  sliced_library_file_id: number | null;
  eligibility_overridden: boolean;
  error_message: string | null;
  created_by: number | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  jobs: PipelineJob[];
  target_kind: 'specific_printer' | 'printer_class' | null;
  target_printer_id: number | null;
  target_model_class: string | null;
  fanout_strategy: 'max_parallel' | 'fill_one_first' | 'round_robin' | null;
}
export interface PipelineRunListResponse {
  runs: PipelineRun[];
  total: number;
}

export interface SliceResponse {
  library_file_id: number;
  name: string;
  print_time_seconds: number;
  filament_used_g: number;
  filament_used_mm: number;
  used_embedded_settings: boolean;
}

export interface SliceArchiveResponse {
  archive_id: number;
  name: string;
  print_time_seconds: number;
  filament_used_g: number;
  filament_used_mm: number;
  used_embedded_settings: boolean;
}

// Background slice-job lifecycle. POST /slice returns 202 + this shape;
// the frontend polls /slice-jobs/{id} until status is terminal.
export type SliceJobStatus = 'pending' | 'running' | 'completed' | 'failed';

export interface SliceJobEnqueueResponse {
  job_id: number;
  status: SliceJobStatus;
  status_url: string;
}

export interface SliceJobProgress {
  /** Stage label emitted by the slicer ("Generating G-code", "Slicing finished"). */
  stage: string;
  total_percent: number;
  plate_percent: number;
  /** 1-indexed plate position; 0 means "all plates" / final completion. */
  plate_index: number;
  plate_count: number;
  updated_at: number;
  /** When the backend is in the cross-class slice-all loop (#1493), each
   *  per-plate sub-slice's progress is augmented with the loop position
   *  so the toast can show "Plate 2 of 5 — Generating G-code 47%". The
   *  fields are absent on a single-plate slice. */
  multi_plate_index?: number;
  multi_plate_count?: number;
}

export interface SliceJobState {
  job_id: number;
  status: SliceJobStatus;
  kind: 'library_file' | 'archive';
  source_id: number;
  source_name: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  /** Live progress fed by the sidecar's --pipe channel; null until the
   * slicer emits its first frame (early "Initializing" phase) or when
   * the sidecar doesn't support progress. */
  progress: SliceJobProgress | null;
  result?: SliceResponse | SliceArchiveResponse;
  error_status?: number;
  error_detail?: string;
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
  plug_type: 'tasmota' | 'homeassistant' | 'mqtt' | 'rest';
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
  // REST/Webhook fields (required when plug_type="rest")
  rest_on_url: string | null;
  rest_on_body: string | null;
  rest_off_url: string | null;
  rest_off_body: string | null;
  rest_method: string | null;
  rest_headers: string | null;
  rest_status_url: string | null;
  rest_status_path: string | null;
  rest_status_on_value: string | null;
  rest_power_url: string | null;
  rest_power_path: string | null;
  rest_power_multiplier: number;
  rest_energy_url: string | null;
  rest_energy_path: string | null;
  rest_energy_multiplier: number;
  // Lifetime counter, separate from the daily one (#2539). A Shelly reports only
  // this; Today and Yesterday are derived from its hourly snapshots.
  rest_energy_total_path: string | null;
  rest_energy_total_multiplier: number;
  printer_id: number | null;
  enabled: boolean;
  auto_on: boolean;
  auto_off: boolean;
  auto_off_persistent: boolean;
  off_delay_mode: 'time' | 'temperature';
  off_delay_minutes: number;
  off_temp_threshold: number;
  // #1349: auto-off after AMS drying completes.
  auto_off_after_drying: boolean;
  off_delay_after_drying_minutes: number;
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
  plug_type?: 'tasmota' | 'homeassistant' | 'mqtt' | 'rest';
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
  // REST fields
  rest_on_url?: string | null;
  rest_on_body?: string | null;
  rest_off_url?: string | null;
  rest_off_body?: string | null;
  rest_method?: string | null;
  rest_headers?: string | null;
  rest_status_url?: string | null;
  rest_status_path?: string | null;
  rest_status_on_value?: string | null;
  rest_power_url?: string | null;
  rest_power_path?: string | null;
  rest_power_multiplier?: number;
  rest_energy_url?: string | null;
  rest_energy_path?: string | null;
  rest_energy_multiplier?: number;
  rest_energy_total_path?: string | null;
  rest_energy_total_multiplier?: number;
  printer_id?: number | null;
  enabled?: boolean;
  auto_on?: boolean;
  auto_off?: boolean;
  auto_off_persistent?: boolean;
  off_delay_mode?: 'time' | 'temperature';
  off_delay_minutes?: number;
  off_temp_threshold?: number;
  // #1349
  auto_off_after_drying?: boolean;
  off_delay_after_drying_minutes?: number;
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
  plug_type?: 'tasmota' | 'homeassistant' | 'mqtt' | 'rest';
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
  // REST fields
  rest_on_url?: string | null;
  rest_on_body?: string | null;
  rest_off_url?: string | null;
  rest_off_body?: string | null;
  rest_method?: string | null;
  rest_headers?: string | null;
  rest_status_url?: string | null;
  rest_status_path?: string | null;
  rest_status_on_value?: string | null;
  rest_power_url?: string | null;
  rest_power_path?: string | null;
  rest_power_multiplier?: number;
  rest_energy_url?: string | null;
  rest_energy_path?: string | null;
  rest_energy_multiplier?: number;
  rest_energy_total_path?: string | null;
  rest_energy_total_multiplier?: number;
  printer_id?: number | null;
  enabled?: boolean;
  auto_on?: boolean;
  auto_off?: boolean;
  auto_off_persistent?: boolean;
  off_delay_mode?: 'time' | 'temperature';
  off_delay_minutes?: number;
  off_temp_threshold?: number;
  // #1349
  auto_off_after_drying?: boolean;
  off_delay_after_drying_minutes?: number;
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
  // Set by the dispatch scheduler when the assigned spool can't satisfy
  // any required slot's grams (#1496). Surfaced on the queue row as a
  // "filament short" badge; cleared on a successful ▶ click (live recheck).
  filament_short: boolean;
  // Persistent "Print Anyway" acknowledgement — once true the scheduler
  // skips the deficit check for this item (#1698-followup). Set by the
  // start route when skip_filament_check=true, or at queue creation if
  // PrintModal's deficit warning was acknowledged.
  skip_filament_check: boolean;
  ams_mapping: number[] | null;  // AMS slot mapping for multi-color prints
  filament_overrides: Array<{ slot_id: number; type: string; color: string; color_name?: string; force_color_match?: boolean }> | null;  // Filament overrides for model-based assignment
  plate_id: number | null;  // Plate ID for multi-plate 3MF files
  // Print options
  bed_levelling: boolean;
  flow_cali: boolean;
  vibration_cali: boolean;
  layer_inspect: boolean;
  timelapse: boolean;
  use_ams: boolean;
  nozzle_offset_cali: boolean;
  preheat_override: 'inherit' | 'on' | 'off';
  preheat_chamber_target_override: number | null;
  status: 'pending' | 'printing' | 'completed' | 'failed' | 'skipped' | 'cancelled';
  started_at: string | null;
  completed_at: string | null;
  error_message: string | null;
  created_at: string;
  archive_name?: string | null;
  archive_thumbnail?: string | null;
  // True when the linked archive has been soft-deleted; archive_name /
  // archive_thumbnail / downstream metadata are left null in that case so
  // the UI doesn't 404-storm the now-missing endpoints (#1348 follow-up).
  archive_deleted?: boolean;
  library_file_name?: string | null;
  library_file_thumbnail?: string | null;
  printer_name?: string | null;
  print_time_seconds?: number | null;  // Estimated print time from archive or library file
  filament_used_grams?: number | null;  // Estimated print weight from archive or library file
  filament_type?: string | null;  // e.g. "PLA", "PETG"
  filament_color?: string | null;  // Hex RGBA from the slicer
  bed_type?: string | null;  // Build plate type for this print (per-plate accurate, #1281)
  // User tracking (Issue #206)
  created_by_id?: number | null;
  created_by_username?: string | null;
  // Batch grouping
  batch_id?: number | null;
  batch_name?: string | null;
  // Shortest-job-first scheduling
  been_jumped?: boolean;
  // Auto-print G-code injection
  gcode_injection?: boolean;
  cleanup_library_after_dispatch?: boolean;
}

export interface PrintBatch {
  id: number;
  name: string;
  archive_id: number | null;
  library_file_id: number | null;
  quantity: number;
  status: string;
  created_at: string;
  created_by_id: number | null;
  created_by_username: string | null;
  pending_count: number;
  printing_count: number;
  completed_count: number;
  failed_count: number;
  cancelled_count: number;
}

export interface PrintQueueItemCreate {
  printer_id?: number | null;  // null = unassigned
  target_model?: string | null;  // Target printer model (mutually exclusive with printer_id)
  target_location?: string | null;  // Target location filter (only used with target_model)
  filament_overrides?: Array<{ slot_id: number; type: string; color: string; color_name?: string; force_color_match?: boolean }> | null;
  archive_id?: number | null;
  library_file_id?: number | null;
  scheduled_time?: string | null;
  require_previous_success?: boolean;
  auto_off_after?: boolean;
  manual_start?: boolean;  // Requires manual trigger to start (staged)
  insert_at_top?: boolean;  // Insert ahead of other pending items in the same queue scope
  insert_position?: number | null;  // 1-indexed insertion position for priority queueing
  // PrintModal "Print Anyway" on the deficit warning — persisted so the
  // scheduler doesn't immediately re-flag this item (#1698-followup).
  skip_filament_check?: boolean;
  ams_mapping?: number[] | null;  // AMS slot mapping for multi-color prints
  plate_id?: number | null;  // Plate ID for multi-plate 3MF files
  // Print options
  bed_levelling?: boolean;
  flow_cali?: boolean;
  vibration_cali?: boolean;
  layer_inspect?: boolean;
  timelapse?: boolean;
  use_ams?: boolean;
  nozzle_offset_cali?: boolean;
  preheat_override?: 'inherit' | 'on' | 'off';
  preheat_chamber_target_override?: number | null;
  // Auto-print G-code injection
  gcode_injection?: boolean;
  // Batch: create multiple copies (creates a batch if > 1)
  quantity?: number;
  // Existing batch to add this item into (multi-plate auto-batch flow).
  batch_id?: number | null;
  // Project to associate the resulting archive with
  project_id?: number;
  // Delete transient uploaded library file after scheduler creates the archive
  cleanup_library_after_dispatch?: boolean;
}

export interface PrintBatchCreate {
  name: string;
  archive_id?: number | null;
  library_file_id?: number | null;
  /** When set, the listed pending items are assigned to the new batch
   *  (manual "Group as batch"). When omitted/empty, an empty batch is
   *  returned so the client can pass batch_id on subsequent addToQueue calls. */
  item_ids?: number[];
}

export interface PrintQueueItemUpdate {
  printer_id?: number | null;  // null = unassign
  target_model?: string | null;  // Target printer model (mutually exclusive with printer_id)
  target_location?: string | null;  // Target location filter (only used with target_model)
  filament_overrides?: Array<{ slot_id: number; type: string; color: string; color_name?: string; force_color_match?: boolean }> | null;
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
  nozzle_offset_cali?: boolean;
  preheat_override?: 'inherit' | 'on' | 'off';
  preheat_chamber_target_override?: number | null;
  // Auto-print G-code injection
  gcode_injection?: boolean;
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
  nozzle_offset_cali?: boolean;
  preheat_override?: 'inherit' | 'on' | 'off';
  preheat_chamber_target_override?: number | null;
  // Auto-print G-code injection
  gcode_injection?: boolean;
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
export type ProviderType = 'callmebot' | 'ntfy' | 'pushover' | 'telegram' | 'email' | 'discord' | 'webhook' | 'homeassistant';

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
  on_print_missing_spool_assignment: boolean;
  // Printer status events
  on_printer_offline: boolean;
  on_printer_error: boolean;
  on_ai_failure_detection: boolean;
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
  // Bed cooled
  on_bed_cooled: boolean;
  // First layer complete
  on_first_layer_complete: boolean;
  // Inventory stock alerts
  on_stock_reorder_alert: boolean;
  on_stock_break_alert: boolean;
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
  on_print_missing_spool_assignment?: boolean;
  // Printer status events
  on_printer_offline?: boolean;
  on_printer_error?: boolean;
  on_ai_failure_detection?: boolean;
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
  // Bed cooled
  on_bed_cooled?: boolean;
  // First layer complete
  on_first_layer_complete?: boolean;
  // Inventory stock alerts
  on_stock_reorder_alert?: boolean;
  on_stock_break_alert?: boolean;
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
  on_print_missing_spool_assignment?: boolean;
  // Printer status events
  on_printer_offline?: boolean;
  on_printer_error?: boolean;
  on_ai_failure_detection?: boolean;
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
  // Bed cooled
  on_bed_cooled?: boolean;
  // First layer complete
  on_first_layer_complete?: boolean;
  // Inventory stock alerts
  on_stock_reorder_alert?: boolean;
  on_stock_break_alert?: boolean;
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
export type GitProviderType = 'github' | 'gitea' | 'forgejo' | 'gitlab';

export interface GitHubBackupConfig {
  id: number;
  repository_url: string;
  has_token: boolean;
  branch: string;
  provider: GitProviderType;
  allow_insecure_http: boolean;
  schedule_enabled: boolean;
  schedule_type: ScheduleType;
  backup_kprofiles: boolean;
  backup_cloud_profiles: boolean;
  backup_settings: boolean;
  backup_spools: boolean;
  backup_archives: boolean;
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
  provider?: GitProviderType;
  allow_insecure_http?: boolean;
  schedule_enabled?: boolean;
  schedule_type?: ScheduleType;
  backup_kprofiles?: boolean;
  backup_cloud_profiles?: boolean;
  backup_settings?: boolean;
  backup_spools?: boolean;
  backup_archives?: boolean;
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

export interface LocalBackupStatus {
  enabled: boolean;
  schedule: string;
  time: string;
  retention: number;
  path: string;
  default_path: string;
  is_running: boolean;
  last_backup_at: string | null;
  last_status: string | null;
  last_message: string | null;
  next_run: string | null;
  timezone: string;
}

export interface LocalBackupFile {
  filename: string;
  size: number;
  created_at: string;
}

/** Result of writing a probe file into the configured backup directory (#2544). */
export interface LocalBackupPathCheck {
  writable: boolean;
  path: string;
  /** 'ok' | 'sandboxed' | 'read_only' | 'permission_denied' | 'no_space' | 'not_a_directory' | 'missing' | 'error' */
  code: string;
  /** Raw OS error, shown as-is under the translated explanation. */
  detail: string | null;
  /** Copy-pasteable fix (systemd drop-in, compose snippet) — not translated. */
  remedy: string | null;
  message: string;
  /** 'container_ephemeral' — writable, but the backups die with the container. */
  warning: string | null;
}

export interface ObicoDetectionEvent {
  printer_id: number;
  task_name: string;
  timestamp: string;
  current_p: number;
  score: number;
  class: 'safe' | 'warning' | 'failure';
  detections: number;
}

export interface ObicoStatus {
  is_running: boolean;
  last_error: string | null;
  per_printer: Record<string, { class: string; frame_count: number; score: number }>;
  thresholds: { low: number; high: number };
  history: ObicoDetectionEvent[];
  enabled: boolean;
  ml_url: string;
  sensitivity: 'low' | 'medium' | 'high';
  action: 'notify' | 'pause' | 'pause_and_off';
  poll_interval: number;
  external_url_configured: boolean;
}

export interface ObicoTestConnection {
  ok: boolean;
  status_code: number | null;
  body: string | null;
  error: string | null;
}

export interface GitHubTestConnectionResponse {
  success: boolean;
  message: string;
  repo_name: string | null;
  permissions: Record<string, boolean> | null;
  // true = confirmed private, false = confirmed public/internal,
  // null = could not determine. Backend rejects save unless true.
  is_private: boolean | null;
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
  filament_vendor: string | null;
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

export interface SpoolmanVendor {
  id: number;
  name: string;
}

export interface SpoolmanFilamentEntry {
  id: number;
  name: string;
  material: string | null;
  color_hex: string | null;
  color_name: string | null;
  weight: number | null;
  spool_weight: number | null;
  vendor: SpoolmanVendor | null;
}

// Inventory types
// Label printing (#809). Mirror of backend.app.services.label_renderer.TemplateName.
export type SpoolLabelTemplate =
  | 'ams_holder_74x33'
  | 'ams_holder_75x55'
  | 'box_40x30'
  | 'box_62x29'
  | 'avery_5160'
  | 'avery_l7160';

export interface InventorySpool {
  id: number;
  material: string;
  subtype: string | null;
  color_name: string | null;
  // True when color_name was synthesised from subtype because Spoolman has no
  // stored value (Spoolman-backed inventory only). The edit form uses this to
  // leave the input blank, so the user doesn't round-trip the synth value
  // back to Spoolman as if it were a real user-set color_name (#1319).
  color_name_is_synthesized?: boolean;
  rgba: string | null;
  // Multi-colour gradient stops (#1154): comma-separated 6/8-char hex.
  extra_colors: string | null;
  // Visual effect overlay: sparkle | wood | marble | glow | matte.
  effect_type: string | null;
  brand: string | null;
  label_weight: number;
  core_weight: number;
  core_weight_catalog_id: number | null;
  weight_used: number;
  // Anchor for the resettable "Total Consumed" display (#1390). The
  // counter shown on the Inventory page is `weight_used - weight_used_baseline`;
  // remaining is still `label_weight - weight_used`, so "Reset usage to 0"
  // zeroes the counter without disturbing remaining. Optional for back-compat
  // with rows from a pre-migration DB snapshot — default to 0.
  weight_used_baseline?: number;
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
  cost_per_kg: number | null;
  last_scale_weight: number | null;
  last_weighed_at: string | null;
  // User-defined category + per-spool low-stock threshold override (#729).
  category: string | null;
  low_stock_threshold_pct: number | null;
  k_profiles?: SpoolKProfile[];
  storage_location?: string | null;
  location_id?: number | null;
}

export interface SpoolmanBulkCreateResult {
  created: InventorySpool[];
  requested_count: number;
  failed_count: number;
}

// ── CSV import/export (#1576) ──────────────────────────────────────────────
/** One row's outcome from the import preview / real import. */
export interface CsvImportRow {
  row_number: number;
  status: 'valid' | 'error' | 'skipped';
  reason: string | null;
  material: string | null;
  brand: string | null;
  color_name: string | null;
  rgba: string | null;
  /** rgba/extra_colors/effect_type were filled from the Color Catalog. */
  resolved_color: boolean;
  /** The catalog match came from a different material's variant (no exact
   *  material match). Shown as a warning in the preview. */
  cross_material_color: boolean;
  /** An active spool with the same material+brand+color already exists.
   *  Informational only — the import still creates the row. */
  duplicate_of_existing: boolean;
}

/** Dry-run preview: per-row classification, no rows written. */
export interface CsvImportPreview {
  columns: string[];
  total: number;
  valid_count: number;
  error_count: number;
  skipped_count: number;
  rows: CsvImportRow[];
  warnings: string[];
}

/** Summary returned after a real (non-dry-run) import. */
export interface CsvImportResult {
  created: number;
  skipped: number;
  errors: number;
  error_rows: CsvImportRow[];
}

export interface SpoolUsageRecord {
  id: number;
  spool_id: number;
  printer_id: number | null;
  print_name: string | null;
  weight_used: number;
  percent_used: number;
  status: string;
  cost: number | null;
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
  pending_config?: boolean;  // Slot was empty at assign time; will configure on insert
  created_at: string;
  ams_label?: string | null;  // User-defined friendly name for the AMS unit
}

export interface FilamentSkuSettings {
  id: number;
  material: string;
  subtype: string | null;
  brand: string | null;
  color_name: string | null;
  lead_time_days: number;
  safety_margin_value: number;
  safety_margin_unit: 'days' | 'g';
  alerts_snoozed: boolean;
}

export interface ShoppingListItem {
  id: number;
  material: string;
  subtype: string | null;
  brand: string | null;
  color_name: string | null;
  quantity_spools: number;
  note: string | null;
  status: 'pending' | 'purchased' | 'received';
  purchased_at: string | null;
  added_at: string;
}

export interface ShoppingListItemCreate {
  material: string;
  subtype: string | null;
  brand: string | null;
  color_name: string | null;
  quantity_spools: number;
  note?: string | null;
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
  is_ha_addon?: boolean;
  is_windows_installer?: boolean;
  update_method?: 'docker' | 'git' | 'ha_addon' | 'windows_installer';
  installer_download_url?: string | null;
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
  | 'printers:read' | 'printers:create' | 'printers:update' | 'printers:delete' | 'printers:control' | 'printers:files' | 'printers:ams_rfid' | 'printers:clear_plate'
  | 'archives:read' | 'archives:read_own' | 'archives:read_all' | 'archives:create'
  | 'archives:update_own' | 'archives:update_all' | 'archives:delete_own' | 'archives:delete_all'
  | 'archives:reprint_own' | 'archives:reprint_all' | 'archives:purge'
  | 'queue:read' | 'queue:read_own' | 'queue:read_all' | 'queue:create'
  | 'queue:update_own' | 'queue:update_all' | 'queue:delete_own' | 'queue:delete_all'
  | 'queue:reorder'
  | 'library:read' | 'library:read_own' | 'library:read_all' | 'library:upload'
  | 'library:update_own' | 'library:update_all' | 'library:delete_own' | 'library:delete_all'
  | 'library:purge'
  | 'projects:read' | 'projects:create' | 'projects:update' | 'projects:delete'
  | 'filaments:read' | 'filaments:create' | 'filaments:update' | 'filaments:delete'
  | 'inventory:read' | 'inventory:create' | 'inventory:update' | 'inventory:delete' | 'inventory:view_assignments'
  | 'inventory:forecast_read' | 'inventory:forecast_write'
  | 'smart_plugs:read' | 'smart_plugs:create' | 'smart_plugs:update' | 'smart_plugs:delete' | 'smart_plugs:control'
  | 'camera:view'
  | 'maintenance:read' | 'maintenance:create' | 'maintenance:update' | 'maintenance:delete'
  | 'kprofiles:read' | 'kprofiles:create' | 'kprofiles:update' | 'kprofiles:delete'
  | 'notifications:read' | 'notifications:create' | 'notifications:update' | 'notifications:delete' | 'notifications:user_email'
  | 'notification_templates:read' | 'notification_templates:update'
  | 'external_links:read' | 'external_links:create' | 'external_links:update' | 'external_links:delete'
  | 'discovery:scan'
  | 'firmware:read' | 'firmware:update'
  | 'ams_history:read'
  | 'stats:read' | 'stats:filter_by_user'
  | 'system:read'
  | 'settings:read' | 'settings:update' | 'settings:backup' | 'settings:restore'
  | 'github:backup' | 'github:restore'
  | 'cloud:auth' | 'orca_cloud:auth'
  | 'makerworld:view' | 'makerworld:import'
  | 'api_keys:read' | 'api_keys:create' | 'api_keys:update' | 'api_keys:delete'
  | 'users:read' | 'users:create' | 'users:update' | 'users:delete'
  | 'groups:read' | 'groups:create' | 'groups:update' | 'groups:delete'
  | 'pipelines:read' | 'pipelines:write' | 'pipelines:run'
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

// User email notification preferences
export interface UserEmailPreferences {
  notify_print_start: boolean;
  notify_print_complete: boolean;
  notify_print_failed: boolean;
  notify_print_stopped: boolean;
}

// Auth types
export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  access_token?: string;
  token_type?: string;
  user?: UserResponse;
  /** Set when 2FA verification is required before a full token is issued. */
  requires_2fa?: boolean;
  pre_auth_token?: string;
  two_fa_methods?: string[];
}

export interface UserResponse {
  id: number;
  username: string;
  email?: string;
  role: string;  // Deprecated, kept for backward compatibility
  is_active: boolean;
  is_admin: boolean;  // Compatibility field computed from Administrators group membership
  auth_source: string;  // "local" or "ldap"
  groups: GroupBrief[];
  permissions: Permission[];  // All permissions from groups
  created_at: string;
}

export interface UserCreate {
  username: string;
  password?: string;  // Optional when advanced auth is enabled
  email?: string;
  group_ids?: number[];
}

export interface UserUpdate {
  username?: string;
  password?: string;
  email?: string;
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

// 2FA / MFA interfaces
export interface TwoFAStatus {
  totp_enabled: boolean;
  email_otp_enabled: boolean;
  backup_codes_remaining: number;
}

export interface TOTPSetupResponse {
  secret: string;
  qr_code_b64: string;
  issuer: string;
}

export interface TOTPEnableResponse {
  message: string;
  backup_codes: string[];
}

export interface BackupCodesResponse {
  backup_codes: string[];
  message: string;
}

export interface TwoFAVerifyRequest {
  pre_auth_token: string;
  code: string;
  method: 'totp' | 'email' | 'backup';
}

/**
 * A URL that is known to be same-origin (a relative path starting with ``/``).
 *
 * Branded so that producers of same-origin URLs (e.g. ``api.oidcProviderIconUrl``)
 * can be distinguished from arbitrary strings at the type level.  The brand
 * is compile-time only; at runtime these are plain strings.
 *
 * Purpose: CSP-safe image sources for ``<img src=...>``. The strict
 * ``img-src 'self' data: blob:`` CSP rejects anything that isn't same-origin,
 * so callers that demand a ``SameOriginUrl`` get a compile-time guarantee
 * that no external URL slips through.
 */
export type SameOriginUrl = string & { readonly __brand: 'SameOriginUrl' };

// OIDC interfaces
export interface OIDCProvider {
  id: number;
  name: string;
  issuer_url: string;
  client_id: string;
  scopes: string;
  is_enabled: boolean;
  auto_create_users: boolean;
  auto_link_existing_accounts: boolean;
  email_claim: string;
  require_email_verified: boolean;
  icon_url?: string | null;
  default_group_id?: number | null;
  // True when the backend has cached icon bytes for this provider.
  // Login page / admin preview consume this via the proxy URL
  // /api/v1/auth/oidc/providers/{id}/icon (#1333) so the SPA never
  // hotlinks the external icon URL — that would require loosening
  // the strict img-src CSP.  Required, not optional: the backend always
  // includes this field in the response (Pydantic default-False is
  // populated unconditionally in the route handler).
  has_icon: boolean;
  // #1589: when true, the LoginPage redirects unauthenticated visitors
  // straight to this provider on mount. At most one provider may carry this.
  is_autologin: boolean;
}

export interface OIDCProviderCreate {
  name: string;
  issuer_url: string;
  client_id: string;
  client_secret: string;
  scopes?: string;
  is_enabled?: boolean;
  auto_create_users?: boolean;
  auto_link_existing_accounts?: boolean;
  email_claim?: string;
  require_email_verified?: boolean;
  icon_url?: string | null;
  default_group_id?: number | null;
  is_autologin?: boolean;  // #1589
}

export interface OIDCLink {
  id: number;
  provider_id: number;
  provider_name: string;
  provider_email?: string | null;
  created_at: string;
}

export interface TestSMTPRequest {
  test_recipient: string;
}

export interface TestSMTPResponse {
  success: boolean;
  message: string;
}

export interface AdvancedAuthStatus {
  advanced_auth_enabled: boolean;
  smtp_configured: boolean;
  // #1589: false hides the username/password form on the LoginPage; the env
  // var BAMBUDDY_LOCAL_LOGIN=true on the server flips this back to true so
  // the recovery path remains visible.
  local_login_enabled: boolean;
  // #1589: when set, LoginPage redirects to this provider's authorize URL
  // on mount unless ?fallback=local is in the URL or the redirect times out.
  autologin_provider_id: number | null;
}

export interface LDAPStatus {
  ldap_enabled: boolean;
  ldap_configured: boolean;
}

export interface EncryptionRowCounts {
  oidc_providers: number;
  user_totp: number;
}

export interface EncryptionStatus {
  key_configured: boolean;
  key_source: 'env' | 'file' | 'generated' | 'none';
  legacy_plaintext_rows: EncryptionRowCounts;
  encrypted_rows: EncryptionRowCounts;
  decryption_broken: boolean;
  // B2: count of rows skipped during the last legacy re-encryption migration.
  // Surfaced via a yellow secondary banner in SecurityStatusCard.
  migration_error_count: number;
}

export interface LDAPTestResponse {
  success: boolean;
  message: string;
}

export interface LDAPSearchResult {
  username: string;
  email: string | null;
  display_name: string | null;
  dn: string;
  already_provisioned: boolean;
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
  // LDAP Authentication
  getLDAPStatus: () => request<LDAPStatus>('/auth/ldap/status'),
  getEncryptionStatus: () => request<EncryptionStatus>('/auth/encryption-status'),
  testLDAP: () =>
    request<LDAPTestResponse>('/auth/ldap/test', {
      method: 'POST',
    }),
  searchLDAPDirectory: (q: string) =>
    request<LDAPSearchResult[]>(`/auth/ldap/search?q=${encodeURIComponent(q)}`),
  provisionLDAPUser: (username: string) =>
    request<UserResponse>('/auth/ldap/provision', {
      method: 'POST',
      body: JSON.stringify({ username }),
    }),
  forgotPassword: (data: ForgotPasswordRequest) =>
    request<ForgotPasswordResponse>('/auth/forgot-password', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  // H-6: Confirm password reset using the token from the emailed link
  forgotPasswordConfirm: (token: string, newPassword: string) =>
    request<ForgotPasswordResponse>('/auth/forgot-password/confirm', {
      method: 'POST',
      body: JSON.stringify({ token, new_password: newPassword }),
    }),
  resetUserPassword: (data: ResetPasswordRequest) =>
    request<ResetPasswordResponse>('/auth/reset-password', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  // 2FA - status
  get2FAStatus: () => request<TwoFAStatus>('/auth/2fa/status'),

  // 2FA - TOTP
  setupTOTP: () => request<TOTPSetupResponse>('/auth/2fa/totp/setup', { method: 'POST' }),
  enableTOTP: (code: string) =>
    request<TOTPEnableResponse>('/auth/2fa/totp/enable', {
      method: 'POST',
      body: JSON.stringify({ code }),
    }),
  disableTOTP: (code: string) =>
    request<{ message: string }>('/auth/2fa/totp/disable', {
      method: 'POST',
      body: JSON.stringify({ code }),
    }),
  regenerateBackupCodes: (code: string) =>
    request<BackupCodesResponse>('/auth/2fa/totp/regenerate-backup-codes', {
      method: 'POST',
      body: JSON.stringify({ code }),
    }),

  // 2FA - Email OTP
  // Step 1: send a verification code to the user's email (proof of possession)
  enableEmailOTP: () =>
    request<{ message: string; setup_token: string }>('/auth/2fa/email/enable', { method: 'POST' }),
  // Step 2: confirm with the code received by email
  confirmEnableEmailOTP: (setup_token: string, code: string) =>
    request<{ message: string }>('/auth/2fa/email/enable/confirm', {
      method: 'POST',
      body: JSON.stringify({ setup_token, code }),
    }),
  // Disable requires account password for re-auth
  disableEmailOTP: (password: string) =>
    request<{ message: string }>('/auth/2fa/email/disable', {
      method: 'POST',
      body: JSON.stringify({ password }),
    }),
  sendEmailOTP: (preAuthToken: string) =>
    request<{ message: string; pre_auth_token?: string }>('/auth/2fa/email/send', {
      method: 'POST',
      body: JSON.stringify({ pre_auth_token: preAuthToken }),
    }),

  // 2FA - verify (completes login)
  verify2FA: (data: TwoFAVerifyRequest) =>
    request<LoginResponse>('/auth/2fa/verify', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  // 2FA - admin
  admin2FADisable: (userId: number) =>
    request<{ message: string }>(`/auth/2fa/admin/${userId}`, { method: 'DELETE' }),

  // OIDC providers (public list)
  getOIDCProviders: () => request<OIDCProvider[]>('/auth/oidc/providers'),

  // OIDC providers (admin)
  getOIDCProvidersAll: () => request<OIDCProvider[]>('/auth/oidc/providers/all'),
  createOIDCProvider: (data: OIDCProviderCreate) =>
    request<OIDCProvider>('/auth/oidc/providers', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateOIDCProvider: (id: number, data: Partial<OIDCProviderCreate>) =>
    request<OIDCProvider>(`/auth/oidc/providers/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  deleteOIDCProvider: (id: number) =>
    request<{ message: string }>(`/auth/oidc/providers/${id}`, { method: 'DELETE' }),

  // OIDC provider icon proxy (#1333) — same-origin path so the strict
  // img-src CSP stays in force. Returns a SameOriginUrl-branded string
  // so a future caller can't accidentally substitute an attacker-
  // controlled URL where this is consumed.
  oidcProviderIconUrl: (id: number): SameOriginUrl =>
    `/api/v1/auth/oidc/providers/${id}/icon` as SameOriginUrl,
  deleteOIDCProviderIcon: (id: number) =>
    request<void>(`/auth/oidc/providers/${id}/icon`, { method: 'DELETE' }),
  refreshOIDCProviderIcon: (id: number) =>
    request<OIDCProvider>(`/auth/oidc/providers/${id}/icon/refresh`, { method: 'POST' }),

  // OIDC authorize URL
  getOIDCAuthorizeUrl: (providerId: number) =>
    request<{ auth_url: string }>(`/auth/oidc/authorize/${providerId}`),

  // OIDC exchange token for JWT
  exchangeOIDCToken: (oidcToken: string) =>
    request<LoginResponse>('/auth/oidc/exchange', {
      method: 'POST',
      body: JSON.stringify({ oidc_token: oidcToken }),
    }),

  // OIDC links for current user
  getOIDCLinks: () => request<OIDCLink[]>('/auth/oidc/links'),
  deleteOIDCLink: (providerId: number) =>
    request<{ message: string }>(`/auth/oidc/links/${providerId}`, { method: 'DELETE' }),

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

  // User Email Notifications
  getUserEmailPreferences: () =>
    request<UserEmailPreferences>('/user-notifications/preferences'),
  updateUserEmailPreferences: (data: UserEmailPreferences) =>
    request<UserEmailPreferences>('/user-notifications/preferences', {
      method: 'PUT',
      body: JSON.stringify(data),
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
  getDeveloperModeWarnings: () =>
    request<{ printer_id: number; name: string }[]>('/printers/developer-mode-warnings'),
  getAvailableFilaments: (model: string, location?: string) => {
    const params = new URLSearchParams({ model });
    if (location) params.set('location', location);
    return request<Array<{ type: string; color: string; tray_info_idx: string; tray_sub_brands: string; extruder_id: number | null }>>(`/printers/available-filaments?${params}`);
  },
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

  // Print Speed Control
  setPrintSpeed: (printerId: number, mode: number) =>
    request<{ success: boolean; message: string }>(`/printers/${printerId}/print-speed?mode=${mode}`, {
      method: 'POST',
    }),

  setNozzleTemperature: (printerId: number, target: number, nozzle: number = 0) =>
    request<{ success: boolean; message: string }>(
      `/printers/${printerId}/temperature/nozzle?target=${target}&nozzle=${nozzle}`,
      { method: 'POST' }
    ),

  setBedTemperature: (printerId: number, target: number) =>
    request<{ success: boolean; message: string }>(`/printers/${printerId}/temperature/bed?target=${target}`, {
      method: 'POST',
    }),

  setChamberTemperature: (printerId: number, target: number) =>
    request<{ success: boolean; message: string }>(`/printers/${printerId}/temperature/chamber?target=${target}`, {
      method: 'POST',
    }),

  setFanSpeed: (printerId: number, fan: 'part' | 'aux' | 'chamber', speed: number) =>
    request<{ success: boolean; message: string }>(`/printers/${printerId}/fan-speed?fan=${fan}&speed=${speed}`, {
      method: 'POST',
    }),

  selectExtruder: (printerId: number, extruder: number) =>
    request<{ success: boolean; message: string }>(`/printers/${printerId}/select-extruder?extruder=${extruder}`, {
      method: 'POST',
    }),

  setAirductMode: (printerId: number, mode: 'cooling' | 'heating') =>
    request<{ success: boolean; message: string }>(`/printers/${printerId}/airduct-mode?mode=${mode}`, {
      method: 'POST',
    }),

  // Bed (Z-axis) jog
  bedJog: (printerId: number, distance: number, force: boolean = false) =>
    request<{ success: boolean; message: string }>(
      `/printers/${printerId}/bed-jog?distance=${distance}&force=${force}`,
      { method: 'POST' }
    ),
  xyJog: (printerId: number, x: number, y: number) =>
    request<{ success: boolean; message: string }>(
      `/printers/${printerId}/xy-jog?x=${x}&y=${y}`,
      { method: 'POST' }
    ),
  extruderJog: (printerId: number, distance: number) =>
    request<{ success: boolean; message: string }>(
      `/printers/${printerId}/extruder-jog?distance=${distance}`,
      { method: 'POST' }
    ),
  homeAxes: (printerId: number, axes: 'z' | 'xy' | 'all' = 'z') =>
    request<{ success: boolean; message: string }>(
      `/printers/${printerId}/home-axes?axes=${axes}`,
      { method: 'POST' }
    ),

  // Chamber Light Control
  setChamberLight: (printerId: number, on: boolean) =>
    request<{ success: boolean; message: string }>(`/printers/${printerId}/chamber-light?on=${on}`, {
      method: 'POST',
    }),

  // AMS Drying Control
  startDrying: (printerId: number, amsId: number, temp: number, duration: number, filament: string = '', rotateTray: boolean = false) =>
    request<{ status: string; ams_id: number; temp: number; duration: number }>(
      `/printers/${printerId}/drying/start?ams_id=${amsId}&temp=${temp}&duration=${duration}&filament=${encodeURIComponent(filament)}&rotate_tray=${rotateTray}`,
      { method: 'POST' }
    ),
  stopDrying: (printerId: number, amsId: number) =>
    request<{ status: string; ams_id: number }>(
      `/printers/${printerId}/drying/stop?ams_id=${amsId}`,
      { method: 'POST' }
    ),

  // AMS Filament Backup (auto-switch to a backup spool when one runs out)
  setAmsFilamentBackup: (printerId: number, enabled: boolean) =>
    request<{ success: boolean; ams_filament_backup: boolean }>(
      `/printers/${printerId}/ams-backup?enabled=${enabled}`,
      { method: 'POST' }
    ),

  // Per-globalTrayId remaining grams for this printer's inventory-bound slots
  // (#1766). Drives the client-side "Prefer Lowest Remaining Filament" sort
  // when computing the AMS mapping; mirrors backend `_build_inventory_remain_overrides`
  // so internal and Spoolman modes both work uniformly.
  getInventoryRemain: (printerId: number) =>
    request<{ inventory_remain_g: Record<string, number> }>(
      `/printers/${printerId}/inventory-remain`,
    ),

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

  // HMS Errors
  clearHMSErrors: (printerId: number) =>
    request<{ success: boolean; message: string }>(`/printers/${printerId}/hms/clear`, { method: 'POST' }),
  executeHMSAction: (printerId: number, data: HMSActionBody) =>
    request<{ success: boolean; message: string }>(`/printers/${printerId}/hms/execute-action`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  // AMS Control
  refreshAmsSlot: (printerId: number, amsId: number, slotId: number) =>
    request<{ success: boolean; message: string }>(
      `/printers/${printerId}/ams/${amsId}/slot/${slotId}/refresh`,
      { method: 'POST' }
    ),

  // Load filament from a tray. trayId: 0-15 for AMS (amsId*4+slotId), 254 for external spool.
  loadAmsTray: (printerId: number, trayId: number) =>
    request<{ success: boolean; message: string }>(
      `/printers/${printerId}/ams/load?tray_id=${trayId}`,
      { method: 'POST' }
    ),

  // Unload the currently loaded filament.
  unloadAms: (printerId: number) =>
    request<{ success: boolean; message: string }>(
      `/printers/${printerId}/ams/unload`,
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
    withStreamToken(`${API_BASE}/printers/${printerId}/files/plate-thumbnail/${plateIndex}?path=${encodeURIComponent(path)}`),
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
  getArchives: (printerId?: number, projectId?: number, limit = 10000, offset = 0, dateFrom?: string, dateTo?: string) => {
    const params = new URLSearchParams();
    if (printerId) params.set('printer_id', String(printerId));
    if (projectId) params.set('project_id', String(projectId));
    params.set('limit', String(limit));
    params.set('offset', String(offset));
    if (dateFrom) params.set('date_from', dateFrom);
    if (dateTo) params.set('date_to', dateTo);
    return request<Archive[]>(`/archives/?${params}`);
  },
  getArchivesSlim: (dateFrom?: string, dateTo?: string, createdById?: number) => {
    const params = new URLSearchParams();
    if (dateFrom) params.set('date_from', dateFrom);
    if (dateTo) params.set('date_to', dateTo);
    if (createdById !== undefined) params.set('created_by_id', String(createdById));
    const qs = params.toString();
    return request<ArchiveSlim[]>(`/archives/slim${qs ? `?${qs}` : ''}`);
  },
  getArchive: (id: number) => request<Archive>(`/archives/${id}`),
  getArchiveRuns: (id: number) => request<PrintLogResponse>(`/archives/${id}/runs`),
  /**
   * Pre-flight for the delete-confirm modal (#1734). Returns the number of
   * related queue items that will be removed along with the archive AND how
   * many are currently printing (server 409s on delete if > 0).
   */
  getArchiveDeleteImpact: (id: number) =>
    request<{ related_queue_items: number; currently_printing: number }>(
      `/archives/${id}/delete-impact`
    ),
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
  getNo3MFWarning: () => request<{ has_fallback: boolean }>('/archives/no-3mf-warning'),
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
  // Soft-deletes by default (#1343): files removed from disk, row hidden
  // from listings, but its filament / time / cost / energy contribution
  // stays in Quick Stats. Pass purgeStats=true to hard-delete and drop the
  // row from statistics too.
  deleteArchive: (id: number, purgeStats: boolean = false) =>
    request<void>(`/archives/${id}${purgeStats ? '?purge_stats=true' : ''}`, { method: 'DELETE' }),

  // ========== Archive auto-purge (#1008 follow-up) ==========
  previewArchivePurge: (olderThanDays: number, purgeStats: boolean = false) =>
    request<ArchivePurgePreview>(
      `/archives/purge/preview?older_than_days=${olderThanDays}&purge_stats=${purgeStats}`,
    ),
  // #1390: purgeStats=false (default) soft-deletes each old archive — Quick Stats
  // preserved, files removed from disk, row hidden via deleted_at. true matches
  // the single-archive delete's `?purge_stats=true` semantics (hard-deletes the
  // linked PrintLogEntry rows so the contribution drops from /stats too).
  executeArchivePurge: (olderThanDays: number, purgeStats: boolean = false) =>
    request<{ deleted: number; purge_stats: boolean }>('/archives/purge', {
      method: 'POST',
      body: JSON.stringify({ older_than_days: olderThanDays, purge_stats: purgeStats }),
    }),
  getArchivePurgeSettings: () =>
    request<ArchivePurgeSettings>('/archives/purge/settings'),
  updateArchivePurgeSettings: (body: ArchivePurgeSettings) =>
    request<ArchivePurgeSettings>('/archives/purge/settings', {
      method: 'PUT',
      body: JSON.stringify(body),
    }),

  getArchiveStats: (options?: { dateFrom?: string; dateTo?: string; createdById?: number }) => {
    const params = new URLSearchParams();
    if (options?.dateFrom) params.set('date_from', options.dateFrom);
    if (options?.dateTo) params.set('date_to', options.dateTo);
    if (options?.createdById !== undefined) params.set('created_by_id', String(options.createdById));
    const qs = params.toString();
    return request<ArchiveStats>(`/archives/stats${qs ? `?${qs}` : ''}`);
  },
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
  getFailureAnalysis: (options?: { days?: number; dateFrom?: string; dateTo?: string; printerId?: number; projectId?: number; createdById?: number }) => {
    const params = new URLSearchParams();
    if (options?.days) params.set('days', String(options.days));
    if (options?.dateFrom) params.set('date_from', options.dateFrom);
    if (options?.dateTo) params.set('date_to', options.dateTo);
    if (options?.printerId) params.set('printer_id', String(options.printerId));
    if (options?.projectId) params.set('project_id', String(options.projectId));
    if (options?.createdById !== undefined) params.set('created_by_id', String(options.createdById));
    const qs = params.toString();
    return request<FailureAnalysis>(`/archives/analysis/failures${qs ? `?${qs}` : ''}`);
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
    createdById?: number;
  }): Promise<{ blob: Blob; filename: string }> => {
    const params = new URLSearchParams();
    if (options?.format) params.set('format', options.format);
    if (options?.days) params.set('days', String(options.days));
    if (options?.printerId) params.set('printer_id', String(options.printerId));
    if (options?.projectId) params.set('project_id', String(options.projectId));
    if (options?.createdById !== undefined) params.set('created_by_id', String(options.createdById));

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
  getArchiveThumbnail: (id: number) => withStreamToken(`${API_BASE}/archives/${id}/thumbnail?v=${Date.now()}`),
  getArchivePlateThumbnail: (id: number, plateIndex: number) =>
    withStreamToken(`${API_BASE}/archives/${id}/plate-thumbnail/${plateIndex}`),
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
  getArchivePlatePreview: (id: number) => withStreamToken(`${API_BASE}/archives/${id}/plate-preview`),
  getArchiveTimelapse: (id: number) => withStreamToken(`${API_BASE}/archives/${id}/timelapse?v=${Date.now()}`),
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
  deleteArchiveTimelapse: (id: number) =>
    request<{ status: string }>(`/archives/${id}/timelapse`, {
      method: 'DELETE',
    }),
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
    withStreamToken(`${API_BASE}/archives/${archiveId}/photos/${encodeURIComponent(filename)}`),
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
  getSource3mfForSlicer: (archiveId: number, filename: string) => {
    // Sanitize: slicers url_decode() the entire URL, so / \ ? # in filenames break path routing
    const safe = filename.replace(/[/\\?#]/g, '_');
    return `${API_BASE}/archives/${archiveId}/source/${encodeURIComponent(safe.endsWith('.3mf') ? safe : safe + '.3mf')}`;
  },
  createSourceSlicerToken: (archiveId: number) =>
    request<{ token: string }>(`/archives/${archiveId}/source-slicer-token`, { method: 'POST' }),
  getSourceSlicerDownloadUrl: (archiveId: number, token: string, filename: string) => {
    const safe = filename.replace(/[/\\?#]/g, '_');
    return `${API_BASE}/archives/${archiveId}/source-dl/${token}/${encodeURIComponent(safe.endsWith('.3mf') ? safe : safe + '.3mf')}`;
  },
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
    withStreamToken(`${API_BASE}/archives/${archiveId}/qrcode?size=${size}`),
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
    withStreamToken(`${API_BASE}/archives/${archiveId}/project-image/${encodeURIComponent(imagePath)}`),
  getArchiveForSlicer: (id: number, filename: string) => {
    const safe = filename.replace(/[/\\?#]/g, '_');
    return `${API_BASE}/archives/${id}/file/${encodeURIComponent(safe.endsWith('.3mf') ? safe : safe + '.3mf')}`;
  },
  createArchiveSlicerToken: (archiveId: number) =>
    request<{ token: string }>(`/archives/${archiveId}/slicer-token`, { method: 'POST' }),
  getArchiveSlicerDownloadUrl: (archiveId: number, token: string, filename: string) => {
    const safe = filename.replace(/[/\\?#]/g, '_');
    return `${API_BASE}/archives/${archiveId}/dl/${token}/${encodeURIComponent(safe.endsWith('.3mf') ? safe : safe + '.3mf')}`;
  },
  getArchivePlates: (archiveId: number) =>
    request<ArchivePlatesResponse>(`/archives/${archiveId}/plates`),
  getArchiveFilamentRequirements: (
    archiveId: number,
    plateId?: number,
    requestId?: string,
  ) => {
    const qs = new URLSearchParams();
    if (plateId !== undefined) qs.set('plate_id', String(plateId));
    if (requestId) qs.set('request_id', requestId);
    return request<{
      archive_id: number;
      filename: string;
      plate_id: number | null;
      filaments: Array<{
        slot_id: number;
        type: string;
        color: string;
        used_grams: number;
        used_meters: number;
        used_in_plate?: boolean;
      }>;
    }>(`/archives/${archiveId}/filament-requirements${qs.toString() ? `?${qs}` : ''}`);
  },
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

  // Print Log
  getPrintLog: (params?: {
    search?: string;
    printerId?: number;
    username?: string;
    status?: string;
    dateFrom?: string;
    dateTo?: string;
    limit?: number;
    offset?: number;
  }) => {
    const searchParams = new URLSearchParams();
    if (params?.search) searchParams.set('search', params.search);
    if (params?.printerId) searchParams.set('printer_id', String(params.printerId));
    if (params?.username) searchParams.set('created_by_username', params.username);
    if (params?.status) searchParams.set('status', params.status);
    if (params?.dateFrom) searchParams.set('date_from', params.dateFrom);
    if (params?.dateTo) searchParams.set('date_to', params.dateTo);
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.offset !== undefined) searchParams.set('offset', String(params.offset));
    return request<PrintLogResponse>(`/print-log/?${searchParams}`);
  },
  getPrintLogThumbnail: (id: number) => withStreamToken(`${API_BASE}/print-log/${id}/thumbnail`),
  clearPrintLog: () =>
    request<{ deleted: number }>('/print-log/', { method: 'DELETE' }),
  deletePrintLogEntry: (id: number) =>
    request<{ status: string; id: number }>(`/print-log/${id}`, { method: 'DELETE' }),
  // Edit failure_reason / status on a single Print Log row (#1687 part 4).
  // Distinct from updateArchive: archives describe the model, log entries
  // describe a single print event. Orphan entries (no archive_id) have no
  // archive to reach through and this is the only path to classify them.
  updatePrintLogEntry: (
    id: number,
    body: { failure_reason?: string | null; status?: string },
  ) =>
    request<PrintLogEntry>(`/print-log/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),

  // Settings
  getSettings: () => request<AppSettings>('/settings/'),
  getDefaultSidebarOrder: () => request<{ default_sidebar_order: string }>('/settings/default-sidebar-order'),
  // Public subset of settings for UI rendering — no settings:read required.
  // Used by pages whose users may not have SETTINGS_READ (e.g. operators with
  // only printers:clear_plate). Keep in sync with _UI_PREFERENCE_FIELDS in
  // backend/app/api/routes/settings.py.
  getUiPreferences: () =>
    request<{
      require_plate_clear?: boolean;
      check_printer_firmware?: boolean;
      camera_view_mode?: 'window' | 'embedded';
      time_format?: 'system' | '12h' | '24h';
      date_format?: string;
      drying_presets?: string;
      ams_humidity_thresholds?: string;
      ams_humidity_good?: number;
      ams_humidity_fair?: number;
      ams_temp_good?: number;
      ams_temp_fair?: number;
      bed_cooled_threshold?: number;
      nozzle_temp_presets?: string;
      bed_temp_presets?: string;
      chamber_temp_presets?: string;
      fan_speed_presets?: string;
    }>('/settings/ui-preferences'),
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
      if (match) filename = match[1].trim().replace(/^"(.*)"$/, '$1');
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
  cloudVerify: (email: string, code: string, tfaKey?: string, region: string = 'global') =>
    request<CloudLoginResponse>('/cloud/verify', {
      method: 'POST',
      body: JSON.stringify({ email, code, tfa_key: tfaKey, region }),
    }),
  cloudSetToken: (access_token: string, region: string = 'global') =>
    request<CloudAuthStatus>('/cloud/token', {
      method: 'POST',
      body: JSON.stringify({ access_token, region }),
    }),
  cloudLogout: () =>
    request<{ success: boolean }>('/cloud/logout', { method: 'POST' }),

  // Orca Cloud — paste-based PKCE flow for OAuth (Google/Apple/GitHub),
  // direct credentials for email+password. start() returns an auth URL the
  // user opens in their browser; after sign-in they paste the callback URL
  // back via finish(). password() skips the dance entirely.
  orcaCloudStartAuth: (provider: OrcaOAuthProvider = 'google') =>
    request<OrcaAuthStartResponse>('/orca-cloud/auth/start', {
      method: 'POST',
      body: JSON.stringify({ provider }),
    }),
  orcaCloudFinishAuth: (callback_url: string) =>
    request<OrcaAuthStatusResponse>('/orca-cloud/auth/finish', {
      method: 'POST',
      body: JSON.stringify({ callback_url }),
    }),
  orcaCloudPasswordLogin: (email: string, password: string) =>
    request<OrcaAuthStatusResponse>('/orca-cloud/auth/password', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),
  orcaCloudStatus: () =>
    request<OrcaAuthStatusResponse>('/orca-cloud/status'),
  orcaCloudLogout: () =>
    request<{ success: boolean }>('/orca-cloud/logout', { method: 'POST' }),
  orcaCloudListProfiles: () =>
    request<OrcaProfileListResponse>('/orca-cloud/profiles'),
  orcaCloudGetProfile: (id: string) =>
    request<OrcaProfileDetail>(`/orca-cloud/profiles/${id}`),
  getCloudSettings: (version = '02.04.00.70') =>
    request<SlicerSettingsResponse>(`/cloud/settings?version=${version}`),
  getBuiltinFilaments: () =>
    request<BuiltinFilament[]>('/cloud/builtin-filaments'),
  getFilamentIdMap: () =>
    request<Record<string, string>>('/cloud/filament-id-map'),

  /** Material-disambiguated hex→name lookup. Same hex can map to different
   *  catalog names depending on material (e.g. #000000 is "Charcoal" in PLA
   *  Matte but "Black" in PLA Basic). The flat ``/inventory/colors/map``
   *  collapses these to the first hit; this endpoint preserves the material
   *  context. Returns ``{color_name: null}`` when the hex isn't in the
   *  catalog at all. #1718. */
  getColorByMaterial: (hex: string, material?: string) => {
    const params = new URLSearchParams({ hex });
    if (material) params.set('material', material);
    return request<{ color_name: string | null }>(
      `/inventory/colors/by-material?${params.toString()}`,
    );
  },

  // MakerWorld URL-paste import flow.
  getMakerworldStatus: () =>
    request<MakerworldStatus>('/makerworld/status'),
  resolveMakerworldUrl: (url: string) =>
    request<MakerworldResolvedModel>('/makerworld/resolve', {
      method: 'POST',
      body: JSON.stringify({ url }),
    }),
  getMakerworldRecentImports: (limit = 10) =>
    request<MakerworldRecentImport[]>(`/makerworld/recent-imports?limit=${limit}`),
  importMakerworldInstance: (
    model_id: number,
    instance_id: number | null,
    profile_id?: number | null,
    folder_id?: number | null,
  ) =>
    request<MakerworldImportResponse>('/makerworld/import', {
      method: 'POST',
      body: JSON.stringify({
        model_id,
        instance_id: instance_id ?? null,
        profile_id: profile_id ?? null,
        folder_id: folder_id ?? null,
      }),
    }),
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

  // REST smart plug
  testRESTConnection: (url: string, method: string = 'GET', headers?: string | null) =>
    request<{ success: boolean; error: string | null }>('/smart-plugs/rest/test-connection', {
      method: 'POST',
      body: JSON.stringify({ url, method, headers }),
    }),

  // Print Queue
  getQueue: (printerId?: number, status?: string, targetModel?: string) => {
    const params = new URLSearchParams();
    if (printerId) params.set('printer_id', String(printerId));
    if (status) params.set('status', status);
    if (targetModel) params.set('target_model', targetModel);
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
  /**
   * Start a staged queue item. The backend re-checks live filament deficit
   * for the assigned spool and, when short, returns 409 with a structured
   * payload so the caller can confirm and retry. Pass `skipFilamentCheck`
   * after the user confirms "Print Anyway" (#1496).
   */
  startQueueItem: (id: number, opts?: { skipFilamentCheck?: boolean }) => {
    const qs = opts?.skipFilamentCheck ? '?skip_filament_check=true' : '';
    return request<PrintQueueItem>(`/queue/${id}/start${qs}`, { method: 'POST' });
  },
  /**
   * Clear the `require_previous_success` gate for a printer after the user
   * resolves the failure. Acknowledges any failed/aborted predecessors and
   * restores skipped items whose error_message matches the gate string
   * back to pending. Returns counts so the UI can render a precise toast.
   */
  resumeQueueAfterFailure: (printerId: number) =>
    request<{ acknowledged: number; restored: number }>(
      `/queue/printer/${printerId}/resume`,
      { method: 'POST' },
    ),
  bulkUpdateQueue: (data: PrintQueueBulkUpdate) =>
    request<PrintQueueBulkUpdateResponse>('/queue/bulk', {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  // Batches
  getBatches: (status?: string) => {
    const params = status ? `?status=${status}` : '';
    return request<PrintBatch[]>(`/queue/batches${params}`);
  },
  getBatch: (id: number) => request<PrintBatch>(`/queue/batches/${id}`),
  cancelBatch: (id: number) =>
    request<{ message: string }>(`/queue/batches/${id}`, { method: 'DELETE' }),
  createBatch: (data: PrintBatchCreate) =>
    request<PrintBatch>('/queue/batches', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  ungroupBatch: (id: number) =>
    request<{ ungrouped_count: number; message: string }>(
      `/queue/batches/${id}/ungroup`,
      { method: 'POST' },
    ),

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

  // AMS Labels (user-defined friendly names)
  getAmsLabels: (printerId: number) =>
    request<Record<number, string>>(`/printers/${printerId}/ams-labels`),
  saveAmsLabel: (printerId: number, amsId: number, label: string, amsSerial = '') =>
    request<{ ams_id: number; label: string }>(
      `/printers/${printerId}/ams-labels/${amsId}`,
      {
        method: 'PUT',
        body: JSON.stringify({ label, ams_serial: amsSerial }),
      }
    ),
  deleteAmsLabel: (printerId: number, amsId: number, amsSerial = '') =>
    request<{ success: boolean }>(`/printers/${printerId}/ams-labels/${amsId}?ams_serial=${encodeURIComponent(amsSerial)}`, {
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

  // Filament Catalog (material types with cost/temp data)
  listFilaments: () => request<Filament[]>('/filament-catalog/'),
  getFilament: (id: number) => request<Filament>(`/filament-catalog/${id}`),
  getFilamentsByType: (type: string) => request<Filament[]>(`/filament-catalog/by-type/${type}`),

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
  /** @deprecated Use getSpoolmanInventoryFilaments() — this endpoint has no SSRF guard */
  getSpoolmanFilaments: () =>
    request<{ filaments: unknown[] }>('/spoolman/filaments'),
  getSpoolmanInventoryFilaments: () =>
    request<SpoolmanFilamentEntry[]>('/spoolman/inventory/filaments'),
  patchSpoolmanFilament: (
    filamentId: number,
    data: { name?: string; spool_weight?: number | null; keep_existing_spools?: boolean },
  ) =>
    request<SpoolmanFilamentEntry>(`/spoolman/inventory/filaments/${filamentId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  getUnlinkedSpools: () =>
    request<UnlinkedSpool[]>('/spoolman/spools/unlinked'),
  getLinkedSpools: () =>
    request<LinkedSpoolsMap>('/spoolman/spools/linked'),
  linkSpool: (
    spoolId: number,
    context: {
      spoolTag: string;
      printerId: number;
      amsId: number;
      trayId: number;
    }
  ) =>
    request<{ success: boolean; message: string }>(`/spoolman/spools/${spoolId}/link`, {
      method: 'POST',
      body: JSON.stringify({
        spool_tag: context.spoolTag,
        printer_id: context.printerId,
        ams_id: context.amsId,
        tray_id: context.trayId,
      }),
    }),
  unlinkSpool: (spoolId: number) =>
    request<{ success: boolean; message: string }>(`/spoolman/spools/${spoolId}/unlink`, {
      method: 'POST',
    }),
  createSpoolmanSpoolFromSlot: (data: { printer_id: number; ams_id: number; tray_id: number }) =>
    request<{ success: boolean; spool_id: number | null }>(`/spoolman/spools/from-slot`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  createSpoolFromSlot: (data: { printer_id: number; ams_id: number; tray_id: number }) =>
    request<InventorySpool>(`/inventory/spools/from-slot`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  getSpoolmanSettings: () =>
    request<{ spoolman_enabled: string; spoolman_url: string; spoolman_sync_mode: string; spoolman_disable_weight_sync: string; spoolman_report_partial_usage: string; auto_add_unknown_rfid: string; }>('/settings/spoolman'),
  updateSpoolmanSettings: (data: { spoolman_enabled?: string; spoolman_url?: string; spoolman_sync_mode?: string; spoolman_disable_weight_sync?: string; spoolman_report_partial_usage?: string; auto_add_unknown_rfid?: string; }) =>
    request<{ spoolman_enabled: string; spoolman_url: string; spoolman_sync_mode: string; spoolman_disable_weight_sync: string; spoolman_report_partial_usage: string; auto_add_unknown_rfid: string; }>('/settings/spoolman', {
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
  bulkCreateSpools: (data: Omit<InventorySpool, 'id' | 'archived_at' | 'created_at' | 'updated_at' | 'k_profiles'>, quantity: number) =>
    request<InventorySpool[]>('/inventory/spools/bulk', {
      method: 'POST',
      body: JSON.stringify({ spool: data, quantity }),
    }),
  // ── CSV import/export (#1576) ────────────────────────────────────────────
  // dry_run=true → preview (no write); omitted → real import. Both share one
  // multipart upload helper; see `uploadSpoolsCsv` below.
  importSpoolsCsvPreview: (file: File): Promise<CsvImportPreview> => uploadSpoolsCsv<CsvImportPreview>(file, true),
  importSpoolsCsv: (file: File): Promise<CsvImportResult> => uploadSpoolsCsv<CsvImportResult>(file, false),
  exportSpoolsCsv: async (): Promise<void> => {
    const headers: Record<string, string> = {};
    if (authToken) headers['Authorization'] = `Bearer ${authToken}`;
    const response = await fetch(`${API_BASE}/inventory/spools/export`, { headers });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    const disposition = response.headers.get('Content-Disposition');
    const filename = parseContentDispositionFilename(disposition) || 'bambuddy_inventory.csv';
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
  resetSpoolConsumedCounter: (id: number) =>
    request<InventorySpool>(`/inventory/spools/${id}/reset-consumed-counter`, { method: 'POST' }),
  bulkResetSpoolConsumedCounter: (spoolIds: number[]) =>
    request<{ reset: number }>(`/inventory/spools/reset-consumed-counter-bulk`, {
      method: 'POST',
      body: JSON.stringify({ spool_ids: spoolIds }),
    }),
  bulkUpdateSpools: (ids: number[], update: Partial<Omit<InventorySpool, 'id' | 'archived_at' | 'created_at' | 'updated_at' | 'k_profiles'>>) =>
    request<{ updated: number; not_found: number[] }>(`/inventory/spools/bulk-update`, {
      method: 'POST',
      body: JSON.stringify({ ids, update }),
    }),
  bulkDeleteSpools: (ids: number[]) =>
    request<{ deleted: number; not_found: number[] }>(`/inventory/spools/bulk-delete`, {
      method: 'POST',
      body: JSON.stringify({ ids }),
    }),
  bulkArchiveSpools: (ids: number[]) =>
    request<{ archived: number; already_archived: number[]; not_found: number[] }>(`/inventory/spools/bulk-archive`, {
      method: 'POST',
      body: JSON.stringify({ ids }),
    }),
  bulkRestoreSpools: (ids: number[]) =>
    request<{ restored: number; already_active: number[]; not_found: number[] }>(`/inventory/spools/bulk-restore`, {
      method: 'POST',
      body: JSON.stringify({ ids }),
    }),
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
  // ── Spool label printing (#809) ──────────────────────────────────────────
  // Both endpoints return application/pdf. Frontend opens the resulting Blob
  // in a new tab so the user can print or save from the browser's PDF viewer.
  printSpoolLabels: async (data: { spool_ids: number[]; template: SpoolLabelTemplate; monochrome?: boolean }): Promise<Blob> => {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (authToken) headers['Authorization'] = `Bearer ${authToken}`;
    const response = await fetch(`${API_BASE}/inventory/labels`, {
      method: 'POST',
      headers,
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    return response.blob();
  },
  printSpoolmanSpoolLabels: async (data: { spool_ids: number[]; template: SpoolLabelTemplate; monochrome?: boolean }): Promise<Blob> => {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (authToken) headers['Authorization'] = `Bearer ${authToken}`;
    const response = await fetch(`${API_BASE}/spoolman/labels`, {
      method: 'POST',
      headers,
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }
    return response.blob();
  },
  getSpoolCatalog: () =>
    request<SpoolCatalogEntry[]>('/inventory/catalog'),
  addCatalogEntry: (data: { name: string; weight: number }) =>
    request<SpoolCatalogEntry>('/inventory/catalog', { method: 'POST', body: JSON.stringify(data) }),
  updateCatalogEntry: (id: number, data: { name: string; weight: number }) =>
    request<SpoolCatalogEntry>(`/inventory/catalog/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteCatalogEntry: (id: number) =>
    request<{ status: string }>(`/inventory/catalog/${id}`, { method: 'DELETE' }),
  bulkDeleteCatalogEntries: (ids: number[]) =>
    request<{ deleted: number }>('/inventory/catalog/bulk-delete', { method: 'POST', body: JSON.stringify({ ids }) }),
  resetSpoolCatalog: () =>
    request<{ status: string }>('/inventory/catalog/reset', { method: 'POST' }),
  getLocations: () =>
    request<StorageLocation[]>('/inventory/locations'),
  createLocation: (data: { name: string; identifier?: string | null }) =>
    request<StorageLocation>('/inventory/locations', { method: 'POST', body: JSON.stringify(data) }),
  updateLocation: (id: number, data: { name?: string; identifier?: string | null }) =>
    request<StorageLocation>(`/inventory/locations/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),
  deleteLocation: (id: number) =>
    request<{ status: string }>(`/inventory/locations/${id}`, { method: 'DELETE' }),
  getColorCatalog: () =>
    request<ColorCatalogEntry[]>('/inventory/colors'),
  getColorNameMap: () =>
    request<{ colors: Record<string, string> }>('/inventory/colors/map'),
  addColorEntry: (data: {
    manufacturer: string;
    color_name: string;
    hex_color: string;
    material: string | null;
    extra_colors?: string | null;
    effect_type?: string | null;
  }) =>
    request<ColorCatalogEntry>('/inventory/colors', { method: 'POST', body: JSON.stringify(data) }),
  updateColorEntry: (
    id: number,
    data: {
      manufacturer: string;
      color_name: string;
      hex_color: string;
      material: string | null;
      extra_colors?: string | null;
      effect_type?: string | null;
    },
  ) => request<ColorCatalogEntry>(`/inventory/colors/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteColorEntry: (id: number) =>
    request<{ status: string }>(`/inventory/colors/${id}`, { method: 'DELETE' }),
  bulkDeleteColorEntries: (ids: number[]) =>
    request<{ deleted: number }>('/inventory/colors/bulk-delete', { method: 'POST', body: JSON.stringify({ ids }) }),
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
  syncWeightsFromAms: () =>
    request<{ synced: number; skipped: number }>('/inventory/sync-ams-weights', { method: 'POST' }),
  getSkuSettings: () =>
    request<FilamentSkuSettings[]>('/inventory/sku-settings'),
  upsertSkuSettings: (data: Omit<FilamentSkuSettings, 'id'>) =>
    request<FilamentSkuSettings>('/inventory/sku-settings', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  getShoppingList: () =>
    request<ShoppingListItem[]>('/inventory/shopping-list'),
  addToShoppingList: (data: ShoppingListItemCreate) =>
    request<ShoppingListItem>('/inventory/shopping-list', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  removeFromShoppingList: (id: number) =>
    request<{ status: string }>(`/inventory/shopping-list/${id}`, { method: 'DELETE' }),
  clearShoppingList: () =>
    request<{ deleted: number }>('/inventory/shopping-list', { method: 'DELETE' }),
  updateShoppingListStatus: (id: number, status: 'pending' | 'purchased' | 'received') =>
    request<ShoppingListItem>(`/inventory/shopping-list/${id}/status`, {
      method: 'PATCH',
      body: JSON.stringify({ status }),
    }),
  getFilamentPresets: () =>
    request<SlicerSetting[]>('/cloud/filaments'),

  // Spoolman Inventory proxy (unified UI when Spoolman is enabled)
  getSpoolmanInventorySpools: (includeArchived = false) =>
    request<InventorySpool[]>(`/spoolman/inventory/spools?include_archived=${includeArchived}`),
  getSpoolmanInventorySpool: (id: number) =>
    request<InventorySpool>(`/spoolman/inventory/spools/${id}`),
  createSpoolmanInventorySpool: (data: Omit<InventorySpool, 'id' | 'archived_at' | 'created_at' | 'updated_at' | 'k_profiles'>) =>
    request<InventorySpool>('/spoolman/inventory/spools', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  bulkCreateSpoolmanInventorySpools: (
    data: Omit<InventorySpool, 'id' | 'archived_at' | 'created_at' | 'updated_at' | 'k_profiles'>,
    quantity: number,
  ) =>
    request<SpoolmanBulkCreateResult | InventorySpool[]>('/spoolman/inventory/spools/bulk', {
      method: 'POST',
      body: JSON.stringify({ spool: data, quantity }),
    }),
  updateSpoolmanInventorySpool: (
    id: number,
    data: Partial<Omit<InventorySpool, 'id' | 'archived_at' | 'created_at' | 'updated_at' | 'k_profiles'>>,
  ) =>
    request<InventorySpool>(`/spoolman/inventory/spools/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  deleteSpoolmanInventorySpool: (id: number) =>
    request<{ status: string }>(`/spoolman/inventory/spools/${id}`, { method: 'DELETE' }),
  archiveSpoolmanInventorySpool: (id: number) =>
    request<InventorySpool>(`/spoolman/inventory/spools/${id}/archive`, { method: 'POST' }),
  restoreSpoolmanInventorySpool: (id: number) =>
    request<InventorySpool>(`/spoolman/inventory/spools/${id}/restore`, { method: 'POST' }),
  resetSpoolmanInventorySpoolConsumedCounter: (id: number) =>
    request<InventorySpool>(`/spoolman/inventory/spools/${id}/reset-consumed-counter`, { method: 'POST' }),
  bulkResetSpoolmanInventorySpoolConsumedCounter: (spoolIds: number[]) =>
    request<{ reset: number }>(`/spoolman/inventory/spools/reset-consumed-counter-bulk`, {
      method: 'POST',
      body: JSON.stringify({ spool_ids: spoolIds }),
    }),
  bulkUpdateSpoolmanInventorySpools: (ids: number[], update: Partial<Omit<InventorySpool, 'id' | 'archived_at' | 'created_at' | 'updated_at' | 'k_profiles'>>) =>
    request<{ updated: number; errors: Array<{ id: number; status: number; detail: string }> }>(`/spoolman/inventory/spools/bulk-update`, {
      method: 'POST',
      body: JSON.stringify({ ids, update }),
    }),
  bulkDeleteSpoolmanInventorySpools: (ids: number[]) =>
    request<{ deleted: number; errors: Array<{ id: number; status: number; detail: string }> }>(`/spoolman/inventory/spools/bulk-delete`, {
      method: 'POST',
      body: JSON.stringify({ ids }),
    }),
  bulkArchiveSpoolmanInventorySpools: (ids: number[]) =>
    request<{ archived: number; errors: Array<{ id: number; status: number; detail: string }> }>(`/spoolman/inventory/spools/bulk-archive`, {
      method: 'POST',
      body: JSON.stringify({ ids }),
    }),
  bulkRestoreSpoolmanInventorySpools: (ids: number[]) =>
    request<{ restored: number; errors: Array<{ id: number; status: number; detail: string }> }>(`/spoolman/inventory/spools/bulk-restore`, {
      method: 'POST',
      body: JSON.stringify({ ids }),
    }),
  linkTagToSpoolmanSpool: (spoolId: number, data: { tag_uid?: string; tray_uuid?: string }) =>
    request<InventorySpool>(`/spoolman/inventory/spools/${spoolId}/tag`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  syncSpoolmanSpoolWeight: (spoolId: number, weightGrams: number) =>
    request<{ status: string; weight_used: number }>(`/spoolman/inventory/spools/${spoolId}/weight`, {
      method: 'PATCH',
      body: JSON.stringify({ weight_grams: weightGrams }),
    }),
  assignSpoolmanSlot: (data: { spoolman_spool_id: number; printer_id: number; ams_id: number; tray_id: number }) =>
    request<InventorySpool>('/spoolman/inventory/slot-assignments', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  unassignSpoolmanSlot: (spoolmanSpoolId: number) =>
    request<InventorySpool>(`/spoolman/inventory/slot-assignments/${spoolmanSpoolId}`, { method: 'DELETE' }),
  getSpoolmanSlotAssignment: (printerId: number, amsId: number, trayId: number) =>
    request<InventorySpool | null>(
      `/spoolman/inventory/slot-assignments?printer_id=${printerId}&ams_id=${amsId}&tray_id=${trayId}`,
    ),
  getSpoolmanSlotAssignments: (printerId?: number) =>
    request<Array<{
      printer_id: number;
      printer_name: string | null;
      ams_id: number;
      tray_id: number;
      spoolman_spool_id: number;
      ams_label: string | null;
    }>>(
      printerId !== undefined
        ? `/spoolman/inventory/slot-assignments/all?printer_id=${printerId}`
        : '/spoolman/inventory/slot-assignments/all',
    ),
  syncSpoolmanAmsWeights: () =>
    request<{ synced: number; skipped: number }>('/spoolman/inventory/sync-ams-weights', { method: 'POST' }),

  getSpoolmanKProfiles: (spoolId: number) =>
    request<SpoolKProfile[]>(`/spoolman/inventory/spools/${spoolId}/k-profiles`),

  saveSpoolmanKProfiles: (spoolId: number, profiles: SpoolKProfileInput[]) =>
    request<SpoolKProfile[]>(`/spoolman/inventory/spools/${spoolId}/k-profiles`, {
      method: 'PUT',
      body: JSON.stringify(profiles),
    }),

  // Updates
  getVersion: () => request<VersionInfo>('/updates/version'),
  checkForUpdates: () => request<UpdateCheckResult>('/updates/check'),
  applyUpdate: () =>
    request<{ success: boolean; message: string; status?: UpdateStatus; is_docker?: boolean; is_ha_addon?: boolean; is_windows_installer?: boolean }>('/updates/apply', {
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
  getCameraStreamToken: () =>
    request<{ token: string }>('/printers/camera/stream-token', { method: 'POST' }),

  // WebSocket auth (GHSA-r2qv follow-up) — mint a short-lived token for
  // the /ws connection. Browsers can't attach Authorization headers to a
  // WebSocket handshake, so the token rides in the ?token= query param.
  getWebSocketToken: () =>
    request<{ token: string }>('/auth/ws-token', { method: 'POST' }),

  // Long-lived camera tokens (#1108, #2531)
  createLongLivedCameraToken: (payload: {
    name: string;
    expires_in_days: number;
    scope?: LongLivedTokenScope;
  }) =>
    request<LongLivedCameraToken>('/auth/tokens', {
      method: 'POST',
      body: JSON.stringify({ scope: 'camera_stream', ...payload }),
    }),
  listMyLongLivedCameraTokens: () =>
    request<LongLivedCameraToken[]>('/auth/tokens'),
  listAllLongLivedCameraTokens: () =>
    request<LongLivedCameraToken[]>('/auth/tokens/all'),
  listLongLivedCameraTokensForUser: (userId: number) =>
    request<LongLivedCameraToken[]>(`/auth/tokens?user_id=${userId}`),
  revokeLongLivedCameraToken: (tokenId: number) =>
    request<void>(`/auth/tokens/${tokenId}`, { method: 'DELETE' }),
  // Token-authenticated Cam Wall feed (#2531). `token` is omitted only when
  // auth is disabled, where the backend gate is a no-op anyway.
  getCamWallPrinters: (token?: string) =>
    request<CamWallPrinter[]>(
      token ? `/camwall/printers?token=${encodeURIComponent(token)}` : '/camwall/printers',
    ),
  getCameraStreamUrl: (printerId: number, fps = 10) =>
    withStreamToken(`${API_BASE}/printers/${printerId}/camera/stream?fps=${fps}`),
  getCameraSnapshotUrl: (printerId: number) =>
    withStreamToken(`${API_BASE}/printers/${printerId}/camera/snapshot`),
  testCameraConnection: (printerId: number) =>
    request<{ success: boolean; message?: string; error?: string }>(`/printers/${printerId}/camera/test`),
  getCameraStatus: (printerId: number) =>
    request<{ active: boolean; stalled: boolean }>(`/printers/${printerId}/camera/status`),
  diagnoseCamera: (printerId: number) =>
    request<CameraDiagnoseResult>(`/printers/${printerId}/camera/diagnose`, { method: 'POST' }),
  diagnosePrinter: (printerId: number) =>
    request<PrinterDiagnosticResult>(`/printers/${printerId}/diagnostic`),
  diagnoseConnection: (body: {
    ip_address: string;
    serial_number?: string;
    access_code?: string;
  }) =>
    request<PrinterDiagnosticResult>('/printers/diagnostic', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  // Plate Detection - Multi-reference calibration (stores up to 5 references per printer)
  checkPlateEmpty: (printerId: number, options?: { useExternal?: boolean; includeDebugImage?: boolean }) => {
    const params = new URLSearchParams();
    // Only forward use_external when the caller explicitly sets it. Omitted →
    // backend derives the default from the printer's external_camera_enabled
    // setting so calibration and runtime checks use the same camera (#1359).
    if (options?.useExternal !== undefined) {
      params.set('use_external', String(options.useExternal));
    }
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
    if (options?.useExternal !== undefined) {
      params.set('use_external', String(options.useExternal));
    }
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
  getPlateReferenceThumbnailUrl: (printerId: number, index: number) =>
    withStreamToken(`${API_BASE}/printers/${printerId}/camera/plate-detection/references/${index}/thumbnail`),
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
  getExternalLinkIconUrl: (id: number) => withStreamToken(`${API_BASE}/external-links/${id}/icon`),

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

  // #1155: Cover image
  // Browsers can't attach `Authorization: Bearer ...` to `<img src>`, so we
  // append the stream-token query string the same way archive thumbnails do.
  getProjectCoverImageUrl: (projectId: number) =>
    withStreamToken(`${API_BASE}/projects/${projectId}/cover-image`),
  uploadProjectCoverImage: async (
    projectId: number,
    file: File
  ): Promise<{ status: string; filename: string; size: number }> => {
    const formData = new FormData();
    formData.append('file', file);
    const headers: Record<string, string> = {};
    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }
    const response = await fetch(`${API_BASE}/projects/${projectId}/cover-image`, {
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
  deleteProjectCoverImage: (projectId: number) =>
    request<{ status: string }>(`/projects/${projectId}/cover-image`, { method: 'DELETE' }),

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

  // Printer heater (nozzle / bed / chamber) sensor history
  getPrinterSensorHistory: (printerId: number, hours = 24, kinds?: string[]) => {
    const params = new URLSearchParams({ hours: String(hours) });
    if (kinds && kinds.length > 0) params.set('kinds', kinds.join(','));
    return request<PrinterSensorHistoryResponse>(`/printer-sensor-history/${printerId}?${params.toString()}`);
  },

  // System Info
  getSystemInfo: () => request<SystemInfo>('/system/info'),
  getSystemHealth: () => request<SystemHealthResult>('/system/health'),
  getStorageUsage: (options?: { refresh?: boolean }) => {
    const params = new URLSearchParams();
    if (options?.refresh) {
      params.set('refresh', 'true');
    }
    const query = params.toString();
    return request<StorageUsageResponse>(`/system/storage-usage${query ? `?${query}` : ''}`);
  },

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
  createExternalFolder: (data: ExternalFolderCreate) =>
    request<LibraryFolder>('/library/folders/external', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  scanExternalFolder: (folderId: number) =>
    request<{ status: string; added: number; removed: number }>(`/library/folders/${folderId}/scan`, {
      method: 'POST',
    }),
  getLibraryFoldersByProject: (projectId: number) =>
    request<LibraryFolder[]>(`/library/folders/by-project/${projectId}`),
  getLibraryFoldersByArchive: (archiveId: number) =>
    request<LibraryFolder[]>(`/library/folders/by-archive/${archiveId}`),

  getLibraryFiles: (
    folderId?: number | null,
    includeRoot = true,
    projectId?: number,
    scope?: 'internal' | 'external',
    recursive = false,
    tagIds: number[] = [],
  ) => {
    const params = new URLSearchParams();
    if (folderId !== undefined && folderId !== null) {
      params.set('folder_id', String(folderId));
    }
    if (projectId !== undefined) {
      params.set('project_id', String(projectId));
    }
    params.set('include_root', String(includeRoot));
    if (scope === 'internal') params.set('internal_only', 'true');
    else if (scope === 'external') params.set('external_only', 'true');
    // recursive=true expands the folder_id filter to include every descendant
    // folder (#1268). Only meaningful when folder_id is set; ignored server-side
    // otherwise. Off by default so non-search callers keep folder-scoped behavior.
    if (recursive) params.set('recursive', 'true');
    // Tag filter (#1268). Repeated ?tag_ids=N&tag_ids=M form for AND semantics
    // — backend joins the association table and HAVING COUNT(DISTINCT) matches
    // the array length. Tag filter intentionally bypasses folder scoping
    // server-side (cross-cutting design decision).
    for (const tagId of tagIds) {
      params.append('tag_ids', String(tagId));
    }
    return request<LibraryFileListItem[]>(`/library/files?${params}`);
  },
  getLibraryFolderReadme: (folderId: number) =>
    request<{ filename: string; content: string; truncated: boolean }>(
      `/library/folders/${folderId}/readme`,
    ),

  // ============ Library tag catalog (#1268) ============
  getLibraryTags: () =>
    request<LibraryTag[]>('/library/tags'),
  createLibraryTag: (name: string) =>
    request<LibraryTag>('/library/tags', {
      method: 'POST',
      body: JSON.stringify({ name }),
    }),
  updateLibraryTag: (id: number, name: string) =>
    request<LibraryTag>(`/library/tags/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ name }),
    }),
  deleteLibraryTag: (id: number) =>
    request<void>(`/library/tags/${id}`, { method: 'DELETE' }),
  bulkAssignLibraryTags: (
    fileIds: number[],
    tagIds: number[],
    action: 'add' | 'remove' | 'replace',
  ) =>
    request<LibraryTagBulkAssignResult>('/library/tags/bulk-assign', {
      method: 'POST',
      body: JSON.stringify({ file_ids: fileIds, tag_ids: tagIds, action }),
    }),
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
    request<{ status: string; message: string; trashed: boolean }>(`/library/files/${id}`, { method: 'DELETE' }),

  // ========== Library Trash (#1008) ==========
  previewLibraryPurge: (olderThanDays: number, includeNeverPrinted: boolean = true) =>
    request<LibraryPurgePreview>(
      `/library/purge/preview?older_than_days=${olderThanDays}&include_never_printed=${includeNeverPrinted}`,
    ),
  executeLibraryPurge: (olderThanDays: number, includeNeverPrinted: boolean = true) =>
    request<{ moved_to_trash: number }>('/library/purge', {
      method: 'POST',
      body: JSON.stringify({ older_than_days: olderThanDays, include_never_printed: includeNeverPrinted }),
    }),
  listLibraryTrash: (limit: number = 100, offset: number = 0) =>
    request<LibraryTrashListResponse>(`/library/trash?limit=${limit}&offset=${offset}`),
  restoreLibraryTrash: (fileId: number) =>
    request<{ status: string; id: number }>(`/library/trash/${fileId}/restore`, { method: 'POST' }),
  hardDeleteLibraryTrash: (fileId: number) =>
    request<{ status: string }>(`/library/trash/${fileId}`, { method: 'DELETE' }),
  emptyLibraryTrash: () => request<{ deleted: number }>('/library/trash', { method: 'DELETE' }),
  getLibraryTrashSettings: () =>
    request<LibraryTrashSettings>('/library/trash/settings'),
  updateLibraryTrashSettings: (body: LibraryTrashSettings) =>
    request<LibraryTrashSettings>('/library/trash/settings', {
      method: 'PUT',
      body: JSON.stringify(body),
    }),
  getLibraryFileDownloadUrl: (id: number) => `${API_BASE}/library/files/${id}/download`,
  createLibrarySlicerToken: (fileId: number) =>
    request<{ token: string }>(`/library/files/${fileId}/slicer-token`, { method: 'POST' }),
  getLibrarySlicerDownloadUrl: (fileId: number, token: string, filename: string) =>
    `${API_BASE}/library/files/${fileId}/dl/${token}/${encodeURIComponent(buildSlicerUrlFilename(filename))}`,
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
  getLibraryFileThumbnailUrl: (id: number) => withStreamToken(`${API_BASE}/library/files/${id}/thumbnail`),
  getLibraryFilePlateThumbnail: (id: number, plateIndex: number) =>
    withStreamToken(`${API_BASE}/library/files/${id}/plate-thumbnail/${plateIndex}`),
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
  getLibraryFilePlates: (fileId: number) =>
    request<LibraryFilePlatesResponse>(`/library/files/${fileId}/plates`),
  getLibraryFileFilamentRequirements: (
    fileId: number,
    plateId?: number,
    requestId?: string,
  ) => {
    const qs = new URLSearchParams();
    if (plateId !== undefined) qs.set('plate_id', String(plateId));
    if (requestId) qs.set('request_id', requestId);
    return request<{
      file_id: number;
      filename: string;
      filaments: Array<{
        slot_id: number;
        type: string;
        color: string;
        used_grams: number;
        used_meters: number;
        used_in_plate?: boolean;
      }>;
    }>(`/library/files/${fileId}/filament-requirements${qs.toString() ? `?${qs}` : ''}`);
  },

  /** Poll the sidecar's per-request progress snapshot via the Bambuddy
   * proxy. Used by the SliceModal's filament-discovery path so the inline
   * spinner + persistent toast can show "Generating G-code (45%)" while
   * the preview slice runs. Returns null on 404 (sidecar doesn't yet
   * have an entry — early race window — or it expired) so the poller
   * can keep trying. */
  getPreviewSliceProgress: async (requestId: string): Promise<SliceJobProgress | null> => {
    try {
      return await request<SliceJobProgress>(`/slicer/preview-progress/${encodeURIComponent(requestId)}`);
    } catch {
      return null;
    }
  },

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

  testGitHubConnection: (repoUrl: string, token: string, provider: GitProviderType = 'github') =>
    request<GitHubTestConnectionResponse>(
      `/github-backup/test?repo_url=${encodeURIComponent(repoUrl)}&token=${encodeURIComponent(token)}&provider=${encodeURIComponent(provider)}`,
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

  // Scheduled local backups
  getLocalBackupStatus: () =>
    request<LocalBackupStatus>('/local-backup/status'),

  triggerLocalBackup: () =>
    request<{ success: boolean; message: string; filename?: string }>('/local-backup/run', { method: 'POST' }),

  checkLocalBackupPath: () =>
    request<LocalBackupPathCheck>('/local-backup/path-check'),

  getLocalBackups: () =>
    request<LocalBackupFile[]>('/local-backup/backups'),

  downloadLocalBackup: async (filename: string): Promise<{ blob: Blob; filename: string }> => {
    const response = await fetch(`${API_BASE}/local-backup/backups/${encodeURIComponent(filename)}/download`, {
      headers: authToken ? { 'Authorization': `Bearer ${authToken}` } : {},
    });
    if (!response.ok) throw new Error('Download failed');
    const blob = await response.blob();
    return { blob, filename };
  },

  restoreLocalBackup: (filename: string) =>
    request<{ success: boolean; message: string }>(`/local-backup/backups/${encodeURIComponent(filename)}/restore`, { method: 'POST' }),

  deleteLocalBackup: (filename: string) =>
    request<{ success: boolean; message: string }>(`/local-backup/backups/${encodeURIComponent(filename)}`, { method: 'DELETE' }),

  // Obico AI failure detection
  getObicoStatus: () =>
    request<ObicoStatus>('/obico/status'),

  testObicoConnection: (url: string) =>
    request<ObicoTestConnection>('/obico/test-connection', {
      method: 'POST',
      body: JSON.stringify({ url }),
    }),

  // Slicer API — slice in the background. Both endpoints return 202 + a
  // job_id; poll /slice-jobs/{id} until status is `completed` or `failed`.
  sliceLibraryFile: (fileId: number, body: SliceRequest) =>
    request<SliceJobEnqueueResponse>(`/library/files/${fileId}/slice`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  sliceArchive: (archiveId: number, body: SliceRequest) =>
    request<SliceJobEnqueueResponse>(`/archives/${archiveId}/slice`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  getSliceJob: (jobId: number) =>
    request<SliceJobState>(`/slice-jobs/${jobId}`),

  // Unified slicer-preset listing — cloud + local + standard, deduped by name.
  // Used by the SliceModal; see UnifiedPresetsResponse for the shape and
  // backend/app/api/routes/slicer_presets.py for the priority rules.
  // `refresh` bypasses the in-process cloud and bundled-preset caches on the
  // backend; the SliceModal's Refresh button passes true so a preset deleted
  // in Bambu Studio or Bambu Handy shows up without the 5-min TTL wait.
  getSlicerPresets: (options?: { refresh?: boolean }) =>
    request<UnifiedPresetsResponse>(
      options?.refresh ? '/slicer/presets?refresh=true' : '/slicer/presets',
    ),

  // Slicer Pipelines (#1425) — preset bundles the SliceModal can apply in
  // one click. CRUD is gated on PIPELINES_READ / PIPELINES_WRITE.
  listSlicerPipelines: () =>
    request<SlicerPipelinesListResponse>('/slicer-pipelines/'),
  getSlicerPipeline: (id: number) =>
    request<SlicerPipeline>(`/slicer-pipelines/${id}`),
  createSlicerPipeline: (data: SlicerPipelineCreateRequest) =>
    request<SlicerPipeline>('/slicer-pipelines/', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  updateSlicerPipeline: (id: number, data: SlicerPipelineUpdateRequest) =>
    request<SlicerPipeline>(`/slicer-pipelines/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),
  deleteSlicerPipeline: (id: number) =>
    request<void>(`/slicer-pipelines/${id}`, { method: 'DELETE' }),
  checkPipelineEligibility: (
    pipelineId: number,
    source: { kind: 'libraryFile'; id: number } | { kind: 'archive'; id: number },
  ) =>
    request<PipelineEligibilityReport>(`/slicer-pipelines/${pipelineId}/check-eligibility`, {
      method: 'POST',
      body: JSON.stringify(
        source.kind === 'libraryFile'
          ? { source_library_file_id: source.id }
          : { source_archive_id: source.id },
      ),
    }),
  runPipeline: (
    pipelineId: number,
    source: { kind: 'libraryFile'; id: number } | { kind: 'archive'; id: number },
    force = false,
    copies = 1,
  ) =>
    request<PipelineRun>(`/slicer-pipelines/${pipelineId}/run`, {
      method: 'POST',
      body: JSON.stringify({
        ...(source.kind === 'libraryFile'
          ? { source_library_file_id: source.id }
          : { source_archive_id: source.id }),
        force,
        copies,
      }),
    }),
  listPipelineRuns: (pipelineId: number, limit = 5) =>
    request<PipelineRunListResponse>(
      `/slicer-pipelines/${pipelineId}/runs?limit=${limit}`,
    ),
  // Dashboard list across all pipelines (#1425 PR C).
  listAllPipelineRuns: (params: {
    limit?: number;
    offset?: number;
    pipelineId?: number;
    status?: string;
    targetPrinterId?: number;
    targetModelClass?: string;
  } = {}) => {
    const search = new URLSearchParams();
    if (params.limit) search.set('limit', String(params.limit));
    if (params.offset) search.set('offset', String(params.offset));
    if (params.pipelineId) search.set('pipeline_id', String(params.pipelineId));
    if (params.status) search.set('status', params.status);
    if (params.targetPrinterId) search.set('target_printer_id', String(params.targetPrinterId));
    if (params.targetModelClass) search.set('target_model_class', params.targetModelClass);
    const q = search.toString();
    return request<PipelineRunListResponse>(
      `/pipeline-runs${q ? '?' + q : ''}`,
    );
  },
  // Clear terminal pipeline runs (#1425 PR C polish). Deletes all runs in
  // a terminal state (completed/failed/cancelled/partial_failure); in-flight
  // runs are preserved.
  clearTerminalPipelineRuns: () =>
    request<{ deleted: number }>('/pipeline-runs/clear', { method: 'POST' }),
  getPipelineRun: (runId: number) =>
    request<PipelineRun>(`/pipeline-runs/${runId}`),
  cancelPipelineRun: (runId: number) =>
    request<PipelineRun>(`/pipeline-runs/${runId}/cancel`, { method: 'POST' }),
  retryFailedPipelineRun: (runId: number) =>
    request<PipelineRun>(`/pipeline-runs/${runId}/retry-failed`, { method: 'POST' }),

  // Canonical Bambu printer-model registry — "Bambu Lab <model>" → short code.
  // Single source of truth shared with backend (PRINTER_MODEL_MAP); the
  // SliceModal uses this to classify cloud / standard presets by their
  // `@BBL <code>` suffix against the selected printer-preset name (#1325).
  getSlicerPrinterModels: () =>
    request<Record<string, string>>('/slicer/printer-models'),

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

export type HeaterSensorKind = 'nozzle' | 'nozzle_2' | 'bed' | 'chamber';

export interface HeaterHistoryPoint {
  recorded_at: string;
  value: number | null;
  target: number | null;
}

export interface HeaterSeries {
  sensor_kind: HeaterSensorKind;
  data: HeaterHistoryPoint[];
  min_value: number | null;
  max_value: number | null;
  avg_value: number | null;
}

export interface PrinterSensorHistoryResponse {
  printer_id: number;
  series: HeaterSeries[];
}

// System Info types
export interface SystemInfo {
  app: {
    version: string;
    base_dir: string;
    archive_dir: string;
  };
  database: {
    engine: string;
    version: string;
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

export interface StorageUsageCategory {
  key: string;
  label: string;
  bytes: number;
  formatted: string;
  percent_of_total: number;
}

export interface StorageUsageOtherItem {
  bucket: string;
  label: string;
  kind: 'system' | 'data';
  deletable: boolean;
  bytes: number;
  formatted: string;
  percent_of_total: number;
}

export interface StorageUsageResponse {
  roots: string[];
  total_bytes: number;
  total_formatted: string;
  categories: StorageUsageCategory[];
  other_breakdown: StorageUsageOtherItem[];
  scan_errors: number;
  generated_at: string;
  cache: {
    hit: boolean;
    age_seconds: number;
    max_age_seconds: number;
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
  is_external: boolean;
  external_path: string | null;
  external_readonly: boolean;
  file_count: number;
  // max(folder.updated_at, max(immediate-child file.updated_at)). Used by
  // the File Manager folder tree's "sort by recent activity" mode (#1770).
  latest_activity_at: string | null;
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
  is_external: boolean;
  external_path: string | null;
  external_readonly: boolean;
  external_show_hidden: boolean;
  file_count: number;
  latest_activity_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface LibraryFolderCreate {
  name: string;
  parent_id?: number | null;
  project_id?: number | null;
  archive_id?: number | null;
}

export interface ExternalFolderCreate {
  name: string;
  external_path: string;
  readonly?: boolean;
  show_hidden?: boolean;
  parent_id?: number | null;
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
  is_external: boolean;
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

export interface LibraryTagSummary {
  id: number;
  name: string;
}

export interface LibraryFileListItem {
  id: number;
  folder_id: number | null;
  is_external: boolean;
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
  // Tags assigned to this file (#1268). The backend always emits an empty
  // array when a file has no tags, but the field is typed optional so any
  // legacy code path (or mock) that constructs a LibraryFileListItem without
  // it doesn't crash the renderer. Read sites use `file.tags ?? []`.
  tags?: LibraryTagSummary[];
}

// Library tag catalog (#1268)
export interface LibraryTag {
  id: number;
  name: string;
  file_count: number;
  created_at: string;
  updated_at: string;
}

export interface LibraryTagBulkAssignResult {
  files_updated: number;
  associations_added: number;
  associations_removed: number;
}

export interface LibraryFileUpdate {
  filename?: string;
  folder_id?: number | null;
  project_id?: number | null;
  notes?: string | null;
}

// Library trash (#1008)
export interface LibraryTrashItem {
  id: number;
  filename: string;
  file_size: number;
  thumbnail_path: string | null;
  folder_id: number | null;
  folder_name: string | null;
  created_by_id: number | null;
  created_by_username: string | null;
  deleted_at: string;
  auto_purge_at: string;
}

export interface LibraryTrashListResponse {
  items: LibraryTrashItem[];
  total: number;
  retention_days: number;
}

export interface LibraryPurgePreview {
  count: number;
  total_bytes: number;
  sample_filenames: string[];
  older_than_days: number;
  include_never_printed: boolean;
}

export interface LibraryTrashSettings {
  retention_days: number;
  auto_purge_enabled: boolean;
  auto_purge_days: number;
  auto_purge_include_never_printed: boolean;
}

export interface ArchivePurgePreview {
  count: number;
  total_bytes: number;
  sample_filenames: string[];
  older_than_days: number;
}

export interface ArchivePurgeSettings {
  enabled: boolean;
  days: number;
  // #1390: when true, bulk-deletes the linked PrintLogEntry rows so the
  // contribution drops from Quick Stats too. Default false — soft-delete,
  // Quick Stats preserved.
  purge_stats: boolean;
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
// Canonical wire values: `archive`, `review`, `queue`, `proxy`. The legacy
// `immediate` (→ archive) and `print_queue` (→ queue) names are still
// accepted by the backend so older API clients keep working, but new code
// should send the canonical names.
export type VirtualPrinterMode = 'archive' | 'review' | 'queue' | 'proxy' | 'immediate' | 'print_queue';

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
  tailscale_disabled: boolean;
  archive_name_source: 'metadata' | 'filename';  // Source for archive's display name
  status: VirtualPrinterStatus;
}

export interface NetworkInterface {
  name: string;
  ip: string;
  netmask: string;
  subnet: string;
  is_alias?: boolean;
  label?: string;
}

export interface VirtualPrinterModels {
  models: Record<string, string>;  // SSDP code -> display name
  default: string;
}

export interface PendingUpload {
  id: number;
  filename: string;
  // Resolved name the review card should show — mirrors what archive_print
  // will eventually write to PrintArchive.print_name (#1152 follow-up). Falls
  // back to the stripped filename stem when the 3MF has no embedded title or
  // the operator has chosen the "filename" archive-name source.
  display_name: string;
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
    mode?: 'archive' | 'review' | 'queue' | 'proxy';
    model?: string;
    target_printer_id?: number;
    remote_interface_ip?: string;
    tailscale_disabled?: boolean;
    archive_name_source?: 'metadata' | 'filename';
  }) => {
    const params = new URLSearchParams();
    if (data.enabled !== undefined) params.set('enabled', String(data.enabled));
    if (data.access_code !== undefined) params.set('access_code', data.access_code);
    if (data.mode !== undefined) params.set('mode', data.mode);
    if (data.model !== undefined) params.set('model', data.model);
    if (data.target_printer_id !== undefined) params.set('target_printer_id', String(data.target_printer_id));
    if (data.remote_interface_ip !== undefined) params.set('remote_interface_ip', data.remote_interface_ip);
    if (data.tailscale_disabled !== undefined) params.set('tailscale_disabled', String(data.tailscale_disabled));
    if (data.archive_name_source !== undefined) params.set('archive_name_source', data.archive_name_source);

    return request<VirtualPrinterSettings>(`/settings/virtual-printer?${params.toString()}`, {
      method: 'PUT',
    });
  },
};

// Multi Virtual Printer API
export interface VirtualPrinterConfig {
  id: number;
  name: string;
  enabled: boolean;
  mode: VirtualPrinterMode;
  model: string | null;
  model_name: string | null;
  access_code_set: boolean;
  serial: string;
  target_printer_id: number | null;
  auto_dispatch: boolean;
  queue_force_color_match: boolean;
  gcode_injection: boolean;
  tailscale_disabled: boolean;
  bind_ip: string | null;
  remote_interface_ip: string | null;
  position: number;
  status: { running: boolean; pending_files: number; proxy?: VirtualPrinterProxyStatus };
}

export interface VirtualPrinterListResponse {
  printers: VirtualPrinterConfig[];
  models: Record<string, string>;
}

export const multiVirtualPrinterApi = {
  list: () => request<VirtualPrinterListResponse>('/virtual-printers'),

  get: (id: number) => request<VirtualPrinterConfig>(`/virtual-printers/${id}`),

  create: (data: {
    name?: string;
    enabled?: boolean;
    mode?: string;
    model?: string;
    access_code?: string;
    target_printer_id?: number;
    auto_dispatch?: boolean;
    queue_force_color_match?: boolean;
    gcode_injection?: boolean;
    bind_ip?: string;
    remote_interface_ip?: string;
  }) =>
    request<VirtualPrinterConfig>('/virtual-printers', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  update: (id: number, data: {
    name?: string;
    enabled?: boolean;
    mode?: string;
    model?: string;
    access_code?: string;
    target_printer_id?: number;
    auto_dispatch?: boolean;
    queue_force_color_match?: boolean;
    gcode_injection?: boolean;
    tailscale_disabled?: boolean;
    bind_ip?: string;
    remote_interface_ip?: string;
  }) =>
    request<VirtualPrinterConfig>(`/virtual-printers/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  remove: (id: number) =>
    request<{ detail: string; id: number }>(`/virtual-printers/${id}`, {
      method: 'DELETE',
    }),

  getTailscaleStatus: () =>
    request<TailscaleStatusResponse>('/virtual-printers/tailscale-status'),

  getCaCertificate: () =>
    request<VPCaCertificate>('/virtual-printers/ca-certificate'),

  diagnose: (id: number) =>
    request<VPDiagnosticResult>(`/virtual-printers/${id}/diagnostic`),
};

/** The shared CA certificate every virtual printer presents — imported once
 *  into the slicer's trust store. Only the public certificate is returned. */
export interface VPCaCertificate {
  pem: string;
  fingerprint_sha256: string;
  not_valid_after: string;
}

export type VPDiagnosticStatus = 'pass' | 'fail' | 'warn' | 'skip';

export interface VPDiagnosticCheck {
  id:
    | 'enabled'
    | 'running'
    | 'bind_interface'
    | 'access_code'
    | 'target_printer'
    | 'port_ftps'
    | 'port_mqtt'
    | 'port_bind'
    | 'certificate';
  status: VPDiagnosticStatus;
  params: Record<string, string | number>;
}

export interface VPDiagnosticResult {
  vp_id: number;
  vp_name: string;
  mode: string;
  overall: 'ok' | 'warnings' | 'problems';
  checks: VPDiagnosticCheck[];
}

export interface TailscaleStatusResponse {
  available: boolean;
  fqdn: string;
  hostname: string;
  tailnet_name: string;
  tailscale_ips: string[];
  error: string | null;
}

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
export interface AvailableFirmwareVersion {
  version: string;
  file_available: boolean;
  download_url: string | null;
  release_notes: string | null;
  release_time: string | null;
}

export interface FirmwareUpdateInfo {
  printer_id: number;
  printer_name: string;
  model: string | null;
  current_version: string | null;
  latest_version: string | null;
  update_available: boolean;
  download_url: string | null;
  release_notes: string | null;
  available_versions: AvailableFirmwareVersion[];
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
  target_version: string | null;
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

  prepareUpload: (printerId: number, version?: string) =>
    request<FirmwareUploadPrepare>(
      `/firmware/updates/${printerId}/prepare${version ? `?version=${encodeURIComponent(version)}` : ''}`,
    ),

  startUpload: (printerId: number, version?: string) =>
    request<{ started: boolean; message: string }>(
      `/firmware/updates/${printerId}/upload${version ? `?version=${encodeURIComponent(version)}` : ''}`,
      { method: 'POST' },
    ),

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

// SpoolBuddy types
export interface SpoolBuddyDevice {
  id: number;
  device_id: string;
  hostname: string;
  ip_address: string;
  backend_url?: string | null;
  firmware_version: string | null;
  has_nfc: boolean;
  has_scale: boolean;
  tare_offset: number;
  calibration_factor: number;
  nfc_reader_type: string | null;
  nfc_connection: string | null;
  display_brightness: number;
  display_blank_timeout: number;
  has_backlight: boolean;
  last_calibrated_at: string | null;
  last_seen: string | null;
  pending_command: string | null;
  nfc_ok: boolean;
  scale_ok: boolean;
  uptime_s: number;
  update_status: string | null;
  update_message: string | null;
  system_stats: {
    os?: { os?: string; kernel?: string; arch?: string; python?: string };
    cpu_temp_c?: number;
    cpu_count?: number;
    load_avg?: number[];
    memory?: { total_mb?: number; available_mb?: number; used_mb?: number; percent?: number };
    disk?: { total_gb?: number; used_gb?: number; free_gb?: number; percent?: number };
    system_uptime_s?: number;
  } | null;
  online: boolean;
}

export interface DaemonUpdateCheck {
  current_version: string;
  latest_version: string | null;
  update_available: boolean;
}

// SpoolBuddy API
export const spoolbuddyApi = {
  getDevices: () =>
    request<SpoolBuddyDevice[]>('/spoolbuddy/devices'),

  deleteDevice: (deviceId: string) =>
    request<{ status: string; device_id: string }>(`/spoolbuddy/devices/${deviceId}`, {
      method: 'DELETE',
    }),

  tare: (deviceId: string) =>
    request<{ status: string }>(`/spoolbuddy/devices/${deviceId}/calibration/tare`, {
      method: 'POST',
      body: '{}',
    }),

  getCalibration: (deviceId: string) =>
    request<{ tare_offset: number; calibration_factor: number }>(`/spoolbuddy/devices/${deviceId}/calibration`),

  setCalibrationFactor: (deviceId: string, knownWeightGrams: number, rawAdc: number, tareRawAdc?: number) =>
    request<{ tare_offset: number; calibration_factor: number }>(`/spoolbuddy/devices/${deviceId}/calibration/set-factor`, {
      method: 'POST',
      body: JSON.stringify({ known_weight_grams: knownWeightGrams, raw_adc: rawAdc, tare_raw_adc: tareRawAdc }),
    }),

  updateSpoolWeight: (spoolId: number, weightGrams: number) =>
    request<{ status: string; weight_used: number }>('/spoolbuddy/scale/update-spool-weight', {
      method: 'POST',
      body: JSON.stringify({ spool_id: spoolId, weight_grams: weightGrams }),
    }),

  updateDisplay: (deviceId: string, brightness: number, blankTimeout: number) =>
    request<{ status: string }>(`/spoolbuddy/devices/${deviceId}/display`, {
      method: 'PUT',
      body: JSON.stringify({ brightness, blank_timeout: blankTimeout }),
    }),

  updateSystemConfig: (deviceId: string, backendUrl: string, apiKey?: string) =>
    request<{ status: string; message: string }>(`/spoolbuddy/devices/${deviceId}/system/config`, {
      method: 'POST',
      body: JSON.stringify({ backend_url: backendUrl, ...(apiKey ? { api_key: apiKey } : {}) }),
    }),

  checkDaemonUpdate: (deviceId: string) =>
    request<DaemonUpdateCheck>(`/spoolbuddy/devices/${deviceId}/update-check`),

  triggerUpdate: (deviceId: string) =>
    request<{ status: string; message: string }>(`/spoolbuddy/devices/${deviceId}/update`, {
      method: 'POST',
      body: '{}',
    }),

  getSSHPublicKey: () =>
    request<{ public_key: string }>('/spoolbuddy/ssh/public-key'),

  writeTag: (deviceId: string, spoolId: number) =>
    request<{ status: string; warnings?: string[] }>('/spoolbuddy/nfc/write-tag', {
      method: 'POST',
      body: JSON.stringify({ device_id: deviceId, spool_id: spoolId }),
    }),

  cancelWrite: (deviceId: string) =>
    request<{ status: string }>(`/spoolbuddy/devices/${deviceId}/cancel-write`, {
      method: 'POST',
      body: '{}',
    }),

  systemCommand: (deviceId: string, command: 'reboot' | 'shutdown' | 'restart_daemon' | 'restart_browser') =>
    request<{ status: string; command: string }>(`/spoolbuddy/devices/${deviceId}/system/command`, {
      method: 'POST',
      body: JSON.stringify({ command }),
    }),

  queueDiagnostics: (deviceId: string, type: 'nfc' | 'scale' | 'read_tag') =>
    request<{ status: string; diagnostic: string; message: string }>(
      `/spoolbuddy/diagnostics/${deviceId}/run?diagnostic=${type}`,
      { method: 'POST', body: '{}' }
    ),

  getDiagnosticResult: (deviceId: string, type: 'nfc' | 'scale' | 'read_tag') =>
    request<{ diagnostic: string; success: boolean; output: string; exit_code: number }>(
      `/spoolbuddy/diagnostics/${deviceId}/result?diagnostic=${type}`,
      { method: 'GET' }
    ),
};

export interface BugReportRequest {
  description: string;
  email?: string;
  screenshot_base64?: string;
  include_support_info?: boolean;
  debug_logs?: string;
}

export interface BugReportResponse {
  success: boolean;
  message: string;
  issue_url?: string;
  issue_number?: number;
}

export const bugReportApi = {
  submit: (data: BugReportRequest) =>
    request<BugReportResponse>('/bug-report/submit', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  startLogging: () =>
    request<{ started: boolean; was_debug: boolean }>('/bug-report/start-logging', {
      method: 'POST',
    }),
  stopLogging: (wasDebug: boolean) =>
    request<{ logs: string }>(`/bug-report/stop-logging?was_debug=${wasDebug}`, {
      method: 'POST',
    }),
};

export interface SponsorPromptCheckResponse {
  show: boolean;
  milestone?: string;
  family?: 'prints' | 'cost' | 'archives' | 'anniversary' | 'version-update';
  threshold?: number;
  payload?: Record<string, unknown>;
}

export const sponsorPromptApi = {
  check: () => request<SponsorPromptCheckResponse>('/sponsor-prompt/check'),
  dismiss: (milestone: string) =>
    request<void>('/sponsor-prompt/dismiss', {
      method: 'POST',
      body: JSON.stringify({ milestone }),
    }),
};
