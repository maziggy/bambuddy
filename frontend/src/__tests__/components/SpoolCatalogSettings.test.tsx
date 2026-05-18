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
  },
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
}));

import { api } from '../../api/client';

describe('SpoolCatalogSettings — local catalog UI', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getSpoolCatalog).mockResolvedValue([]);
  });

  it('shows local CRUD buttons regardless of Spoolman state', async () => {
    render(<SpoolCatalogSettings />);

    await waitFor(() => {
      expect(screen.getByText('common.add')).toBeTruthy();
    });

    expect(screen.getByText('common.export')).toBeTruthy();
    expect(screen.getByText('common.import')).toBeTruthy();
    expect(screen.getByText('common.reset')).toBeTruthy();
  });

  it('renders the local Spool Catalog header and column layout', async () => {
    render(<SpoolCatalogSettings />);

    await waitFor(() => {
      expect(screen.getByText('settings.catalog.spoolCatalog')).toBeTruthy();
    });

    expect(screen.getByText('common.name')).toBeTruthy();
    expect(screen.getByText('settings.catalog.weight')).toBeTruthy();
    expect(screen.getByText('settings.catalog.type')).toBeTruthy();

    // No Spoolman-only columns leak in
    expect(screen.queryByText('settings.catalog.material')).toBeNull();
    expect(screen.queryByText('settings.catalog.spoolWeight')).toBeNull();
    expect(screen.queryByText('settings.spoolmanFilamentCatalogTitle')).toBeNull();
  });
});
