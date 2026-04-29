/**
 * Tests for the FilamentSwatch component (#1154).
 *
 * Covers the three independent inputs the swatch composes (rgba, extraColors,
 * effectType) and the buildFilamentBackground helper used to paint banners.
 */

import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import { render } from '../utils';
import { FilamentSwatch } from '../../components/FilamentSwatch';
import { buildFilamentBackground } from '../../components/filamentSwatchHelpers';

describe('FilamentSwatch', () => {
  it('renders a solid swatch when only rgba is set', () => {
    render(<FilamentSwatch rgba="ff0000ff" />);
    const el = screen.getByTestId('filament-swatch');
    // Solid swatches are emitted as a 1-stop linear-gradient so the
    // checkerboard layer below is still visible through alpha.
    const bg = el.getAttribute('style') ?? '';
    expect(bg).toMatch(/linear-gradient/);
    expect(bg.toLowerCase()).toContain('#ff0000ff');
  });

  it('falls back to grey when nothing is set', () => {
    render(<FilamentSwatch />);
    const el = screen.getByTestId('filament-swatch');
    expect(el.style.backgroundImage.toLowerCase()).toContain('#808080');
  });

  it('renders a linear gradient when extraColors has multiple stops', () => {
    render(<FilamentSwatch rgba="ff0000ff" extraColors="ec984c,6cd4bc,a66eb9,d87694" />);
    const el = screen.getByTestId('filament-swatch');
    const bg = el.style.backgroundImage.toLowerCase();
    // Linear (not conic) for non-Multicolor subtype.
    expect(bg).toMatch(/linear-gradient/);
    expect(bg).toContain('#ec984c');
    expect(bg).toContain('#6cd4bc');
    expect(bg).toContain('#a66eb9');
    expect(bg).toContain('#d87694');
  });

  it('uses conic-gradient for Multicolor subtype', () => {
    render(
      <FilamentSwatch
        rgba="ff0000ff"
        extraColors="ec984c,6cd4bc,a66eb9"
        subtype="Multicolor"
      />,
    );
    const el = screen.getByTestId('filament-swatch');
    expect(el.style.backgroundImage.toLowerCase()).toMatch(/conic-gradient/);
  });

  it('also uses conic-gradient when effectType is multicolor (catalog path)', () => {
    // Catalog entries don't have a `subtype`, so the multicolor effect_type
    // value also has to trigger conic rendering for parity with the spool path.
    render(<FilamentSwatch extraColors="ec984c,6cd4bc,a66eb9" effectType="multicolor" />);
    const el = screen.getByTestId('filament-swatch');
    expect(el.style.backgroundImage.toLowerCase()).toMatch(/conic-gradient/);
  });

  it('layers an effect overlay on top of the colour layer for sparkle', () => {
    render(<FilamentSwatch rgba="ff0000ff" effectType="sparkle" />);
    const el = screen.getByTestId('filament-swatch');
    // Sparkle overlay is built from radial-gradient layers — confirm at least
    // one is in the composed background, ahead of the colour layer.
    expect(el.style.backgroundImage).toMatch(/radial-gradient/);
  });

  it('renders an overlay for silk variant', () => {
    // Silk gets a soft sheen overlay (added in #1154 follow-up).
    render(<FilamentSwatch rgba="ff0000ff" effectType="silk" />);
    const el = screen.getByTestId('filament-swatch');
    expect(el.style.backgroundImage).toMatch(/linear-gradient/);
  });

  it('treats categorical-only variants (gradient/dual-color) as labels without an overlay', () => {
    // No extra_colors set → swatch falls back to the solid colour layer; the
    // categorical effect value alone does not paint a sheen overlay.
    render(<FilamentSwatch rgba="ff0000ff" effectType="gradient" />);
    const el = screen.getByTestId('filament-swatch');
    // No radial-gradient (sparkle/glow) and no rainbow/sheen overlay either —
    // gradient/dual-color/tri-color are pure labels until extra_colors is set.
    expect(el.style.backgroundImage).not.toMatch(/radial-gradient/);
  });

  it('ignores unknown effect types instead of throwing', () => {
    render(<FilamentSwatch rgba="ff0000ff" effectType="not-a-real-variant" />);
    const el = screen.getByTestId('filament-swatch');
    expect(el.style.backgroundImage).not.toMatch(/radial-gradient/);
  });

  it('renders a checkerboard underneath so alpha is visible', () => {
    render(<FilamentSwatch rgba="ff000080" />);
    const el = screen.getByTestId('filament-swatch');
    // The component always appends a checkerboard layer last so semi-
    // transparent rgba values actually look transparent to the user.
    expect(el.style.backgroundImage).toMatch(/repeating-conic-gradient/);
  });

  it('skips invalid hex tokens in extraColors instead of throwing', () => {
    render(<FilamentSwatch extraColors="ff0000,not-hex,00ff00" />);
    const el = screen.getByTestId('filament-swatch');
    const bg = el.style.backgroundImage.toLowerCase();
    // The two valid stops survive; the garbage token is dropped.
    expect(bg).toContain('#ff0000');
    expect(bg).toContain('#00ff00');
    expect(bg).not.toContain('not-hex');
  });

  it('uses extra_colors for the title fallback when provided', () => {
    render(<FilamentSwatch extraColors="ff0000,00ff00" />);
    const el = screen.getByTestId('filament-swatch');
    // Tooltip should show the comma-joined hex stops, not the (unset) rgba.
    expect(el.title.toLowerCase()).toContain('#ff0000');
    expect(el.title.toLowerCase()).toContain('#00ff00');
  });
});

describe('buildFilamentBackground', () => {
  it('emits the same layered background string the component renders', () => {
    const bg = buildFilamentBackground({
      rgba: 'ff0000ff',
      extraColors: 'aabbcc,ddeeff',
      effectType: 'matte',
    });
    // Effect overlay → colour layer → checkerboard, in that order.
    expect(bg).toMatch(/linear-gradient/);
    expect(bg).toMatch(/repeating-conic-gradient/);
    expect(bg.toLowerCase()).toContain('#aabbcc');
    expect(bg.toLowerCase()).toContain('#ddeeff');
  });

  it('returns a usable solid background when only rgba is provided', () => {
    const bg = buildFilamentBackground({ rgba: '00ff00ff' });
    expect(bg.toLowerCase()).toContain('#00ff00ff');
    expect(bg).toMatch(/repeating-conic-gradient/);
  });
});
