/**
 * Empty spools of the chosen brand first in the spool weight picker (#3619).
 */
import { describe, it, expect, vi } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { render } from '../../utils';
import { AdditionalSection } from '../../../components/spool-form/AdditionalSection';
import { defaultFormData } from '../../../components/spool-form/types';

const catalog = [
  { id: 1, name: 'Bambu Lab - Plastic', weight: 250 },
  { id: 2, name: 'PolyMaker - Cardboard', weight: 140 },
  { id: 3, name: 'PolyTerra PLA - Cardboard', weight: 150 },
  { id: 4, name: 'Polymaker Plastic', weight: 190 },
  { id: 5, name: 'Polymakerx Odd', weight: 100 },
  { id: 6, name: 'Sunlu - Plastic', weight: 180 },
];

const colors = [
  { manufacturer: 'Polymaker', color_name: 'Cotton White', hex_color: '#ffffff', material: 'PolyTerra PLA' },
  { manufacturer: 'Polymaker', color_name: 'Lite Red', hex_color: '#ff0000', material: 'PolyLite PLA' },
  { manufacturer: 'Polymaker', color_name: 'Lite Carbon', hex_color: '#111111', material: 'PolyLite PLA-CF' },
  { manufacturer: 'Polymaker', color_name: 'Silk Gold', hex_color: '#aa9900', material: 'PLA Silk' },
  { manufacturer: 'Bambu Lab', color_name: 'Basic Blue', hex_color: '#0000ff', material: 'PLA Basic' },
];

function setup(brand?: string, catalogColors?: typeof colors) {
  render(
    <AdditionalSection
      formData={{ ...defaultFormData, core_weight: 0, core_weight_catalog_id: null }}
      updateField={vi.fn()}
      spoolCatalog={catalog}
      currencySymbol="$"
      availableCategories={[]}
      availableMaterialNumbers={[]}
      globalLowStockThreshold={20}
      brand={brand}
      catalogColors={catalogColors}
    />,
  );
  const input = screen.getByPlaceholderText(/spool weight/i);
  fireEvent.focus(input);
  return input;
}

const names = () =>
  screen.getAllByRole('button').map(b => b.querySelector('span.truncate')?.textContent).filter(Boolean);

describe('SpoolWeightPicker brand grouping (#3619)', () => {
  it('lists PolyMaker entries as suggested for brand Polymaker', () => {
    setup('Polymaker');
    expect(screen.getByText('Suggested')).toBeInTheDocument();
    expect(screen.getByText('All')).toBeInTheDocument();
    expect(names()).toEqual([
      'PolyMaker - Cardboard',
      'Polymaker Plastic',
      'Bambu Lab - Plastic',
      'PolyTerra PLA - Cardboard',
      'Polymakerx Odd',
      'Sunlu - Plastic',
    ]);
  });

  it('suggests PolyTerra PLA - Cardboard for brand PolyTerra', () => {
    setup('PolyTerra');
    expect(names()[0]).toBe('PolyTerra PLA - Cardboard');
    expect(names()).toHaveLength(catalog.length);
  });

  it('typed filter applies to both groups', () => {
    const input = setup('Polymaker');
    fireEvent.change(input, { target: { value: 'cardboard' } });
    expect(names()).toEqual(['PolyMaker - Cardboard', 'PolyTerra PLA - Cardboard']);
    expect(screen.getByText('Suggested')).toBeInTheDocument();
    expect(screen.getByText('All')).toBeInTheDocument();
  });

  it('keeps the list unchanged without a brand', () => {
    setup(undefined);
    expect(screen.queryByText('Suggested')).not.toBeInTheDocument();
    expect(names()).toEqual(catalog.map(c => c.name));
  });

  it('shows no headings when nothing matches the brand', () => {
    setup('Elegoo');
    expect(screen.queryByText('Suggested')).not.toBeInTheDocument();
    expect(names()).toEqual(catalog.map(c => c.name));
  });

  it('without the colour catalog only the brand itself matches', () => {
    setup('Polymaker');
    expect(names()[0]).toBe('PolyMaker - Cardboard');
    expect(names().indexOf('PolyTerra PLA - Cardboard')).toBeGreaterThan(1);
  });

  it('brand Polymaker also suggests its product lines from the catalog', () => {
    setup('Polymaker', colors);
    expect(names().slice(0, 3)).toEqual(['PolyMaker - Cardboard', 'PolyTerra PLA - Cardboard', 'Polymaker Plastic']);
    expect(names().slice(3)).toEqual(['Bambu Lab - Plastic', 'Polymakerx Odd', 'Sunlu - Plastic']);
  });

  it('brand PolyTerra also suggests its manufacturer from the catalog', () => {
    setup('PolyTerra', colors);
    expect(names().slice(0, 3)).toEqual(['PolyMaker - Cardboard', 'PolyTerra PLA - Cardboard', 'Polymaker Plastic']);
  });

  it('does not turn variant words into aliases (no "Silk" or "Basic" suggestions)', () => {
    setup('Bambu Lab', [...colors, { manufacturer: 'Bambu Lab', color_name: 'x', hex_color: '#000', material: 'Silk PLA' }]);
    // Only the plain brand matches; "PLA Basic" / "Silk PLA" yield no alias.
    expect(names()[0]).toBe('Bambu Lab - Plastic');
    expect(screen.getByText('Suggested')).toBeInTheDocument();
  });

  it('shows no empty All heading when every entry is suggested', () => {
    render(
      <AdditionalSection
        formData={{ ...defaultFormData, core_weight: 0, core_weight_catalog_id: null }}
        updateField={vi.fn()}
        spoolCatalog={[{ id: 1, name: 'Sunlu - Plastic', weight: 180 }, { id: 2, name: 'Sunlu Cardboard', weight: 150 }]}
        currencySymbol="$"
        availableCategories={[]}
        availableMaterialNumbers={[]}
        globalLowStockThreshold={20}
        brand="Sunlu"
      />,
    );
    fireEvent.focus(screen.getByPlaceholderText(/spool weight/i));
    expect(screen.getByText('Suggested')).toBeInTheDocument();
    expect(screen.queryByText('All')).not.toBeInTheDocument();
    expect(names()).toEqual(['Sunlu - Plastic', 'Sunlu Cardboard']);
  });
});
