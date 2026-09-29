import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { render } from '../utils';
import { AdditionalSection } from '../../components/spool-form/AdditionalSection';
import { defaultFormData } from '../../components/spool-form/types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

const baseProps = {
  formData: defaultFormData,
  updateField: vi.fn(),
  spoolCatalog: [],
  currencySymbol: '$',
  availableCategories: [],
  availableMaterialNumbers: [],
  globalLowStockThreshold: 20,
};

describe('AdditionalSection', () => {
  it('renders SpoolWeightPicker', () => {
    render(<AdditionalSection {...baseProps} />);
    // SpoolWeightPicker renders the 'inventory.coreWeight' label
    expect(screen.getByText('inventory.coreWeight')).toBeTruthy();
  });

  it('renders the material number field in internal mode (#2870)', () => {
    render(<AdditionalSection {...baseProps} spoolmanMode={false} />);
    expect(screen.getByText('inventory.materialNumber')).toBeTruthy();
  });

  it('hides the material number field in Spoolman mode (#2870)', () => {
    // In Spoolman mode the number is the filament-level article_number,
    // maintained in Spoolman itself — the form must not offer an input
    // whose value would be silently dropped.
    render(<AdditionalSection {...baseProps} spoolmanMode={true} />);
    expect(screen.queryByText('inventory.materialNumber')).toBeNull();
  });
});
