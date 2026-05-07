/**
 * Regression tests for the ColorSection hex input normalization (#1055).
 *
 * The original bug: typing 5 hex chars on the RRGGBB field produced a 7-char
 * rgba ("FFFFF" + "FF" alpha = 7 chars); typing 7 chars left the 7-char string
 * unpadded. Either way the value passed frontend validation, survived a backend
 * PATCH (SpoolUpdate had no pattern constraint), and then bricked the entire
 * Filaments page because SpoolResponse enforced the 8-char pattern on serialize
 * and one bad row 500'd the whole list endpoint.
 *
 * The input now emits a valid 8-char RRGGBBAA on every keystroke: shorter input
 * is right-padded with '0' and given FF alpha; 7-char input drops the stray 7th
 * char; 8-char paste passes through unchanged.
 *
 * These tests drive the onChange handler directly (via fireEvent.change) rather
 * than userEvent.type so each assertion exercises a specific input length. The
 * component itself is a controlled input whose displayed value derives from
 * formData.rgba.substring(0, 6), so the real-world UX of typing one char at a
 * time is quirkier than the handler contract — but the handler contract is
 * what this regression guards.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../../../i18n';
import { ColorSection } from '../../../components/spool-form/ColorSection';
import { defaultFormData } from '../../../components/spool-form/types';

type UpdateField = <K extends keyof typeof defaultFormData>(
  key: K,
  value: (typeof defaultFormData)[K],
) => void;

function renderColorSection(overrides: Partial<typeof defaultFormData> = {}) {
  const updateField = vi.fn() as ReturnType<typeof vi.fn> & UpdateField;
  const formData = { ...defaultFormData, ...overrides };

  render(
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

  const hexInput = screen.getByPlaceholderText('RRGGBB') as HTMLInputElement;
  return { hexInput, updateField };
}

function lastRgba(updateField: ReturnType<typeof vi.fn>): string | undefined {
  const rgbaCalls = updateField.mock.calls.filter(([key]) => key === 'rgba');
  return rgbaCalls.at(-1)?.[1] as string | undefined;
}

describe('ColorSection hex input normalization (#1055)', () => {
  it('pads a 6-char RRGGBB to 8-char RRGGBBAA with FF alpha', () => {
    const { hexInput, updateField } = renderColorSection();
    fireEvent.change(hexInput, { target: { value: 'FF0000' } });
    expect(lastRgba(updateField)).toBe('FF0000FF');
  });

  it('passes an 8-char RRGGBBAA paste through unchanged', () => {
    const { hexInput, updateField } = renderColorSection();
    fireEvent.change(hexInput, { target: { value: '00112233' } });
    expect(lastRgba(updateField)).toBe('00112233');
  });

  it('drops the stray 7th char — the exact #1055 trigger pattern', () => {
    const { hexInput, updateField } = renderColorSection();
    fireEvent.change(hexInput, { target: { value: 'FFFFFFF' } });
    // Previously emitted "FFFFFFF" (7 chars) verbatim. Must now be 8 chars.
    const rgba = lastRgba(updateField);
    expect(rgba).toBe('FFFFFFFF');
    expect(rgba).toMatch(/^[0-9A-F]{8}$/);
  });

  it('pads a 5-char input to 8 chars instead of emitting a 7-char rgba', () => {
    // 5-char + 'FF' alpha = 7 chars was the other #1055 trigger pattern.
    // Right-pad RGB to 6 with '0' so the output is always 8 chars.
    const { hexInput, updateField } = renderColorSection();
    fireEvent.change(hexInput, { target: { value: 'FFFFF' } });
    const rgba = lastRgba(updateField);
    expect(rgba).toBe('FFFFF0FF');
    expect(rgba).toMatch(/^[0-9A-F]{8}$/);
  });

  it('pads any partial input to exactly 8 chars — never 7', () => {
    // The essential invariant: for every legal input length (0..8), the
    // emitted rgba must be 8 chars. Anything else risks reintroducing #1055.
    const { hexInput, updateField } = renderColorSection();
    for (const input of ['', 'F', 'FF', 'FFF', 'FFFF', 'FFFFF', 'FFFFFF', 'FFFFFFF', 'FFFFFFFF']) {
      updateField.mockClear();
      fireEvent.change(hexInput, { target: { value: input } });
      const rgba = lastRgba(updateField);
      expect(rgba).toBeDefined();
      expect(rgba!.length).toBe(8);
      expect(rgba).toMatch(/^[0-9A-F]{8}$/);
    }
  });

  it('ignores input past 8 chars (no updateField call)', () => {
    const { hexInput, updateField } = renderColorSection({ rgba: 'FFFFFFFF' });
    updateField.mockClear();
    fireEvent.change(hexInput, { target: { value: '0011223344' } });
    expect(updateField.mock.calls.filter(([k]) => k === 'rgba')).toHaveLength(0);
  });

  it('strips non-hex characters before normalizing', () => {
    // '#FF00ZZ' → strip '#' and non-hex → 'FF00' (4 chars) → pad to 6 + FF alpha
    const { hexInput, updateField } = renderColorSection();
    fireEvent.change(hexInput, { target: { value: '#FF00ZZ' } });
    expect(lastRgba(updateField)).toBe('FF0000FF');
  });
});
