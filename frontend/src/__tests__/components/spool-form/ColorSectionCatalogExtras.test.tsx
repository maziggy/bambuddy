/**
 * Regression test for #1340: clicking a catalog color must apply its
 * extra_colors (gradient stops) and effect_type alongside hex + name.
 *
 * Previously only hex + name were copied onto the spool, so a catalog entry
 * configured as a multi-color gradient with a visual effect would degrade to
 * a flat solid swatch the moment the user picked it.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../../../i18n';
import { ColorSection } from '../../../components/spool-form/ColorSection';
import { defaultFormData } from '../../../components/spool-form/types';

function renderColorSection(opts: {
  catalogColors: Parameters<typeof ColorSection>[0]['catalogColors'];
  formData?: Partial<typeof defaultFormData>;
}) {
  const formData = {
    ...defaultFormData,
    brand: 'Bambu Lab',
    material: 'PLA',
    ...opts.formData,
  };
  const updateField = vi.fn();
  render(
    <I18nextProvider i18n={i18n}>
      <ColorSection
        formData={formData}
        updateField={updateField}
        recentColors={[]}
        onColorUsed={vi.fn()}
        catalogColors={opts.catalogColors}
      />
    </I18nextProvider>,
  );
  return { updateField };
}

describe('ColorSection — catalog color picker (#1340)', () => {
  it('applies extra_colors and effect_type from the catalog entry', () => {
    const { updateField } = renderColorSection({
      catalogColors: [
        {
          manufacturer: 'Bambu Lab',
          color_name: 'Galaxy PLA',
          hex_color: '#1a1a2e',
          material: 'PLA',
          extra_colors: 'ec984c,6cd4bc,a66eb9,d87694',
          effect_type: 'sparkle',
        },
      ],
    });

    // Catalog swatches are rendered as buttons with the hex as their background;
    // the most reliable handle is the title text built from name + manufacturer.
    const swatch = screen.getByTitle(/Galaxy PLA \(Bambu Lab/);
    fireEvent.click(swatch);

    expect(updateField).toHaveBeenCalledWith('rgba', '1A1A2EFF');
    expect(updateField).toHaveBeenCalledWith('color_name', 'Galaxy PLA');
    expect(updateField).toHaveBeenCalledWith('extra_colors', 'ec984c,6cd4bc,a66eb9,d87694');
    expect(updateField).toHaveBeenCalledWith('effect_type', 'sparkle');
  });

  it('clears existing extras when a catalog entry has none (preset replaces look)', () => {
    const { updateField } = renderColorSection({
      catalogColors: [
        {
          manufacturer: 'Bambu Lab',
          color_name: 'Plain Red',
          hex_color: '#ff0000',
          material: 'PLA',
          extra_colors: null,
          effect_type: null,
        },
      ],
      formData: { extra_colors: 'aabbcc,ddeeff', effect_type: 'sparkle' },
    });

    const swatch = screen.getByTitle(/Plain Red \(Bambu Lab/);
    fireEvent.click(swatch);

    // The catalog entry is a complete preset — picking a solid preset must
    // wipe the previously-set gradient and effect, not leave them clinging.
    expect(updateField).toHaveBeenCalledWith('extra_colors', '');
    expect(updateField).toHaveBeenCalledWith('effect_type', '');
  });

  it('leaves extras and effect untouched when a plain swatch is clicked', () => {
    // The fallback hardcoded palette renders when no catalog colors match the
    // brand/material. Those are plain hex pickers — they must NOT clobber an
    // existing gradient on the spool.
    const { updateField } = renderColorSection({
      catalogColors: [],
      formData: {
        brand: '',
        material: '',
        extra_colors: 'aabbcc,ddeeff',
        effect_type: 'sparkle',
      },
    });

    // The QUICK_COLORS palette includes Black/White/etc. Pick any one.
    const whiteSwatch = screen.getByTitle('White');
    fireEvent.click(whiteSwatch);

    expect(updateField).toHaveBeenCalledWith('rgba', 'FFFFFFFF');
    // No extra_colors / effect_type updates — those buttons aren't presets.
    const calledKeys = updateField.mock.calls.map(c => c[0]);
    expect(calledKeys).not.toContain('extra_colors');
    expect(calledKeys).not.toContain('effect_type');
  });
});
