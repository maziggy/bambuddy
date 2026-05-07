/**
 * Tests for the SpoolFormModal weightTouched behavior.
 *
 * Verifies that weight_used is only included in the PATCH payload when the user
 * explicitly changes the remaining weight field. This prevents stale React Query
 * cache values from overwriting usage-tracked weight data on the backend.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import { SpoolFormModal } from '../../components/SpoolFormModal';
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
    createSpoolmanInventorySpool: vi.fn().mockResolvedValue({ id: 88 }),
    updateSpool: vi.fn().mockResolvedValue({ id: 1 }),
    saveSpoolKProfiles: vi.fn().mockResolvedValue([]),
    saveSpoolmanKProfiles: vi.fn().mockResolvedValue([]),
    updateSpoolmanInventorySpool: vi.fn().mockResolvedValue({ id: 42 }),
    bulkCreateSpoolmanInventorySpools: vi.fn().mockResolvedValue({
      created: [{ id: 1, material: 'PLA' }],
      requested_count: 1,
      failed_count: 0,
    }),
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

// Mock validateForm so we can bypass validation for the create-mode test
// (editing tests pass validation naturally since the spool has material + slicer_filament)
vi.mock('../../components/spool-form/types', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../components/spool-form/types')>();
  return {
    ...actual,
    validateForm: vi.fn().mockReturnValue({ isValid: true, errors: {} }),
  };
});

// Mock the toast context
const mockShowToast = vi.fn();
vi.mock('../../contexts/ToastContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/ToastContext')>();
  return {
    ...actual,
    useToast: () => ({ showToast: mockShowToast }),
  };
});

import { api } from '../../api/client';

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
};

describe('SpoolFormModal weightTouched', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('excludes weight_used from PATCH when editing without changing weight', async () => {
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        spool={existingSpool}
        currencySymbol="$"
      />
    );

    // Wait for the modal to render with the edit title
    await waitFor(() => {
      expect(screen.getByText('Edit Spool')).toBeInTheDocument();
    });

    // Click Save without touching the weight field
    const saveButton = screen.getByRole('button', { name: /save/i });
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(api.updateSpool).toHaveBeenCalledTimes(1);
    });

    const [spoolId, payload] = vi.mocked(api.updateSpool).mock.calls[0];
    expect(spoolId).toBe(1);
    // weight_used must NOT be present in the payload
    expect(payload).not.toHaveProperty('weight_used');
    // Other fields should still be present
    expect(payload).toHaveProperty('material', 'PLA');
    expect(payload).toHaveProperty('label_weight', 1000);
  });

  it('includes weight_used in PATCH when editing and changing remaining weight', async () => {
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        spool={existingSpool}
        currencySymbol="$"
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Edit Spool')).toBeInTheDocument();
    });

    // The remaining weight is (label_weight - weight_used) = 1000 - 300 = 700.
    // The input is a number input displaying 700. Find it by its displayed value.
    const remainingInput = screen.getByDisplayValue('700');
    expect(remainingInput).toBeInTheDocument();

    // Change the remaining weight from 700 to 500 (weight_used becomes 1000 - 500 = 500)
    fireEvent.change(remainingInput, { target: { value: '500' } });
    // Blur triggers updateField('weight_used', ...) which sets weightTouched
    fireEvent.blur(remainingInput);

    // Click Save
    const saveButton = screen.getByRole('button', { name: /save/i });
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(api.updateSpool).toHaveBeenCalledTimes(1);
    });

    const [spoolId, payload] = vi.mocked(api.updateSpool).mock.calls[0];
    expect(spoolId).toBe(1);
    // weight_used MUST be present since the user changed the weight
    expect(payload).toHaveProperty('weight_used', 500);
  });

  it('includes weight_used when creating a new spool', async () => {
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        currencySymbol="$"
      />
    );

    // Wait for the modal to render with the create title
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Add Spool' })).toBeInTheDocument();
    });

    // Click the submit button (validation is mocked to always pass).
    // The default form data has weight_used=0, and for create mode the condition
    //   if (!isEditing || weightTouched) { data.weight_used = formData.weight_used; }
    // always includes weight_used since isEditing is false.
    // The submit button also says "Add Spool" — use getAllByText and pick the button.
    const addButtons = screen.getAllByRole('button', { name: /add spool/i });
    const submitButton = addButtons.find(btn => btn.tagName === 'BUTTON' && btn.querySelector('svg.lucide-save'));
    expect(submitButton).toBeTruthy();
    fireEvent.click(submitButton!);

    await waitFor(() => {
      expect(api.createSpool).toHaveBeenCalledTimes(1);
    });

    const [payload] = vi.mocked(api.createSpool).mock.calls[0];
    // weight_used MUST be included for new spools (default value 0)
    expect(payload).toHaveProperty('weight_used', 0);
  });

  it('preserves core_weight_catalog_id when editing other fields', async () => {
    const spoolWithCatalogId: InventorySpool = {
      ...existingSpool,
      core_weight_catalog_id: 5,
    };

    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        spool={spoolWithCatalogId}
        currencySymbol="$"
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Edit Spool')).toBeInTheDocument();
    });

    // Change the note field (unrelated to catalog ID)
    const noteInputs = screen.getAllByPlaceholderText(/note/i);
    expect(noteInputs.length).toBeGreaterThan(0);
    fireEvent.change(noteInputs[0], { target: { value: 'Updated note' } });

    // Click Save
    const saveButton = screen.getByRole('button', { name: /save/i });
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(api.updateSpool).toHaveBeenCalledTimes(1);
    });

    const [spoolId, payload] = vi.mocked(api.updateSpool).mock.calls[0];
    expect(spoolId).toBe(1);
    // core_weight_catalog_id MUST be preserved when editing other fields
    expect(payload).toHaveProperty('core_weight_catalog_id', 5);
    // Other changes should also be present
    expect(payload).toHaveProperty('note', 'Updated note');
  });

  it('includes core_weight_catalog_id when selecting from catalog', async () => {
    const mockCatalog = [
      { id: 1, name: 'Generic 250g', weight: 250 },
      { id: 2, name: 'Bambu Lab 250g', weight: 250 },
      { id: 3, name: 'Standard 300g', weight: 300 },
    ];

    vi.mocked(api.getSpoolCatalog).mockResolvedValue(mockCatalog);

    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        currencySymbol="$"
      />
    );

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Add Spool' })).toBeInTheDocument();
    });

    // Wait for catalog to load
    await waitFor(() => {
      expect(api.getSpoolCatalog).toHaveBeenCalled();
    });

    // Click on the empty spool weight field to open dropdown
    const weightInputs = screen.getAllByPlaceholderText(/search/i);
    const weightPicker = weightInputs.find(input =>
      input.getAttribute('placeholder')?.toLowerCase().includes('spool')
    );
    expect(weightPicker).toBeTruthy();
    fireEvent.focus(weightPicker!);

    // Click on "Bambu Lab 250g" option
    const bambuOption = await screen.findByText('Bambu Lab 250g');
    fireEvent.click(bambuOption);

    // Click the add spool button
    const addButtons = screen.getAllByRole('button', { name: /add spool/i });
    const submitButton = addButtons.find(btn => btn.tagName === 'BUTTON' && btn.querySelector('svg.lucide-save'));
    expect(submitButton).toBeTruthy();
    fireEvent.click(submitButton!);

    await waitFor(() => {
      expect(api.createSpool).toHaveBeenCalledTimes(1);
    });

    const [payload] = vi.mocked(api.createSpool).mock.calls[0];
    // Both weight AND catalog ID should be sent
    expect(payload).toHaveProperty('core_weight', 250);
    expect(payload).toHaveProperty('core_weight_catalog_id', 2); // ID of "Bambu Lab 250g"
  });

  it('preserves cost_per_kg when editing spool', async () => {
    const spoolWithCost: InventorySpool = {
      ...existingSpool,
      cost_per_kg: 25.50,
    };

    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        spool={spoolWithCost}
        currencySymbol="$"
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Edit Spool')).toBeInTheDocument();
    });

    // Click Save without changing cost
    const saveButton = screen.getByRole('button', { name: /save/i });
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(api.updateSpool).toHaveBeenCalledTimes(1);
    });

    const [spoolId, payload] = vi.mocked(api.updateSpool).mock.calls[0];
    expect(spoolId).toBe(1);
    // cost_per_kg should be preserved in the update payload
    expect(payload).toHaveProperty('cost_per_kg', 25.50);
  });

  it('sends null cost_per_kg when spool has no cost', async () => {
    const spoolWithoutCost: InventorySpool = {
      ...existingSpool,
      cost_per_kg: null,
    };

    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        spool={spoolWithoutCost}
        currencySymbol="$"
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Edit Spool')).toBeInTheDocument();
    });

    const saveButton = screen.getByRole('button', { name: /save/i });
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(api.updateSpool).toHaveBeenCalledTimes(1);
    });

    const [, payload] = vi.mocked(api.updateSpool).mock.calls[0];
    // cost_per_kg should be null when not set
    expect(payload).toHaveProperty('cost_per_kg', null);
  });

  it('normalizes a malformed legacy rgba on edit-form load so PATCH is not rejected (#1055)', async () => {
    // #1055 regression guard: a spool with a legacy 7-char rgba (e.g. 'FFFFFFF')
    // was editable in the UI but any save 422'd because SpoolUpdate now enforces
    // the 8-char pattern. The form must sanitize the loaded value to a valid
    // default so users can edit unrelated fields without being forced to fix
    // a color they may not even have noticed was broken.
    const spoolWithBadRgba: InventorySpool = {
      ...existingSpool,
      rgba: 'FFFFFFF', // 7 chars — the exact #1055 trigger pattern
    };

    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        spool={spoolWithBadRgba}
        currencySymbol="$"
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Edit Spool')).toBeInTheDocument();
    });

    const saveButton = screen.getByRole('button', { name: /save/i });
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(api.updateSpool).toHaveBeenCalledTimes(1);
    });

    const [, payload] = vi.mocked(api.updateSpool).mock.calls[0];
    // The PATCH payload must carry a valid 8-char rgba — never the raw 7-char
    // value loaded from the stale DB row.
    expect(payload).toHaveProperty('rgba');
    expect(typeof (payload as { rgba: unknown }).rgba).toBe('string');
    expect((payload as { rgba: string }).rgba).toMatch(/^[0-9A-Fa-f]{8}$/);
  });

  it('preserves a valid existing rgba on edit (no forced default)', async () => {
    // Sanity: the normalization only kicks in for malformed values. A valid
    // 8-char rgba must round-trip untouched so untouched edits don't quietly
    // reset a user's chosen color.
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        spool={existingSpool} // rgba = 'FF0000FF' (valid)
        currencySymbol="$"
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Edit Spool')).toBeInTheDocument();
    });

    const saveButton = screen.getByRole('button', { name: /save/i });
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(api.updateSpool).toHaveBeenCalledTimes(1);
    });

    const [, payload] = vi.mocked(api.updateSpool).mock.calls[0];
    expect((payload as { rgba: string }).rgba).toBe('FF0000FF');
  });

  it('shows warning toast on partial bulk-create in Spoolman mode (T1/partial)', async () => {
    vi.mocked(api.bulkCreateSpoolmanInventorySpools).mockResolvedValueOnce({
      created: [{ id: 1, material: 'PLA' } as InventorySpool],
      requested_count: 3,
      failed_count: 2,
    });

    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        currencySymbol="$"
        spoolmanMode={true}
      />
    );

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Add Spool' })).toBeInTheDocument();
    });

    // Enable Quick Add mode so the quantity field appears
    const quickAddRow = screen.getByText('Quick Add (Stock)').closest('div[class*="justify-between"]');
    const toggleButton = quickAddRow?.querySelector('button[type="button"]');
    expect(toggleButton).toBeTruthy();
    fireEvent.click(toggleButton!);

    // Set quantity to 3 (triggers bulkCreateMutation instead of createMutation)
    const quantityContainer = screen.getByText('Quantity').closest('div');
    const quantityInput = quantityContainer?.querySelector('input[type="number"]');
    expect(quantityInput).toBeTruthy();
    fireEvent.change(quantityInput!, { target: { value: '3' } });

    // Click the submit button
    const addButtons = screen.getAllByRole('button', { name: /add spool/i });
    const submitButton = addButtons.find(btn => btn.tagName === 'BUTTON' && btn.querySelector('svg.lucide-save'));
    expect(submitButton).toBeTruthy();
    fireEvent.click(submitButton!);

    await waitFor(() => {
      expect(api.bulkCreateSpoolmanInventorySpools).toHaveBeenCalledTimes(1);
    });

    // Should show a warning toast for partial failure (1 created, 2 failed, 3 requested)
    expect(mockShowToast).toHaveBeenCalledWith(
      expect.stringContaining('1 of 3'),
      'warning',
    );
  });

  it('shows success toast on full bulk-create success in Spoolman mode (T1/success)', async () => {
    vi.mocked(api.bulkCreateSpoolmanInventorySpools).mockResolvedValueOnce({
      created: [
        { id: 1, material: 'PLA' } as InventorySpool,
        { id: 2, material: 'PLA' } as InventorySpool,
        { id: 3, material: 'PLA' } as InventorySpool,
      ],
      requested_count: 3,
      failed_count: 0,
    });

    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        currencySymbol="$"
        spoolmanMode={true}
      />
    );

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Add Spool' })).toBeInTheDocument();
    });

    // Enable Quick Add mode so the quantity field appears
    const quickAddRow = screen.getByText('Quick Add (Stock)').closest('div[class*="justify-between"]');
    const toggleButton = quickAddRow?.querySelector('button[type="button"]');
    expect(toggleButton).toBeTruthy();
    fireEvent.click(toggleButton!);

    // Set quantity to 3
    const quantityContainer = screen.getByText('Quantity').closest('div');
    const quantityInput = quantityContainer?.querySelector('input[type="number"]');
    expect(quantityInput).toBeTruthy();
    fireEvent.change(quantityInput!, { target: { value: '3' } });

    // Click the submit button
    const addButtons = screen.getAllByRole('button', { name: /add spool/i });
    const submitButton = addButtons.find(btn => btn.tagName === 'BUTTON' && btn.querySelector('svg.lucide-save'));
    expect(submitButton).toBeTruthy();
    fireEvent.click(submitButton!);

    await waitFor(() => {
      expect(api.bulkCreateSpoolmanInventorySpools).toHaveBeenCalledTimes(1);
    });

    // Should show a success toast listing the count of created spools
    expect(mockShowToast).toHaveBeenCalledWith(
      expect.stringContaining('3'),
      'success',
    );
  });

  it('displays correct catalog name when duplicates exist', async () => {
    const spoolWithCatalogId: InventorySpool = {
      ...existingSpool,
      core_weight: 250,
      core_weight_catalog_id: 2, // "Bambu Lab 250g", not the first match
    };

    const mockCatalog = [
      { id: 1, name: 'Generic 250g', weight: 250 },
      { id: 2, name: 'Bambu Lab 250g', weight: 250 },
      { id: 3, name: 'Standard 300g', weight: 300 },
    ];

    vi.mocked(api.getSpoolCatalog).mockResolvedValue(mockCatalog);

    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        spool={spoolWithCatalogId}
        currencySymbol="$"
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Edit Spool')).toBeInTheDocument();
    });

    // Wait for catalog to load
    await waitFor(() => {
      expect(api.getSpoolCatalog).toHaveBeenCalled();
    });

    // Should display "Bambu Lab 250g" (by ID), not "Generic 250g" (first match by weight)
    await waitFor(() => {
      const weightInputs = screen.getAllByDisplayValue(/250|Bambu/i);
      const bambuFound = weightInputs.some(input =>
        input.value === 'Bambu Lab 250g' || input.getAttribute('value') === 'Bambu Lab 250g'
      );
      expect(bambuFound).toBeTruthy();
    });
  });
});

describe('SpoolFormModal Spoolman K-profile support', () => {
  const spoolmanSpool: InventorySpool = {
    ...{
      id: 42,
      material: 'PLA',
      subtype: 'Basic',
      brand: 'BrandX',
      color_name: 'Black',
      rgba: '000000FF',
      label_weight: 1000,
      core_weight: 250,
      core_weight_catalog_id: null,
      weight_used: 200,
      slicer_filament: '',
      slicer_filament_name: '',
      nozzle_temp_min: null,
      nozzle_temp_max: null,
      note: null,
      added_full: null,
      last_used: null,
      encode_time: null,
      tag_uid: null,
      tray_uuid: null,
      data_origin: 'spoolman',
      tag_type: 'spoolman',
      archived_at: null,
      created_at: '2025-01-01T00:00:00Z',
      updated_at: '2025-01-01T00:00:00Z',
      k_profiles: [],
    },
  } as InventorySpool;

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows PA Profile tab for Spoolman spools in non-quickAdd mode', async () => {
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        spool={spoolmanSpool}
        currencySymbol="$"
        spoolmanMode={true}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Edit Spool')).toBeInTheDocument();
    });

    // PA Profile tab should be visible in Spoolman mode
    expect(screen.getByText('PA Profile')).toBeInTheDocument();
  });

  it('calls saveSpoolmanKProfiles (not saveSpoolKProfiles) on update in Spoolman mode', async () => {
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        spool={spoolmanSpool}
        currencySymbol="$"
        spoolmanMode={true}
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Edit Spool')).toBeInTheDocument();
    });

    const saveButton = screen.getByRole('button', { name: /save/i });
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(api.updateSpoolmanInventorySpool).toHaveBeenCalledTimes(1);
    });

    // saveSpoolmanKProfiles is always called on update (even with empty list)
    await waitFor(() => {
      expect(api.saveSpoolmanKProfiles).toHaveBeenCalledWith(42, []);
    });
    expect(api.saveSpoolKProfiles).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// T2: SpoolmanFilamentPicker integration with SpoolFormModal
// ---------------------------------------------------------------------------

vi.mock('../../components/spool-form/SpoolmanFilamentPicker', () => ({
  SpoolmanFilamentPicker: ({ onSelect, selectedId }: { onSelect: (f: unknown) => void; selectedId: number | null; isLoading: boolean; filaments: unknown[] }) => {
    return (
      <div>
        <span data-testid="picker-selected-id">{selectedId ?? 'none'}</span>
        <button data-testid="picker-select-btn" onClick={() => onSelect({
          id: 7,
          name: 'PLA Basic',
          material: 'PLA',
          color_hex: 'FF0000',
          color_name: 'Red',
          weight: 1000,
          spool_weight: 196,
          vendor: { id: 1, name: 'Bambu Lab' },
        })}>
          Select Filament
        </button>
      </div>
    );
  },
}));

describe('SpoolFormModal — SpoolmanFilamentPicker integration (T2)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders SpoolmanFilamentPicker in Spoolman create mode', async () => {
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        currencySymbol="$"
        spoolmanMode={true}
      />
    );

    await waitFor(() => {
      expect(screen.getByTestId('picker-select-btn')).toBeInTheDocument();
    });
  });

  it('does NOT render SpoolmanFilamentPicker in local inventory mode', async () => {
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        currencySymbol="$"
        spoolmanMode={false}
      />
    );

    await waitFor(() => {
      expect(screen.queryByTestId('picker-select-btn')).not.toBeInTheDocument();
    });
  });

  it('prefills form fields when a filament is selected from the picker', async () => {
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        currencySymbol="$"
        spoolmanMode={true}
      />
    );

    await waitFor(() => {
      expect(screen.getByTestId('picker-select-btn')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId('picker-select-btn'));

    // After selection, the picker should reflect the selected ID
    await waitFor(() => {
      expect(screen.getByTestId('picker-selected-id').textContent).toBe('7');
    });
  });

  it('includes spoolman_filament_id in the submit payload when a filament is pre-selected', async () => {
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        currencySymbol="$"
        spoolmanMode={true}
        spoolsQueryKey={['spoolman-spools']}
      />
    );

    await waitFor(() => {
      expect(screen.getByTestId('picker-select-btn')).toBeInTheDocument();
    });

    // Select a filament
    fireEvent.click(screen.getByTestId('picker-select-btn'));

    // Submit the form
    const saveButton = screen.getByRole('button', { name: /save|add spool/i });
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(api.createSpoolmanInventorySpool).toHaveBeenCalledTimes(1);
    });

    const callArg = vi.mocked(api.createSpoolmanInventorySpool).mock.calls[0][0] as Record<string, unknown>;
    expect(callArg.spoolman_filament_id).toBe(7);
  });

  it('clears spoolman_filament_id and shows unlink toast when user edits a linked field', async () => {
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        currencySymbol="$"
        spoolmanMode={true}
      />
    );

    await waitFor(() => {
      expect(screen.getByTestId('picker-select-btn')).toBeInTheDocument();
    });

    // Select a filament from the catalog picker
    fireEvent.click(screen.getByTestId('picker-select-btn'));

    await waitFor(() => {
      expect(screen.getByTestId('picker-selected-id').textContent).toBe('7');
    });

    // Manually edit the color_name field (a linked field)
    const colorNameInput = screen.getByPlaceholderText('Jade White, Fire Red...');
    fireEvent.change(colorNameInput, { target: { value: 'Custom Blue' } });

    // spoolman_filament_id must be cleared (picker shows 'none')
    await waitFor(() => {
      expect(screen.getByTestId('picker-selected-id').textContent).toBe('none');
    });

    // Unlink toast must have been shown
    expect(mockShowToast).toHaveBeenCalledWith(
      expect.stringContaining('catalog link'),
      'info',
    );
  });
});

describe('SpoolFormModal storageLocationTouched', () => {
  /**
   * Regression tests for the round-trip bug: saving the edit modal without
   * touching the Storage Location field must NOT include storage_location in
   * the PATCH payload, so Spoolman's location field is never overwritten with
   * a stale cached value.
   */
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const spoolWithStorageLocation: InventorySpool = {
    ...existingSpool,
    storage_location: 'IKEAREGAL',
  };

  it('excludes storage_location from PATCH when editing without changing it', async () => {
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        spool={spoolWithStorageLocation}
        currencySymbol="$"
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Edit Spool')).toBeInTheDocument();
    });

    // Save without touching the storage location field
    const saveButton = screen.getByRole('button', { name: /save/i });
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(api.updateSpool).toHaveBeenCalledTimes(1);
    });

    const [spoolId, payload] = vi.mocked(api.updateSpool).mock.calls[0];
    expect(spoolId).toBe(1);
    // storage_location must NOT be in the payload — prevents Spoolman location overwrite
    expect(payload).not.toHaveProperty('storage_location');
    // Other fields should still be present
    expect(payload).toHaveProperty('material', 'PLA');
  });

  it('includes storage_location in PATCH when editing and changing it', async () => {
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        spool={spoolWithStorageLocation}
        currencySymbol="$"
      />
    );

    await waitFor(() => {
      expect(screen.getByText('Edit Spool')).toBeInTheDocument();
    });

    // Find the storage location input and change it
    const locationInput = screen.getByPlaceholderText('e.g. Shelf A, Drawer 1');
    fireEvent.change(locationInput, { target: { value: 'Shelf B' } });

    const saveButton = screen.getByRole('button', { name: /save/i });
    fireEvent.click(saveButton);

    await waitFor(() => {
      expect(api.updateSpool).toHaveBeenCalledTimes(1);
    });

    const [spoolId, payload] = vi.mocked(api.updateSpool).mock.calls[0];
    expect(spoolId).toBe(1);
    // storage_location MUST be present since the user changed it
    expect(payload).toHaveProperty('storage_location', 'Shelf B');
  });

  it('includes storage_location when creating a new spool', async () => {
    render(
      <SpoolFormModal
        isOpen={true}
        onClose={vi.fn()}
        currencySymbol="$"
      />
    );

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Add Spool' })).toBeInTheDocument();
    });

    // Submit without setting storage_location (validation is mocked to pass)
    const addButtons = screen.getAllByRole('button', { name: /add spool/i });
    const submitButton = addButtons.find(btn => btn.tagName === 'BUTTON' && btn.querySelector('svg.lucide-save'));
    expect(submitButton).toBeTruthy();
    fireEvent.click(submitButton!);

    await waitFor(() => {
      expect(api.createSpool).toHaveBeenCalledTimes(1);
    });

    const [payload] = vi.mocked(api.createSpool).mock.calls[0];
    // storage_location MUST be included for new spools (default empty string → null)
    expect(payload).toHaveProperty('storage_location', null);
  });
});
