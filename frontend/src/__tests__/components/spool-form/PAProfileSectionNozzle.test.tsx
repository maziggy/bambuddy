/**
 * Regression test for the PA-Profil picker's nozzle blindness (#2618).
 *
 * When a printer has two K-profiles for the same filament that differ only in
 * nozzle size (e.g. PAHT-CF at 0.4mm K=0.042 and 0.6mm K=0.028), the picker
 * must offer BOTH and label each with its nozzle diameter — not collapse to a
 * single entry. The underlying cause lived in the fetch (it defaulted to the
 * 0.4mm nozzle and never retrieved the 0.6mm profile); this test guards the
 * rendering half: given both calibrations, the section shows both with a
 * nozzle badge so they are distinguishable.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../../../i18n';
import { PAProfileSection } from '../../../components/spool-form/PAProfileSection';
import { defaultFormData } from '../../../components/spool-form/types';
import type { PrinterWithCalibrations } from '../../../components/spool-form/types';

const printers = [
  {
    printer: { id: 1, name: 'H2D', connected: true },
    calibrations: [
      // Same filament (non-generic id → id-match), different nozzle + extruder.
      { cali_idx: 10, filament_id: 'GFN05', setting_id: '', name: 'PAHT-CF', k_value: 0.042, n_coef: 0, extruder_id: 0, nozzle_diameter: '0.4' },
      { cali_idx: 11, filament_id: 'GFN05', setting_id: '', name: 'PAHT-CF', k_value: 0.028, n_coef: 0, extruder_id: 1, nozzle_diameter: '0.6' },
    ],
  },
] as unknown as PrinterWithCalibrations[];

describe('PAProfileSection nozzle-specific profiles (#2618)', () => {
  it('renders both nozzle profiles for one filament, each with a nozzle badge', () => {
    render(
      <I18nextProvider i18n={i18n}>
        <PAProfileSection
          formData={{ ...defaultFormData, material: 'PAHT-CF', slicer_filament: 'GFN05' }}
          updateField={vi.fn()}
          printersWithCalibrations={printers}
          selectedProfiles={new Set()}
          setSelectedProfiles={vi.fn()}
          expandedPrinters={new Set(['1'])}
          setExpandedPrinters={vi.fn()}
        />
      </I18nextProvider>,
    );

    // Both nozzle-specific K values are offered — not just the 0.4mm one.
    expect(screen.getByText('K=0.042')).toBeInTheDocument();
    expect(screen.getByText('K=0.028')).toBeInTheDocument();

    // Each is labelled by its nozzle so identically-named profiles are distinct.
    expect(screen.getByText('0.4mm')).toBeInTheDocument();
    expect(screen.getByText('0.6mm')).toBeInTheDocument();
  });
});
