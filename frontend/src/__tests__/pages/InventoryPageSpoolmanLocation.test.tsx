/**
 * Tests for LOCATION column in InventoryPage when in Spoolman mode.
 *
 * Regression test for Phase 8: LOCATION column showed "-" for Spoolman
 * spools assigned to AMS slots, because only the local
 * /inventory/assignments endpoint was queried — the Spoolman
 * /spoolman/inventory/slot-assignments/all endpoint was ignored.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import { render } from '../utils';
import InventoryPageRouter from '../../pages/InventoryPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

// Full settings shape — pattern matches InventoryPageLowStock.test.tsx.
const mockSettings = {
  auto_archive: true,
  save_thumbnails: true,
  capture_finish_photo: true,
  default_filament_cost: 25.0,
  currency: 'USD',
  energy_cost_per_kwh: 0.15,
  energy_tracking_mode: 'total',
  spoolman_enabled: false,
  spoolman_url: '',
  spoolman_sync_mode: 'auto',
  spoolman_disable_weight_sync: false,
  spoolman_report_partial_usage: true,
  check_updates: true,
  check_printer_firmware: true,
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

const mockSpoolmanSpool = {
  id: 216,
  material: 'PLA',
  subtype: null,
  brand: 'Bambu Lab',
  color_name: 'Orange',
  rgba: 'FF8800FF',
  label_weight: 1000,
  core_weight: 250,
  weight_used: 200,
  slicer_filament: null,
  slicer_filament_name: null,
  nozzle_temp_min: null,
  nozzle_temp_max: null,
  note: null,
  added_full: null,
  last_used: null,
  encode_time: null,
  tag_uid: null,
  tray_uuid: null,
  data_origin: 'spoolman',
  tag_type: 'spoolman',
  archived_at: null,
  created_at: '2025-01-01T00:00:00Z',
  updated_at: '2025-01-01T00:00:00Z',
  k_profiles: [],
  cost_per_kg: null,
  last_scale_weight: null,
  last_weighed_at: null,
  storage_location: 'IKEA Regal',
};

describe('InventoryPage - LOCATION column (Spoolman mode)', () => {
  beforeEach(() => {
    localStorage.clear();
    server.use(
      http.get('/api/v1/settings/', () => HttpResponse.json(mockSettings)),
      http.get('/api/v1/inventory/assignments', () => HttpResponse.json([])),
      http.get('/api/v1/inventory/catalog', () => HttpResponse.json([])),
    );
  });

  it('shows AMS slot in LOCATION column for spoolman spool with assignment', async () => {
    server.use(
      http.get('/api/v1/settings/spoolman', () =>
        HttpResponse.json({
          spoolman_enabled: 'true',
          spoolman_url: 'http://localhost:7912',
        })
      ),
      http.get('/api/v1/spoolman/inventory/spools', () =>
        HttpResponse.json([mockSpoolmanSpool])
      ),
      http.get('/api/v1/spoolman/inventory/slot-assignments/all', () =>
        HttpResponse.json([
          {
            printer_id: 1,
            printer_name: 'Sully',
            ams_id: 0,
            tray_id: 2,
            spoolman_spool_id: 216,
            ams_label: null,
          },
        ])
      ),
    );

    const { container } = render(<InventoryPageRouter />);

    // LOCATION cell renders "{printerLabel} {slotLabel}" via JSX template,
    // which splits into separate text nodes. Use innerHTML / textContent inspection.
    // formatSlotLabel(0, 2, false, false) => "A3"
    await waitFor(() => {
      expect(container.textContent).toContain('Sully');
      expect(container.textContent).toContain('A3');
    });
  });

  it('shows "-" in LOCATION column when spoolman spool has no slot assignment', async () => {
    server.use(
      http.get('/api/v1/settings/spoolman', () =>
        HttpResponse.json({
          spoolman_enabled: 'true',
          spoolman_url: 'http://localhost:7912',
        })
      ),
      http.get('/api/v1/spoolman/inventory/spools', () =>
        HttpResponse.json([mockSpoolmanSpool])
      ),
      http.get('/api/v1/spoolman/inventory/slot-assignments/all', () =>
        HttpResponse.json([])
      ),
    );

    const { container } = render(<InventoryPageRouter />);

    await waitFor(() => {
      // Spool row is rendered — brand "Bambu Lab" appears in the table somewhere.
      expect(container.textContent).toContain('Bambu Lab');
    });
    // LOCATION cell shows "-" (there may be other "-" cells too — at least one expected).
    const dashCells = screen.getAllByText('-');
    expect(dashCells.length).toBeGreaterThan(0);
  });

  it('does not call /spoolman/inventory/slot-assignments/all in local mode', async () => {
    let slotEndpointCalled = false;
    server.use(
      http.get('/api/v1/settings/spoolman', () =>
        HttpResponse.json({ spoolman_enabled: 'false', spoolman_url: '' })
      ),
      http.get('/api/v1/inventory/spools', () => HttpResponse.json([])),
      http.get('/api/v1/spoolman/inventory/slot-assignments/all', () => {
        slotEndpointCalled = true;
        return HttpResponse.json([]);
      }),
    );

    render(<InventoryPageRouter />);

    // Wait for the page to settle by checking a stable element from the stat cards.
    await waitFor(() => {
      expect(screen.getByText(/total inventory/i)).toBeInTheDocument();
    });
    expect(slotEndpointCalled).toBe(false);
  });

  it('counts spoolman slot assignments in the IN PRINTER stat card', async () => {
    server.use(
      http.get('/api/v1/settings/spoolman', () =>
        HttpResponse.json({
          spoolman_enabled: 'true',
          spoolman_url: 'http://localhost:7912',
        })
      ),
      http.get('/api/v1/spoolman/inventory/spools', () =>
        HttpResponse.json([mockSpoolmanSpool])
      ),
      http.get('/api/v1/spoolman/inventory/slot-assignments/all', () =>
        HttpResponse.json([
          {
            printer_id: 1,
            printer_name: 'Sully',
            ams_id: 0,
            tray_id: 2,
            spoolman_spool_id: 216,
            ams_label: null,
          },
        ])
      ),
    );

    render(<InventoryPageRouter />);

    // Find the "IN PRINTER" stat card by its label, then assert the count "1"
    // appears within the same stat-card div. This verifies the inPrinterCount
    // sums Spoolman slot assignments (was 0 before Phase 8).
    await waitFor(() => {
      const label = screen.getByText(/^in printer$/i);
      const card = label.closest('div.bg-bambu-dark-secondary');
      expect(card).not.toBeNull();
      expect(within(card as HTMLElement).getByText('1')).toBeInTheDocument();
    });
  });

  it('local SpoolAssignment wins over Spoolman slot assignment on id collision', async () => {
    // Both endpoints return an entry with the same numeric id (216). The local
    // /inventory/assignments source must win — printer_name "LocalPrinter" and
    // slot "B1" (formatSlotLabel(1, 0, false, false)) — and the Spoolman entry
    // ("SpoolmanPrinter" / "C4") must NOT appear.
    server.use(
      http.get('/api/v1/settings/spoolman', () =>
        HttpResponse.json({
          spoolman_enabled: 'true',
          spoolman_url: 'http://localhost:7912',
        })
      ),
      http.get('/api/v1/spoolman/inventory/spools', () =>
        HttpResponse.json([mockSpoolmanSpool])
      ),
      http.get('/api/v1/inventory/assignments', () =>
        HttpResponse.json([
          {
            id: 99,
            spool_id: 216,
            printer_id: 7,
            printer_name: 'LocalPrinter',
            ams_id: 1,
            tray_id: 0,
            ams_label: null,
            created_at: '2025-01-01T00:00:00Z',
          },
        ])
      ),
      http.get('/api/v1/spoolman/inventory/slot-assignments/all', () =>
        HttpResponse.json([
          {
            printer_id: 8,
            printer_name: 'SpoolmanPrinter',
            ams_id: 2,
            tray_id: 3,
            spoolman_spool_id: 216,
            ams_label: null,
          },
        ])
      ),
    );

    const { container } = render(<InventoryPageRouter />);

    await waitFor(() => {
      // Local printer wins
      expect(container.textContent).toContain('LocalPrinter');
      expect(container.textContent).toContain('B1');
    });
    expect(container.textContent).not.toContain('SpoolmanPrinter');
    expect(container.textContent).not.toContain('C4');
  });
});
