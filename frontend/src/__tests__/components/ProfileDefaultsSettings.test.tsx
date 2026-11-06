/**
 * Settings > Filament > Profile defaults (#1326): lists the defaults, applies
 * one to the inventory in use, deletes one after asking.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { ProfileDefaultsSettings } from '../../components/ProfileDefaultsSettings';
import { api } from '../../api/client';

vi.mock('../../api/client', () => ({
  api: {
    getSettings: vi.fn().mockResolvedValue({}),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
    getProfileDefaults: vi.fn(),
    getPrinters: vi.fn().mockResolvedValue([{ id: 3, name: 'H2D Left Bench' }]),
    getSpoolmanSettings: vi.fn(),
    applyProfileDefault: vi.fn().mockResolvedValue({ updated: 4 }),
    deleteProfileDefault: vi.fn().mockResolvedValue({ status: 'deleted' }),
  },
}));

const mockShowToast = vi.fn();
vi.mock('../../contexts/ToastContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/ToastContext')>();
  return { ...actual, useToast: () => ({ showToast: mockShowToast }) };
});

const mApi = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const row = {
  id: 7,
  brand: 'Bambu Lab',
  material: 'PLA',
  subtype: 'Basic',
  color_name: '',
  slicer_filament: 'PFUS01',
  slicer_filament_name: 'My PLA',
  presets: [],
  k_profiles: [{ printer_id: 3, extruder: 0, nozzle_diameter: '0.4', nozzle_type: null, k_value: 0.02, name: 'PLA HF', cali_idx: 5, setting_id: null }],
  updated_at: '2026-10-25T10:00:00Z',
};

describe('ProfileDefaultsSettings', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mApi.getProfileDefaults.mockResolvedValue([row]);
    mApi.getSpoolmanSettings.mockResolvedValue({ spoolman_enabled: 'false', spoolman_url: '' });
  });

  it('lists a default with its preset and K profiles', async () => {
    render(<ProfileDefaultsSettings />);
    expect(await screen.findByText('Bambu Lab PLA Basic')).toBeInTheDocument();
    expect(screen.getByText('All colours')).toBeInTheDocument();
    expect(await screen.findByText('H2D Left Bench 0.4 mm: PLA HF K=0.020')).toBeInTheDocument();
  });

  it.each([
    ['internal', 'false', false],
    ['Spoolman', 'true', true],
  ])('applies to the %s inventory', async (_label, enabled, spoolmanMode) => {
    mApi.getSpoolmanSettings.mockResolvedValue({ spoolman_enabled: enabled, spoolman_url: 'http://spoolman.test' });
    render(<ProfileDefaultsSettings />);
    await screen.findByText('Bambu Lab PLA Basic');
    // Enabled once the inventory mode is known: the route depends on it.
    const apply = screen.getByRole('button', { name: /Apply to existing spools/ });
    await waitFor(() => expect(apply).not.toBeDisabled());
    fireEvent.click(apply);
    await waitFor(() => expect(mApi.applyProfileDefault).toHaveBeenCalledWith(7, spoolmanMode));
    await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith('Filled in on 4 spools', 'success'));
  });

  it('cannot apply before the inventory mode is known', async () => {
    mApi.getSpoolmanSettings.mockReturnValue(new Promise(() => {}));
    render(<ProfileDefaultsSettings />);
    await screen.findByText('Bambu Lab PLA Basic');
    expect(screen.getByRole('button', { name: /Apply to existing spools/ })).toBeDisabled();
  });

  it('deletes only after confirming', async () => {
    render(<ProfileDefaultsSettings />);
    await screen.findByText('Bambu Lab PLA Basic');
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(mApi.deleteProfileDefault).not.toHaveBeenCalled();
    const confirm = await screen.findAllByRole('button', { name: 'Delete' });
    fireEvent.click(confirm[confirm.length - 1]);
    await waitFor(() => expect(mApi.deleteProfileDefault).toHaveBeenCalledWith(7));
  });
});
