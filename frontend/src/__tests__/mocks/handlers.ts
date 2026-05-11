/**
 * MSW request handlers for mocking API responses in tests.
 */

import { http, HttpResponse } from 'msw';

// Sample data
const mockSmartPlugs = [
  {
    id: 1,
    name: 'Test Plug',
    ip_address: '192.168.1.100',
    printer_id: 1,
    enabled: true,
    auto_on: true,
    auto_off: true,
    off_delay_mode: 'time',
    off_delay_minutes: 5,
    off_temp_threshold: 70,
    username: null,
    password: null,
    power_alert_enabled: false,
    power_alert_high: null,
    power_alert_low: null,
    power_alert_last_triggered: null,
    schedule_enabled: false,
    schedule_on_time: null,
    schedule_off_time: null,
    last_state: 'ON',
    last_checked: null,
    auto_off_executed: false,
    auto_off_pending: false,
    auto_off_pending_since: null,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  },
];

const mockNotificationProviders = [
  {
    id: 1,
    name: 'Test Webhook',
    provider_type: 'webhook',
    enabled: true,
    config: { webhook_url: 'http://test.local/webhook' },
    on_print_start: true,
    on_print_complete: true,
    on_print_failed: true,
    on_print_stopped: false,
    on_print_progress: false,
    on_printer_offline: false,
    on_printer_error: false,
    on_filament_low: false,
    on_maintenance_due: false,
    on_ams_humidity_high: false,
    on_ams_temperature_high: false,
    on_ams_ht_humidity_high: false,
    on_ams_ht_temperature_high: false,
    quiet_hours_enabled: false,
    quiet_hours_start: null,
    quiet_hours_end: null,
    daily_digest_enabled: false,
    daily_digest_time: null,
    printer_id: null,
    last_success: null,
    last_error: null,
    last_error_at: null,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  },
];

const mockPrinters = [
  {
    id: 1,
    name: 'Test Printer',
    serial_number: '00M09A000000000',
    ip_address: '192.168.1.200',
    is_active: true,
    model: 'X1C',
    nozzle_count: 1,
    auto_archive: true,
    location: null,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  },
];

export const handlers = [
  // ========================================================================
  // Smart Plugs
  // ========================================================================

  http.get('/api/v1/smart-plugs/', () => {
    return HttpResponse.json(mockSmartPlugs);
  }),

  http.get('/api/v1/smart-plugs/:id', ({ params }) => {
    const plug = mockSmartPlugs.find((p) => p.id === Number(params.id));
    if (!plug) {
      return new HttpResponse(null, { status: 404 });
    }
    return HttpResponse.json(plug);
  }),

  http.post('/api/v1/smart-plugs/', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    const { id: _id, ...baseData } = mockSmartPlugs[0];
    const newPlug = {
      id: mockSmartPlugs.length + 1,
      ...baseData,
      ...body,
    };
    return HttpResponse.json(newPlug);
  }),

  http.patch('/api/v1/smart-plugs/:id', async ({ params, request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    const plug = mockSmartPlugs.find((p) => p.id === Number(params.id));
    if (!plug) {
      return new HttpResponse(null, { status: 404 });
    }
    return HttpResponse.json({ ...plug, ...body });
  }),

  http.delete('/api/v1/smart-plugs/:id', ({ params }) => {
    const index = mockSmartPlugs.findIndex((p) => p.id === Number(params.id));
    if (index === -1) {
      return new HttpResponse(null, { status: 404 });
    }
    return HttpResponse.json({ success: true });
  }),

  http.get('/api/v1/smart-plugs/:id/status', () => {
    return HttpResponse.json({
      state: 'ON',
      reachable: true,
      device_name: 'Test Plug',
      energy: {
        power: 150.5,
        voltage: 120.0,
        current: 1.25,
        today: 2.5,
        total: 100.0,
      },
    });
  }),

  http.post('/api/v1/smart-plugs/:id/control', async ({ request }) => {
    const body = (await request.json()) as { action: string };
    return HttpResponse.json({
      success: true,
      action: body.action,
    });
  }),

  // ========================================================================
  // Notification Providers
  // ========================================================================

  http.get('/api/v1/notifications/', () => {
    return HttpResponse.json(mockNotificationProviders);
  }),

  http.get('/api/v1/notifications/:id', ({ params }) => {
    const provider = mockNotificationProviders.find(
      (p) => p.id === Number(params.id)
    );
    if (!provider) {
      return new HttpResponse(null, { status: 404 });
    }
    return HttpResponse.json(provider);
  }),

  http.post('/api/v1/notifications/', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    const { id: _id, ...baseData } = mockNotificationProviders[0];
    const newProvider = {
      id: mockNotificationProviders.length + 1,
      ...baseData,
      ...body,
    };
    return HttpResponse.json(newProvider);
  }),

  http.patch('/api/v1/notifications/:id', async ({ params, request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    const provider = mockNotificationProviders.find(
      (p) => p.id === Number(params.id)
    );
    if (!provider) {
      return new HttpResponse(null, { status: 404 });
    }
    return HttpResponse.json({ ...provider, ...body });
  }),

  http.delete('/api/v1/notifications/:id', ({ params }) => {
    const index = mockNotificationProviders.findIndex(
      (p) => p.id === Number(params.id)
    );
    if (index === -1) {
      return new HttpResponse(null, { status: 404 });
    }
    return HttpResponse.json({ success: true });
  }),

  http.post('/api/v1/notifications/:id/test', () => {
    return HttpResponse.json({
      success: true,
      message: 'Test notification sent',
    });
  }),

  // ========================================================================
  // Printers
  // ========================================================================

  http.get('/api/v1/printers/', () => {
    return HttpResponse.json(mockPrinters);
  }),

  http.get('/api/v1/printers/:id', ({ params }) => {
    const printer = mockPrinters.find((p) => p.id === Number(params.id));
    if (!printer) {
      return new HttpResponse(null, { status: 404 });
    }
    return HttpResponse.json(printer);
  }),

  http.get('/api/v1/printers/:id/status', ({ params }) => {
    return HttpResponse.json({
      id: Number(params.id),
      name: 'Test Printer',
      connected: true,
      state: 'IDLE',
      progress: 0,
      layer_num: 0,
      total_layers: 0,
      temperatures: {
        nozzle: 25,
        bed: 25,
        chamber: 25,
      },
      remaining_time: 0,
      filename: null,
    });
  }),

  // ========================================================================
  // Settings
  // ========================================================================

  http.get('/api/v1/settings/', () => {
    return HttpResponse.json({
      auto_archive: true,
      save_thumbnails: true,
      capture_finish_photo: true,
      default_filament_cost: 25.0,
      currency: 'USD',
      ams_humidity_good: 40,
      ams_humidity_fair: 60,
      ams_temp_good: 30,
      ams_temp_fair: 35,
    });
  }),

  http.patch('/api/v1/settings/', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json(body);
  }),

  // ========================================================================
  // Auth
  // ========================================================================

  http.get('*/api/v1/auth/status', () => {
    return HttpResponse.json({
      auth_enabled: false,
      requires_setup: false,
    });
  }),

  http.get('/api/v1/auth/me', () => {
    return HttpResponse.json({
      id: 1,
      username: 'admin',
      role: 'admin',
      is_active: true,
      is_admin: true,
      groups: [{ id: 1, name: 'Administrators' }],
      permissions: [],
      created_at: '2024-01-01T00:00:00Z',
    });
  }),

  // ========================================================================
  // Groups
  // ========================================================================

  http.get('/api/v1/groups/', () => {
    return HttpResponse.json([
      {
        id: 1,
        name: 'Administrators',
        description: 'Full access to all features',
        permissions: ['printers:read', 'settings:update', 'users:create'],
        is_system: true,
        created_at: '2024-01-01T00:00:00Z',
        updated_at: '2024-01-01T00:00:00Z',
      },
      {
        id: 2,
        name: 'Operators',
        description: 'Control printers and manage content',
        permissions: ['printers:read', 'printers:control'],
        is_system: true,
        created_at: '2024-01-01T00:00:00Z',
        updated_at: '2024-01-01T00:00:00Z',
      },
      {
        id: 3,
        name: 'Viewers',
        description: 'Read-only access',
        permissions: ['printers:read'],
        is_system: true,
        created_at: '2024-01-01T00:00:00Z',
        updated_at: '2024-01-01T00:00:00Z',
      },
    ]);
  }),

  http.get('/api/v1/groups/permissions', () => {
    return HttpResponse.json({
      'Printers': ['printers:read', 'printers:create', 'printers:update', 'printers:delete', 'printers:control'],
      'Archives': ['archives:read', 'archives:create', 'archives:update', 'archives:delete'],
      'Settings': ['settings:read', 'settings:update'],
    });
  }),

  http.post('/api/v1/groups/', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({
      id: 4,
      ...body,
      is_system: false,
      created_at: '2024-01-01T00:00:00Z',
      updated_at: '2024-01-01T00:00:00Z',
    });
  }),

  http.patch('/api/v1/groups/:id', async ({ params, request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({
      id: Number(params.id),
      name: 'Updated Group',
      ...body,
      is_system: false,
      created_at: '2024-01-01T00:00:00Z',
      updated_at: '2024-01-01T00:00:00Z',
    });
  }),

  http.delete('/api/v1/groups/:id', () => {
    return new HttpResponse(null, { status: 204 });
  }),

  // ========================================================================
  // Discovery
  // ========================================================================

  http.get('/api/v1/discovery/info', () => {
    return HttpResponse.json({
      is_docker: false,
      ssdp_running: false,
      scan_running: false,
      subnets: ['192.168.1.0/24'],
    });
  }),

  // ========================================================================
  // Version / Health
  // ========================================================================

  http.get('/api/v1/version', () => {
    return HttpResponse.json({
      version: '0.1.5',
      build: 'test',
    });
  }),

  http.get('/health', () => {
    return HttpResponse.json({ status: 'healthy' });
  }),

  // ========================================================================
  // Archives
  // ========================================================================

  http.get('/api/v1/archives/:id/plates', ({ params }) => {
    const archiveId = Number(params.id);
    return HttpResponse.json({
      archive_id: Number.isFinite(archiveId) ? archiveId : 0,
      filename: 'sample.3mf',
      plates: [],
      is_multi_plate: false,
    });
  }),

  http.get('/api/v1/archives/:id/filament-requirements', () => {
    return HttpResponse.json([]);
  }),

  // ========================================================================
  // Library
  // ========================================================================

  http.get('/api/v1/library/stats', () => {
    return HttpResponse.json({
      total_files: 0,
      total_size: 0,
      total_folders: 0,
    });
  }),

  // ========================================================================
  // Read-on-mount fallbacks
  // ------------------------------------------------------------------------
  // Components fire background fetches when they mount (status badges,
  // notification-template loaders, oidc/ldap/2fa probes, etc.). Without
  // handlers these fall through to the real network and the rejected promise
  // surfaces as an ECONNREFUSED stack trace in test stderr. These minimal
  // disabled-state stubs silence the trace. Per-test handlers added via
  // server.use(...) still win.
  // ========================================================================

  // Lists → empty arrays
  http.get('/api/v1/archives/', () => HttpResponse.json([])),
  http.get('/api/v1/auth/oidc/providers', () => HttpResponse.json([])),
  http.get('/api/v1/auth/oidc/providers/all', () => HttpResponse.json([])),
  http.get('/api/v1/auth/tokens', () => HttpResponse.json([])),
  http.get('/api/v1/auth/tokens/all', () => HttpResponse.json([])),
  http.get('/api/v1/external-links/', () => HttpResponse.json([])),
  http.get('/api/v1/inventory/assignments', () => HttpResponse.json([])),
  http.get('/api/v1/inventory/catalog', () => HttpResponse.json([])),
  http.get('/api/v1/inventory/colors', () => HttpResponse.json([])),
  http.get('/api/v1/inventory/spools', () => HttpResponse.json([])),
  http.get('/api/v1/library/folders', () => HttpResponse.json([])),
  http.get('/api/v1/library/folders/by-archive/:id', () => HttpResponse.json([])),
  http.get('/api/v1/maintenance/overview', () => HttpResponse.json([])),
  http.get('/api/v1/makerworld/recent-imports', () => HttpResponse.json([])),
  http.get('/api/v1/notification-templates', () => HttpResponse.json([])),
  http.get('/api/v1/pending-uploads/', () => HttpResponse.json([])),
  http.get('/api/v1/printers/:id/ams-labels', () => HttpResponse.json([])),
  http.get('/api/v1/printers/:id/slot-presets', () => HttpResponse.json([])),
  http.get('/api/v1/smart-plugs/by-printer/:id', () => HttpResponse.json([])),
  http.get('/api/v1/smart-plugs/by-printer/:id/scripts', () => HttpResponse.json([])),
  http.get('/api/v1/spoolbuddy/devices', () => HttpResponse.json([])),
  http.get('/api/v1/spoolman/inventory/filaments', () => HttpResponse.json([])),
  http.get('/api/v1/spoolman/spools/linked', () => HttpResponse.json([])),
  http.get('/api/v1/spoolman/spools/unlinked', () => HttpResponse.json([])),
  http.get('/api/v1/users/', () => HttpResponse.json([])),

  // Status / object endpoints → minimal disabled-state responses
  http.get('/api/v1/archives/purge/settings', () =>
    HttpResponse.json({ enabled: false, retention_days: 0 })
  ),
  http.get('/api/v1/auth/2fa/status', () =>
    HttpResponse.json({ totp_enabled: false, email_otp_enabled: false, backup_codes_remaining: 0 })
  ),
  http.get('/api/v1/auth/advanced-auth/status', () =>
    HttpResponse.json({ advanced_auth_enabled: false, smtp_configured: false })
  ),
  http.get('/api/v1/auth/ldap/status', () =>
    HttpResponse.json({ ldap_enabled: false, ldap_configured: false })
  ),
  http.get('/api/v1/cloud/status', () =>
    HttpResponse.json({ is_authenticated: false, email: null, region: null })
  ),
  http.get('/api/v1/firmware/updates/:id', () => HttpResponse.json(null)),
  http.get('/api/v1/github-backup/status', () =>
    HttpResponse.json({
      enabled: false,
      configured: false,
      last_backup_at: null,
      last_error: null,
      schedule_enabled: false,
    })
  ),
  http.get('/api/v1/library/trash', () => HttpResponse.json({ items: [], total: 0 })),
  http.get('/api/v1/library/trash/settings', () =>
    HttpResponse.json({ enabled: false, retention_days: 0 })
  ),
  http.get('/api/v1/obico/status', () =>
    HttpResponse.json({
      is_running: false,
      last_error: null,
      per_printer: {},
      thresholds: { low: 0, high: 0 },
      history: [],
      enabled: false,
      ml_url: '',
      sensitivity: 'medium',
      action: 'notify',
      poll_interval: 30,
      external_url_configured: false,
    })
  ),
  http.get('/api/v1/printers/:id/current-print-user', () => HttpResponse.json(null)),
  http.get('/api/v1/settings/check-ffmpeg', () =>
    HttpResponse.json({ available: false, version: null })
  ),
  http.get('/api/v1/settings/mqtt/status', () =>
    HttpResponse.json({ enabled: false, connected: false, broker: '', port: 1883, topic_prefix: '' })
  ),
  // NOTE: /api/v1/settings/spoolman intentionally NOT stubbed globally —
  // src/__tests__/api/client.test.ts uses it as an auth-header canary with
  // its own setupServer instance, and a global default would race with that
  // file's runtime handlers. Tests that need this endpoint stub it locally.
  http.get('/api/v1/settings/virtual-printer', () => HttpResponse.json({})),
  http.get('/api/v1/spoolman/status', () =>
    HttpResponse.json({ enabled: false, connected: false, url: null })
  ),
  http.get('/api/v1/system/storage-usage', () =>
    HttpResponse.json({
      roots: [],
      total_bytes: 0,
      total_formatted: '0 B',
      categories: [],
      other_breakdown: [],
      scan_errors: 0,
      generated_at: '2024-01-01T00:00:00Z',
      cache: { hit: false, age_seconds: 0, max_age_seconds: 0 },
    })
  ),
  http.get('/api/v1/updates/check', () =>
    HttpResponse.json({ update_available: false, latest_version: null })
  ),
  http.get('/api/v1/updates/status', () =>
    HttpResponse.json({ update_available: false, latest_version: null })
  ),
  http.get('/api/v1/updates/version', () =>
    HttpResponse.json({ version: '0.1.5', commit: 'test' })
  ),
  http.get('/openapi.json', () =>
    HttpResponse.json({ openapi: '3.0.0', info: { title: 'Bambuddy', version: '0.1.5' }, paths: {} })
  ),
  http.post('/api/v1/cloud/filament-info', () => HttpResponse.json({ filaments: [] })),
  http.post('/api/v1/printers/camera/stream-token', () =>
    HttpResponse.json({ token: 'test-token', expires_at: '2099-01-01T00:00:00Z' })
  ),
  http.put('/api/v1/settings/', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json(body);
  }),
];
