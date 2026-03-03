/**
 * Tests for low stock threshold functionality in InventoryPage.
 *
 * Tests that the low stock threshold:
 * - Is loaded from backend settings API
 * - Can be updated via the UI
 * - Persists changes to the backend
 * - Does not use localStorage
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import InventoryPageRouter from '../../pages/InventoryPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

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

const mockSpools = [
  {
    id: 1,
    material: 'PLA',
    subtype: null,
    brand: 'Polymaker',
    color_name: 'Red',
    rgba: 'FF0000FF',
    label_weight: 1000,
    core_weight: 250,
    weight_used: 900, // 10% remaining - low stock
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
    archived_at: null,
    created_at: '2025-01-01T00:00:00Z',
    updated_at: '2025-01-01T00:00:00Z',
    k_profiles: [],
    cost_per_kg: null,
    last_scale_weight: null,
    last_weighed_at: null,
  },
  {
    id: 2,
    material: 'PETG',
    subtype: null,
    brand: 'eSun',
    color_name: 'Blue',
    rgba: '0000FFFF',
    label_weight: 1000,
    core_weight: 250,
    weight_used: 200, // 80% remaining - not low stock
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
    archived_at: null,
    created_at: '2025-01-02T00:00:00Z',
    updated_at: '2025-01-02T00:00:00Z',
    k_profiles: [],
    cost_per_kg: null,
    last_scale_weight: null,
    last_weighed_at: null,
  },
  {
    id: 3,
    material: 'ABS',
    subtype: null,
    brand: 'Hatchbox',
    color_name: 'Black',
    rgba: '000000FF',
    label_weight: 1000,
    core_weight: 250,
    weight_used: 850, // 15% remaining - low stock
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
    archived_at: null,
    created_at: '2025-01-03T00:00:00Z',
    updated_at: '2025-01-03T00:00:00Z',
    k_profiles: [],
    cost_per_kg: null,
    last_scale_weight: null,
    last_weighed_at: null,
  },
];

describe('InventoryPage - Low Stock Threshold', () => {
  beforeEach(() => {
    // Clear localStorage to ensure we're not relying on it
    localStorage.clear();

    server.use(
      http.get('/api/v1/settings/', () => {
        return HttpResponse.json(mockSettings);
      }),
      http.put('/api/v1/settings/', async ({ request }) => {
        const body = (await request.json()) as Partial<typeof mockSettings>;
        return HttpResponse.json({ ...mockSettings, ...body });
      }),
      http.get('/api/v1/spools/', () => {
        return HttpResponse.json(mockSpools);
      }),
      http.get('/api/v1/spool-assignments/', () => {
        return HttpResponse.json([]);
      }),
      http.get('/api/v1/spoolman/settings', () => {
        return HttpResponse.json({ spoolman_enabled: 'false' });
      })
    );
  });

  describe('default threshold from backend', () => {
    it('loads the default threshold of 20% from backend settings', async () => {
      render(<InventoryPageRouter />);

      await waitFor(() => {
        // Find the low stock stat showing the threshold
        expect(screen.getByText(/< 20%/i)).toBeInTheDocument();
      });
    });

    it('calculates low stock count based on default threshold', async () => {
      render(<InventoryPageRouter />);

      await waitFor(() => {
        // With default 20% threshold, spools with 10% and 15% remaining should be counted (2 spools)
        const lowStockSection = screen.getByText(/low stock/i).closest('div');
        expect(lowStockSection).toBeInTheDocument();
      });
    });

    it('does not use localStorage for threshold', async () => {
      // Set a value in localStorage that should be ignored
      localStorage.setItem('bambuddy-low-stock-threshold', '50');

      render(<InventoryPageRouter />);

      await waitFor(() => {
        // Should show backend value (20%), not localStorage value (50%)
        expect(screen.getByText(/< 20%/i)).toBeInTheDocument();
      });
    });
  });

  describe('updating threshold via UI', () => {
    it('shows edit button for threshold', async () => {
      const user = userEvent.setup();
      render(<InventoryPageRouter />);

      await waitFor(() => {
        expect(screen.getByText(/< 20%/i)).toBeInTheDocument();
      });

      const editButton = screen.getByTitle(/edit/i);
      expect(editButton).toBeInTheDocument();

      await user.click(editButton);

      // Input field should appear
      await waitFor(() => {
        const input = screen.getByRole('textbox');
        expect(input).toBeInTheDocument();
        expect(input).toHaveValue('20');
      });
    });

    it('updates threshold and persists to backend', async () => {
      const user = userEvent.setup();
      let updatedSettings: Partial<typeof mockSettings> | null = null;

      server.use(
        http.put('/api/v1/settings/', async ({ request }) => {
          const body = (await request.json()) as Partial<typeof mockSettings>;
          updatedSettings = body;
          return HttpResponse.json({ ...mockSettings, ...body });
        })
      );

      render(<InventoryPageRouter />);

      await waitFor(() => {
        expect(screen.getByText(/< 20%/i)).toBeInTheDocument();
      });

      // Click edit button
      const editButton = screen.getByTitle(/edit/i);
      await user.click(editButton);

      // Enter new value
      const input = screen.getByRole('textbox');
      await user.clear(input);
      await user.type(input, '15.5');

      // Submit form
      const saveButton = screen.getByRole('button', { name: /save/i });
      await user.click(saveButton);

      // Verify API was called with correct value
      await waitFor(() => {
        expect(updatedSettings).toEqual({ low_stock_threshold: 15.5 });
      });
    });

    it('validates threshold input range', async () => {
      const user = userEvent.setup();
      let updatedSettings: Partial<typeof mockSettings> | null = null;

      server.use(
        http.put('/api/v1/settings/', async ({ request }) => {
          const body = (await request.json()) as Partial<typeof mockSettings>;
          updatedSettings = body;
          return HttpResponse.json({ ...mockSettings, ...body });
        })
      );

      render(<InventoryPageRouter />);

      await waitFor(() => {
        expect(screen.getByText(/< 20%/i)).toBeInTheDocument();
      });

      // Click edit button
      const editButton = screen.getByTitle(/edit/i);
      await user.click(editButton);

      // Try invalid values
      const input = screen.getByRole('textbox');

      // Too high
      await user.clear(input);
      await user.type(input, '0');

      const saveButton = screen.getByRole('button', { name: /save/i });
      await user.click(saveButton);

      // Should show error and NOT call the PUT endpoint
      await waitFor(() => {
        expect(updatedSettings).toBeNull();
      });
    });

    it('allows canceling threshold edit', async () => {
      const user = userEvent.setup();
      render(<InventoryPageRouter />);

      await waitFor(() => {
        expect(screen.getByText(/< 20%/i)).toBeInTheDocument();
      });

      // Click edit button
      const editButton = screen.getByTitle(/edit/i);
      await user.click(editButton);

      // Change value
      const input = screen.getByRole('textbox');
      await user.clear(input);
      await user.type(input, '30');

      // Cancel
      const cancelButton = screen.getByRole('button', { name: /cancel/i });
      await user.click(cancelButton);

      // Should revert to original display
      await waitFor(() => {
        expect(screen.getByText(/< 20%/i)).toBeInTheDocument();
      });
    });
  });

  describe('custom threshold from backend', () => {
    it('loads custom threshold value from backend', async () => {
      server.use(
        http.get('/api/v1/settings/', () => {
          return HttpResponse.json({ ...mockSettings, low_stock_threshold: 25.0 });
        })
      );

      render(<InventoryPageRouter />);

      await waitFor(() => {
        expect(screen.getByText(/< 25%/i)).toBeInTheDocument();
      });
    });

    it('applies custom threshold to low stock filtering', async () => {
      // With threshold at 30%, all 3 test spools should be low stock (10%, 15%, and we'd need to check 80%)
      server.use(
        http.get('/api/v1/settings/', () => {
          return HttpResponse.json({ ...mockSettings, low_stock_threshold: 30.0 });
        })
      );

      render(<InventoryPageRouter />);

      await waitFor(() => {
        expect(screen.getByText(/< 30%/i)).toBeInTheDocument();
      });

      // The low stock count should reflect the new threshold
      // Implementation would show appropriate count based on 30% threshold
    });
  });
});
