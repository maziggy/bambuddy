/** Name / colour order toggle of the catalog colours (#3633). */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../../../i18n';
import { ColorSection } from '../../../components/spool-form/ColorSection';
import { defaultFormData } from '../../../components/spool-form/types';
import { CATALOG_COLOR_ORDER_KEY } from '../../../components/spool-form/constants';

const catalogColors = [
  { manufacturer: 'Polymaker', color_name: 'Zeta Blue', hex_color: '#0000ff', material: 'PolyTerra PLA' },
  { manufacturer: 'Polymaker', color_name: 'Alpha Clear', hex_color: '#ffffff00', material: 'PolyTerra PLA' },
  { manufacturer: 'Polymaker', color_name: 'Mid Red', hex_color: '#ff0000', material: 'PolyTerra PLA' },
  { manufacturer: 'Polymaker', color_name: 'Other Green', hex_color: '#00ff00', material: 'PolyLite PETG' },
  { manufacturer: 'Polymaker', color_name: 'Other Red', hex_color: '#ff0000', material: 'PolyLite PETG' },
];

const Section = (props: { brand: string; material: string }) => (
  <I18nextProvider i18n={i18n}>
    <ColorSection
      formData={{ ...defaultFormData, ...props }}
      updateField={vi.fn()}
      recentColors={[]}
      onColorUsed={vi.fn()}
      catalogColors={catalogColors}
    />
  </I18nextProvider>
);

const renderSection = (brand = 'Polymaker', material = 'PLA') => render(<Section brand={brand} material={material} />);

/** Titles of the catalog swatches in DOM order. */
const swatchNames = () =>
  screen.getAllByTitle(/\(Polymaker/).map(b => (b.getAttribute('title') ?? '').replace(/ \(.*$/, ''));

describe('ColorSection catalog colour order (#3633)', () => {
  // The global test setup stubs localStorage with no-ops; use a real in-memory one here.
  beforeEach(() => {
    const store = new Map<string, string>();
    vi.stubGlobal('localStorage', {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => { store.set(k, String(v)); },
      removeItem: (k: string) => { store.delete(k); },
      clear: () => store.clear(),
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  it('defaults to name order (server order) with the name button pressed', () => {
    renderSection();
    expect(swatchNames()).toEqual(['Zeta Blue', 'Alpha Clear', 'Mid Red']);
    expect(screen.getByRole('group', { name: 'Sort catalog colors' })).toBeTruthy();
    expect(screen.getByLabelText('Sort by name').getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByLabelText('Sort by color').getAttribute('aria-pressed')).toBe('false');
  });

  it('switches to colour order and back', () => {
    renderSection();
    fireEvent.click(screen.getByLabelText('Sort by color'));
    expect(swatchNames()).toEqual(['Mid Red', 'Zeta Blue', 'Alpha Clear']);
    expect(screen.getByLabelText('Sort by color').getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(screen.getByLabelText('Sort by name'));
    expect(swatchNames()).toEqual(['Zeta Blue', 'Alpha Clear', 'Mid Red']);
  });

  it('remembers the choice across a remount', () => {
    const first = renderSection();
    fireEvent.click(screen.getByLabelText('Sort by color'));
    first.unmount();
    renderSection();
    expect(swatchNames()).toEqual(['Mid Red', 'Zeta Blue', 'Alpha Clear']);
    expect(localStorage.getItem(CATALOG_COLOR_ORDER_KEY)).toBe('color');
  });

  it('treats a garbage stored value as name order', () => {
    localStorage.setItem(CATALOG_COLOR_ORDER_KEY, 'rainbow');
    renderSection();
    expect(swatchNames()).toEqual(['Zeta Blue', 'Alpha Clear', 'Mid Red']);
  });

  it('falls back to name order when localStorage throws', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => { throw new Error('blocked'); },
      setItem: () => { throw new Error('blocked'); },
    });
    renderSection();
    expect(swatchNames()).toEqual(['Zeta Blue', 'Alpha Clear', 'Mid Red']);
    fireEvent.click(screen.getByLabelText('Sort by color'));
    expect(swatchNames()).toEqual(['Mid Red', 'Zeta Blue', 'Alpha Clear']);
  });

  it('is hidden with the fallback palette', () => {
    renderSection('', 'PLA');
    expect(screen.queryByRole('group', { name: 'Sort catalog colors' })).toBeNull();
    expect(screen.queryByLabelText('Sort by color')).toBeNull();
  });

  it('keeps the matching block first under Show all colors, each block sorted', () => {
    localStorage.setItem(CATALOG_COLOR_ORDER_KEY, 'color');
    renderSection();
    fireEvent.click(screen.getByTestId('catalog-colors-toggle'));
    expect(swatchNames()).toEqual(['Mid Red', 'Zeta Blue', 'Alpha Clear', 'Other Red', 'Other Green']);
    fireEvent.click(screen.getByLabelText('Sort by name'));
    expect(swatchNames()).toEqual(['Zeta Blue', 'Alpha Clear', 'Mid Red', 'Other Green', 'Other Red']);
  });

  it('applies the order to search results', () => {
    localStorage.setItem(CATALOG_COLOR_ORDER_KEY, 'color');
    renderSection();
    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: 'a' } });
    expect(swatchNames()).toEqual(['Mid Red', 'Zeta Blue', 'Alpha Clear']);
    expect(within(screen.getByRole('group', { name: 'Sort catalog colors' })).getAllByRole('button')).toHaveLength(2);
  });
});
