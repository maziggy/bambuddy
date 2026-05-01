/**
 * Regression tests for ColorSection extra_colors hydration (#1154 follow-up).
 *
 * Bug: when the SpoolFormModal opens to edit an existing spool, the parent
 * component renders ColorSection IMMEDIATELY (with default-empty formData),
 * then fills formData via a useEffect a tick later. The previous
 * implementation seeded ``extraColorsDraft`` from formData via
 * ``useState(formData.extra_colors)`` only at mount time, so the late
 * arrival of the saved value never made it into the input — the field
 * appeared blank even though the COLOR preview banner above was rendering
 * correctly from the now-populated formData.
 *
 * Fix: a ref-guarded useEffect resyncs the draft when the parent's
 * formData.extra_colors changes via an external update (e.g. modal opening
 * with a spool). User typing routes through ``commitExtraColors`` which
 * updates the ref before calling ``updateField``, so the resync useEffect
 * sees a no-op on round-tripped values and doesn't clobber the user's text.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../../../i18n';
import { ColorSection } from '../../../components/spool-form/ColorSection';
import { defaultFormData } from '../../../components/spool-form/types';

type FormData = typeof defaultFormData;

function renderWithFormData(initial: Partial<FormData>) {
  const formData = { ...defaultFormData, ...initial };
  const updateField = vi.fn();

  const { rerender } = render(
    <I18nextProvider i18n={i18n}>
      <ColorSection
        formData={formData}
        updateField={updateField}
        recentColors={[]}
        onColorUsed={vi.fn()}
        catalogColors={[]}
      />
    </I18nextProvider>,
  );

  return {
    updateField,
    /**
     * Re-render with a different formData object — simulates the parent's
     * useEffect-driven setFormData(...) that fills the form from the spool
     * record after the modal mounts.
     */
    updateFormData: (next: Partial<FormData>) => {
      const merged = { ...defaultFormData, ...initial, ...next };
      rerender(
        <I18nextProvider i18n={i18n}>
          <ColorSection
            formData={merged}
            updateField={updateField}
            recentColors={[]}
            onColorUsed={vi.fn()}
            catalogColors={[]}
          />
        </I18nextProvider>,
      );
    },
  };
}

function findExtraColorsInput(): HTMLInputElement {
  // The input is identified by its English placeholder copy. If the i18n
  // string changes we'll need to update this — preferred over a brittle
  // ``getByDisplayValue`` because the value is the thing under test.
  return screen.getByPlaceholderText(/EC984C,/i) as HTMLInputElement;
}

describe('ColorSection extra_colors hydration (#1154 follow-up)', () => {
  it('hydrates the input when formData.extra_colors arrives via a parent update', () => {
    // Mount with empty formData (the realistic state when SpoolFormModal
    // opens — it conditionally renders the modal before its own effect
    // fills formData from the spool).
    const { updateFormData } = renderWithFormData({ extra_colors: '' });

    const input = findExtraColorsInput();
    expect(input.value).toBe('');

    // Parent's setFormData lands a tick later with the spool's saved
    // extra_colors. The component must pick up the new value.
    updateFormData({ extra_colors: 'ec984c,6cd4bc,a66eb9,d87694' });

    const refreshedInput = findExtraColorsInput();
    expect(refreshedInput.value).toBe('ec984c,6cd4bc,a66eb9,d87694');
  });

  it('resyncs when the spool changes (e.g. user edits a different spool)', () => {
    // Mount already-populated, then swap to a different spool's data —
    // the field should track the new spool, not stick on the first value.
    const { updateFormData } = renderWithFormData({
      extra_colors: 'aabbcc,ddeeff',
    });
    expect(findExtraColorsInput().value).toBe('aabbcc,ddeeff');

    updateFormData({ extra_colors: '7f3696,006ec9' });
    expect(findExtraColorsInput().value).toBe('7f3696,006ec9');
  });

  it('does not clobber user typing on the same render cycle', () => {
    // The fix uses a ref to track our own commits so the resync effect
    // doesn't fight the user. Type into the field, trigger a parent
    // re-render (no formData change), and verify the typed value sticks.
    const { updateFormData, updateField } = renderWithFormData({
      extra_colors: '',
    });

    const input = findExtraColorsInput();
    fireEvent.change(input, { target: { value: 'ff0000,00ff00' } });

    // The commit handler updates the canonical formData via updateField.
    // Simulate the parent reflecting that change back into formData on
    // the next render — the input must keep showing the user's text, not
    // collapse to '' (the previous round-trip bug shape).
    expect(updateField).toHaveBeenCalledWith('extra_colors', 'ff0000,00ff00');
    updateFormData({ extra_colors: 'ff0000,00ff00' });

    expect(findExtraColorsInput().value).toBe('ff0000,00ff00');
  });

  it('clears the input when the spool has no extra_colors', () => {
    // The previous-spool-with-colours → next-spool-without-colours flow
    // must clear the field; otherwise opening a plain solid spool right
    // after a multi-colour one would show stale text.
    const { updateFormData } = renderWithFormData({
      extra_colors: 'aabbcc,ddeeff',
    });
    expect(findExtraColorsInput().value).toBe('aabbcc,ddeeff');

    updateFormData({ extra_colors: '' });
    expect(findExtraColorsInput().value).toBe('');
  });
});
