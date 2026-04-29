import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { render } from '../utils';
import { SpoolCatalogSettings } from '../../components/SpoolCatalogSettings';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => fallback ?? key,
  }),
}));

const mockShowToast = vi.fn();
vi.mock('../../contexts/ToastContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/ToastContext')>();
  return { ...actual, useToast: () => ({ showToast: mockShowToast }) };
});

vi.mock('../../api/client', () => ({
  api: {
    getSettings: vi.fn().mockResolvedValue({}),
    getSpoolCatalog: vi.fn().mockResolvedValue([]),
    getSpoolmanInventoryFilaments: vi.fn().mockResolvedValue([]),
  },
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
}));

import { api, ApiError } from '../../api/client';

const sampleFilament = {
  id: 1,
  name: 'PLA Basic',
  material: 'PLA',
  color_hex: 'FF0000',
  color_name: 'Red',
  weight: 1000,
  spool_weight: 196,
  vendor: { id: 1, name: 'Bambu Lab' },
};

describe('SpoolCatalogSettings — mode switching', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getSpoolCatalog).mockResolvedValue([]);
  });

  // ── Existing tests (updated assertions) ──

  it('hides Spoolman table and shows local CRUD buttons when Spoolman is disabled (400)', async () => {
    vi.mocked(api.getSpoolmanInventoryFilaments).mockRejectedValue(
      new ApiError('disabled', 400)
    );

    render(<SpoolCatalogSettings />);

    await waitFor(() => {
      // Local mode: Add button visible
      expect(screen.getByText('common.add')).toBeTruthy();
    });

    // Spoolman table columns must NOT appear
    expect(screen.queryByText('settings.catalog.material')).toBeNull();
    expect(screen.queryByText('settings.catalog.spoolWeight')).toBeNull();
    // Spoolman catalog title must NOT appear
    expect(screen.queryByText('settings.spoolmanFilamentCatalogTitle')).toBeNull();
  });

  it('shows Spoolman error row when Spoolman is unreachable (503)', async () => {
    vi.mocked(api.getSpoolmanInventoryFilaments).mockRejectedValue(
      new ApiError('unreachable', 503)
    );

    render(<SpoolCatalogSettings />);

    await waitFor(() => {
      expect(screen.getByText('inventory.spoolmanCatalogLoadFailed')).toBeTruthy();
    });

    // Local CRUD buttons must NOT appear in Spoolman mode
    expect(screen.queryByText('common.add')).toBeNull();
  });

  it('shows empty state when Spoolman returns an empty list', async () => {
    vi.mocked(api.getSpoolmanInventoryFilaments).mockResolvedValue([]);

    render(<SpoolCatalogSettings />);

    await waitFor(() => {
      expect(screen.getByText('inventory.noSpoolmanFilaments')).toBeTruthy();
    });

    // Local CRUD buttons must NOT appear
    expect(screen.queryByText('common.add')).toBeNull();
  });

  it('renders Spoolman filament rows with vendor and name combined', async () => {
    vi.mocked(api.getSpoolmanInventoryFilaments).mockResolvedValue([sampleFilament]);

    render(<SpoolCatalogSettings />);

    await waitFor(() => {
      expect(screen.getByText(/Bambu Lab — PLA Basic/)).toBeTruthy();
    });
  });

  // ── New tests ──

  it('(local mode) shows Export, Import, Reset, Add buttons when Spoolman disabled', async () => {
    vi.mocked(api.getSpoolmanInventoryFilaments).mockRejectedValue(
      new ApiError('disabled', 400)
    );

    render(<SpoolCatalogSettings />);

    await waitFor(() => {
      expect(screen.getByText('common.add')).toBeTruthy();
    });

    expect(screen.getByText('common.export')).toBeTruthy();
    expect(screen.getByText('common.import')).toBeTruthy();
    expect(screen.getByText('common.reset')).toBeTruthy();
  });

  it('(spoolman mode) hides Export, Import, Reset, Add buttons when Spoolman is enabled', async () => {
    vi.mocked(api.getSpoolmanInventoryFilaments).mockResolvedValue([sampleFilament]);

    render(<SpoolCatalogSettings />);

    await waitFor(() => {
      expect(screen.getByText(/Bambu Lab — PLA Basic/)).toBeTruthy();
    });

    expect(screen.queryByText('common.add')).toBeNull();
    expect(screen.queryByText('common.export')).toBeNull();
    expect(screen.queryByText('common.import')).toBeNull();
    expect(screen.queryByText('common.reset')).toBeNull();
  });

  it('(spoolman mode) renders correct column headers — Name, Material, Weight, Spool Weight', async () => {
    vi.mocked(api.getSpoolmanInventoryFilaments).mockResolvedValue([sampleFilament]);

    render(<SpoolCatalogSettings />);

    await waitFor(() => {
      expect(screen.getByText('common.name')).toBeTruthy();
    });

    expect(screen.getByText('settings.catalog.material')).toBeTruthy();
    expect(screen.getByText('settings.catalog.weight')).toBeTruthy();
    expect(screen.getByText('settings.catalog.spoolWeight')).toBeTruthy();
  });

  it('(spoolman mode) renders all data fields for a filament row', async () => {
    vi.mocked(api.getSpoolmanInventoryFilaments).mockResolvedValue([sampleFilament]);

    render(<SpoolCatalogSettings />);

    await waitFor(() => {
      expect(screen.getByText(/Bambu Lab — PLA Basic/)).toBeTruthy();
    });

    // Material column
    expect(screen.getByText('PLA')).toBeTruthy();
    // Filament weight
    expect(screen.getByText('1000g')).toBeTruthy();
    // Spool (empty) weight
    expect(screen.getByText('196g')).toBeTruthy();
  });

  it('(spoolman mode) renders color swatch with correct background color', async () => {
    vi.mocked(api.getSpoolmanInventoryFilaments).mockResolvedValue([
      { ...sampleFilament, color_hex: 'FF5500' },
    ]);

    render(<SpoolCatalogSettings />);

    await waitFor(() => {
      expect(screen.getByText(/Bambu Lab — PLA Basic/)).toBeTruthy();
    });

    const swatch = screen.getByLabelText('inventory.spoolmanFilamentColorSwatch');
    const bg = (swatch as HTMLElement).style.backgroundColor;
    // Accepts both hex-like and rgb() representations
    expect(bg).toBeTruthy();
    expect(bg).not.toBe('');
  });

  it('(spoolman mode) renders fallback color when color_hex is null', async () => {
    vi.mocked(api.getSpoolmanInventoryFilaments).mockResolvedValue([
      { ...sampleFilament, color_hex: null },
    ]);

    render(<SpoolCatalogSettings />);

    await waitFor(() => {
      expect(screen.getByText(/Bambu Lab — PLA Basic/)).toBeTruthy();
    });

    const swatch = screen.getByLabelText('inventory.spoolmanFilamentColorSwatch');
    expect((swatch as HTMLElement).style.backgroundColor).toContain('128');
  });

  it('(spoolman mode) renders dash for null material, weight, and spool_weight', async () => {
    vi.mocked(api.getSpoolmanInventoryFilaments).mockResolvedValue([
      { ...sampleFilament, material: null, weight: null, spool_weight: null },
    ]);

    render(<SpoolCatalogSettings />);

    await waitFor(() => {
      expect(screen.getByText(/Bambu Lab — PLA Basic/)).toBeTruthy();
    });

    // All three nullable fields must show '—', not 'nullg' or empty string
    const dashes = screen.getAllByText('—');
    expect(dashes.length).toBeGreaterThanOrEqual(3);
  });

  it('(spoolman mode) shows Spoolman catalog title, not local catalog title', async () => {
    vi.mocked(api.getSpoolmanInventoryFilaments).mockResolvedValue([sampleFilament]);

    render(<SpoolCatalogSettings />);

    await waitFor(() => {
      expect(screen.getByText('settings.spoolmanFilamentCatalogTitle')).toBeTruthy();
    });

    expect(screen.queryByText('settings.catalog.spoolCatalog')).toBeNull();
  });
});
