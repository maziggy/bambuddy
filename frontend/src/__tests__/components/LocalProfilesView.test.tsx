/**
 * Tests for LocalProfilesView component.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { render } from '../utils';
import { LocalProfilesView } from '../../components/LocalProfilesView';

const mockLocalPresets = {
  filament: [
    {
      id: 1,
      name: 'Overture PLA Matte @BBL X1C',
      preset_type: 'filament',
      source: 'orcaslicer',
      filament_type: 'PLA',
      filament_vendor: 'Overture',
      nozzle_temp_min: 190,
      nozzle_temp_max: 230,
      pressure_advance: '["0.04"]',
      default_filament_colour: '["#FFAA00"]',
      filament_cost: '24.99',
      filament_density: '1.24',
      compatible_printers: '["Bambu Lab X1 Carbon 0.4 nozzle"]',
      inherits: 'Bambu PLA Basic @BBL X1C',
      version: '2.3.0.4',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    },
    {
      id: 2,
      name: 'eSUN PETG @Bambu Lab H2D',
      preset_type: 'filament',
      source: 'orcaslicer',
      filament_type: 'PETG',
      filament_vendor: null,
      nozzle_temp_min: 220,
      nozzle_temp_max: 250,
      pressure_advance: null,
      default_filament_colour: null,
      filament_cost: null,
      filament_density: null,
      compatible_printers: null,
      inherits: null,
      version: null,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    },
  ],
  process: [
    {
      id: 3,
      name: '0.20mm Standard @BBL X1C',
      preset_type: 'process',
      source: 'orcaslicer',
      filament_type: null,
      filament_vendor: null,
      nozzle_temp_min: null,
      nozzle_temp_max: null,
      pressure_advance: null,
      default_filament_colour: null,
      filament_cost: null,
      filament_density: null,
      compatible_printers: null,
      inherits: '0.20mm Standard @BBL X1C',
      version: '2.3.0.4',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    },
  ],
  printer: [],
};

describe('LocalProfilesView', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/local-presets/', () => {
        return HttpResponse.json(mockLocalPresets);
      }),
      http.delete('/api/v1/local-presets/:id', () => {
        return HttpResponse.json({ success: true });
      }),
    );
  });

  it('renders filament and process columns', async () => {
    render(<LocalProfilesView />);

    await waitFor(() => {
      expect(screen.getByText('Overture PLA Matte @BBL X1C')).toBeInTheDocument();
    });

    expect(screen.getByText('eSUN PETG @Bambu Lab H2D')).toBeInTheDocument();
    expect(screen.getByText('0.20mm Standard @BBL X1C')).toBeInTheDocument();
  });

  it('shows material badges from filament_type', async () => {
    render(<LocalProfilesView />);

    await waitFor(() => {
      expect(screen.getByText('Overture PLA Matte @BBL X1C')).toBeInTheDocument();
    });

    // PLA badge should appear for the first preset
    const plaBadges = screen.getAllByText('PLA');
    expect(plaBadges.length).toBeGreaterThan(0);
  });

  it('shows vendor from filament_vendor field', async () => {
    render(<LocalProfilesView />);

    await waitFor(() => {
      expect(screen.getByText('Overture')).toBeInTheDocument();
    });
  });

  it('parses vendor from name when filament_vendor is null', async () => {
    render(<LocalProfilesView />);

    await waitFor(() => {
      expect(screen.getByText('eSUN PETG @Bambu Lab H2D')).toBeInTheDocument();
    });

    // eSUN should be parsed from the name
    expect(screen.getByText('eSUN')).toBeInTheDocument();
  });

  it('filters presets by search query', async () => {
    render(<LocalProfilesView />);

    await waitFor(() => {
      expect(screen.getByText('Overture PLA Matte @BBL X1C')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText(/search/i);
    fireEvent.change(searchInput, { target: { value: 'PETG' } });

    expect(screen.queryByText('Overture PLA Matte @BBL X1C')).not.toBeInTheDocument();
    expect(screen.getByText('eSUN PETG @Bambu Lab H2D')).toBeInTheDocument();
  });

  it('keeps the search bar visible when no presets match the query (#1470)', async () => {
    render(<LocalProfilesView />);

    await waitFor(() => {
      expect(screen.getByText('Overture PLA Matte @BBL X1C')).toBeInTheDocument();
    });

    const searchInput = screen.getByPlaceholderText(/search/i);
    fireEvent.change(searchInput, { target: { value: 'zzz-nothing-matches' } });

    // The search bar must stay mounted so the query can be cleared/edited
    // without a page refresh, and it keeps the typed value.
    expect(screen.getByPlaceholderText(/search/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/search/i)).toHaveValue('zzz-nothing-matches');
    // A no-matches message replaces the columns (not the "import some" empty state).
    expect(screen.getByText(/no presets match your search/i)).toBeInTheDocument();
  });

  it('shows empty state when no presets', async () => {
    server.use(
      http.get('/api/v1/local-presets/', () => {
        return HttpResponse.json({ filament: [], process: [], printer: [] });
      }),
    );

    render(<LocalProfilesView />);

    await waitFor(() => {
      expect(screen.getByText(/no local presets/i)).toBeInTheDocument();
    });
  });

  it('shows Local badge on preset cards', async () => {
    render(<LocalProfilesView />);

    await waitFor(() => {
      expect(screen.getByText('Overture PLA Matte @BBL X1C')).toBeInTheDocument();
    });

    const badges = screen.getAllByText(/^Local$/i);
    expect(badges.length).toBeGreaterThan(0);
  });

  it('shows delete confirmation modal', async () => {
    render(<LocalProfilesView />);

    await waitFor(() => {
      expect(screen.getByText('Overture PLA Matte @BBL X1C')).toBeInTheDocument();
    });

    // Click first delete button
    const deleteButtons = screen.getAllByTitle(/delete/i);
    fireEvent.click(deleteButtons[0]);

    await waitFor(() => {
      expect(screen.getByText(/are you sure/i)).toBeInTheDocument();
    });
  });

  it('shows import zone', async () => {
    render(<LocalProfilesView />);

    await waitFor(() => {
      expect(screen.getByText(/import profiles/i)).toBeInTheDocument();
    });

    expect(screen.getByText(/\.bbscfg/i)).toBeInTheDocument();
  });
});
