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

describe('SpoolCatalogSettings — SpoolmanFilamentCatalogSection', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getSpoolCatalog).mockResolvedValue([]);
  });

  it('hides the Spoolman catalog section when Spoolman is disabled (400)', async () => {
    vi.mocked(api.getSpoolmanInventoryFilaments).mockRejectedValue(
      new ApiError('disabled', 400)
    );

    render(<SpoolCatalogSettings />);

    // The section title must not appear — 400 means Spoolman is disabled
    await waitFor(() => {
      expect(screen.queryByText('settings.spoolmanFilamentCatalogTitle')).toBeNull();
    });
  });

  it('shows error message when Spoolman is unreachable (503)', async () => {
    vi.mocked(api.getSpoolmanInventoryFilaments).mockRejectedValue(
      new ApiError('unreachable', 503)
    );

    render(<SpoolCatalogSettings />);

    await waitFor(() => {
      expect(screen.getByText('inventory.spoolmanCatalogLoadFailed')).toBeTruthy();
    });
  });

  it('shows empty state when filament list is empty', async () => {
    vi.mocked(api.getSpoolmanInventoryFilaments).mockResolvedValue([]);

    render(<SpoolCatalogSettings />);

    await waitFor(() => {
      expect(screen.getByText('inventory.noSpoolmanFilaments')).toBeTruthy();
    });
  });

  it('renders filament list when data is loaded', async () => {
    vi.mocked(api.getSpoolmanInventoryFilaments).mockResolvedValue([
      {
        id: 1,
        name: 'PLA Basic',
        material: 'PLA',
        color_hex: 'FF0000',
        color_name: 'Red',
        weight: 1000,
        spool_weight: 196,
        vendor: { id: 1, name: 'Bambu Lab' },
      },
    ]);

    render(<SpoolCatalogSettings />);

    await waitFor(() => {
      expect(screen.getByText(/Bambu Lab — PLA Basic/)).toBeTruthy();
    });
  });
});
