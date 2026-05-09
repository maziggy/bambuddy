/**
 * Tests for the ?spool= deep-link flow in InventoryPage.
 *
 * Three scenarios:
 *  1. Spool is already in the loaded list → modal opens immediately.
 *  2. Spool is not in list → targeted API fetch succeeds → modal opens.
 *  3. Spool is not found (404) → error toast shown, param removed from URL.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { render } from '../utils';
import InventoryPageRouter from '../../pages/InventoryPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

// Minimal spool fixture shared across scenarios
const BASE_SPOOL = {
  id: 42,
  material: 'PLA',
  subtype: 'Basic',
  brand: 'Bambu Lab',
  color_name: 'Red',
  rgba: 'FF0000FF',
  label_weight: 1000,
  core_weight: 250,
  weight_used: 100,
  slicer_filament: null,
  slicer_filament_name: null,
  nozzle_temp_min: 220,
  nozzle_temp_max: 240,
  note: null,
  added_full: null,
  last_used: null,
  encode_time: null,
  tag_uid: null,
  tray_uuid: null,
  data_origin: null,
  tag_type: null,
  archived_at: null,
  created_at: '2025-01-01T00:00:00Z',
  updated_at: '2025-01-01T00:00:00Z',
  k_profiles: [],
  cost_per_kg: null,
  last_scale_weight: null,
  last_weighed_at: null,
  storage_location: null,
  weight_locked: false,
};

const MOCK_SETTINGS = {
  auto_archive: false,
  save_thumbnails: false,
  capture_finish_photo: false,
  default_filament_cost: 25.0,
  currency: 'USD',
  energy_cost_per_kwh: 0.15,
  energy_tracking_mode: 'total',
  spoolman_enabled: false,
  spoolman_url: '',
  spoolman_sync_mode: 'auto',
  spoolman_disable_weight_sync: false,
  spoolman_report_partial_usage: true,
  check_updates: false,
  check_printer_firmware: false,
  include_beta_updates: false,
  language: 'en',
  notification_language: 'en',
  bed_cooled_threshold: 35,
  ams_humidity_good: 40,
  ams_humidity_fair: 60,
  ams_temp_good: 28,
  ams_temp_fair: 35,
  ams_history_retention_days: 30,
  per_printer_mapping_expanded: false,
  date_format: 'system',
  time_format: 'system',
  default_printer_id: null,
  virtual_printer_enabled: false,
  virtual_printer_access_code: '',
  virtual_printer_mode: 'immediate',
  dark_style: 'classic',
  dark_background: 'neutral',
  dark_accent: 'green',
  light_style: 'classic',
  light_background: 'neutral',
  light_accent: 'green',
  ftp_retry_enabled: true,
  ftp_retry_count: 3,
  ftp_retry_delay: 2,
  ftp_timeout: 30,
  mqtt_enabled: false,
  mqtt_broker: '',
  mqtt_port: 1883,
  mqtt_username: '',
  mqtt_password: '',
  mqtt_topic_prefix: 'bambuddy',
  mqtt_use_tls: false,
  external_url: '',
  ha_enabled: false,
  ha_url: '',
  ha_token: '',
  ha_url_from_env: false,
  ha_token_from_env: false,
  ha_env_managed: false,
  library_archive_mode: 'ask',
  library_disk_warning_gb: 5.0,
  camera_view_mode: 'window',
  preferred_slicer: 'bambu_studio',
  prometheus_enabled: false,
  prometheus_token: '',
  low_stock_threshold: 20.0,
};

function setupCommonHandlers(spoolList: object[]) {
  server.use(
    http.get('/api/v1/settings/', () => HttpResponse.json(MOCK_SETTINGS)),
    // getSpoolmanSettings calls /api/v1/settings/spoolman (not /api/v1/spoolman/settings)
    http.get('/api/v1/settings/spoolman', () =>
      HttpResponse.json({ spoolman_enabled: 'false', spoolman_url: '', spoolman_sync_mode: 'auto', spoolman_disable_weight_sync: 'false', spoolman_report_partial_usage: 'true' })
    ),
    http.get('/api/v1/inventory/spools', () => HttpResponse.json(spoolList)),
    http.get('/api/v1/inventory/assignments', () => HttpResponse.json([])),
    http.get('/api/v1/inventory/catalog', () => HttpResponse.json([])),
    // Deep-link flows open SpoolFormModal, which fires off these fetches the
    // moment it mounts. Without handlers MSW would passthrough to the real
    // network (ECONNREFUSED); the rejected fetch then resolves after the
    // test environment is torn down, surfacing as an unhandled rejection
    // ("window is not defined") in the modal's setState finally.
    http.get('/api/v1/cloud/status', () =>
      HttpResponse.json({ is_authenticated: false })
    ),
    http.get('/api/v1/cloud/local-presets', () =>
      HttpResponse.json({ filament: [], printer: [], process: [] })
    ),
    http.get('/api/v1/cloud/builtin-filaments', () => HttpResponse.json([])),
    http.get('/api/v1/inventory/color-catalog', () => HttpResponse.json([])),
    http.get('/api/v1/inventory/spool-catalog', () => HttpResponse.json([])),
    http.get('/api/v1/printers/', () => HttpResponse.json([])),
  );
}

describe('InventoryPage - deep-link ?spool= flow', () => {
  const originalLocation = window.location.href;

  afterEach(() => {
    // Restore URL after each test
    window.history.replaceState({}, '', originalLocation);
  });

  describe('scenario 1: spool is already in the loaded list', () => {
    beforeEach(() => {
      window.history.pushState({}, '', '/?spool=42');
      setupCommonHandlers([BASE_SPOOL]);
    });

    it('removes ?spool= param from URL after handling', async () => {
      render(<InventoryPageRouter />);

      await waitFor(() => {
        expect(window.location.search).not.toContain('spool=42');
      });
    });

    it('opens the edit modal for the linked spool', async () => {
      render(<InventoryPageRouter />);

      // The SpoolFormModal should open — it renders material name in a heading or field
      await waitFor(() => {
        // Modal is open when the spool form inputs are visible
        expect(screen.getAllByText(/PLA/i).length).toBeGreaterThan(0);
      });
    });
  });

  describe('scenario 2: targeted fetch (spool not in initial list)', () => {
    beforeEach(() => {
      window.history.pushState({}, '', '/?spool=42');
      // List is empty; single-spool fetch returns the spool
      setupCommonHandlers([]);
      server.use(
        http.get('/api/v1/inventory/spools/:id', ({ params }) => {
          if (Number(params.id) === 42) {
            return HttpResponse.json(BASE_SPOOL);
          }
          return HttpResponse.json({ detail: 'Not found' }, { status: 404 });
        })
      );
    });

    it('removes ?spool= param from URL after successful targeted fetch', async () => {
      render(<InventoryPageRouter />);

      await waitFor(() => {
        expect(window.location.search).not.toContain('spool=42');
      });
    });
  });

  describe('scenario 3: spool not found (404)', () => {
    beforeEach(() => {
      window.history.pushState({}, '', '/?spool=9999');
      setupCommonHandlers([]);
      server.use(
        http.get('/api/v1/inventory/spools/:id', () =>
          HttpResponse.json({ detail: 'Not found' }, { status: 404 })
        )
      );
    });

    it('removes ?spool= param from URL on 404', async () => {
      render(<InventoryPageRouter />);

      await waitFor(() => {
        expect(window.location.search).not.toContain('spool=9999');
      });
    });

    it('shows an error notification when spool is not found', async () => {
      render(<InventoryPageRouter />);

      // Must render the exact i18n string for deepLinkSpoolNotFound (en: 'Spool not found').
      // Using findByText fails the test if the toast is absent or uses the wrong key.
      await screen.findByText('Spool not found');
    });
  });

  describe('scenario 4: targeted fetch returns 5xx server error', () => {
    beforeEach(() => {
      window.history.pushState({}, '', '/?spool=42');
      setupCommonHandlers([]);
      server.use(
        http.get('/api/v1/inventory/spools/:id', () =>
          HttpResponse.json({ detail: 'Internal Server Error' }, { status: 500 })
        )
      );
    });

    it('shows the deepLinkFetchFailed toast on 5xx', async () => {
      render(<InventoryPageRouter />);

      // The deep-link query has a custom retry callback (up to 2 retries with
      // exponential backoff) that overrides the test QueryClient's retry:false.
      // Allow up to 6 s for the retries to exhaust before the toast appears.
      await screen.findByText('Could not load spool — try again', {}, { timeout: 6000 });
    });
  });

  describe('scenario 5 (T-Gap 8): deep-link works in Spoolman mode', () => {
    beforeEach(() => {
      window.history.pushState({}, '', '/?spool=42');
      // Override settings with Spoolman enabled
      server.use(
        http.get('/api/v1/settings/', () =>
          HttpResponse.json({ ...MOCK_SETTINGS, spoolman_enabled: true, spoolman_url: 'http://spoolman.local:7912' })
        ),
        http.get('/api/v1/settings/spoolman', () =>
          HttpResponse.json({ spoolman_enabled: 'true', spoolman_url: 'http://spoolman.local:7912', spoolman_sync_mode: 'auto', spoolman_disable_weight_sync: 'false', spoolman_report_partial_usage: 'true' })
        ),
        // In Spoolman mode the component fetches from /api/v1/spoolman/inventory/spools
        http.get('/api/v1/spoolman/inventory/spools', () => HttpResponse.json([BASE_SPOOL])),
        http.get('/api/v1/inventory/assignments', () => HttpResponse.json([])),
        http.get('/api/v1/inventory/catalog', () => HttpResponse.json([])),
      );
    });

    it('removes ?spool= param from URL in Spoolman mode', async () => {
      render(<InventoryPageRouter />);

      await waitFor(() => {
        expect(window.location.search).not.toContain('spool=42');
      });
    });

    it('opens the edit modal for the linked local spool in Spoolman mode', async () => {
      render(<InventoryPageRouter />);

      await waitFor(() => {
        expect(screen.getAllByText(/PLA/i).length).toBeGreaterThan(0);
      });
    });
  });
});
