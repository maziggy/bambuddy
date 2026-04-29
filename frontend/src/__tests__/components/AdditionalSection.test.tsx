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
  it('renders SpoolWeightPicker when spoolmanMode is false', () => {
    render(<AdditionalSection {...baseProps} spoolmanMode={false} />);
    // SpoolWeightPicker renders the 'inventory.coreWeight' label
    expect(screen.getByText('inventory.coreWeight')).toBeTruthy();
    // Info notice must NOT be present
    expect(screen.queryByText('inventory.spoolWeightManagedBySpoolman')).toBeNull();
  });

  it('hides SpoolWeightPicker and shows info notice when spoolmanMode is true', () => {
    render(<AdditionalSection {...baseProps} spoolmanMode={true} />);
    // Info notice must appear
    expect(screen.getByText('inventory.spoolWeightManagedBySpoolman')).toBeTruthy();
    // SpoolWeightPicker must NOT be rendered
    expect(screen.queryByText('inventory.coreWeight')).toBeNull();
  });

  it('defaults to spoolmanMode=false when prop is omitted', () => {
    render(<AdditionalSection {...baseProps} />);
    // SpoolWeightPicker present by default
    expect(screen.getByText('inventory.coreWeight')).toBeTruthy();
  });
});
