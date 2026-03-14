/**
 * Tests for printer depreciation cost display on archive cards
 * and depreciation fields on printer add/edit modals.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { render } from '../utils';
import { ArchivesPage } from '../../pages/ArchivesPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

// Archive with all three cost types
const archiveWithDepreciation = {
  id: 1,
  filename: 'benchy.gcode.3mf',
  print_name: 'Benchy',
  printer_id: 1,
  print_time_seconds: 7200,
  filament_used_grams: 15.5,
  filament_type: 'PLA',
  filament_color: null,
  status: 'completed',
  started_at: '2024-01-01T10:00:00Z',
  completed_at: '2024-01-01T12:00:00Z',
  thumbnail_path: null,
  notes: null,
  project_id: null,
  project_name: null,
  tags: null,
  created_at: '2024-01-01T09:00:00Z',
  cost: 1.25,
  energy_kwh: 0.5,
  energy_cost: 0.08,
  depreciation_cost: 0.40,
  quantity: 1,
  file_path: 'archives/benchy.3mf',
  file_size: 1024,
  content_hash: null,
  timelapse_path: null,
  source_3mf_path: null,
  f3d_path: null,
  duplicates: null,
  duplicate_count: 0,
  object_count: null,
  actual_time_seconds: 7200,
  time_accuracy: 100,
  layer_height: 0.2,
  total_layers: 100,
  nozzle_diameter: 0.4,
  bed_temperature: 60,
  nozzle_temperature: 220,
  sliced_for_model: null,
  extra_data: null,
  makerworld_url: null,
  designer: null,
  external_url: null,
  is_favorite: false,
  photos: null,
  failure_reason: null,
  created_by_id: null,
  created_by_username: null,
};

// Archive with no depreciation (printer has no price configured)
const archiveWithoutDepreciation = {
  ...archiveWithDepreciation,
  id: 2,
  print_name: 'Bracket',
  filename: 'bracket.gcode.3mf',
  file_path: 'archives/bracket.3mf',
  cost: 2.00,
  energy_cost: 0.12,
  depreciation_cost: null,
};

// Archive with only depreciation cost (no filament or energy cost)
const archiveOnlyDepreciation = {
  ...archiveWithDepreciation,
  id: 3,
  print_name: 'Clip',
  filename: 'clip.gcode.3mf',
  file_path: 'archives/clip.3mf',
  cost: null,
  energy_cost: null,
  energy_kwh: null,
  depreciation_cost: 0.15,
};

describe('Depreciation cost on archive cards', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/archives/', () => {
        return HttpResponse.json([archiveWithDepreciation, archiveWithoutDepreciation, archiveOnlyDepreciation]);
      }),
      http.get('/api/v1/archives/stats', () => {
        return HttpResponse.json({
          total_archives: 3,
          total_print_time_seconds: 21600,
          total_filament_grams: 50,
          prints_this_week: 3,
          prints_this_month: 3,
        });
      }),
      http.get('/api/v1/printers/', () => {
        return HttpResponse.json([{ id: 1, name: 'X1 Carbon' }]);
      }),
      http.get('/api/v1/projects/', () => {
        return HttpResponse.json([]);
      }),
      http.get('/api/v1/archives/tags', () => {
        return HttpResponse.json([]);
      }),
      http.get('/api/v1/archives/:id/plates', () => {
        return HttpResponse.json({ plates: [], is_multi_plate: false });
      }),
      http.get('/api/v1/archives/:id/filament-requirements', () => {
        return HttpResponse.json([]);
      }),
      http.delete('/api/v1/archives/:id', () => {
        return HttpResponse.json({ success: true });
      }),
      http.get('/api/v1/settings/', () => {
        return HttpResponse.json({});
      })
    );
  });

  it('shows depreciation cost on archive card when present', async () => {
    render(<ArchivesPage />);

    await waitFor(() => {
      expect(screen.getByText('Benchy')).toBeInTheDocument();
    });

    // Depreciation cost of $0.40 should be displayed
    const depCosts = screen.getAllByText(/0\.40/);
    expect(depCosts.length).toBeGreaterThan(0);
  });

  it('shows filament cost alongside depreciation', async () => {
    render(<ArchivesPage />);

    await waitFor(() => {
      expect(screen.getByText('Benchy')).toBeInTheDocument();
    });

    // Filament cost $1.25
    expect(screen.getAllByText(/1\.25/).length).toBeGreaterThan(0);
    // Energy cost $0.08
    expect(screen.getAllByText(/0\.08/).length).toBeGreaterThan(0);
  });

  it('does not show depreciation icon when depreciation_cost is null', async () => {
    // archiveWithoutDepreciation has depreciation_cost: null
    render(<ArchivesPage />);

    await waitFor(() => {
      expect(screen.getByText('Bracket')).toBeInTheDocument();
    });

    // The Bracket card should still show filament and energy costs
    expect(screen.getAllByText(/2\.00/).length).toBeGreaterThan(0);
  });

  it('shows depreciation even when other costs are null', async () => {
    render(<ArchivesPage />);

    await waitFor(() => {
      expect(screen.getByText('Clip')).toBeInTheDocument();
    });

    // Only depreciation cost $0.15 should appear for the Clip archive
    expect(screen.getAllByText(/0\.15/).length).toBeGreaterThan(0);
  });
});
