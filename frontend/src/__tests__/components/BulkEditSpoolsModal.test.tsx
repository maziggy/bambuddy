/**
 * Bulk edit: the field list must only offer fields the active inventory
 * backend can actually store (#2870).
 *
 * In Spoolman mode a spool has no material number, category or low-stock
 * override of its own — SpoolmanInventoryUpdate has no such fields, so the
 * payload dumps to {} and the route answers 400 "update must include at
 * least one field". The user ticks a box, types a value, clicks Apply and
 * gets an error. Filtering the list is the fix.
 */

import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { render } from '../utils';
import { BulkEditSpoolsModal } from '../../components/BulkEditSpoolsModal';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

const baseProps = {
  isOpen: true,
  selectedCount: 3,
  isPending: false,
  availableLocations: [],
  availableMaterials: [],
  availableSubtypes: [],
  availableBrands: [],
  availableCategories: [],
  availableMaterialNumbers: [],
  availableSlicerFilaments: [],
  availableSlicerFilamentNames: [],
  onClose: vi.fn(),
  onApply: vi.fn(),
};

const INTERNAL_ONLY = [
  'inventory.materialNumber',
  'inventory.category',
  'inventory.lowStockThresholdOverride',
];

describe('BulkEditSpoolsModal field list', () => {
  it('offers the internal-only fields in internal mode', () => {
    render(<BulkEditSpoolsModal {...baseProps} spoolmanMode={false} />);
    for (const key of INTERNAL_ONLY) {
      expect(screen.getByText(key)).toBeTruthy();
    }
  });

  it('hides every field Spoolman cannot store in Spoolman mode', () => {
    render(<BulkEditSpoolsModal {...baseProps} spoolmanMode={true} />);
    for (const key of INTERNAL_ONLY) {
      expect(screen.queryByText(key)).toBeNull();
    }
    // The fields Spoolman does accept stay.
    expect(screen.getByText('inventory.material')).toBeTruthy();
    expect(screen.getByText('inventory.note')).toBeTruthy();
    expect(screen.getByText('inventory.costPerKg')).toBeTruthy();
  });

  it('defaults to internal mode when the prop is omitted', () => {
    render(<BulkEditSpoolsModal {...baseProps} />);
    expect(screen.getByText('inventory.materialNumber')).toBeTruthy();
  });
});
