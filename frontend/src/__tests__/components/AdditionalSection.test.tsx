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
  globalLowStockThreshold: 20,
};

describe('AdditionalSection', () => {
  it('renders SpoolWeightPicker', () => {
    render(<AdditionalSection {...baseProps} />);
    // SpoolWeightPicker renders the 'inventory.coreWeight' label
    expect(screen.getByText('inventory.coreWeight')).toBeTruthy();
  });
});
