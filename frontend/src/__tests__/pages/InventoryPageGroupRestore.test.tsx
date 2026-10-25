/**
 * Restore on an archived spool inside an expanded group row of the table.
 * The group row passed a no-op to its members, so the button did nothing.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import InventoryPageRouter from '../../pages/InventoryPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const baseSettings = {
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
  library_disk_warning_gb: 5.0,
  camera_view_mode: 'window',
  preferred_slicer: 'bambu_studio',
  prometheus_enabled: false,
  prometheus_token: '',
  low_stock_threshold: 20.0,
};

const archivedSpool = {
    // Archived spool: 500 g consumed. Pre-fix this consumption disappeared
    // from "Total Consumed" the moment the spool was archived (#1390 fb).
    id: 2,
    material: 'PETG',
    subtype: null,
    brand: 'eSun',
    color_name: 'Blue',
    rgba: '0000FFFF',
    label_weight: 1000,
    core_weight: 250,
    weight_used: 500,
    weight_used_baseline: 0,
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
    data_origin: null,
    tag_type: null,
    archived_at: '2026-04-01T00:00:00Z',
    created_at: '2025-01-02T00:00:00Z',
    updated_at: '2025-01-02T00:00:00Z',
    k_profiles: [],
    cost_per_kg: null,
    last_scale_weight: null,
    last_weighed_at: null,
  };

describe('InventoryPage — restore inside a group row', () => {
  const restored: number[] = [];

  beforeEach(() => {
    restored.length = 0;
    server.use(
      http.get('/api/v1/settings/', () => HttpResponse.json(baseSettings)),
      http.get('/api/v1/inventory/spools', () =>
        // Only unused, unassigned spools are grouped.
        HttpResponse.json([
          { ...archivedSpool, weight_used: 0 },
          { ...archivedSpool, id: 3, weight_used: 0 },
        ]),
      ),
      http.get('/api/v1/inventory/assignments', () => HttpResponse.json([])),
      http.get('/api/v1/spoolman/settings', () => HttpResponse.json({ spoolman_enabled: 'false' })),
      http.post('/api/v1/inventory/spools/:id/restore', ({ params }) => {
        restored.push(Number(params.id));
        return HttpResponse.json({ ...archivedSpool, id: Number(params.id), archived_at: null });
      }),
    );
  });

  afterEach(() => {
    localStorage.removeItem('bambuddy-inventory-group');
  });

  it('restores the spool whose button was clicked', async () => {
    render(<InventoryPageRouter />);

    fireEvent.click(await screen.findByRole('button', { name: 'Archived' }));
    fireEvent.click(screen.getByRole('button', { name: 'Group' }));
    const header = await screen.findByText('#2, #3');
    fireEvent.click(header.closest('tr')!);

    const buttons = await screen.findAllByTitle('Restore');
    fireEvent.click(buttons[0]);

    await waitFor(() => expect(restored).toHaveLength(1));
    expect([2, 3]).toContain(restored[0]);
  });
});
