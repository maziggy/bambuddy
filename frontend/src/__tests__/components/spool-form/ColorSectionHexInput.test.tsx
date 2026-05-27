/**
 * Regression tests for the ColorSection hex input.
 *
 * Original bug (#1055): typing 5 or 7 hex chars produced a 7-char rgba
 * ("FFFFF" + "FF" alpha = 7 chars). That passed frontend validation, survived
 * a backend PATCH (SpoolUpdate had no pattern constraint), and then bricked
 * the entire Filaments page because SpoolResponse enforced the 8-char pattern
 * on serialize and one bad row 500'd the whole list endpoint.
 *
 * Second bug (#1407): the original #1055 fix solved the "no malformed rgba"
 * problem by aggressively normalizing to 8 chars on EVERY keystroke. That
 * worked for the data contract but broke typing: after the first char the
 * controlled input value snapped to e.g. "A00000", the cursor jumped to the
 * end, and the user's next keystroke landed at position 7 — which the 7-char
 * branch then truncated away. Every keystroke past the first was lost.
 *
 * Current contract:
 *   - The hex input has its own draft state (0–6 chars) decoupled from
 *     `formData.rgba`. Typing one char at a time works naturally.
 *   - `updateField('rgba', ...)` is only called once the draft reaches a full
 *     6-char hex — at which point we append "FF" alpha for an 8-char result.
 *   - On blur, a partial draft (1–5 chars) is right-padded with '0' and
 *     committed. Preserves the #1055 invariant: anything the backend ever
 *     sees is exactly 8 hex chars.
 *   - Paste of 7/8-char strings (rare alpha-channel case) truncates to the
 *     leading 6-char RGB on input. Bambu filaments are opaque, so an alpha
 *     affordance was never exposed in the UI.
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

function rgbaCallCount(updateField: ReturnType<typeof vi.fn>): number {
  return updateField.mock.calls.filter(([key]) => key === 'rgba').length;
}

describe('ColorSection hex input — typing UX (#1407)', () => {
  it('reflects each keystroke in the draft input value (the #1407 trigger)', () => {
    // Pre-fix: after typing the first char the controlled value snapped to
    // e.g. "A00000" with cursor at position 6, so the user's next keystroke
    // landed at position 7 and got truncated by the 7-char branch. The draft
    // state must now hold whatever the user has typed, regardless of length.
    const { hexInput } = renderColorSection();

    fireEvent.change(hexInput, { target: { value: 'A' } });
    expect(hexInput.value).toBe('A');

    fireEvent.change(hexInput, { target: { value: 'AB' } });
    expect(hexInput.value).toBe('AB');

    fireEvent.change(hexInput, { target: { value: 'ABC' } });
    expect(hexInput.value).toBe('ABC');

    fireEvent.change(hexInput, { target: { value: 'ABCDE' } });
    expect(hexInput.value).toBe('ABCDE');
  });

  it('does NOT commit to formData.rgba while the draft is partial (1–5 chars)', () => {
    // Committing a partial value mid-typing was the entire cause of #1407 —
    // the controlled value snap then re-rendered the input back over what
    // the user was typing. Defer commit until the draft is a complete RGB.
    const { hexInput, updateField } = renderColorSection();

    for (const partial of ['A', 'AB', 'ABC', 'ABCD', 'ABCDE']) {
      updateField.mockClear();
      fireEvent.change(hexInput, { target: { value: partial } });
      expect(rgbaCallCount(updateField)).toBe(0);
    }
  });

  it('commits to formData.rgba once the draft reaches 6 chars', () => {
    const { hexInput, updateField } = renderColorSection();
    fireEvent.change(hexInput, { target: { value: 'FF0000' } });
    expect(lastRgba(updateField)).toBe('FF0000FF');
  });

  it('on blur, pads a partial draft to 6 chars and commits', () => {
    // Backstop: a user who leaves the field with "AB" must end up with a
    // valid form state, not a malformed rgba (#1055 invariant).
    const { hexInput, updateField } = renderColorSection();

    fireEvent.change(hexInput, { target: { value: 'AB' } });
    fireEvent.blur(hexInput);

    const rgba = lastRgba(updateField);
    expect(rgba).toBe('AB0000FF');
    expect(rgba).toMatch(/^[0-9A-F]{8}$/);
  });

  it('on blur, does NOT commit when the draft is empty', () => {
    // Clearing the field then tabbing away must not auto-fill the form with
    // a synthetic colour the user never picked.
    const { hexInput, updateField } = renderColorSection({ rgba: 'FF0000FF' });
    updateField.mockClear();

    fireEvent.change(hexInput, { target: { value: '' } });
    fireEvent.blur(hexInput);

    expect(rgbaCallCount(updateField)).toBe(0);
  });
});

describe('ColorSection hex input — backend invariant (#1055)', () => {
  it('committed rgba is always exactly 8 hex chars', () => {
    // The essential invariant: anything that reaches the backend must match
    // /^[0-9A-F]{8}$/. The new contract enforces this two ways — commit only
    // at length 6 (always padded with "FF") and pad on blur.
    const { hexInput, updateField } = renderColorSection();

    for (const input of ['F', 'FF', 'FFF', 'FFFF', 'FFFFF', 'FFFFFF']) {
      updateField.mockClear();
      fireEvent.change(hexInput, { target: { value: input } });
      fireEvent.blur(hexInput);
      const rgba = lastRgba(updateField);
      expect(rgba).toBeDefined();
      expect(rgba!.length).toBe(8);
      expect(rgba).toMatch(/^[0-9A-F]{8}$/);
    }
  });

  it('truncates paste of 7–8 chars to the leading RGB triplet', () => {
    // Pre-fix, an 8-char paste passed through and a 7-char paste dropped the
    // last char. Both are rare alpha-channel cases; Bambu filaments are
    // opaque and the UI exposes no alpha affordance, so we truncate to the
    // leading 6-char RGB and force FF alpha. Loses the (undocumented) 8-char
    // paste-with-alpha case, gains uniform commit-at-6 semantics.
    const { hexInput, updateField } = renderColorSection();

    fireEvent.change(hexInput, { target: { value: '0011223344' } });
    expect(hexInput.value).toBe('001122');
    expect(lastRgba(updateField)).toBe('001122FF');
  });

  it('strips non-hex characters', () => {
    // '#FF00ZZ' → strip '#' and 'Z' → 'FF00' (length 4, no commit yet).
    // Append two more hex chars to reach length 6, then commit.
    const { hexInput, updateField } = renderColorSection();

    fireEvent.change(hexInput, { target: { value: '#FF00ZZ' } });
    expect(hexInput.value).toBe('FF00');
    expect(rgbaCallCount(updateField)).toBe(0);

    fireEvent.change(hexInput, { target: { value: 'FF0011' } });
    expect(lastRgba(updateField)).toBe('FF0011FF');
  });
});
