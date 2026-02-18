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
    updateSpool: vi.fn().mockResolvedValue({ id: 1 }),
    saveSpoolKProfiles: vi.fn().mockResolvedValue([]),
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
