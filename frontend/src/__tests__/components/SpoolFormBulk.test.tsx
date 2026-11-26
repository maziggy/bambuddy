/**
 * Tests for bulk spool creation and quick-add mode.
 *
 * Verifies:
 * - Quick-add toggle appears only in create mode
 * - Quick-add mode shows brand and subtype as optional (no asterisk)
 * - Quick-add mode hides slicer preset field
 * - Quick-add mode hides the Printers tab
 * - The whole Quick Add row is the switch, so clicking its label toggles it
 * - Quantity field is rendered in quick-add mode and copy mode
 * - Quantity field is hidden in edit mode
 * - Copy with quantity > 1 names the count on the button and bulk-creates
 * - A failed profile save on a bulk copy keeps the modal open
 * - A Quantity hidden by turning Quick Add off is not applied
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
    getLocations: vi.fn().mockResolvedValue([]),
    getColorCatalog: vi.fn().mockResolvedValue([]),
    getLocalPresets: vi.fn().mockResolvedValue({ filament: [] }),
    getBuiltinFilaments: vi.fn().mockResolvedValue([]),
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
    getSpoolFilamentPresets: vi.fn().mockResolvedValue([]),
    // 3D Filament Profiles (#2981): the form loads the spool's link, and
    // its lookup section asks whether the user is connected.
    getThreedfpSpoolLink: vi.fn().mockResolvedValue({ filament_id: null }),
    setThreedfpSpoolLink: vi.fn().mockResolvedValue({ filament_id: null }),
    threedfpStatus: vi.fn().mockResolvedValue({ connected: false, email: null }),
    threedfpLookup: vi.fn().mockResolvedValue({ results: [] }),
    threedfpGetFilament: vi.fn(),
    saveSpoolFilamentPresets: vi.fn().mockResolvedValue([]),
    getSpoolmanFilamentPresets: vi.fn().mockResolvedValue([]),
    saveSpoolmanFilamentPresets: vi.fn().mockResolvedValue([]),
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
  extra_colors: null,
  effect_type: null,
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
        mode="create"
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
        mode="edit"
        currencySymbol="$"
      />,
    );

    await waitFor(() => {
      expect(screen.getByText('Edit Spool')).toBeInTheDocument();
    });

    expect(screen.queryByText('Quick Add (Stock)')).not.toBeInTheDocument();
  });

  it('hides the Printers tab when quick-add is enabled', async () => {
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        mode="create"
        currencySymbol="$"
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Add Spool' })).toBeInTheDocument();
    });

    // Printers tab should be visible initially
    expect(screen.getByText('Printers')).toBeInTheDocument();

    // Toggle quick-add on — the whole row is the switch
    const quickAddToggle = screen.getByRole('switch', { name: /Quick Add/ });
    fireEvent.click(quickAddToggle);

    // Printers tab should be hidden
    await waitFor(() => {
      expect(screen.queryByText('Printers')).not.toBeInTheDocument();
    });
  });

  it('hides quantity field by default (non-quick-add)', async () => {
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        mode="create"
        currencySymbol="$"
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Add Spool' })).toBeInTheDocument();
    });

    // Quantity field should NOT be visible in normal create mode
    expect(screen.queryByText('Quantity')).not.toBeInTheDocument();
  });

  it('shows quantity field only in quick-add mode', async () => {
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        mode="create"
        currencySymbol="$"
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Add Spool' })).toBeInTheDocument();
    });

    // Toggle quick-add on
    const quickAddToggle = screen.getByRole('switch', { name: /Quick Add/ });
    fireEvent.click(quickAddToggle);

    // Quantity field should now be visible
    await waitFor(() => {
      expect(screen.getByText('Quantity')).toBeInTheDocument();
    });
  });

  it('hides quantity field in edit mode', async () => {
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        spool={existingSpool}
        mode="edit"
        currencySymbol="$"
      />,
    );

    await waitFor(() => {
      expect(screen.getByText('Edit Spool')).toBeInTheDocument();
    });

    // Quantity field should NOT be visible in edit mode
    expect(screen.queryByText('Quantity')).not.toBeInTheDocument();
  });

  it('shows brand and subtype in quick-add mode without asterisk', async () => {
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        mode="create"
        currencySymbol="$"
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Add Spool' })).toBeInTheDocument();
    });

    // Toggle quick-add on
    const quickAddToggle = screen.getByRole('switch', { name: /Quick Add/ });
    fireEvent.click(quickAddToggle);

    // Brand and Subtype should be visible (without asterisk = optional)
    await waitFor(() => {
      const brandLabel = screen.getByText('Brand');
      expect(brandLabel).toBeInTheDocument();
      expect(brandLabel.textContent).not.toContain('*');

      const subtypeLabel = screen.getByText('Subtype');
      expect(subtypeLabel).toBeInTheDocument();
      expect(subtypeLabel.textContent).not.toContain('*');
    });
  });

  it('toggles quick-add when the label text is clicked', async () => {
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        mode="create"
        currencySymbol="$"
      />,
    );

    const toggle = await screen.findByRole('switch', { name: /Quick Add/ });
    expect(toggle).toHaveAttribute('aria-checked', 'false');

    fireEvent.click(screen.getByText('Quick Add (Stock)'));

    expect(toggle).toHaveAttribute('aria-checked', 'true');
    await waitFor(() => {
      expect(screen.getByText('Quantity')).toBeInTheDocument();
    });
  });

  it('shows quantity in copy mode and bulk-creates the copies', async () => {
    const { api } = await import('../../api/client');
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        spool={existingSpool}
        mode="copy"
        currencySymbol="$"
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Copy Spool' })).toBeInTheDocument();
    });
    // Copy mode has no Quick Add switch, but does have Quantity (default 1).
    expect(screen.queryByRole('switch', { name: /Quick Add/ })).not.toBeInTheDocument();
    const quantity = screen.getByText('Quantity').parentElement!.querySelector('input') as HTMLInputElement;
    expect(quantity.value).toBe('1');
    expect(screen.getByRole('button', { name: /^Copy Spool$/ })).toBeInTheDocument();

    fireEvent.change(quantity, { target: { value: '3' } });

    // The count is named on the button, never hidden behind the singular title.
    const submit = await screen.findByRole('button', { name: /^Create 3 copies$/ });
    fireEvent.click(submit);

    await waitFor(() => {
      expect(api.bulkCreateSpools).toHaveBeenCalledWith(
        expect.objectContaining({ material: 'PLA', brand: 'Polymaker', weight_used: 0 }),
        3,
      );
    });
    expect(api.createSpool).not.toHaveBeenCalled();
  });

  it('keeps the modal open when a bulk copy fails to save its profiles', async () => {
    const { api } = await import('../../api/client');
    vi.mocked(api.saveSpoolKProfiles).mockRejectedValueOnce(new Error('boom'));
    const onClose = vi.fn();
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={onClose}
        spool={existingSpool}
        mode="copy"
        currencySymbol="$"
      />,
    );

    const quantity = (await screen.findByText('Quantity')).parentElement!.querySelector('input') as HTMLInputElement;
    fireEvent.change(quantity, { target: { value: '3' } });
    fireEvent.click(await screen.findByRole('button', { name: /^Create 3 copies$/ }));

    await waitFor(() => expect(api.bulkCreateSpools).toHaveBeenCalled());
    await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith(expect.any(String), 'warning'));
    expect(mockShowToast).not.toHaveBeenCalledWith(expect.stringMatching(/spools created/), 'success');
    expect(onClose).not.toHaveBeenCalled();
  });

  it('ignores a quantity hidden by turning Quick Add back off', async () => {
    const { api } = await import('../../api/client');
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        spool={existingSpool}
        mode="create"
        currencySymbol="$"
      />,
    );

    const toggle = await screen.findByRole('switch', { name: /Quick Add/ });
    fireEvent.click(toggle);
    const quantity = (await screen.findByText('Quantity')).parentElement!.querySelector('input') as HTMLInputElement;
    fireEvent.change(quantity, { target: { value: '5' } });
    fireEvent.click(toggle);
    expect(screen.queryByText('Quantity')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^Add Spool$/ }));

    await waitFor(() => {
      expect(api.createSpool).toHaveBeenCalled();
    });
    expect(api.bulkCreateSpools).not.toHaveBeenCalled();
  });
});
