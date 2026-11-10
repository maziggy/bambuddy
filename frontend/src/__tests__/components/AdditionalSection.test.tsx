import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, screen } from '@testing-library/react';
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

  describe('batch ID, owner and purchase date (#2828, #1301, #3408)', () => {
    it('are offered in internal mode', () => {
      render(<AdditionalSection {...baseProps} spoolmanMode={false} />);
      expect(screen.getByLabelText('inventory.batchId')).toBeTruthy();
      expect(screen.getByLabelText('inventory.owner')).toBeTruthy();
      expect(screen.getByLabelText('inventory.purchaseDate')).toBeTruthy();
    });

    it('are hidden in Spoolman mode, which has nowhere to put them', () => {
      render(<AdditionalSection {...baseProps} spoolmanMode={true} />);
      expect(screen.queryByLabelText('inventory.batchId')).toBeNull();
      expect(screen.queryByLabelText('inventory.owner')).toBeNull();
      expect(screen.queryByLabelText('inventory.purchaseDate')).toBeNull();
    });

    it('show what the spool has', () => {
      render(
        <AdditionalSection
          {...baseProps}
          formData={{ ...defaultFormData, batch_id: 'LOT-1', owner: 'Alice', purchase_date: '2026-03-14' }}
        />,
      );
      expect(screen.getByLabelText('inventory.batchId')).toHaveValue('LOT-1');
      expect(screen.getByLabelText('inventory.owner')).toHaveValue('Alice');
      expect(screen.getByLabelText('inventory.purchaseDate')).toHaveValue('2026-03-14');
    });

    it('report each change through updateField', () => {
      const updateField = vi.fn();
      render(<AdditionalSection {...baseProps} updateField={updateField} />);

      fireEvent.change(screen.getByLabelText('inventory.batchId'), { target: { value: 'LOT-2' } });
      fireEvent.change(screen.getByLabelText('inventory.owner'), { target: { value: 'Bob' } });
      fireEvent.change(screen.getByLabelText('inventory.purchaseDate'), { target: { value: '2026-04-05' } });

      expect(updateField).toHaveBeenCalledWith('batch_id', 'LOT-2');
      expect(updateField).toHaveBeenCalledWith('owner', 'Bob');
      expect(updateField).toHaveBeenCalledWith('purchase_date', '2026-04-05');
    });

    it('suggest owners in the styled list, not a native datalist', () => {
      const { container } = render(<AdditionalSection {...baseProps} availableOwners={['Alice', 'Bob']} />);
      expect(container.querySelector('datalist')).toBeNull();

      fireEvent.focus(screen.getByLabelText('inventory.owner'));

      expect(screen.getByRole('button', { name: 'Alice' })).toBeTruthy();
      expect(screen.getByRole('button', { name: 'Bob' })).toBeTruthy();
    });

    it('fill the owner in when one is picked', () => {
      const updateField = vi.fn();
      render(<AdditionalSection {...baseProps} updateField={updateField} availableOwners={['Alice', 'Bob']} />);

      fireEvent.focus(screen.getByLabelText('inventory.owner'));
      fireEvent.click(screen.getByRole('button', { name: 'Bob' }));

      expect(updateField).toHaveBeenCalledWith('owner', 'Bob');
    });

    it('limit the lengths to what is stored', () => {
      render(<AdditionalSection {...baseProps} />);
      expect(screen.getByLabelText('inventory.batchId')).toHaveAttribute('maxlength', '64');
      expect(screen.getByLabelText('inventory.owner')).toHaveAttribute('maxlength', '100');
    });
  });
});
