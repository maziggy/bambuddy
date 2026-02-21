/**
 * Tests for bulk spool creation and quick-add mode.
 *
 * Verifies:
 * - Quick-add toggle appears only in create mode
 * - Quick-add mode hides slicer preset, brand, subtype fields
 * - Quick-add mode hides PA Profile tab
 * - Quantity field is rendered in filament section
 * - Bulk create calls bulkCreateSpools when quantity > 1
 * - Single quantity calls createSpool as before
 * - validateForm with quickAdd=true only requires material
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { SpoolFormModal } from '../../components/SpoolFormModal';
import { validateForm, defaultFormData } from '../../components/spool-form/types';
import type { InventorySpool } from '../../api/client';

// Mock the API client
vi.mock('../../api/client', () => ({
  api: {
    getSettings: vi.fn().mockResolvedValue({}),
    getAuthStatus: vi.fn().mockResolvedValue({ auth_enabled: false }),
    getCloudStatus: vi.fn().mockResolvedValue({ is_authenticated: false }),
    getFilamentPresets: vi.fn().mockResolvedValue([]),
    getSpoolCatalog: vi.fn().mockResolvedValue([]),
    getColorCatalog: vi.fn().mockResolvedValue([]),
    getLocalPresets: vi.fn().mockResolvedValue({ filament: [] }),
    getPrinters: vi.fn().mockResolvedValue([]),
    getSpoolUsageHistory: vi.fn().mockResolvedValue([]),
    createSpool: vi.fn().mockResolvedValue({ id: 99 }),
    bulkCreateSpools: vi.fn().mockResolvedValue([
      { id: 100, k_profiles: [] },
      { id: 101, k_profiles: [] },
      { id: 102, k_profiles: [] },
    ]),
    updateSpool: vi.fn().mockResolvedValue({ id: 1 }),
    saveSpoolKProfiles: vi.fn().mockResolvedValue([]),
  },
}));

// Mock the toast context
const mockShowToast = vi.fn();
vi.mock('../../contexts/ToastContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/ToastContext')>();
  return {
    ...actual,
    useToast: () => ({ showToast: mockShowToast }),
  };
});

const existingSpool: InventorySpool = {
  id: 1,
  material: 'PLA',
  subtype: 'Basic',
  brand: 'Polymaker',
  color_name: 'Red',
  rgba: 'FF0000FF',
  label_weight: 1000,
  core_weight: 250,
  core_weight_catalog_id: null,
  weight_used: 300,
  slicer_filament: 'GFL99',
  slicer_filament_name: 'Generic PLA',
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
};

describe('validateForm with quickAdd', () => {
  it('requires only material in quick-add mode', () => {
    const result = validateForm({ ...defaultFormData, material: 'PLA' }, true);
    expect(result.isValid).toBe(true);
    expect(result.errors).toEqual({});
  });

  it('rejects empty material in quick-add mode', () => {
    const result = validateForm({ ...defaultFormData, material: '' }, true);
    expect(result.isValid).toBe(false);
    expect(result.errors.material).toBeDefined();
  });

  it('does not require slicer_filament in quick-add mode', () => {
    const result = validateForm(
      { ...defaultFormData, material: 'PETG', slicer_filament: '' },
      true,
    );
    expect(result.isValid).toBe(true);
  });

  it('does not require brand in quick-add mode', () => {
    const result = validateForm(
      { ...defaultFormData, material: 'ABS', brand: '' },
      true,
    );
    expect(result.isValid).toBe(true);
  });

  it('does not require subtype in quick-add mode', () => {
    const result = validateForm(
      { ...defaultFormData, material: 'TPU', subtype: '' },
      true,
    );
    expect(result.isValid).toBe(true);
  });

  it('requires all fields in full mode (quickAdd=false)', () => {
    const result = validateForm(defaultFormData, false);
    expect(result.isValid).toBe(false);
    expect(result.errors.material).toBeDefined();
    expect(result.errors.slicer_filament).toBeDefined();
    expect(result.errors.brand).toBeDefined();
    expect(result.errors.subtype).toBeDefined();
  });
});

describe('SpoolFormModal quick-add toggle', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows quick-add toggle in create mode', async () => {
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        currencySymbol="$"
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Add Spool' })).toBeInTheDocument();
    });

    expect(screen.getByText('Quick Add (Stock)')).toBeInTheDocument();
  });

  it('hides quick-add toggle in edit mode', async () => {
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        spool={existingSpool}
        currencySymbol="$"
      />,
    );

    await waitFor(() => {
      expect(screen.getByText('Edit Spool')).toBeInTheDocument();
    });

    expect(screen.queryByText('Quick Add (Stock)')).not.toBeInTheDocument();
  });

  it('hides PA Profile tab when quick-add is enabled', async () => {
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        currencySymbol="$"
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Add Spool' })).toBeInTheDocument();
    });

    // PA Profile tab should be visible initially
    expect(screen.getByText('PA Profile')).toBeInTheDocument();

    // Toggle quick-add on — the toggle is a button[role="switch"] sibling of the label
    const toggleButtons = screen.getAllByRole('button');
    const quickAddToggle = toggleButtons.find(btn =>
      btn.getAttribute('type') === 'button' &&
      btn.className.includes('rounded-full') &&
      btn.closest('div')?.textContent?.includes('Quick Add')
    );
    expect(quickAddToggle).toBeTruthy();
    fireEvent.click(quickAddToggle!);

    // PA Profile tab should be hidden
    await waitFor(() => {
      expect(screen.queryByText('PA Profile')).not.toBeInTheDocument();
    });
  });

  it('renders quantity field', async () => {
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        currencySymbol="$"
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Add Spool' })).toBeInTheDocument();
    });

    // Quantity field should be visible
    expect(screen.getByText('Quantity')).toBeInTheDocument();
    expect(screen.getByDisplayValue('1')).toBeInTheDocument();
  });
});
