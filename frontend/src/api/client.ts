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
  is_active: boolean;
  auto_archive: boolean;
  created_at: string;
  updated_at: string;
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
export interface Archive {
  id: number;
  printer_id: number | null;
  filename: string;
  file_path: string;
  file_size: number;
  thumbnail_path: string | null;
  timelapse_path: string | null;
  print_name: string | null;
  print_time_seconds: number | null;
  filament_used_grams: number | null;
  filament_type: string | null;
  filament_color: string | null;
  layer_height: number | null;
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
  default_filament_cost: number;
  currency: string;
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
  getArchiveThumbnail: (id: number) => `${API_BASE}/archives/${id}/thumbnail`,
  getArchiveDownload: (id: number) => `${API_BASE}/archives/${id}/download`,
  getArchiveGcode: (id: number) => `${API_BASE}/archives/${id}/gcode`,
  getArchiveTimelapse: (id: number) => `${API_BASE}/archives/${id}/timelapse`,
  scanArchiveTimelapse: (id: number) =>
    request<{ status: string; message: string; filename?: string }>(`/archives/${id}/timelapse/scan`, {
      method: 'POST',
    }),
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
  // QR Code
  getArchiveQRCodeUrl: (archiveId: number, size = 200) =>
    `${API_BASE}/archives/${archiveId}/qrcode?size=${size}`,
  getArchiveCapabilities: (id: number) =>
    request<{
      has_model: boolean;
      has_gcode: boolean;
      build_volume: { x: number; y: number; z: number };
    }>(`/archives/${id}/capabilities`),
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
};
