/** "Show all colours" toggle of the catalog colours (#3619). */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../../../i18n';
import { ColorSection } from '../../../components/spool-form/ColorSection';
import { defaultFormData } from '../../../components/spool-form/types';

const catalogColors = [
  { manufacturer: 'Polymaker', color_name: 'Cotton White', hex_color: '#ffffff', material: 'PolyTerra PLA' },
  { manufacturer: 'Polymaker', color_name: 'Lite Red', hex_color: '#ff0000', material: 'PolyLite PLA' },
  { manufacturer: 'Polymaker', color_name: 'Lite Carbon', hex_color: '#111111', material: 'PolyLite PLA-CF' },
  { manufacturer: 'Polymaker', color_name: 'Lite PETG', hex_color: '#00ff00', material: 'PolyLite PETG' },
];

function renderSection(formData: Partial<typeof defaultFormData>, colors = catalogColors) {
  render(
    <I18nextProvider i18n={i18n}>
      <ColorSection
        formData={{ ...defaultFormData, ...formData }}
        updateField={vi.fn()}
        recentColors={[]}
        onColorUsed={vi.fn()}
        catalogColors={colors}
      />
    </I18nextProvider>,
  );
}

describe('ColorSection catalog toggle (#3619)', () => {
  it('shows product-line colours and widens to all brand colours on toggle', () => {
    renderSection({ brand: 'Polymaker', material: 'PLA' });
    expect(screen.getByTitle(/Cotton White/)).toBeTruthy();
    expect(screen.getByTitle(/Lite Red/)).toBeTruthy();
    expect(screen.queryByTitle(/Lite Carbon/)).toBeNull();
    expect(screen.queryByTitle(/Lite PETG/)).toBeNull();

    fireEvent.click(screen.getByTestId('catalog-colors-toggle'));
    expect(screen.getByTitle(/Lite Carbon/)).toBeTruthy();
    expect(screen.getByTitle(/Lite PETG/)).toBeTruthy();

    fireEvent.click(screen.getByTestId('catalog-colors-toggle'));
    expect(screen.queryByTitle(/Lite PETG/)).toBeNull();
  });

  it('has no toggle when the brand match already covers everything', () => {
    renderSection({ brand: 'Polymaker', material: '' });
    expect(screen.queryByTestId('catalog-colors-toggle')).toBeNull();
  });

  it('has no toggle without a brand (fallback palette keeps its own)', () => {
    renderSection({ brand: '', material: 'PLA' });
    expect(screen.queryByTestId('catalog-colors-toggle')).toBeNull();
  });

  it('offers the brand colours when none matches the material', () => {
    // Polymaker has no TPU: the fallback palette shows, with a way to the brand's catalog colours.
    renderSection({ brand: 'Polymaker', material: 'TPU' });
    expect(screen.queryByTitle(/Cotton White/)).toBeNull();
    const toggle = screen.getByTestId('catalog-colors-toggle');
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(toggle.textContent).toContain('All Polymaker colors');

    fireEvent.click(toggle);
    expect(screen.getByText('All Polymaker colors')).toBeTruthy();
    expect(screen.getByTitle(/Cotton White/)).toBeTruthy();
    expect(screen.getByTitle(/Lite PETG/)).toBeTruthy();
    const back = screen.getByTestId('catalog-colors-toggle');
    expect(back.getAttribute('aria-expanded')).toBe('true');

    fireEvent.click(back);
    expect(screen.queryByTitle(/Cotton White/)).toBeNull();
  });

  it('names the brand in the header while showing all, and sets aria-expanded', () => {
    renderSection({ brand: 'Polymaker', material: 'PLA' });
    const toggle = screen.getByTestId('catalog-colors-toggle');
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(toggle);
    expect(screen.getByText('All Polymaker colors')).toBeTruthy();
    expect(screen.getByTestId('catalog-colors-toggle').getAttribute('aria-expanded')).toBe('true');
  });

  it('shows the product again once brand or material changes', () => {
    const formData = { ...defaultFormData, brand: 'Polymaker', material: 'PLA' };
    const props = { updateField: vi.fn(), recentColors: [], onColorUsed: vi.fn(), catalogColors };
    const { rerender } = render(
      <I18nextProvider i18n={i18n}><ColorSection {...props} formData={formData} /></I18nextProvider>,
    );
    fireEvent.click(screen.getByTestId('catalog-colors-toggle'));
    expect(screen.getByTitle(/Lite PETG/)).toBeTruthy();
    rerender(
      <I18nextProvider i18n={i18n}><ColorSection {...props} formData={{ ...formData, material: 'PETG' }} /></I18nextProvider>,
    );
    expect(screen.queryByText('All Polymaker colors')).toBeNull();
    expect(screen.getByTestId('catalog-colors-toggle').getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(screen.getByTestId('catalog-colors-toggle'));
    rerender(
      <I18nextProvider i18n={i18n}><ColorSection {...props} formData={{ ...formData, material: 'PETG', brand: 'PolyLite' }} /></I18nextProvider>,
    );
    expect(screen.getByTestId('catalog-colors-toggle').getAttribute('aria-expanded')).toBe('false');
  });
});
