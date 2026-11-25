import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { render } from '../utils';
import { ProfileDefaultsSettings } from '../../components/ProfileDefaultsSettings';
import type { ProfileDefault } from '../../api/client';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) => (opts && 'count' in opts ? `${key}:${opts.count}` : key),
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
    getUiSettings: vi.fn().mockResolvedValue({}),
    getProfileDefaults: vi.fn(),
    getPrinters: vi.fn().mockResolvedValue([]),
    getSpoolmanSettings: vi.fn().mockResolvedValue({ spoolman_enabled: 'false', spoolman_url: '' }),
    applyProfileDefault: vi.fn(),
    deleteProfileDefault: vi.fn(),
  },
}));

import { api } from '../../api/client';

function makeDefault(id: number, brand: string, material: string, colour: string, preset: string): ProfileDefault {
  return {
    id,
    brand,
    material,
    subtype: '',
    color_name: colour,
    slicer_filament: `GF${id}`,
    slicer_filament_name: preset,
    presets: [{ printer_model: 'X1C', nozzle_diameter: '0.4', slicer_filament: `GF${id}`, slicer_filament_name: preset }],
    k_profiles: [],
    updated_at: '2026-11-08T00:00:00Z',
  } as unknown as ProfileDefault;
}

const DEFAULTS = [
  makeDefault(1, 'Polymaker', 'PETG', '', 'Polymaker PETG'),
  makeDefault(2, 'Bambu', 'PLA', 'Red', 'Bambu PLA Basic'),
  makeDefault(3, 'Bambu', 'PLA', '', 'Bambu PLA Basic'),
];

describe('ProfileDefaultsSettings', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getProfileDefaults).mockResolvedValue(DEFAULTS);
  });

  it('lists one sorted line per default with its details folded away', async () => {
    render(<ProfileDefaultsSettings />);

    const rows = await screen.findAllByRole('button', { name: 'common.expand' });
    // Sorted by product.
    expect(rows.map((r) => r.querySelector('span.text-white')?.textContent)).toEqual([
      'Bambu PLA',
      'Bambu PLA',
      'Polymaker PETG',
    ]);
    expect(rows.some((r) => r.textContent?.includes('Red'))).toBe(true);
    // The per-model presets show only once a row is opened.
    expect(screen.queryByText(/^X1C 0.4 mm/)).toBeNull();

    fireEvent.click(rows[2]);

    expect(screen.getByText('X1C 0.4 mm: Polymaker PETG')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'common.collapse' })).toBeTruthy();
  });

  it('filters by product, colour or preset', async () => {
    render(<ProfileDefaultsSettings />);
    await screen.findAllByRole('button', { name: 'common.expand' });

    fireEvent.change(screen.getByPlaceholderText('inventory.profileDefaults.search'), { target: { value: 'red' } });
    expect(screen.getAllByRole('button', { name: 'common.expand' })).toHaveLength(1);

    fireEvent.change(screen.getByPlaceholderText('inventory.profileDefaults.search'), { target: { value: 'nothing' } });
    expect(screen.queryAllByRole('button', { name: 'common.expand' })).toHaveLength(0);
    expect(screen.getByText('inventory.profileDefaults.noMatch')).toBeTruthy();
  });

  it('apply all applies every default and reports the spools filled in', async () => {
    vi.mocked(api.applyProfileDefault).mockResolvedValue({ updated: 2 });
    render(<ProfileDefaultsSettings />);
    await screen.findAllByRole('button', { name: 'common.expand' });
    // Also with a search active: it applies all of them, not only those shown.
    fireEvent.change(screen.getByPlaceholderText('inventory.profileDefaults.search'), { target: { value: 'red' } });

    const button = screen.getByRole('button', { name: /inventory.profileDefaults.applyAll$/ });
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(button);

    await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith('inventory.profileDefaults.applied:6', 'success'));
    expect(vi.mocked(api.applyProfileDefault).mock.calls.map((c) => c[0]).sort()).toEqual([1, 2, 3]);
  });

  it('apply all goes on after a failure and says how many failed', async () => {
    vi.mocked(api.applyProfileDefault)
      .mockResolvedValueOnce({ updated: 1 })
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValueOnce({ updated: 1 });
    render(<ProfileDefaultsSettings />);
    const button = await screen.findByRole('button', { name: /inventory.profileDefaults.applyAll$/ });
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));

    fireEvent.click(button);

    await waitFor(() =>
      expect(mockShowToast).toHaveBeenCalledWith('inventory.profileDefaults.applyAllFailed', 'error'),
    );
    expect(api.applyProfileDefault).toHaveBeenCalledTimes(3);
  });
});
